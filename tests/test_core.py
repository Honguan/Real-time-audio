import json
import queue
import sys
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from realtime_audio_translator.audio import audio_segment_active, device_name_from_label
from realtime_audio_translator.asr import AudioTranscriber, add_runtime_dll_directory, add_xxl_data
from realtime_audio_translator.commands import parse_help_options
from realtime_audio_translator.config import DEFAULT_CONFIG, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, save_config
from realtime_audio_translator.engine import RealtimeEngine, drain_queue, overlay_text_from_config
from realtime_audio_translator.gui import LANGUAGE_CHOICES, PERFORMANCE_CHOICES, PROVIDER_CHOICES, TTS_PROVIDER_CHOICES, TranslatorApp, format_overlay_line, mode_notice, overlay_clipboard_text, overlay_font_size_value, overlay_hold_seconds_value, overlay_opacity_value, overlay_visibility_action, performance_segment_seconds, subtitle_updates_allowed, swap_language_values, troubleshooting_action, visible_setting_keys
from realtime_audio_translator.logbook import ConversationLog
from realtime_audio_translator.models import cuda_hardware_from_check_output, list_models, model_available, model_download_command, model_install_message, recommend_model
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
            self.assertTrue((root / "cache" / "temp_audio").is_dir())
            self.assertTrue((root / "exports" / "subtitles").is_dir())

    def test_app_dirs_create_empty_glossary_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            glossary = root / "glossary.json"
            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {})

            glossary.write_text(json.dumps({"Dragon Pit": "龍坑"}), encoding="utf-8")
            ensure_app_dirs(root)
            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {"Dragon Pit": "龍坑"})

    def test_app_dirs_create_commands_json_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            commands = root / "commands.json"
            self.assertEqual(json.loads(commands.read_text(encoding="utf-8")), {})

            commands.write_text(json.dumps({"model": {"choices": ["medium"]}}), encoding="utf-8")
            ensure_app_dirs(root)
            self.assertEqual(json.loads(commands.read_text(encoding="utf-8")), {"model": {"choices": ["medium"]}})

    def test_ensure_glossary_file_creates_parent_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "nested" / "glossary.json"
            self.assertEqual(ensure_glossary_file(glossary), glossary)
            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {})

            glossary.write_text(json.dumps({"mid lane": "中路"}), encoding="utf-8")
            ensure_glossary_file(glossary)
            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {"mid lane": "中路"})

    def test_conversation_logs_are_off_by_default(self):
        self.assertFalse(DEFAULT_CONFIG["record_logs"])
        self.assertEqual(DEFAULT_CONFIG["log_dir"], str(Path.home() / ".realtime-audio" / "logs"))
        self.assertEqual(DEFAULT_CONFIG["tts_rate"], 0)
        self.assertEqual(DEFAULT_CONFIG["tts_volume"], 100)
        self.assertEqual(DEFAULT_CONFIG["tts_voice_name"], "")
        self.assertTrue(DEFAULT_CONFIG["show_translated_text"])

    def test_advanced_settings_expose_openai_tts_options(self):
        settings = visible_setting_keys(True)

        self.assertIn("openai_model", settings)
        self.assertIn("openai_tts_model", settings)
        self.assertIn("openai_tts_voice", settings)

    def test_record_logs_toggle_saves_immediately(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('ttk.Checkbutton(frame, text="Record logs", variable=self.record_logs, command=self._save)', gui_source)
        self.assertIn('ttk.Checkbutton(frame, text="Show translation", variable=self.show_translated_text, command=self._save)', gui_source)

    def test_open_logs_button_opens_configured_log_dir(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Open logs", self._open_logs)', gui_source)
        self.assertIn('def _open_logs(self) -> None:', gui_source)
        self.assertIn('subprocess.Popen(["explorer", str(path)])', gui_source)

    def test_open_app_folder_button_opens_app_dir(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Open app folder", self._open_app_dir)', gui_source)
        self.assertIn('def _open_app_dir(self) -> None:', gui_source)
        self.assertIn('path = APP_DIR', gui_source)

    def test_google_json_picker_saves_immediately(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('self.vars["google_service_account_json"].set(path)\n            self._save()', gui_source)

    def test_device_model_voice_choices_save_immediately(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('elif key.endswith("device") or key in ("model", "tts_voice_name"):\n                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=[])\n                widget.bind("<<ComboboxSelected>>", lambda _event: self._save())', gui_source)

    def test_push_to_talk_button_holds_unmute(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('ptt_button = ttk.Button(buttons, text="Push to talk")', gui_source)
        self.assertIn('ptt_button.bind("<ButtonPress-1>", lambda _event: self._push_to_talk(True))', gui_source)
        self.assertIn('ptt_button.bind("<ButtonRelease-1>", lambda _event: self._push_to_talk(False))', gui_source)
        self.assertIn('self.engine.set_muted(False)', gui_source)

    def test_subtitle_test_button_updates_overlay(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Subtitle test", self._test_subtitles)', gui_source)
        self.assertIn('def _test_subtitles(self) -> None:', gui_source)
        self.assertIn('self.overlay.update_lines("Subtitle test", "字幕測試")', gui_source)

    def test_overlay_quick_toggle_switches_visibility(self):
        import realtime_audio_translator.gui as gui_module

        self.assertTrue(hasattr(gui_module, "toggle_overlay_visibility"))
        self.assertFalse(gui_module.toggle_overlay_visibility(True))
        self.assertTrue(gui_module.toggle_overlay_visibility(False))

        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("Toggle subtitles", self._toggle_subtitles)', gui_source)
        self.assertIn("self.overlay_visible.set(toggle_overlay_visibility(self.overlay_visible.get()))", gui_source)

    def test_speech_quick_toggle_switches_tts_output(self):
        import realtime_audio_translator.gui as gui_module

        self.assertTrue(hasattr(gui_module, "toggle_speech_enabled"))
        self.assertFalse(gui_module.toggle_speech_enabled(True))
        self.assertTrue(gui_module.toggle_speech_enabled(False))

        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("Toggle speech", self._toggle_speech)', gui_source)
        self.assertIn("self.tts_enabled.set(toggle_speech_enabled(self.tts_enabled.get()))", gui_source)

    def test_audio_source_quick_toggles_switch_capture_sources(self):
        import realtime_audio_translator.gui as gui_module

        self.assertTrue(DEFAULT_CONFIG["speaker_enabled"])
        self.assertTrue(DEFAULT_CONFIG["microphone_enabled"])
        self.assertFalse(gui_module.toggle_source_enabled(True))
        self.assertTrue(gui_module.toggle_source_enabled(False))

        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("Toggle speaker", self._toggle_speaker)', gui_source)
        self.assertIn('("Toggle mic", self._toggle_microphone)', gui_source)

    def test_mic_test_button_reports_input_level(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Mic test", self._test_mic)', gui_source)
        self.assertIn('def _test_mic(self) -> None:', gui_source)
        self.assertIn('self.status.set(f"mic level {level:.4f}")', gui_source)

    def test_speaker_test_button_uses_loopback_capture(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Speaker test", self._test_speaker)', gui_source)
        self.assertIn('def _test_speaker(self) -> None:', gui_source)
        self.assertIn('capture_wav(path, device, 0.5, loopback=True)', gui_source)
        self.assertIn('self.status.set("speaker audio detected" if active else "speaker audio quiet")', gui_source)

    def test_tts_test_button_uses_configured_output(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("TTS test", self._test_tts)', gui_source)
        self.assertIn('def _test_tts(self) -> None:', gui_source)
        self.assertIn('provider = config.get("tts_provider", "local")', gui_source)
        self.assertIn('tts.speak_local("Translation output test", device)', gui_source)
        self.assertIn('audio = tts.synthesize_openai_linear16("Translation output test")', gui_source)
        self.assertIn('audio = tts.synthesize_google_linear16("Translation output test", config["target_language"])', gui_source)

    def test_setup_guide_button_shows_first_run_steps(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Setup guide", self._show_setup_guide)', gui_source)
        self.assertIn('def _show_setup_guide(self) -> None:', gui_source)
        self.assertIn("Import runtime", gui_source)
        self.assertIn("Download model", gui_source)
        self.assertIn("CABLE Output", gui_source)
        self.assertIn("Subtitle test", gui_source)

    def test_push_to_talk_restores_previous_mute_state(self):
        app = TranslatorApp.__new__(TranslatorApp)

        class Engine:
            def __init__(self):
                self.muted = False
                self.calls = []

            def set_muted(self, muted):
                self.muted = muted
                self.calls.append(muted)

        app.engine = Engine()

        app._push_to_talk(True)
        app._push_to_talk(False)

        self.assertEqual(app.engine.calls, [False, False])

    def test_quit_button_stops_engine_and_closes_window(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("Quit", self._quit)', gui_source)
        self.assertIn('self.protocol("WM_DELETE_WINDOW", self._quit)', gui_source)

        app = TranslatorApp.__new__(TranslatorApp)
        calls = []

        class Engine:
            def stop(self):
                calls.append("stop")

        app.engine = Engine()
        app.destroy = lambda: calls.append("destroy")

        app._quit()

        self.assertEqual(calls, ["stop", "destroy"])

    def test_engine_uses_configured_log_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "custom-logs"
            config = DEFAULT_CONFIG.copy()
            config["record_logs"] = True
            config["log_dir"] = str(log_dir)

            engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

            self.assertEqual(engine.log.jsonl_path.parent, log_dir)

    def test_default_mode_uses_free_local_providers(self):
        self.assertEqual(DEFAULT_CONFIG["provider"], "local")
        self.assertEqual(DEFAULT_CONFIG["tts_provider"], "local")
        self.assertFalse(DEFAULT_CONFIG["advanced_mode"])
        self.assertEqual(DEFAULT_CONFIG["performance_mode"], "balanced")
        notice = mode_notice(DEFAULT_CONFIG["provider"], DEFAULT_CONFIG["tts_provider"])
        self.assertIn("目前模式：本機免費模式", notice)
        self.assertIn("語音是否上傳：否", notice)
        self.assertIn("是否可能產生 API 費用：否", notice)

    def test_performance_mode_controls_segment_seconds(self):
        self.assertEqual(PERFORMANCE_CHOICES, ("low_latency", "balanced", "quality"))
        self.assertLess(performance_segment_seconds("low_latency"), performance_segment_seconds("quality"))
        self.assertEqual(performance_segment_seconds("bad"), performance_segment_seconds("balanced"))

    def test_simple_mode_hides_advanced_settings(self):
        simple = visible_setting_keys(False)
        advanced = visible_setting_keys(True)
        self.assertIn("source_language", simple)
        self.assertIn("local_translate_url", simple)
        self.assertNotIn("google_service_account_json", simple)
        self.assertIn("google_service_account_json", advanced)

    def test_clear_logs_and_cache_keep_app_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "logs" / "session.jsonl").write_text("secret", encoding="utf-8")
            (root / "cache" / "audio" / "clip.wav").write_bytes(b"audio")
            (root / "cache" / "temp_audio" / "clip.wav").write_bytes(b"audio")

            clear_logs(root)
            clear_cache(root)

            self.assertEqual(list((root / "logs").iterdir()), [])
            self.assertEqual(list((root / "cache" / "audio").iterdir()), [])
            self.assertEqual(list((root / "cache" / "temp_audio").iterdir()), [])

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

    def test_translator_applies_glossary_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"Dragon Pit": "龍坑", "mid lane": "中路"}), encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["glossary_path"] = str(glossary)

            translated = Translator(config).translate("Push mid lane near Dragon Pit", "en", "zh-TW")

        self.assertEqual(translated, "Push 中路 near 龍坑")

    def test_translator_applies_longer_glossary_terms_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"Dragon": "龍", "Dragon Pit": "龍坑"}), encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["glossary_path"] = str(glossary)

            translated = Translator(config).translate("Dragon Pit", "en", "zh-TW")

        self.assertEqual(translated, "龍坑")

    def test_translator_ignores_empty_glossary_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"": "BAD", "Dragon Pit": "龍坑"}), encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["glossary_path"] = str(glossary)

            translated = Translator(config).translate("Dragon Pit", "en", "zh-TW")

        self.assertEqual(translated, "龍坑")

    def test_translator_applies_glossary_to_cached_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"Dragon Pit": "龍坑"}), encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["glossary_path"] = str(glossary)
            translator = Translator(config)

            self.assertEqual(translator.translate("Dragon Pit", "en", "zh-TW"), "龍坑")
            self.assertEqual(translator.translate("Dragon Pit", "en", "zh-TW"), "龍坑")

    def test_translator_ignores_invalid_glossary_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text("{bad", encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["glossary_path"] = str(glossary)

            translated = Translator(config).translate("Dragon Pit", "en", "zh-TW")

        self.assertEqual(translated, "Dragon Pit")

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

    def test_google_tts_can_request_configured_voice(self):
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"audioContent": "cGNt"}

        original_post = providers_module.requests.post
        original_token = providers_module.google_access_token
        providers_module.google_access_token = lambda path: "test-token"
        providers_module.requests.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
        try:
            config = DEFAULT_CONFIG.copy()
            config["google_tts_voice"] = "en-US-Neural2-A"
            audio = TextToSpeech(config).synthesize_google_linear16("hello", "en-US")
        finally:
            providers_module.requests.post = original_post
            providers_module.google_access_token = original_token

        self.assertEqual(audio, b"pcm")
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(calls[0][1]["json"]["voice"]["name"], "en-US-Neural2-A")

    def test_local_tts_uses_windows_sapi(self):
        import realtime_audio_translator.providers as providers_module

        calls = []
        original_speak = providers_module.speak_windows_sapi
        providers_module.speak_windows_sapi = lambda text, device, rate=0, volume=100, voice_name="": calls.append((text, device, rate, volume, voice_name))
        try:
            config = DEFAULT_CONFIG.copy()
            config["tts_rate"] = -2
            config["tts_volume"] = 80
            config["tts_voice_name"] = "Microsoft Jenny"
            TextToSpeech(config).speak_local("hello", "CABLE Input")
        finally:
            providers_module.speak_windows_sapi = original_speak

        self.assertEqual(calls, [("hello", "CABLE Input", -2, 80, "Microsoft Jenny")])

    def test_windows_sapi_receives_voice_name(self):
        import realtime_audio_translator.tts as tts_module

        calls = []
        original_run = tts_module.subprocess.run
        tts_module.subprocess.run = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            tts_module.speak_windows_sapi("hello", "CABLE Input", voice_name="Microsoft Jenny")
        finally:
            tts_module.subprocess.run = original_run

        self.assertEqual(calls[0][1]["env"]["RAT_TTS_VOICE"], "Microsoft Jenny")

    def test_windows_sapi_strips_hostapi_from_output_device(self):
        import realtime_audio_translator.tts as tts_module

        calls = []
        original_run = tts_module.subprocess.run
        tts_module.subprocess.run = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            tts_module.speak_windows_sapi("hello", "CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]")
        finally:
            tts_module.subprocess.run = original_run

        self.assertEqual(calls[0][1]["env"]["RAT_TTS_DEVICE"], "CABLE Input (VB-Audio Virtual Cable)")

    def test_windows_sapi_lists_voice_names(self):
        import realtime_audio_translator.tts as tts_module

        class Result:
            stdout = "Microsoft Jenny Desktop\r\n\r\nMicrosoft Haruka Desktop\r\n"

        original_run = tts_module.subprocess.run
        tts_module.subprocess.run = lambda *args, **kwargs: Result()
        try:
            voices = tts_module.list_windows_sapi_voices()
        finally:
            tts_module.subprocess.run = original_run

        self.assertEqual(voices, ["Microsoft Jenny Desktop", "Microsoft Haruka Desktop"])

    def test_conversation_log_writes_markdown_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "你好", "google")
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["session_id"], "session")
            self.assertEqual(row["translated_text"], "你好")
            md = (Path(tmp) / "session.md").read_text(encoding="utf-8")
            self.assertIn("created:", md)
            self.assertIn("speaker", md)
            self.assertIn("provider: google", md)
            self.assertIn("你好", md)

    def test_conversation_log_auto_session_ids_do_not_collide_within_same_second(self):
        class Clock:
            calls = 0

            @classmethod
            def now(cls, _tz=None):
                cls.calls += 1
                return datetime(2026, 7, 1, 12, 0, 0, cls.calls)

        with tempfile.TemporaryDirectory() as tmp:
            with patch("realtime_audio_translator.logbook.datetime", Clock):
                first = ConversationLog(Path(tmp))
                second = ConversationLog(Path(tmp))

            self.assertNotEqual(first.session_id, second.session_id)
            self.assertNotEqual(first.jsonl_path, second.jsonl_path)

    def test_conversation_log_can_write_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "hi", "google", latency_seconds=1.25)
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["latency_seconds"], 1.25)

    def test_pause_discards_stale_audio_segments(self):
        segments = queue.Queue()
        segments.put("old-1.wav")
        segments.put("old-2.wav")

        self.assertEqual(drain_queue(segments), 2)
        self.assertTrue(segments.empty())

    def test_model_recommendation_prefers_turbo_on_small_cuda_vram(self):
        self.assertEqual(recommend_model(cuda_devices=1, vram_gb=4, prefer_quality=False), "large-v3-turbo")
        self.assertEqual(recommend_model(cuda_devices=0, vram_gb=0, prefer_quality=False), "medium")

    def test_cuda_check_output_reports_devices_and_vram(self):
        devices, vram_gb = cuda_hardware_from_check_output("CUDA device 0: RTX 3060, total memory: 6144 MB")

        self.assertEqual(devices, 1)
        self.assertEqual(vram_gb, 6)

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

    def test_model_available_accepts_downloaded_model_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_models = Path(tmp) / "models"
            (app_models / "faster-whisper-medium").mkdir(parents=True)

            self.assertTrue(model_available("medium", Path(tmp) / "missing", app_models))
            self.assertFalse(model_available("large-v3-turbo", Path(tmp) / "missing", app_models))

    def test_model_install_message_shows_model_folder(self):
        message = model_install_message("medium", Path(r"C:\Users\me\.realtime-audio\models"))

        self.assertIn("medium", message)
        self.assertIn(r"C:\Users\me\.realtime-audio\models", message)
        self.assertIn("Download model", message)

    def test_start_checks_model_before_engine(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('if not model_available(self.config["model"], self.repo_root / "_models", APP_DIR / "models"):', gui_source)
        self.assertIn('messagebox.showerror("Model missing", model_install_message(self.config["model"], APP_DIR / "models"))', gui_source)

    def test_package_script_builds_release_zip_with_readme(self):
        script = Path("scripts/package.ps1").read_text(encoding="utf-8")
        self.assertIn("RealtimeAudioTranslator-$Version-win-x64.zip", script)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-$Version.zip", script)
        self.assertNotIn("RealtimeAudioTranslator-runtime-cuda12-core-$Version.zip", script)
        self.assertNotIn("RealtimeAudioTranslator-runtime-cuda12-dlls-$Version.zip", script)
        self.assertIn("README.md", script)
        self.assertIn("RELEASE_NOTES.md", script)

    def test_package_script_writes_sha256sums(self):
        script = Path("scripts/package.ps1").read_text(encoding="utf-8")

        self.assertIn("SHA256SUMS.txt", script)
        self.assertIn("System.Security.Cryptography.SHA256", script)
        self.assertNotIn("Get-FileHash", script)

    def test_github_release_workflow_uploads_zip_assets(self):
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertIn("tags:", workflow)
        self.assertIn("v*", workflow)
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("build_runtime", workflow)
        self.assertIn("require_runtime_asset", workflow)
        self.assertIn("github.event_name == 'push' || inputs.build_runtime == 'true'", workflow)
        self.assertIn("python -m pip install -r requirements.txt", workflow)
        self.assertIn("unittest discover -s tests -v", workflow)
        self.assertIn("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }", workflow)
        self.assertIn("compileall realtime_audio_translator tests", workflow)
        self.assertIn("releases?per_page=20", workflow)
        self.assertNotIn("/releases/latest", workflow)
        self.assertIn("Sort-Object updated_at -Descending", workflow)
        self.assertIn("Faster-Whisper-XXL_.*_windows", workflow)
        self.assertIn("cuBLAS.and.cuDNN_CUDA12_win_v3.7z", workflow)
        self.assertIn("cublas64_12.dll", workflow)
        self.assertIn("cublasLt64_12.dll", workflow)
        self.assertIn("cudnn64_9.dll", workflow)
        self.assertNotIn("-Filter *.dll", workflow)
        self.assertIn("& ./scripts/package.ps1 -Version $version -RuntimeSource \"downloaded-runtime\"", workflow)
        self.assertIn("& ./scripts/package.ps1 -Version $version", workflow)
        self.assertNotIn("@args", workflow)
        self.assertNotIn("@packageArgs", workflow)
        self.assertIn("softprops/action-gh-release", workflow)
        self.assertIn("tag_name:", workflow)
        self.assertIn("inputs.version || github.ref_name", workflow)
        self.assertIn("release-output/*.zip", workflow)
        self.assertIn("release-output/SHA256SUMS.txt", workflow)

    def test_release_notes_include_public_download_instructions(self):
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        self.assertIn("最快使用", notes)
        self.assertIn("RealtimeAudioTranslator.exe", notes)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-<tag>.zip", notes)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\runtime\\cuda12", notes)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\models", notes)
        self.assertIn("VB-CABLE", notes)
        self.assertIn("GitHub Releases", notes)
        self.assertIn("https://github.com/Purfview/whisper-standalone-win/releases", notes)
        self.assertIn("cuBLAS.and.cuDNN_CUDA12_win_v3.7z", notes)
        self.assertIn("Local translate URL", notes)

    def test_quick_start_doc_exists_for_app_zip(self):
        quick_start = Path("docs/README_QUICK_START_zh-TW.txt").read_text(encoding="utf-8")

        self.assertIn("RealtimeAudioTranslator.exe", quick_start)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-<tag>.zip", quick_start)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\runtime\\cuda12", quick_start)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\models", quick_start)
        self.assertIn("Local translate URL", quick_start)

    def test_readme_and_release_notes_cover_required_faq(self):
        required = (
            "沒有字幕",
            "聽不到對方聲音",
            "對方聽不到翻譯語音",
            "找不到 runtime",
            "找不到模型",
            "Discord 沒有收到虛擬麥克風聲音",
            "字幕延遲太高",
            "GPU 無法使用",
        )

        for path in (Path("README.md"), Path("docs/RELEASE_NOTES.md")):
            text = path.read_text(encoding="utf-8")
            for item in required:
                self.assertIn(item, text)

    def test_readme_mentions_push_to_talk(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("Push to talk", readme)
        self.assertIn("hold it to unmute TTS output", readme)

    def test_readme_mentions_open_logs(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("Open logs", readme)
        self.assertIn("開啟紀錄資料夾", readme)

    def test_readme_mentions_open_app_folder(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("Open app folder", readme)
        self.assertIn("%USERPROFILE%\\.realtime-audio", readme)

    def test_readme_mentions_tts_test_provider(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("TTS test", readme)
        self.assertIn("TTS provider", readme)
        self.assertIn("OpenAI model", readme)
        self.assertIn("OpenAI TTS voice", readme)

    def test_readme_mentions_overlay_language_and_topmost(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("Show language", readme)
        self.assertIn("Overlay topmost", readme)

    def test_readme_mentions_release_checksums(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("SHA256SUMS.txt", readme)
        self.assertIn("GitHub Releases", readme)
        self.assertIn("RealtimeAudioTranslator.exe", readme)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\runtime\\cuda12", readme)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\models", readme)

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

    def test_language_choices_cover_mvp_languages(self):
        self.assertEqual(LANGUAGE_CHOICES, ("auto", "zh", "en", "ja", "ko"))

    def test_google_translate_auto_source_omits_source_language(self):
        request = build_google_translate_request("hello", "zh", "auto", "project")
        self.assertNotIn("sourceLanguageCode", request["json"])
        self.assertEqual(request["json"]["targetLanguageCode"], "zh")

    def test_whisper_auto_language_omits_language_flag(self):
        import realtime_audio_translator.asr as asr_module

        calls = []
        original_run = asr_module.subprocess.run
        asr_module.subprocess.run = lambda command, **kwargs: calls.append(command) or type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "out"
                out.mkdir()
                transcriber = AudioTranscriber.__new__(AudioTranscriber)
                transcriber.exe_path = Path("fw.exe")
                transcriber.model_name = "medium"
                transcriber.model_dir = Path("models")
                transcriber._transcribe_with_exe(out / "clip.wav", "auto")
        finally:
            asr_module.subprocess.run = original_run

        self.assertNotIn("--language", calls[0])

    def test_add_xxl_data_prefers_runtime_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_data = root / "repo" / "_xxl_data"
            runtime_data = root / "runtime" / "_xxl_data"
            repo_data.mkdir(parents=True)
            runtime_data.mkdir(parents=True)
            original_path = sys.path[:]
            try:
                add_xxl_data(root / "repo", root / "runtime")
                self.assertEqual(sys.path[0], str(root / "runtime" / "_xxl_data"))
                self.assertIn(str(root / "repo" / "_xxl_data"), sys.path)
            finally:
                sys.path[:] = original_path

    def test_add_runtime_dll_directory_keeps_handle(self):
        import realtime_audio_translator.asr as asr_module

        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            runtime.mkdir()
            calls = []
            original_add = getattr(asr_module.os, "add_dll_directory", None)
            original_handles = asr_module.DLL_DIRECTORIES[:]
            asr_module.os.add_dll_directory = lambda path: calls.append(path) or "handle"
            try:
                asr_module.DLL_DIRECTORIES.clear()
                add_runtime_dll_directory(runtime)
                self.assertEqual(calls, [str(runtime)])
                self.assertEqual(asr_module.DLL_DIRECTORIES, ["handle"])
            finally:
                if original_add is None:
                    delattr(asr_module.os, "add_dll_directory")
                else:
                    asr_module.os.add_dll_directory = original_add
                asr_module.DLL_DIRECTORIES[:] = original_handles

    def test_whisper_model_stores_detected_language(self):
        transcriber = AudioTranscriber.__new__(AudioTranscriber)
        transcriber.model_name = "medium"
        transcriber.model_dir = Path("models")

        class Segment:
            text = " hello "

        class Model:
            def transcribe(self, *args, **kwargs):
                return [Segment()], type("Info", (), {"language": "ja"})()

        transcriber.model = Model()

        self.assertEqual(transcriber.transcribe(Path("clip.wav"), "auto"), "hello")
        self.assertEqual(transcriber.last_language, "ja")

    def test_troubleshooting_actions_cover_common_setup_issues(self):
        self.assertEqual(troubleshooting_action("speaker_audio"), ("open", "ms-settings:sound"))
        self.assertEqual(troubleshooting_action("mic_output"), ("open", "https://vb-audio.com/Cable/"))
        self.assertEqual(troubleshooting_action("subtitles"), ("overlay", "show"))
        self.assertEqual(troubleshooting_action("local_translation"), ("open", "https://github.com/LibreTranslate/LibreTranslate"))

    def test_runtime_controls_link_cuda12_dependency(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('text="Download runtime zip"', gui_source)
        self.assertIn('text="Fallback runtime source"', gui_source)
        self.assertIn("RUNTIME_RELEASE_URL", gui_source)
        self.assertIn("UPSTREAM_RUNTIME_RELEASE_URL", gui_source)

    def test_import_runtime_refreshes_commands_json(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('refresh_commands(whisper_exe(target), APP_DIR / "commands.json")', gui_source)

    def test_provider_choices_are_fixed(self):
        self.assertEqual(PROVIDER_CHOICES, ("local", "google", "openai"))
        self.assertEqual(TTS_PROVIDER_CHOICES, ("local", "google", "openai"))

    def test_mode_notice_discloses_cloud_api_cost_risk(self):
        cloud_notice = mode_notice("google", "openai")
        self.assertIn("目前模式：雲端 API 模式", cloud_notice)
        self.assertIn("可能傳送到第三方服務", cloud_notice)
        self.assertIn("可能依 API 供應商產生費用", cloud_notice)

        local_notice = mode_notice("local", "local", False, "")
        self.assertIn("目前模式：本機免費模式", local_notice)
        self.assertIn("語音是否上傳：否", local_notice)
        self.assertIn("是否可能產生 API 費用：否", local_notice)
        self.assertIn("對話紀錄：關閉", local_notice)
        self.assertIn("本機翻譯 URL 未設定", local_notice)
        self.assertIn("對話紀錄：開啟", mode_notice("local", "local", True))

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
                self.queue = self
                self.wav = wav

            def get(self, timeout):
                engine.running = False
                return self.wav

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            self._write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine.translator = Translator()
            engine._process_segments("speaker", Worker(wav))

        self.assertEqual(overlays[0][0], "en: hello\nzh: 你好")

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
            self._write_wav(wav, 12000)
            engine.running = True
            engine.transcriber = Transcriber()
            engine.translator = Translator()
            engine._process_segments("speaker", Worker(wav))

        self.assertEqual(calls, [("konnichiwa", "ja", "zh")])
        self.assertEqual(overlays[0][0], "zh: 你好")

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
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: overlays.append((speaker, mine)), lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                raise RuntimeError("translation down")

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

        self.assertEqual(overlays[0][0], "en: hello")

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
            self._write_wav(wav, 12000)
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
        self.assertEqual(statuses[-1], "Runtime missing: faster-whisper-xxl.exe")

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
