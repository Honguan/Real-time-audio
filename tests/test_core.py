import json
import queue
import tempfile
import unittest
import wave
from pathlib import Path

from realtime_audio_translator.audio import audio_segment_active, device_name_from_label
from realtime_audio_translator.commands import parse_help_options
from realtime_audio_translator.config import DEFAULT_CONFIG, clear_cache, clear_logs, ensure_app_dirs, load_config, save_config
from realtime_audio_translator.engine import RealtimeEngine
from realtime_audio_translator.gui import PROVIDER_CHOICES, TTS_PROVIDER_CHOICES, format_overlay_line, mode_notice, overlay_clipboard_text, overlay_font_size_value, overlay_hold_seconds_value, overlay_opacity_value, overlay_visibility_action, subtitle_updates_allowed, swap_language_values
from realtime_audio_translator.logbook import ConversationLog
from realtime_audio_translator.models import list_models, model_download_command, recommend_model
from realtime_audio_translator.providers import TextToSpeech, Translator, build_google_translate_request, build_openai_translation_request


class CoreTests(unittest.TestCase):
    def test_config_round_trip_creates_expected_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            config = load_config(root)
            self.assertEqual(config["source_language"], DEFAULT_CONFIG["source_language"])
            config["target_language"] = "ja"
            save_config(root, config)
            self.assertEqual(load_config(root)["target_language"], "ja")
            self.assertTrue((root / "models").is_dir())
            self.assertTrue((root / "logs").is_dir())
            self.assertTrue((root / "cache" / "audio").is_dir())

    def test_conversation_logs_are_off_by_default(self):
        self.assertFalse(DEFAULT_CONFIG["record_logs"])

    def test_default_mode_uses_free_local_providers(self):
        self.assertEqual(DEFAULT_CONFIG["provider"], "local")
        self.assertEqual(DEFAULT_CONFIG["tts_provider"], "local")
        self.assertIn("local/offline", mode_notice(DEFAULT_CONFIG["provider"], DEFAULT_CONFIG["tts_provider"]))

    def test_clear_logs_and_cache_keep_app_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "logs" / "session.jsonl").write_text("secret", encoding="utf-8")
            (root / "cache" / "audio" / "clip.wav").write_bytes(b"audio")

            clear_logs(root)
            clear_cache(root)

            self.assertEqual(list((root / "logs").iterdir()), [])
            self.assertEqual(list((root / "cache" / "audio").iterdir()), [])

    def test_parse_help_options_extracts_choices_and_flags(self):
        help_text = """
        --model MODEL, -m MODEL
        --task {transcribe,translate}
        --output_format [{json,lrc,txt,text,vtt,srt,tsv,all} ...]
        --checkcuda, -cc
        """
        options = parse_help_options(help_text)
        self.assertEqual(options["model"]["aliases"], ["-m"])
        self.assertEqual(options["task"]["choices"], ["transcribe", "translate"])
        self.assertIn("json", options["output_format"]["choices"])
        self.assertTrue(options["checkcuda"]["flag"])

    def test_provider_request_builders_do_not_embed_secrets(self):
        openai = build_openai_translation_request("hello", "zh-TW", "en")
        self.assertEqual(openai["headers"]["Authorization"], "Bearer ${OPENAI_API_KEY}")
        self.assertIn("Translate", openai["json"]["input"])

        google = build_google_translate_request("hello", "zh-TW", "en", "project-1")
        self.assertIn("/projects/project-1:translateText", google["url"])
        self.assertEqual(google["json"]["targetLanguageCode"], "zh-TW")

    def test_translator_caches_repeated_requests(self):
        import os
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": "你好"}

        original_key = os.environ.get("OPENAI_API_KEY")
        original_post = providers_module.requests.post
        os.environ["OPENAI_API_KEY"] = "test-key"
        providers_module.requests.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
        try:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "openai"
            translator = Translator(config)
            self.assertEqual(translator.translate("hello", "en", "zh-TW"), "你好")
            self.assertEqual(translator.translate("hello", "en", "zh-TW"), "你好")
        finally:
            providers_module.requests.post = original_post
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertEqual(len(calls), 1)

    def test_local_provider_returns_text_without_cloud_request(self):
        config = DEFAULT_CONFIG.copy()
        config["provider"] = "local"
        translator = Translator(config)

        self.assertEqual(translator.translate("hello", "en", "zh-TW"), "hello")

    def test_local_provider_can_call_libretranslate_endpoint(self):
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"translatedText": "你好"}

        original_post = providers_module.requests.post
        providers_module.requests.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
        try:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["local_translate_url"] = "http://127.0.0.1:5000/translate"
            translator = Translator(config)

            self.assertEqual(translator.translate("hello", "en", "zh-TW"), "你好")
        finally:
            providers_module.requests.post = original_post

        self.assertEqual(calls[0][0][0], "http://127.0.0.1:5000/translate")
        self.assertEqual(calls[0][1]["json"]["q"], "hello")
        self.assertEqual(calls[0][1]["json"]["source"], "en")
        self.assertEqual(calls[0][1]["json"]["target"], "zh-TW")

    def test_openai_tts_requests_pcm_audio(self):
        import os
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            content = b"pcm"

            def raise_for_status(self):
                return None

        original_key = os.environ.get("OPENAI_API_KEY")
        original_post = providers_module.requests.post
        os.environ["OPENAI_API_KEY"] = "test-key"
        providers_module.requests.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
        try:
            audio = TextToSpeech(DEFAULT_CONFIG.copy()).synthesize_openai_linear16("hello")
        finally:
            providers_module.requests.post = original_post
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertEqual(audio, b"pcm")
        self.assertEqual(calls[0][1]["json"]["response_format"], "pcm")

    def test_local_tts_uses_windows_sapi(self):
        import realtime_audio_translator.providers as providers_module

        calls = []
        original_speak = providers_module.speak_windows_sapi
        providers_module.speak_windows_sapi = lambda text, device: calls.append((text, device))
        try:
            TextToSpeech(DEFAULT_CONFIG.copy()).speak_local("hello", "CABLE Input")
        finally:
            providers_module.speak_windows_sapi = original_speak

        self.assertEqual(calls, [("hello", "CABLE Input")])

    def test_conversation_log_writes_markdown_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "你好", "google")
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["translated_text"], "你好")
            md = (Path(tmp) / "session.md").read_text(encoding="utf-8")
            self.assertIn("speaker", md)
            self.assertIn("你好", md)

    def test_conversation_log_can_write_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "hi", "google", latency_seconds=1.25)
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["latency_seconds"], 1.25)

    def test_model_recommendation_prefers_turbo_on_small_cuda_vram(self):
        self.assertEqual(recommend_model(cuda_devices=1, vram_gb=4, prefer_quality=False), "large-v3-turbo")
        self.assertEqual(recommend_model(cuda_devices=0, vram_gb=0, prefer_quality=False), "medium")

    def test_model_download_command_uses_app_model_dir(self):
        command = model_download_command(Path("fw.exe"), Path("probe.wav"), "medium", Path("models"))
        self.assertEqual(command[0], "fw.exe")
        self.assertIn("--model_dir", command)
        self.assertIn("models", command)

    def test_list_models_keeps_known_download_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_models = Path(tmp) / "models"
            (app_models / "faster-whisper-medium").mkdir(parents=True)

            models = list_models(Path(tmp) / "missing", app_models)

            self.assertIn("medium", models)
            self.assertIn("large-v3-turbo", models)

    def test_package_script_builds_release_zip_with_readme(self):
        script = Path("scripts/package.ps1").read_text(encoding="utf-8")
        self.assertIn("RealtimeAudioTranslator-0.1.0-win-x64.zip", script)
        self.assertIn("README.md", script)
        self.assertIn("RUNTIME_DOWNLOADS.txt", script)

    def test_device_label_strips_hostapi_suffix(self):
        self.assertEqual(device_name_from_label("CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"), "CABLE Input (VB-Audio Virtual Cable)")

    def test_audio_segment_active_uses_rms_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            quiet = Path(tmp) / "quiet.wav"
            loud = Path(tmp) / "loud.wav"
            self._write_wav(quiet, 0)
            self._write_wav(loud, 12000)

            self.assertFalse(audio_segment_active(quiet, 0.01))
            self.assertTrue(audio_segment_active(loud, 0.01))
            self.assertTrue(audio_segment_active(quiet, 0))

    def _write_wav(self, path: Path, sample: int) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(sample.to_bytes(2, "little", signed=True) * 1600)

    def test_format_overlay_line_can_show_language(self):
        self.assertEqual(format_overlay_line("hello", "en", True), "en: hello")
        self.assertEqual(format_overlay_line("hello", "en", False), "hello")

    def test_overlay_clipboard_text_joins_visible_lines(self):
        self.assertEqual(overlay_clipboard_text("speaker", "mine"), "speaker\nmine")
        self.assertEqual(overlay_clipboard_text("", "mine"), "mine")
        self.assertEqual(overlay_clipboard_text("speaker", ""), "speaker")

    def test_overlay_opacity_value_is_bounded(self):
        self.assertEqual(overlay_opacity_value("0.7"), 0.7)
        self.assertEqual(overlay_opacity_value("bad"), 0.86)
        self.assertEqual(overlay_opacity_value("0.1"), 0.2)
        self.assertEqual(overlay_opacity_value("2"), 1.0)

    def test_overlay_font_size_value_is_bounded(self):
        self.assertEqual(overlay_font_size_value("24"), 24)
        self.assertEqual(overlay_font_size_value("bad"), 18)
        self.assertEqual(overlay_font_size_value("8"), 12)
        self.assertEqual(overlay_font_size_value("80"), 48)

    def test_overlay_hold_seconds_value_is_bounded(self):
        self.assertEqual(overlay_hold_seconds_value("5"), 5.0)
        self.assertEqual(overlay_hold_seconds_value("bad"), 8.0)
        self.assertEqual(overlay_hold_seconds_value("0"), 1.0)
        self.assertEqual(overlay_hold_seconds_value("99"), 60.0)

    def test_overlay_visibility_action(self):
        self.assertEqual(overlay_visibility_action(True), "show")
        self.assertEqual(overlay_visibility_action(False), "hide")

    def test_subtitle_updates_allowed_respects_pause(self):
        self.assertTrue(subtitle_updates_allowed(False))
        self.assertFalse(subtitle_updates_allowed(True))

    def test_swap_language_values(self):
        self.assertEqual(swap_language_values("zh", "en"), ("en", "zh"))

    def test_provider_choices_are_fixed(self):
        self.assertEqual(PROVIDER_CHOICES, ("local", "google", "openai"))
        self.assertEqual(TTS_PROVIDER_CHOICES, ("local", "google", "openai"))

    def test_mode_notice_discloses_cloud_api_cost_risk(self):
        self.assertIn("cloud API", mode_notice("google", "openai"))
        self.assertIn("may incur costs", mode_notice("google", "openai"))
        self.assertIn("local/offline", mode_notice("local", "local"))

    def test_engine_reports_segment_latency(self):
        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "你好"

        class Worker:
            def __init__(self, wav):
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            self._write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine.translator = Translator()
            engine._process_segments("speaker", Worker(wav))

        self.assertTrue(any(status.startswith("speaker latency ") for status in statuses))

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
                self.queue = queue.Queue()
                self.queue.put(wav)

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            self._write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine.translator = Translator()
            engine._process_segments("speaker", Worker(wav))

        self.assertEqual(overlays[0][0], "hello\n你好")

    def test_engine_uses_openai_tts_provider_for_mic_output(self):
        import realtime_audio_translator.engine as engine_module

        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["tts_provider"] = "openai"
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
                self._write_wav(wav, 12000)
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
                self._write_wav(wav, 12000)
                engine.running = True
                engine.transcriber = Transcriber()
                engine.translator = Translator()
                engine.tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(spoken, [("hi", "CABLE Input")])

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
                self._write_wav(wav, 12000)
                engine.running = True
                engine.transcriber = Transcriber()
                engine.translator = Translator()
                engine.tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(played, [])

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
        self.assertEqual(statuses[-1], "no audio devices")

    def test_engine_stop_stops_workers(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

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


if __name__ == "__main__":
    unittest.main()
