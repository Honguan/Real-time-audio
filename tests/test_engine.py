import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from realtime_audio_translator.asr import TranscriptionResult
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config
from realtime_audio_translator.audio import DeviceResolutionError, WorkerHealth, device_identity
from realtime_audio_translator.engine import RealtimeEngine, audio_devices_overlap, direction_label, drain_queue, overlay_text_from_config, safe_target_language
from realtime_audio_translator.providers import TextToSpeech, TranslationResult, Translator, build_google_translate_request, build_openai_translation_request, google_access_token
from tests.helpers import QueuedWorker, StaticTranscriber, StoppingTranslator, write_wav


class EngineTests(unittest.TestCase):
    def test_engine_stops_running_when_its_only_capture_direction_fails(self):
        statuses = []
        engine = RealtimeEngine(Path("."), DEFAULT_CONFIG.copy(), lambda speaker, mine: None, statuses.append)
        engine.running = True
        engine._session = "health"
        engine._active_directions = {"me"}
        error = OSError("device removed")
        health = WorkerHealth("me", "failed", "audio_io_error", str(error), time.time(), 4, error)

        engine._capture_health_changed(health, "health")

        self.assertFalse(engine.running)
        self.assertTrue(engine._cancel.is_set())
        self.assertIs(engine.capture_health["me"].error, error)
        self.assertIn("麥克風擷取失敗 [audio_io_error]：device removed；請停止後重新啟動", statuses)

    def test_capture_thread_failure_is_wired_to_engine_health(self):
        import realtime_audio_translator.audio as audio_module
        import realtime_audio_translator.engine as engine_module

        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)
        engine.running = True
        engine._session = "health-thread"
        engine.transcriber = StaticTranscriber("hello")

        with patch.object(engine_module, "find_device", return_value=1), patch.object(audio_module, "capture_wav", side_effect=RuntimeError("device removed")):
            self.assertTrue(engine._start_direction("me", "Microphone", False))
            for thread in engine.threads:
                thread.join(1)

        self.assertFalse(any(thread.is_alive() for thread in engine.threads))
        self.assertFalse(engine.running)
        self.assertEqual(engine.capture_health["me"].state, "failed")
        self.assertTrue(any("麥克風擷取失敗 [capture_fatal]：device removed" in status for status in statuses))

    def test_engine_keeps_healthy_direction_running_when_other_capture_fails(self):
        statuses = []
        engine = RealtimeEngine(Path("."), DEFAULT_CONFIG.copy(), lambda speaker, mine: None, statuses.append)
        engine.running = True
        engine._session = "health"
        engine._active_directions = {"speaker", "me"}
        engine.capture_health["speaker"] = WorkerHealth("speaker", "capturing")
        health = WorkerHealth("me", "failed", "portaudio_-9985", "device unavailable", time.time(), 4, OSError("device unavailable"))

        engine._capture_health_changed(health, "health")

        self.assertTrue(engine.running)
        self.assertFalse(engine._cancel.is_set())
        self.assertIn("麥克風擷取失敗 [portaudio_-9985]：device unavailable；請停止後重新啟動", statuses)

        engine.set_paused(False)
        engine.set_muted(False)

        self.assertEqual(statuses[-2:], ["部分可用；麥克風擷取失敗；請停止後重新啟動"] * 2)

    def test_start_status_rechecks_capture_health_after_waiting_for_callback_lock(self):
        statuses = []
        engine = RealtimeEngine(Path("."), DEFAULT_CONFIG.copy(), lambda speaker, mine: None, statuses.append)
        engine.running = True
        engine._session = "health-race"
        engine._callback_lock.acquire()
        publish = threading.Thread(target=engine._publish_capture_status, args=("health-race",))
        try:
            publish.start()
            engine.capture_health["me"] = WorkerHealth("me", "failed")
        finally:
            engine._callback_lock.release()
        publish.join(1)

        self.assertFalse(publish.is_alive())
        self.assertEqual(statuses, ["部分可用；麥克風擷取失敗；請停止後重新啟動"])

    def test_engine_rejects_identical_fixed_languages_before_start(self):
        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["source_language"] = "en"
        config["target_language"] = "en"
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        engine.start()

        self.assertFalse(engine.running)
        self.assertEqual(statuses, ["來源與目標語言不可相同"])

    def test_engine_uses_configured_log_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "custom-logs"
            config = DEFAULT_CONFIG.copy()
            config["record_logs"] = True
            config["log_dir"] = str(log_dir)

            engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

            self.assertEqual(engine.log.jsonl_path.parent, log_dir)

    def test_engine_reports_segment_latency(self):
        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False

        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append, state_root)
            wav = state_root / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("hello")
            engine._pipeline("speaker").translator = StoppingTranslator(engine, "你好")
            engine._process_segments("speaker", QueuedWorker(wav))
            self.assertIsInstance(load_config(state_root)["last_latency_seconds"], float)
            self.assertFalse(wav.exists())

        self.assertTrue(any(status.startswith("喇叭延遲 ") for status in statuses))

    def test_engine_reports_bounded_queue_overload_metrics(self):
        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wavs = [root / f"clip-{index}.wav" for index in range(3)]
            for wav in wavs:
                write_wav(wav, 12000)
            worker = QueuedWorker(wavs[0])
            worker.queue = queue.Queue(maxsize=3)
            for wav in wavs:
                worker.queue.put_nowait(wav)
            worker.dropped_segments = 4
            engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)
            engine.running = True
            engine.transcriber = StaticTranscriber("hello")
            engine._pipeline("speaker").translator = StoppingTranslator(engine, "你好")

            engine._process_segments("speaker", worker)

            self.assertEqual(engine.pipelines["speaker"].metrics["last_queue_depth"], 2)
            self.assertEqual(engine.pipelines["speaker"].metrics["last_dropped_segments"], 4)
            self.assertTrue(any("處理落後，已略過 4 段；佇列 2/3" in status for status in statuses))
            self.assertFalse(wavs[0].exists())
            drain_queue(worker.queue)

    def test_engine_can_overlay_original_and_translation(self):
        overlays = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["show_original_text"] = True
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: overlays.append((speaker, mine)), lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", "en")

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("你好", 0.8)

        class Worker:
            maxsize = 3

            def __init__(self, wav):
                self.queue = self
                self.wav = wav

            def get(self, timeout):
                engine.running = False
                return self.wav

            def qsize(self):
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._pipeline("speaker").translator = Translator()
            engine._process_segments("speaker", Worker(wav))

        self.assertEqual(overlays[0][0], "en: hello\nzh: 你好")

    def test_engine_speaker_capture_uses_auto_language(self):
        languages = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                languages.append(source_language)
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("hi", 0.8)

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._pipeline("speaker").translator = Translator()
            engine._process_segments("speaker", Worker(wav))

        self.assertEqual(languages, ["auto"])

    def test_engine_uses_detected_language_when_source_is_auto(self):
        overlays = []
        calls = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["target_language"] = "auto"
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: overlays.append((speaker, mine)), lambda status: None)

        class Transcriber:
            last_language = "ja"

            def transcribe(self, wav, source_language):
                return TranscriptionResult("konnichiwa", "ja", 0.9)

        class Translator:
            def translate(self, text, source_language, target_language):
                calls.append((text, source_language, target_language))
                engine.running = False
                return TranslationResult("你好", 0.8)

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._pipeline("speaker").translator = Translator()
            engine._process_segments("speaker", Worker(wav))

        self.assertEqual(calls, [("konnichiwa", "ja", "zh")])
        self.assertEqual(overlays[0][0], "ja: konnichiwa\nzh: 你好")

    def test_overlay_text_can_toggle_original_and_translation(self):
        config = DEFAULT_CONFIG.copy()
        config["show_original_text"] = True
        config["show_translated_text"] = True
        self.assertEqual(overlay_text_from_config("source", "translated", "en", "zh", config), "en: source\nzh: translated")

        config["show_original_text"] = False
        self.assertEqual(overlay_text_from_config("source", "translated", "en", "zh", config), "zh: translated")

        config["show_original_text"] = True
        config["show_translated_text"] = False
        self.assertEqual(overlay_text_from_config("source", "translated", "en", "zh", config), "en: source")

        config["show_language_labels"] = False
        self.assertEqual(overlay_text_from_config("source", "translated", "en", "zh", config), "source")

    def test_engine_shows_original_when_translation_fails(self):
        overlays = []
        statuses = []
        spoken = []
        logged = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["source_language"] = "en"
        config["target_language"] = "zh"
        config["virtual_mic_enabled"] = True
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: overlays.append((speaker, mine)), statuses.append)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                raise RuntimeError("找不到 en→zh 的本機翻譯模型")

        class TTS:
            def speak_local(self, text, device):
                spoken.append((text, device))

        class Log:
            def append(self, *args, **kwargs):
                logged.append((args, kwargs))

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._pipeline("me").translator = Translator()
            engine._pipeline("me").tts = TTS()
            engine.log = Log()
            engine._process_segments("me", Worker(wav))

        self.assertEqual(overlays[0][1], "en: hello")
        self.assertIn("麥克風：翻譯失敗：找不到 en→zh 的本機翻譯模型", statuses)
        self.assertEqual(spoken, [])
        self.assertEqual(logged, [])

    def test_engine_records_empty_translation_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("hello")
            engine._pipeline("speaker").translator = StoppingTranslator(engine, "")
            engine._process_segments("speaker", QueuedWorker(wav))

        self.assertTrue(engine.config["last_translation_empty"])

    def test_engine_remembers_last_translation_for_glossary_fix(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("push mid")
            engine._pipeline("me").translator = StoppingTranslator(engine, "推中")
            engine._process_segments("me", QueuedWorker(wav))

        self.assertEqual(engine.config["last_source_text"], "push mid")
        self.assertEqual(engine.config["last_translated_text"], "推中")

    def test_engine_reports_confidence_status_after_successful_segment(self):
        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("hello", last_language="en", last_language_probability=0.92)
            engine._pipeline("speaker").translator = StoppingTranslator(engine, "你好")
            engine._process_segments("speaker", QueuedWorker(wav))

        self.assertIn("喇叭延遲", statuses[-1])
        self.assertIn("本機免費模式", statuses[-1])
        self.assertIn("翻譯服務 本機", statuses[-1])
        self.assertIn("延遲", statuses[-1])

    def test_engine_records_translation_confidence_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("hello")
            engine._pipeline("me").translator = StoppingTranslator(engine, "你好", last_confidence=0.3)
            engine._process_segments("me", QueuedWorker(wav))

        self.assertEqual(engine.config["last_translation_confidence"], 0.3)

    def test_engine_records_asr_confidence_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("hello", last_confidence=0.4)
            engine._pipeline("me").translator = StoppingTranslator(engine, "你好")
            engine._process_segments("me", QueuedWorker(wav))

        self.assertEqual(engine.config["last_asr_confidence"], 0.4)

    def test_engine_records_language_confidence_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["source_language"] = "auto"
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("hello", last_language="en", last_language_probability=0.42)
            engine._pipeline("me").translator = StoppingTranslator(engine, "你好")
            engine._process_segments("me", QueuedWorker(wav))

        self.assertEqual(engine.config["last_detected_language"], "en")
        self.assertEqual(engine.config["last_language_confidence"], 0.42)

    def test_engine_records_speech_speed_for_auto_tuning(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["segment_seconds"] = 2.0
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("push mid now")
            engine._pipeline("me").translator = StoppingTranslator(engine, "推中")
            engine._process_segments("me", QueuedWorker(wav))

        self.assertEqual(engine.config["last_speech_units_per_second"], 1.5)

    def test_engine_uses_openai_tts_provider_for_mic_output(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_provider"] = "openai"
        config["virtual_mic_enabled"] = True
        played = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("hi", 0.8)

        class TTS:
            def synthesize_google_linear16(self, text, language_code):
                raise AssertionError("google tts should not be used")

            def synthesize_openai_linear16(self, text):
                return b"\0\0"

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        original_play = engine_module.play_linear16
        engine_module.play_linear16 = lambda audio, device: played.append((audio, device))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "clip.wav"
                write_wav(wav, 12000)
                engine.running = True
                engine.transcriber = Transcriber()
                engine._pipeline("me").translator = Translator()
                engine._pipeline("me").tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(played, [(b"\0\0", "")])

    def test_engine_uses_local_tts_provider_for_mic_output(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_provider"] = "local"
        config["virtual_mic_enabled"] = True
        spoken = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("hi", 0.8)

        class TTS:
            def speak_local(self, text, device):
                spoken.append((text, device))

            def synthesize_google_linear16(self, text, language_code):
                raise AssertionError("cloud tts should not be used")

            def synthesize_openai_linear16(self, text):
                raise AssertionError("cloud tts should not be used")

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        original_play = engine_module.play_linear16
        engine_module.play_linear16 = lambda audio, device: (_ for _ in ()).throw(AssertionError("pcm playback should not be used"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "clip.wav"
                write_wav(wav, 12000)
                engine.running = True
                engine.transcriber = Transcriber()
                engine._pipeline("me").translator = Translator()
                engine._pipeline("me").tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(spoken, [("hi", "")])

    def test_engine_can_speak_speaker_translation_to_listener_output(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_provider"] = "local"
        config["speaker_tts_enabled"] = True
        config["speaker_tts_output_device"] = ""
        spoken = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            last_language = "en"

            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("你好", 0.8)

        class TTS:
            def speak_local(self, text, device):
                spoken.append((text, device))

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._pipeline("speaker").translator = Translator()
            engine._pipeline("speaker").tts = TTS()
            engine._process_segments("speaker", Worker(wav))

        self.assertEqual(spoken, [("你好", "")])

    def test_engine_requires_virtual_mic_enabled_for_tts_output(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_enabled"] = True
        config["tts_provider"] = "openai"
        config["virtual_mic_enabled"] = False
        played = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("hi", 0.8)

        class TTS:
            def synthesize_google_linear16(self, text, language_code):
                return b"\0\0"

            def synthesize_openai_linear16(self, text):
                return b"\0\0"

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        original_play = engine_module.play_linear16
        engine_module.play_linear16 = lambda audio, device: played.append((audio, device))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "clip.wav"
                write_wav(wav, 12000)
                engine.running = True
                engine.transcriber = Transcriber()
                engine._pipeline("me").translator = Translator()
                engine._pipeline("me").tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(played, [])

    def test_engine_can_start_muted_for_push_to_talk_mode(self):
        config = DEFAULT_CONFIG.copy()
        config["start_muted"] = True

        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        self.assertTrue(engine.muted)

    def test_engine_records_tts_failure_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_provider"] = "local"
        config["virtual_mic_enabled"] = True
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("hi", 0.8)

        class TTS:
            def speak_local(self, text, device):
                raise RuntimeError("no audio")

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._pipeline("me").translator = Translator()
            engine._pipeline("me").tts = TTS()
            engine._process_segments("me", Worker(wav))

        self.assertTrue(engine.config["last_tts_failed"])

    def test_engine_records_tts_latency_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_provider"] = "local"
        config["virtual_mic_enabled"] = True
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("hi", 0.8)

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._pipeline("me").translator = Translator()
            engine._speak_translation = lambda direction, translated, target, device, session=None: 2.4
            engine._process_segments("me", Worker(wav))

        self.assertEqual(engine.config["last_tts_latency_seconds"], 2.4)

    def test_engine_can_disable_tts_output(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_enabled"] = False
        played = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return TranscriptionResult("hello", source_language)

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return TranslationResult("hi", 0.8)

        class TTS:
            def synthesize_google_linear16(self, text, language_code):
                return b"\0\0"

            def synthesize_openai_linear16(self, text):
                return b"\0\0"

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        original_play = engine_module.play_linear16
        engine_module.play_linear16 = lambda audio, device: played.append((audio, device))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "clip.wav"
                write_wav(wav, 12000)
                engine.running = True
                engine.transcriber = Transcriber()
                engine._pipeline("me").translator = Translator()
                engine._pipeline("me").tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(played, [])

    def test_engine_skips_disabled_audio_source(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["speaker_enabled"] = False
        transcribed = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                transcribed.append(wav)
                engine.running = False
                return TranscriptionResult("hello", source_language)

        class Worker:
            def __init__(self, wav):
                self.queue = self
                self.wav = wav

            def get(self, timeout):
                engine.running = False
                return self.wav

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._process_segments("speaker", Worker(wav))

        self.assertEqual(transcribed, [])

    def test_engine_start_ignores_disabled_capture_sources(self):
        import realtime_audio_translator.engine as engine_module

        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["speaker_enabled"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)
        started = []

        original_transcriber = engine_module.AudioTranscriber
        engine_module.AudioTranscriber = lambda *args, **kwargs: object()
        engine._start_direction = lambda direction, device_hint, loopback: started.append(direction) or True
        try:
            engine.start()
        finally:
            engine_module.AudioTranscriber = original_transcriber

        self.assertEqual(started, ["me"])

    def test_engine_start_skips_speaker_capture_matching_tts_output(self):
        import realtime_audio_translator.engine as engine_module

        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        identity = device_identity({
            "index": 1,
            "name": "CABLE Input",
            "hostapi": "Windows WASAPI",
            "input_channels": 0,
            "output_channels": 2,
            "default_samplerate": 48000,
        })
        config["speaker_device"] = identity
        config["tts_output_device"] = identity
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)
        started = []

        original_transcriber = engine_module.AudioTranscriber
        engine_module.AudioTranscriber = lambda *args, **kwargs: object()
        engine._start_direction = lambda direction, device_hint, loopback: started.append(direction) or True
        try:
            engine.start()
        finally:
            engine_module.AudioTranscriber = original_transcriber

        self.assertEqual(started, ["me"])
        self.assertEqual(statuses[-1], "執行中；喇叭擷取已略過：和 TTS 輸出相同")

    def test_engine_start_skips_microphone_capture_matching_virtual_mic_output(self):
        import realtime_audio_translator.engine as engine_module

        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["speaker_enabled"] = False
        identity = device_identity({
            "index": 2,
            "name": "CABLE Output",
            "hostapi": "Windows WASAPI",
            "input_channels": 2,
            "output_channels": 0,
            "default_samplerate": 48000,
        })
        config["microphone_device"] = identity
        config["virtual_mic_input_device"] = identity
        config["virtual_mic_enabled"] = True
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)
        started = []

        original_transcriber = engine_module.AudioTranscriber
        engine_module.AudioTranscriber = lambda *args, **kwargs: object()
        engine._start_direction = lambda direction, device_hint, loopback: started.append((direction, device_hint, loopback)) or True
        try:
            engine.start()
        finally:
            engine_module.AudioTranscriber = original_transcriber

        self.assertEqual(started, [])
        self.assertEqual(statuses[-1], "沒有可用音訊裝置；麥克風擷取已略過：和虛擬麥克風輸出相同")

    def test_engine_start_stops_when_no_audio_devices_start(self):
        import realtime_audio_translator.engine as engine_module

        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        class Transcriber:
            def __init__(self, *args, **kwargs):
                return None

        original_transcriber = engine_module.AudioTranscriber
        original_find_device = engine_module.find_device
        engine_module.AudioTranscriber = Transcriber
        def unavailable_device(*args, **kwargs):
            raise DeviceResolutionError("找不到音訊裝置")

        engine_module.find_device = unavailable_device
        try:
            engine.start()
        finally:
            engine_module.AudioTranscriber = original_transcriber
            engine_module.find_device = original_find_device

        self.assertFalse(engine.running)
        self.assertEqual(statuses[-1], "沒有可用音訊裝置")

    def test_engine_default_microphone_capture_uses_microphone_not_cable_output(self):
        import realtime_audio_translator.engine as engine_module

        calls = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        original_find_device = engine_module.find_device
        def unavailable_device(name, want_output):
            calls.append((name, want_output))
            raise DeviceResolutionError("找不到音訊裝置")

        engine_module.find_device = unavailable_device
        try:
            engine._start_direction("me", "", False)
        finally:
            engine_module.find_device = original_find_device

        self.assertEqual(calls, [("", False)])

    def test_engine_start_reports_transcriber_failure(self):
        import realtime_audio_translator.engine as engine_module

        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append, state_root)

            class BrokenTranscriber:
                def __init__(self, *args, **kwargs):
                    raise RuntimeError("Runtime missing: faster-whisper-xxl.exe")

            original_transcriber = engine_module.AudioTranscriber
            engine_module.AudioTranscriber = BrokenTranscriber
            try:
                engine.start()
            finally:
                engine_module.AudioTranscriber = original_transcriber

            self.assertTrue(load_config(state_root)["last_asr_failed"])

        self.assertFalse(engine.running)
        self.assertEqual(statuses[-1], "找不到 runtime：faster-whisper-xxl.exe")
        self.assertTrue(engine.config["last_asr_failed"])

    def test_engine_stop_stops_workers(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        statuses = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        class Worker:
            def __init__(self):
                self.stopped = False
                self.queue = queue.Queue()

            def stop(self):
                self.stopped = True

        worker = Worker()
        engine.running = True
        engine.workers = [worker]
        class Thread:
            def __init__(self):
                self.joined = False

            def join(self, timeout=None):
                self.joined = True

            def is_alive(self):
                return False

        thread = Thread()
        engine.threads = [thread]

        engine.stop()

        self.assertFalse(engine.running)
        self.assertTrue(worker.stopped)
        self.assertTrue(thread.joined)
        self.assertEqual(engine.workers, [])
        self.assertEqual(engine.threads, [])
        self.assertEqual(statuses[-1], "已停止")

    def test_stop_before_start_prevents_delayed_start(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)
        transcribers = []
        original_transcriber = engine_module.AudioTranscriber
        engine_module.AudioTranscriber = lambda *args, **kwargs: transcribers.append(object())
        try:
            engine.stop()
            engine.start()
        finally:
            engine_module.AudioTranscriber = original_transcriber

        self.assertFalse(engine.running)
        self.assertEqual(transcribers, [])

    def test_stop_timeout_is_honored_when_callback_is_blocked(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        statuses = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)
        engine.running = True
        engine._callback_lock.acquire()
        started = time.perf_counter()
        try:
            engine.stop(join_timeout=0.01)
        finally:
            engine._callback_lock.release()

        self.assertLess(time.perf_counter() - started, 0.2)
        self.assertEqual(statuses[-1], "停止逾時：仍有 1 個背景工作")

    def test_stopped_sessions_cannot_publish_after_blocked_calls_return(self):
        for blocked_stage in ("asr", "translation", "tts"):
            with self.subTest(blocked_stage=blocked_stage), tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["record_logs"] = False
                config["speaker_tts_enabled"] = blocked_stage == "tts"
                config["tts_provider"] = "local"
                overlays = []
                statuses = []
                entered = threading.Event()
                release = threading.Event()
                engine = RealtimeEngine(Path("."), config, lambda speaker, mine: overlays.append((speaker, mine)), statuses.append)

                def block(stage):
                    if blocked_stage == stage:
                        entered.set()
                        release.wait()

                class Transcriber:
                    def transcribe(self, wav, source_language):
                        block("asr")
                        return TranscriptionResult("stale", source_language)

                class Translator:
                    def translate(self, text, source_language, target_language):
                        block("translation")
                        return TranslationResult("過期", 0.8)

                class Tts:
                    def speak_local(self, text, device, cancel_event=None):
                        if blocked_stage == "tts":
                            entered.set()
                            (cancel_event or release).wait()

                wav = Path(tmp) / "clip.wav"
                write_wav(wav, 12000)
                engine.running = True
                engine._session = 1
                engine.transcriber = Transcriber()
                engine._pipeline("speaker").translator = Translator()
                engine._pipeline("speaker").tts = Tts()
                worker = QueuedWorker(wav)
                worker.stop = lambda: None
                thread = threading.Thread(target=engine._process_segments, args=("speaker", worker, 1))
                engine.workers = [worker]
                engine.threads = [thread]
                thread.start()
                self.assertTrue(entered.wait(1))

                engine.stop(join_timeout=0.01)
                callbacks_after_stop = (len(overlays), len(statuses))
                engine.running = True
                engine._session = 2
                engine._cancel = threading.Event()
                release.set()
                thread.join(1)

                self.assertFalse(thread.is_alive())
                self.assertEqual((len(overlays), len(statuses)), callbacks_after_stop)

    def test_one_hundred_start_stop_cycles_leave_no_worker_threads(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["speaker_enabled"] = True
        config["microphone_enabled"] = False
        config["tts_enabled"] = False
        class Worker:
            def __init__(self):
                self.queue = queue.Queue()

            def stop(self):
                return None

        created_threads = []

        def start_direction(direction, device_hint, loopback):
            worker = Worker()
            thread = threading.Thread(target=engine._cancel.wait)
            created_threads.append(thread)
            engine.workers.append(worker)
            engine.threads.append(thread)
            thread.start()
            return True

        original_transcriber = engine_module.AudioTranscriber
        engine_module.AudioTranscriber = lambda *args, **kwargs: object()
        sessions = set()
        try:
            for _ in range(100):
                engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)
                engine._start_direction = start_direction
                engine.start()
                sessions.add(engine._session)
                engine.stop(join_timeout=1)
                self.assertEqual(engine.threads, [])
        finally:
            engine_module.AudioTranscriber = original_transcriber

        self.assertEqual(len(sessions), 100)
        self.assertFalse(any(thread.is_alive() for thread in created_threads))

    def test_each_session_uses_its_own_audio_cache(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        cache_dirs = []
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)
        engine.running = True
        engine._session = "unique"

        class Worker:
            def __init__(self, cache_dir, *args):
                cache_dirs.append(cache_dir)

            def run(self, direction):
                return None

        class Thread:
            def __init__(self, **kwargs):
                return None

            def start(self):
                return None

        original_find_device = engine_module.find_device
        original_worker = engine_module.SegmentWorker
        original_thread = engine_module.threading.Thread
        engine_module.find_device = lambda *args, **kwargs: 1
        engine_module.SegmentWorker = Worker
        engine_module.threading.Thread = Thread
        try:
            self.assertTrue(engine._start_direction("speaker", "Speakers", True))
        finally:
            engine_module.find_device = original_find_device
            engine_module.SegmentWorker = original_worker
            engine_module.threading.Thread = original_thread

        self.assertEqual(cache_dirs[0].name, "session-unique")

    def test_speaker_and_microphone_use_independent_pipeline_state(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)
        engine.running = True
        engine._session = "pipeline"

        class Worker:
            def __init__(self, *args):
                return None

            def run(self, direction):
                return None

        class Thread:
            def __init__(self, **kwargs):
                return None

            def start(self):
                return None

        original_find_device = engine_module.find_device
        original_worker = engine_module.SegmentWorker
        original_thread = engine_module.threading.Thread
        engine_module.find_device = lambda *args, **kwargs: 1
        engine_module.SegmentWorker = Worker
        engine_module.threading.Thread = Thread
        try:
            engine._start_direction("speaker", "Speakers", True)
            engine._start_direction("me", "Microphone", False)
        finally:
            engine_module.find_device = original_find_device
            engine_module.SegmentWorker = original_worker
            engine_module.threading.Thread = original_thread

        self.assertIsNot(engine.pipelines["speaker"].translator, engine.pipelines["me"].translator)
        self.assertIsNot(engine.pipelines["speaker"].tts, engine.pipelines["me"].tts)
        self.assertIsNot(engine.pipelines["speaker"].metrics, engine.pipelines["me"].metrics)
        self.assertNotIn("last_source_text", engine.pipelines["speaker"].config)
        self.assertNotIn("last_source_text", engine.pipelines["me"].config)

        speaker = engine.pipelines["speaker"].translator
        microphone = engine.pipelines["me"].translator
        for translator, prefix in ((speaker, "speaker"), (microphone, "microphone")):
            translator.config["provider"] = "local"
            translator.config["translation_cache_enabled"] = False
            translator._local_translate = lambda text, source, target, prefix=prefix: f"{prefix}:{text}"
        threads = [
            threading.Thread(target=translator.translate, args=(f"{prefix}-{index}", "en", "zh"))
            for translator, prefix in ((speaker, "speaker"), (microphone, "microphone"))
            for index in range(20)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertTrue(all(source.startswith("speaker-") for source, _ in speaker.context))
        self.assertTrue(all(source.startswith("microphone-") for source, _ in microphone.context))

    def test_parallel_direction_processing_keeps_results_and_metrics_isolated(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_enabled"] = False
        overlays = []
        overlay_lock = threading.Lock()
        complete = threading.Event()
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: record_overlay(speaker, mine), lambda status: None)

        def record_overlay(speaker, mine):
            with overlay_lock:
                overlays.append((speaker, mine))
                if len(overlays) == 2:
                    complete.set()

        class Transcriber:
            def transcribe(self, wav, source_language):
                if wav.stem == "speaker":
                    return TranscriptionResult("speaker text", "ja", 0.91, 0.81)
                return TranscriptionResult("microphone text", "en", 0.92, 0.82)

        barrier = threading.Barrier(2)

        class Translator:
            def __init__(self, direction):
                self.direction = direction

            def translate(self, text, source_language, target_language):
                barrier.wait()
                return TranslationResult(f"{self.direction} translation", 0.7 if self.direction == "speaker" else 0.6)

        with tempfile.TemporaryDirectory() as tmp:
            speaker_wav = Path(tmp) / "speaker.wav"
            microphone_wav = Path(tmp) / "microphone.wav"
            write_wav(speaker_wav, 12000)
            write_wav(microphone_wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine._pipeline("speaker").translator = Translator("speaker")
            engine._pipeline("me").translator = Translator("microphone")
            threads = [
                threading.Thread(target=engine._process_segments, args=("speaker", QueuedWorker(speaker_wav))),
                threading.Thread(target=engine._process_segments, args=("me", QueuedWorker(microphone_wav))),
            ]
            for thread in threads:
                thread.start()
            self.assertTrue(complete.wait(1))
            engine.running = False
            for thread in threads:
                thread.join(1)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertIn(("ja: speaker text\nzh: speaker translation", ""), overlays)
        self.assertIn(("", "zh: microphone text\nen: microphone translation"), overlays)
        self.assertEqual(engine.pipelines["speaker"].metrics["last_language_confidence"], 0.91)
        self.assertEqual(engine.pipelines["me"].metrics["last_language_confidence"], 0.92)
        self.assertEqual(engine.pipelines["speaker"].metrics["last_translation_confidence"], 0.7)
        self.assertEqual(engine.pipelines["me"].metrics["last_translation_confidence"], 0.6)


if __name__ == "__main__":
    unittest.main()
