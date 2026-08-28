import queue
import tempfile
import unittest
from pathlib import Path
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config
from realtime_audio_translator.engine import RealtimeEngine, audio_devices_overlap, direction_label, drain_queue, overlay_text_from_config, safe_target_language
from realtime_audio_translator.providers import TextToSpeech, Translator, build_google_translate_request, build_openai_translation_request, google_access_token
from tests.helpers import QueuedWorker, StaticTranscriber, StoppingTranslator, write_wav


class EngineTests(unittest.TestCase):
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
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = StaticTranscriber("hello")
            engine.translator = StoppingTranslator(engine, "你好")
            engine._process_segments("speaker", QueuedWorker(wav))

        self.assertTrue(any(status.startswith("喇叭延遲 ") for status in statuses))

    def test_engine_can_overlay_original_and_translation(self):
        overlays = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["show_original_text"] = True
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: overlays.append((speaker, mine)), lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "你好"

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
            engine.translator = Translator()
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
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "hi"

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine.translator = Translator()
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
                return "konnichiwa"

        class Translator:
            def translate(self, text, source_language, target_language):
                calls.append((text, source_language, target_language))
                engine.running = False
                return "你好"

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine.translator = Translator()
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
                return "hello"

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
            engine.translator = Translator()
            engine.tts = TTS()
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
            engine.translator = StoppingTranslator(engine, "")
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
            engine.translator = StoppingTranslator(engine, "推中")
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
            engine.translator = StoppingTranslator(engine, "你好")
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
            engine.translator = StoppingTranslator(engine, "你好", last_confidence=0.3)
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
            engine.translator = StoppingTranslator(engine, "你好")
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
            engine.translator = StoppingTranslator(engine, "你好")
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
            engine.translator = StoppingTranslator(engine, "推中")
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
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "hi"

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
                engine.translator = Translator()
                engine.tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(played, [(b"\0\0", "CABLE Input")])

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
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "hi"

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
                engine.translator = Translator()
                engine.tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(spoken, [("hi", "CABLE Input")])

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
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "你好"

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
            engine.translator = Translator()
            engine.tts = TTS()
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
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "hi"

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
                engine.translator = Translator()
                engine.tts = TTS()
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
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "hi"

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
            engine.translator = Translator()
            engine.tts = TTS()
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
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "hi"

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine.translator = Translator()
            engine._speak_translation = lambda direction, translated, target, device: 2.4
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
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "hi"

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
                engine.translator = Translator()
                engine.tts = TTS()
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
                return "hello"

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
        config["speaker_device"] = "CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"
        config["tts_output_device"] = "CABLE Input"
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
        config["microphone_device"] = "CABLE Output (VB-Audio Virtual Cable) [Windows WASAPI]"
        config["tts_output_device"] = "CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"
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
        engine_module.find_device = lambda *args, **kwargs: None
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
        engine_module.find_device = lambda name, want_output: calls.append((name, want_output)) or None
        try:
            engine._start_direction("me", "", False)
        finally:
            engine_module.find_device = original_find_device

        self.assertIn(("Microphone", False), calls)
        self.assertNotIn(("CABLE Output", False), calls)

    def test_engine_start_reports_transcriber_failure(self):
        import realtime_audio_translator.engine as engine_module

        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        class BrokenTranscriber:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("Runtime missing: faster-whisper-xxl.exe")

        original_transcriber = engine_module.AudioTranscriber
        engine_module.AudioTranscriber = BrokenTranscriber
        try:
            engine.start()
        finally:
            engine_module.AudioTranscriber = original_transcriber

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

            def stop(self):
                self.stopped = True

        worker = Worker()
        engine.running = True
        engine.workers = [worker]
        engine.threads = [object()]

        engine.stop()

        self.assertFalse(engine.running)
        self.assertTrue(worker.stopped)
        self.assertEqual(engine.workers, [])
        self.assertEqual(engine.threads, [])
        self.assertEqual(statuses[-1], "已停止")


if __name__ == "__main__":
    unittest.main()
