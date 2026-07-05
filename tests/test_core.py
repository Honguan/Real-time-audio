import json
import os
import queue
import sqlite3
import sys
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from realtime_audio_translator.audio import audio_segment_active, device_name_from_label, find_device, virtual_mic_recaptures_tts
from realtime_audio_translator.asr import AudioTranscriber, add_runtime_dll_directory, add_xxl_data
from realtime_audio_translator.commands import parse_help_options
from realtime_audio_translator.config import DEFAULT_CONFIG, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, save_audio_devices, save_config
from realtime_audio_translator.ai_orchestrator import plan_session
from realtime_audio_translator.ai_auto_tuner import apply_tuning, recommend_tuning
from realtime_audio_translator.ai_confidence import build_confidence_snapshot, format_confidence_status
from realtime_audio_translator.ai_memory import add_glossary_term, cache_translation, cached_translation
from realtime_audio_translator.app_log import append_app_log
from realtime_audio_translator.diagnostics import DiagnosticIssue, collect_diagnostics
from realtime_audio_translator.engine import RealtimeEngine, audio_devices_overlap, drain_queue, overlay_text_from_config
from realtime_audio_translator.gui import LANGUAGE_CHOICES, PERFORMANCE_CHOICES, PROVIDER_CHOICES, TTS_PROVIDER_CHOICES, TranslatorApp, diagnostic_action_label, first_diagnostic_action, first_run_setup_action, first_run_wizard_needed, format_overlay_line, language_lock_value, latency_seconds_value, main_status_summary, mode_notice, overlay_clipboard_text, overlay_font_size_value, overlay_hold_seconds_value, overlay_opacity_value, overlay_visibility_action, performance_segment_seconds, record_logs_requires_confirmation, subtitle_updates_allowed, swap_language_values, troubleshooting_action, visible_button_texts, visible_setting_keys
from realtime_audio_translator.logbook import ConversationLog
from realtime_audio_translator.models import cuda_hardware_from_check_output, list_models, model_available, model_download_command, model_install_message, models_dir, recommend_model
from realtime_audio_translator.providers import TextToSpeech, Translator, build_google_translate_request, build_openai_translation_request
from realtime_audio_translator.release_updater import RELEASES_URL, current_version, is_newer_version, latest_release_tag_from_json, release_update_message
from realtime_audio_translator.scenarios import SCENARIO_CHOICES, apply_scenario
from realtime_audio_translator.subtitle_export import export_jsonl_to_srt, srt_timestamp


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
            self.assertTrue((root / "models" / "whisper-small").is_dir())
            self.assertTrue((root / "models" / "translation").is_dir())
            self.assertTrue((root / "models" / "tts").is_dir())
            self.assertTrue((root / "config").is_dir())
            self.assertEqual(json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))["target_language"], "ja")
            self.assertEqual(json.loads((root / "config" / "audio_devices.json").read_text(encoding="utf-8")), [])
            self.assertEqual(json.loads((root / "config" / "glossary.json").read_text(encoding="utf-8")), {})
            self.assertTrue((root / "logs").is_dir())
            self.assertTrue((root / "logs" / "app.log").is_file())
            self.assertTrue((root / "cache" / "audio").is_dir())
            self.assertTrue((root / "cache" / "temp_audio").is_dir())
            db = sqlite3.connect(root / "cache" / "translation_cache.db")
            try:
                row = db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'translations'").fetchone()
            finally:
                db.close()
            self.assertEqual(row[0], "translations")
            self.assertTrue((root / "exports" / "subtitles").is_dir())

    def test_release_updater_compares_versions_and_reads_latest_tag(self):
        self.assertTrue(is_newer_version("v0.2.0", "v0.1.9"))
        self.assertFalse(is_newer_version("v0.1.0", "v0.1.0"))
        self.assertEqual(latest_release_tag_from_json(b'{"tag_name":"v1.2.3"}'), "v1.2.3")
        self.assertIn("v0.2.0", release_update_message("v0.1.0", "v0.2.0"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "release_version.txt").write_text("v9.9.9\n", encoding="utf-8")
            self.assertEqual(current_version(root), "v9.9.9")

    def test_app_dirs_create_empty_glossary_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            glossary = root / "config" / "glossary.json"
            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {})

            glossary.write_text(json.dumps({"Dragon Pit": "龍坑"}), encoding="utf-8")
            ensure_app_dirs(root)
            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {"Dragon Pit": "龍坑"})

    def test_default_glossary_path_uses_config_folder(self):
        self.assertTrue(DEFAULT_CONFIG["glossary_path"].endswith(".realtime-audio\\config\\glossary.json"))

    def test_app_dirs_create_commands_json_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            commands = root / "commands.json"
            self.assertEqual(json.loads(commands.read_text(encoding="utf-8")), {})

            commands.write_text(json.dumps({"model": {"choices": ["medium"]}}), encoding="utf-8")
            ensure_app_dirs(root)
            self.assertEqual(json.loads(commands.read_text(encoding="utf-8")), {"model": {"choices": ["medium"]}})

    def test_load_config_accepts_config_settings_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(json.dumps({"target_language": "ko"}), encoding="utf-8")

            self.assertEqual(load_config(root)["target_language"], "ko")

    def test_load_config_accepts_public_ui_mode_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(json.dumps({"ui_mode": "advanced"}), encoding="utf-8")

            self.assertTrue(load_config(root)["advanced_mode"])

    def test_load_config_accepts_public_asr_model_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(json.dumps({"asr_model": "medium"}), encoding="utf-8")

            self.assertEqual(load_config(root)["model"], "medium")

    def test_load_config_accepts_public_provider_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(json.dumps({"translation_engine": "openai", "tts_engine": "system", "cloud_api_enabled": True}), encoding="utf-8")

            config = load_config(root)

            self.assertEqual(config["provider"], "openai")
            self.assertEqual(config["tts_provider"], "local")

    def test_load_config_blocks_cloud_without_public_confirmation_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(json.dumps({"provider": "openai", "tts_provider": "google", "cloud_api_enabled": False}), encoding="utf-8")

            config = load_config(root)

            self.assertEqual(config["provider"], "local")
            self.assertEqual(config["tts_provider"], "local")

    def test_save_config_mirrors_public_mode_and_log_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["advanced_mode"] = True
            config["record_logs"] = True
            config["overlay_topmost"] = False
            config["model"] = "medium"
            config["provider"] = "openai"
            config["tts_provider"] = "local"
            config["runtime_dir"] = str(root / "runtime" / "cuda12")

            save_config(root, config)

            saved = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["ui_mode"], "advanced")
            self.assertEqual(saved["asr_model"], "medium")
            self.assertEqual(saved["translation_engine"], "openai")
            self.assertEqual(saved["tts_engine"], "system")
            self.assertEqual(saved["runtime_path"], str(root / "runtime" / "cuda12"))
            self.assertTrue(saved["save_conversation_history"])
            self.assertFalse(saved["subtitle_always_on_top"])

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
        self.assertTrue(DEFAULT_CONFIG["show_original_text"])
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
        self.assertIn('("Export subtitles", self._export_subtitles)', gui_source)
        self.assertIn('def _open_logs(self) -> None:', gui_source)
        self.assertIn('def _export_subtitles(self) -> None:', gui_source)
        self.assertIn("export_jsonl_to_srt", gui_source)
        self.assertIn("append_app_log", gui_source)
        self.assertIn("save_audio_devices", gui_source)
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
        self.assertIn("Virtual mic output", gui_source)
        self.assertIn('config["virtual_mic_enabled"] = self.virtual_mic_enabled.get()', gui_source)
        self.assertIn('self.virtual_mic_enabled.set(bool(updated.get("virtual_mic_enabled", self.virtual_mic_enabled.get())))', gui_source)

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
        self.assertIn('config["last_mic_quiet"] = level < float(self.vars["speech_threshold"].get())', gui_source)

    def test_speaker_test_button_uses_loopback_capture(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Speaker test", self._test_speaker)', gui_source)
        self.assertIn('def _test_speaker(self) -> None:', gui_source)
        self.assertIn('capture_wav(path, device, 0.5, loopback=True)', gui_source)
        self.assertIn('self.status.set("speaker audio detected" if active else "speaker audio quiet")', gui_source)
        self.assertIn('config["last_speaker_quiet"] = not active', gui_source)

    def test_tts_test_button_uses_configured_output(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("TTS test", self._test_tts)', gui_source)
        self.assertIn('("Virtual mic test", self._test_virtual_mic)', gui_source)
        self.assertIn('def _test_tts(self) -> None:', gui_source)
        self.assertIn('def _test_virtual_mic(self) -> None:', gui_source)
        self.assertIn('config["last_virtual_mic_failed"] = False', gui_source)
        self.assertIn('config["last_virtual_mic_failed"] = True', gui_source)
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
        self.assertIn("Apply scenario", gui_source)
        self.assertIn("Optimize settings", gui_source)
        self.assertIn("Subtitle test", gui_source)

    def test_first_run_wizard_opens_for_audio_setup_issues(self):
        issues = [DiagnosticIssue("microphone_device_missing", "warning", "找不到麥克風", "", "", "audio_settings")]
        info_only = [DiagnosticIssue("local_translate_url_missing", "info", "本機翻譯 URL 未設定", "", "", "local_translation")]

        self.assertTrue(first_run_wizard_needed(issues))
        self.assertFalse(first_run_wizard_needed(info_only))

    def test_first_run_setup_action_shows_guide_once_when_no_blocking_issues(self):
        info_only = [DiagnosticIssue("local_translate_url_missing", "info", "本機翻譯 URL 未設定", "", "", "local_translation")]
        blocking = [DiagnosticIssue("runtime_missing", "error", "找不到 runtime", "", "", "open_runtime")]

        self.assertEqual(first_run_setup_action(blocking, False), "diagnostics")
        self.assertEqual(first_run_setup_action(info_only, False), "guide")
        self.assertEqual(first_run_setup_action(info_only, True), "")

    def test_first_diagnostic_action_prefers_runtime_then_model_then_audio(self):
        issues = [
            DiagnosticIssue("microphone_device_missing", "warning", "找不到麥克風", "", "", "audio_settings"),
            DiagnosticIssue("model_missing", "error", "找不到模型", "", "", "download_model"),
            DiagnosticIssue("runtime_missing", "error", "找不到 runtime", "", "", "open_runtime"),
        ]

        self.assertEqual(first_diagnostic_action(issues), "open_runtime")
        self.assertEqual(first_diagnostic_action(issues[:2]), "download_model")
        self.assertEqual(first_diagnostic_action([]), "")

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
        self.assertEqual(DEFAULT_CONFIG["app_language"], "zh-TW")
        self.assertEqual(DEFAULT_CONFIG["ui_mode"], "simple")
        self.assertEqual(DEFAULT_CONFIG["asr_engine"], "faster-whisper-xxl")
        self.assertEqual(DEFAULT_CONFIG["asr_model"], "small")
        self.assertEqual(DEFAULT_CONFIG["model"], "small")
        self.assertEqual(DEFAULT_CONFIG["translation_engine"], "local")
        self.assertEqual(DEFAULT_CONFIG["tts_engine"], "system")
        self.assertEqual(DEFAULT_CONFIG["runtime_path"], str(Path.home() / ".realtime-audio" / "runtime" / "cuda12"))
        self.assertEqual(DEFAULT_CONFIG["models_path"], str(Path.home() / ".realtime-audio" / "models"))
        self.assertFalse(DEFAULT_CONFIG["save_conversation_history"])
        self.assertFalse(DEFAULT_CONFIG["cloud_api_enabled"])
        self.assertTrue(DEFAULT_CONFIG["subtitle_always_on_top"])
        self.assertFalse(DEFAULT_CONFIG["virtual_mic_enabled"])
        self.assertEqual(DEFAULT_CONFIG["provider"], "local")
        self.assertEqual(DEFAULT_CONFIG["tts_provider"], "local")
        self.assertFalse(DEFAULT_CONFIG["advanced_mode"])
        self.assertEqual(DEFAULT_CONFIG["scenario"], "discord_chat")
        self.assertTrue(DEFAULT_CONFIG["ai_auto_optimize"])
        self.assertTrue(DEFAULT_CONFIG["ai_self_diagnosis"])
        self.assertFalse(DEFAULT_CONFIG["setup_guide_shown"])
        self.assertEqual(DEFAULT_CONFIG["performance_mode"], "balanced")
        notice = mode_notice(DEFAULT_CONFIG["provider"], DEFAULT_CONFIG["tts_provider"])
        self.assertIn("目前模式：本機免費模式", notice)
        self.assertIn("語音是否上傳：否", notice)
        self.assertIn("是否可能產生 API 費用：否", notice)

    def test_diagnostics_report_runtime_model_feedback_and_provider_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(root / "runtime")
            config["model"] = "missing-model"
            config["provider"] = "openai"
            config["speaker_device"] = "CABLE Input [Windows WASAPI]"
            config["tts_output_device"] = "CABLE Input"

            issues = collect_diagnostics(config, root)
            codes = [issue.code for issue in issues]

        self.assertIn("runtime_missing", codes)
        self.assertIn("model_missing", codes)
        self.assertIn("feedback_risk", codes)
        self.assertIn("cloud_credentials_missing", codes)
        runtime_issue = next(issue for issue in issues if issue.code == "runtime_missing")
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-<version>.zip", runtime_issue.fix)
        self.assertNotIn("runtime core", runtime_issue.fix)
        self.assertTrue(all(isinstance(issue.title, str) for issue in issues))
        self.assertTrue(all(issue.action for issue in issues))

    def test_diagnostics_warn_when_speaker_tts_output_matches_speaker_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(Path(tmp) / "runtime")
            config["speaker_device"] = "Speakers [Windows WASAPI]"
            config["tts_output_device"] = "CABLE Input"
            config["speaker_tts_enabled"] = True
            config["speaker_tts_output_device"] = "Speakers"

            issues = collect_diagnostics(config, Path(tmp))

        issue = next(item for item in issues if item.code == "feedback_risk")
        self.assertIn("Speaker TTS output", issue.fix)

    def test_diagnostics_warn_when_virtual_mic_output_is_not_cable_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["tts_enabled"] = True
            config["tts_output_device"] = "Speakers"

            issues = collect_diagnostics(config, root)
            config["virtual_mic_enabled"] = True
            enabled_issues = collect_diagnostics(config, root)

        self.assertNotIn("virtual_mic_route", [item.code for item in issues])
        issue = next(item for item in enabled_issues if item.code == "virtual_mic_route")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("CABLE Input", issue.fix)
        self.assertIn("CABLE Output", issue.fix)

    def test_diagnostics_warn_when_microphone_captures_virtual_mic_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["microphone_device"] = "CABLE Output (VB-Audio Virtual Cable) [Windows WASAPI]"
            config["tts_output_device"] = "CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"
            config["virtual_mic_enabled"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "microphone_feedback_risk")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("實體麥克風", issue.fix)

    def test_diagnostics_report_high_subtitle_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_latency_seconds"] = 4.2

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "subtitle_latency_high")
        self.assertEqual(issue.action, "optimize_settings")
        self.assertIn("4.2", issue.detail)
        self.assertIn("Optimize settings", issue.fix)

    def test_local_provider_without_translate_url_is_info_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["provider"] = "local"
            config["local_translate_url"] = ""

            issues = collect_diagnostics(config, root)

        local_issue = next(issue for issue in issues if issue.code == "local_translate_url_missing")
        self.assertEqual(local_issue.severity, "info")
        self.assertNotIn("runtime_missing", [issue.code for issue in issues])
        self.assertNotIn("model_missing", [issue.code for issue in issues])

    def test_diagnostics_uses_configured_models_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            configured_models = root / "custom-models"
            model = configured_models / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["models_path"] = str(configured_models)
            config["model"] = "medium"

            issues = collect_diagnostics(config, root)

        self.assertEqual(models_dir(config), configured_models)
        self.assertNotIn("model_missing", [issue.code for issue in issues])

    def test_models_dir_expands_windows_environment_variables(self):
        self.assertEqual(models_dir({"models_path": r"%USERPROFILE%\models"}), Path(os.environ["USERPROFILE"]) / "models")

    def test_diagnostics_report_empty_translation_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_translation_empty"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "translation_empty")
        self.assertEqual(issue.severity, "warning")
        self.assertEqual(issue.action, "local_translation")

    def test_diagnostics_report_low_translation_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_translation_confidence"] = 0.3

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "translation_confidence_low")
        self.assertEqual(issue.action, "local_translation")
        self.assertIn("Fix last translation", issue.fix)

    def test_diagnostics_report_tts_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_tts_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "tts_no_sound")
        self.assertEqual(issue.severity, "warning")
        self.assertEqual(issue.action, "audio_settings")

    def test_diagnostics_report_high_tts_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_tts_latency_seconds"] = 2.4

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "tts_latency_high")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("local TTS", issue.fix)

    def test_diagnostics_report_virtual_mic_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["virtual_mic_enabled"] = True
            config["last_virtual_mic_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "virtual_mic_no_output")
        self.assertIn("Discord", issue.title)
        self.assertIn("CABLE Output", issue.fix)

    def test_diagnostics_report_quiet_audio_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_mic_quiet"] = True
            config["last_speaker_quiet"] = True

            issues = collect_diagnostics(config, root)

        codes = [item.code for item in issues]
        self.assertIn("microphone_no_sound", codes)
        self.assertIn("speaker_no_sound", codes)

    def test_diagnostics_report_gpu_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "large-v3-turbo"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["device"] = "cuda"
            config["model"] = "large-v3-turbo"
            config["last_cuda_devices"] = 0
            config["last_vram_gb"] = 0

            no_gpu = collect_diagnostics(config, root)
            config["last_cuda_devices"] = 1
            config["last_vram_gb"] = 3
            low_vram = collect_diagnostics(config, root)

        self.assertIn("gpu_unavailable", [item.code for item in no_gpu])
        self.assertIn("gpu_low_vram", [item.code for item in low_vram])
        no_gpu_auto = next(item for item in no_gpu if item.code == "auto_tune_recommended")
        self.assertIn("切換 CPU 與 medium 模型", no_gpu_auto.detail)
        low_vram_auto = next(item for item in low_vram if item.code == "auto_tune_recommended")
        self.assertIn("低 VRAM 使用 medium 模型", low_vram_auto.detail)

    def test_diagnostics_report_asr_runtime_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_asr_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "asr_runtime_failed")
        self.assertEqual(issue.action, "open_runtime")

    def test_diagnostics_report_ffmpeg_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_ffmpeg_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "ffmpeg_failed")
        self.assertEqual(issue.action, "open_runtime")

    def test_diagnostics_include_auto_tune_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["scenario"] = "game_voice"
            config["performance_mode"] = "quality"
            config["ai_auto_optimize"] = True

            issues = collect_diagnostics(config, root)

        auto_issue = next(issue for issue in issues if issue.code == "auto_tune_recommended")
        self.assertEqual(auto_issue.severity, "info")
        self.assertIn("遊戲場景使用低延遲模式", auto_issue.detail)

    def test_diagnostics_suggest_locking_language_when_auto_detection_is_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["source_language"] = "auto"
            config["last_detected_language"] = "en"
            config["last_language_confidence"] = 0.42

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "language_lock_recommended")
        self.assertEqual(issue.severity, "info")
        self.assertEqual(issue.action, "language_settings")

    def test_scenarios_apply_expected_existing_settings(self):
        self.assertEqual(SCENARIO_CHOICES, ("game_voice", "discord_chat", "meeting", "customer_support", "subtitle_only", "mic_translate", "two_way"))
        base = DEFAULT_CONFIG.copy()

        game = apply_scenario(base, "game_voice")
        meeting = apply_scenario(base, "meeting")
        support = apply_scenario(base, "customer_support")
        discord = apply_scenario(base, "discord_chat")
        subtitle = apply_scenario(base, "subtitle_only")
        mic = apply_scenario(base, "mic_translate")
        two_way = apply_scenario(base, "two_way")

        self.assertEqual(game["performance_mode"], "low_latency")
        self.assertFalse(game["virtual_mic_enabled"])
        self.assertEqual(game["segment_seconds"], 1.5)
        self.assertTrue(meeting["record_logs"])
        self.assertFalse(meeting["virtual_mic_enabled"])
        self.assertTrue(discord["virtual_mic_enabled"])
        self.assertEqual(support["performance_mode"], "quality")
        self.assertTrue(support["record_logs"])
        self.assertTrue(support["virtual_mic_enabled"])
        self.assertFalse(subtitle["tts_enabled"])
        self.assertFalse(subtitle["virtual_mic_enabled"])
        self.assertFalse(mic["speaker_enabled"])
        self.assertTrue(mic["microphone_enabled"])
        self.assertTrue(mic["tts_enabled"])
        self.assertTrue(mic["virtual_mic_enabled"])
        self.assertTrue(two_way["speaker_enabled"])
        self.assertTrue(two_way["microphone_enabled"])
        self.assertTrue(two_way["virtual_mic_enabled"])
        self.assertEqual(base["performance_mode"], DEFAULT_CONFIG["performance_mode"])

    def test_ai_orchestrator_combines_scenario_tuning_and_diagnostics_without_enabling_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["tts_provider"] = "local"
            config["scenario"] = "game_voice"
            config["performance_mode"] = "quality"
            config["device"] = "cuda"
            config["model"] = "large-v3-turbo"

            decision = plan_session(config, root, cuda_devices=0, vram_gb=0)

        self.assertEqual(decision.config["scenario"], "game_voice")
        self.assertEqual(decision.config["performance_mode"], "low_latency")
        self.assertEqual(decision.config["device"], "cpu")
        self.assertEqual(decision.config["model"], "medium")
        self.assertEqual(decision.config["provider"], "local")
        self.assertEqual(decision.config["tts_provider"], "local")
        self.assertIn("use_cpu_medium", [item.code for item in decision.recommendations])
        self.assertIn("runtime_missing", [item.code for item in decision.issues])
        self.assertIn("本機免費模式", decision.summary)

    def test_ai_orchestrator_does_not_enable_logs_without_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["scenario"] = "meeting"
            config["record_logs"] = False

            decision = plan_session(config, Path(tmp), cuda_devices=1, vram_gb=6)

        self.assertFalse(decision.config["record_logs"])

    def test_auto_tuner_recommends_cpu_medium_without_cuda(self):
        config = DEFAULT_CONFIG.copy()
        config["device"] = "cuda"
        config["model"] = "large-v3-turbo"

        recommendations = recommend_tuning(config, cuda_devices=0, vram_gb=0)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("use_cpu_medium", [item.code for item in recommendations])
        self.assertEqual(tuned["device"], "cpu")
        self.assertEqual(tuned["model"], "medium")
        self.assertEqual(config["device"], "cuda")

    def test_auto_tuner_reduces_latency_settings(self):
        config = DEFAULT_CONFIG.copy()
        config["performance_mode"] = "quality"
        config["segment_seconds"] = 3.0

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=6, latency_seconds=4.2)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("reduce_latency", [item.code for item in recommendations])
        self.assertEqual(tuned["performance_mode"], "low_latency")
        self.assertEqual(tuned["segment_seconds"], 1.5)
        self.assertEqual(tuned["speech_threshold"], 0.02)

    def test_auto_tuner_shortens_segments_for_fast_speech(self):
        config = DEFAULT_CONFIG.copy()
        config["segment_seconds"] = 3.0
        config["last_speech_units_per_second"] = 3.5

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("fast_speech_segments", [item.code for item in recommendations])
        self.assertEqual(tuned["performance_mode"], "low_latency")
        self.assertEqual(tuned["segment_seconds"], 1.5)

    def test_auto_tuner_recommends_medium_for_low_vram(self):
        config = DEFAULT_CONFIG.copy()
        config["model"] = "large-v2"

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=3)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("low_vram_medium", [item.code for item in recommendations])
        self.assertEqual(tuned["model"], "medium")

    def test_auto_tuner_uses_local_tts_when_cloud_tts_is_slow(self):
        config = DEFAULT_CONFIG.copy()
        config["tts_provider"] = "openai"
        config["tts_engine"] = "openai"
        config["last_tts_latency_seconds"] = 2.4

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("use_local_tts", [item.code for item in recommendations])
        self.assertEqual(tuned["tts_provider"], "local")
        self.assertEqual(tuned["tts_engine"], "system")

    def test_auto_tuner_shows_original_when_translation_confidence_is_low(self):
        config = DEFAULT_CONFIG.copy()
        config["show_original_text"] = False
        config["last_translation_confidence"] = 0.3

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("show_original_on_low_confidence", [item.code for item in recommendations])
        self.assertTrue(tuned["show_original_text"])

    def test_confidence_status_reports_local_mode_latency_and_provider(self):
        config = DEFAULT_CONFIG.copy()
        snapshot = build_confidence_snapshot(config, "en", "zh", asr_latency_seconds=0.82, translation_latency_seconds=0.11)
        status = format_confidence_status(snapshot)

        self.assertFalse(snapshot.cloud_enabled)
        self.assertFalse(snapshot.cost_risk)
        self.assertIn("本機免費模式", status)
        self.assertIn("latency 0.93s", status)
        self.assertIn("provider local", status)

    def test_confidence_status_reports_cloud_cost_and_advanced_details(self):
        config = DEFAULT_CONFIG.copy()
        config["provider"] = "openai"
        config["tts_provider"] = "google"
        snapshot = build_confidence_snapshot(
            config,
            "en",
            "zh",
            asr_latency_seconds=0.82,
            translation_latency_seconds=0.11,
            tts_latency_seconds=0.24,
            language_confidence=0.92,
            asr_confidence=0.8,
            translation_confidence=0.7,
        )
        status = format_confidence_status(snapshot, advanced=True)

        self.assertTrue(snapshot.cloud_enabled)
        self.assertTrue(snapshot.cost_risk)
        self.assertIn("雲端 API 模式", status)
        self.assertIn("費用 可能", status)
        self.assertIn("偵測語言 en 92%", status)
        self.assertIn("ASR 延遲 820ms", status)
        self.assertIn("翻譯延遲 110ms", status)
        self.assertIn("TTS 延遲 240ms", status)

    def test_gui_exposes_scenarios_and_diagnostics(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Scenario", "scenario")', gui_source)
        self.assertIn("SCENARIO_CHOICES", gui_source)
        self.assertIn('("Apply scenario", self._apply_scenario)', gui_source)
        self.assertIn('("Optimize settings", self._optimize_settings)', gui_source)
        self.assertIn('("Run diagnostics", self._run_diagnostics)', gui_source)
        self.assertIn("def _show_first_run_wizard", gui_source)
        self.assertIn("first_run_setup_action", gui_source)
        self.assertIn('self.vars["setup_guide_shown"].set("True")', gui_source)
        self.assertIn("def _show_diagnostics", gui_source)
        self.assertIn("def _run_diagnostic_action", gui_source)
        self.assertIn("webbrowser.open(RUNTIME_RELEASE_URL)", gui_source)
        self.assertIn("collect_diagnostics", gui_source)
        self.assertIn("問題名稱", gui_source)
        self.assertIn("可能原因", gui_source)
        self.assertIn("自動檢查結果", gui_source)
        self.assertIn("建議修復步驟", gui_source)
        self.assertIn("一鍵修復按鈕", gui_source)
        self.assertIn("進階日誌", gui_source)
        self.assertIn("app.log", gui_source)
        self.assertIn("plan_session", gui_source)
        self.assertIn('config["last_cuda_devices"] = devices', gui_source)
        self.assertIn('config["last_vram_gb"] = vram_gb', gui_source)
        self.assertIn("def _auto_optimize_before_start", gui_source)
        self.assertIn("self._auto_optimize_before_start()", gui_source)
        self.assertIn('("Check updates", self._check_updates)', gui_source)
        self.assertIn("latest_release_tag", gui_source)

    def test_auto_optimize_before_start_applies_recommended_config_only_when_enabled(self):
        app = TranslatorApp.__new__(TranslatorApp)
        app.config = {"ai_auto_optimize": True}
        config = {
            "ai_auto_optimize": True,
            "device": "cuda",
            "model": "large-v3-turbo",
            "virtual_mic_enabled": False,
            "last_latency_seconds": "4.2",
            "performance_mode": "quality",
            "segment_seconds": 3.0,
        }
        calls = []
        app._config_from_vars = lambda: config
        app._cuda_hardware = lambda current: (1, 6)
        app._load_config_into_widgets = lambda config: calls.append(("load", config))
        app._save = lambda: calls.append(("save", None))

        app._auto_optimize_before_start()

        self.assertEqual(calls[0][0], "load")
        self.assertEqual(calls[0][1]["performance_mode"], "low_latency")
        self.assertEqual(calls[0][1]["segment_seconds"], 1.5)
        self.assertFalse(calls[0][1]["virtual_mic_enabled"])
        self.assertEqual(calls[1], ("save", None))
        calls.clear()
        app.config = {"ai_auto_optimize": False}

        app._auto_optimize_before_start()

        self.assertEqual(calls, [])

    def test_latency_seconds_value_accepts_bad_values(self):
        self.assertEqual(latency_seconds_value("4.2"), 4.2)
        self.assertIsNone(latency_seconds_value(""))

    def test_diagnostic_action_label_shows_user_button_names(self):
        self.assertEqual(diagnostic_action_label("open_runtime"), "Open runtime folder / Download runtime files")
        self.assertEqual(diagnostic_action_label("download_model"), "Download model")
        self.assertEqual(diagnostic_action_label("unknown"), "unknown")

    def test_performance_mode_controls_segment_seconds(self):
        self.assertEqual(PERFORMANCE_CHOICES, ("low_latency", "balanced", "quality", "offline_light"))
        self.assertLess(performance_segment_seconds("low_latency"), performance_segment_seconds("quality"))
        self.assertEqual(performance_segment_seconds("offline_light"), 2.5)
        self.assertEqual(performance_segment_seconds("bad"), performance_segment_seconds("balanced"))

    def test_simple_mode_hides_advanced_settings(self):
        simple = visible_setting_keys(False)
        advanced = visible_setting_keys(True)
        self.assertIn("source_language", simple)
        self.assertIn("scenario", simple)
        self.assertIn("speaker_device", simple)
        self.assertIn("microphone_device", simple)
        self.assertIn("tts_output_device", simple)
        self.assertIn("local_translate_url", simple)
        self.assertNotIn("provider", simple)
        self.assertNotIn("tts_provider", simple)
        self.assertIn("provider", advanced)
        self.assertIn("tts_provider", advanced)
        self.assertNotIn("google_service_account_json", simple)
        self.assertIn("google_service_account_json", advanced)

    def test_simple_mode_hides_advanced_buttons(self):
        buttons = [
            "Setup guide",
            "Refresh",
            "Apply scenario",
            "Optimize settings",
            "Download model",
            "Run diagnostics",
            "API test",
            "Open app folder",
            "Virtual mic test",
            "Speaker test",
            "Subtitle test",
            "Start",
            "Fix local translation",
            "Clear cache",
            "Open logs",
            "Clear logs",
            "Push to talk",
        ]

        simple = visible_button_texts(buttons, False)
        advanced = visible_button_texts(buttons, True)

        self.assertIn("Setup guide", simple)
        self.assertIn("Apply scenario", simple)
        self.assertIn("Optimize settings", simple)
        self.assertIn("Download model", simple)
        self.assertIn("Run diagnostics", simple)
        self.assertIn("API test", simple)
        self.assertIn("Open app folder", simple)
        self.assertIn("Virtual mic test", simple)
        self.assertIn("Speaker test", simple)
        self.assertIn("Subtitle test", simple)
        self.assertIn("Start", simple)
        self.assertIn("Push to talk", simple)
        self.assertIn("Fix local translation", simple)
        self.assertIn("Clear cache", simple)
        self.assertIn("Open logs", simple)
        self.assertIn("Clear logs", simple)
        self.assertNotIn("Refresh", simple)
        self.assertEqual(advanced, buttons)

    def test_clear_logs_and_cache_keep_app_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom_logs = root / "custom-logs"
            ensure_app_dirs(root)
            (root / "logs" / "app.log").write_text("app event", encoding="utf-8")
            (root / "logs" / "session.jsonl").write_text("secret", encoding="utf-8")
            custom_logs.mkdir()
            (custom_logs / "session.jsonl").write_text("secret", encoding="utf-8")
            (root / "cache" / "audio" / "clip.wav").write_bytes(b"audio")
            (root / "cache" / "temp_audio" / "clip.wav").write_bytes(b"audio")
            cache_translation(root / "cache" / "translation_cache.db", "local", "en", "zh", "hello", "你好")

            clear_logs(root)
            clear_logs(root, custom_logs)
            clear_cache(root)

            self.assertEqual([path.name for path in (root / "logs").iterdir()], ["app.log"])
            self.assertEqual((root / "logs" / "app.log").read_text(encoding="utf-8"), "")
            self.assertEqual([path.name for path in custom_logs.iterdir()], ["app.log"])
            self.assertEqual(list((root / "cache" / "audio").iterdir()), [])
            self.assertEqual(list((root / "cache" / "temp_audio").iterdir()), [])
            self.assertIsNone(cached_translation(root / "cache" / "translation_cache.db", "local", "en", "zh", "hello"))

    def test_app_log_appends_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = append_app_log(Path(tmp), "start", model="small")

            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["event"], "start")
            self.assertEqual(row["model"], "small")
            self.assertIn("timestamp", row)

    def test_audio_device_snapshot_writes_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save_audio_devices(Path(tmp), [{"index": 0, "name": "Speakers", "hostapi": "WASAPI"}])

            self.assertEqual(path, Path(tmp) / "config" / "audio_devices.json")
            devices = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(devices[0]["name"], "Speakers")

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

        contextual = build_openai_translation_request("it", "zh-TW", "en", context=[("hello", "你好")])
        self.assertIn("Recent context", contextual["json"]["input"])
        self.assertIn("hello -> 你好", contextual["json"]["input"])

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
            with tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "openai"
                config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
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

    def test_translator_sends_short_term_context_to_openai(self):
        import os
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": self.text}

        original_key = os.environ.get("OPENAI_API_KEY")
        original_post = providers_module.requests.post
        responses = [Response("你好"), Response("它")]
        providers_module.requests.post = lambda *args, **kwargs: calls.append((args, kwargs)) or responses.pop(0)
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "openai"
            config["translation_cache_enabled"] = False
            translator = Translator(config)
            translator.translate("hello", "en", "zh-TW")
            translator.translate("it", "en", "zh-TW")
        finally:
            providers_module.requests.post = original_post
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertIn("hello -> 你好", calls[1][1]["json"]["input"])

    def test_short_term_context_keeps_only_recent_items(self):
        translator = Translator(DEFAULT_CONFIG.copy())
        for index in range(6):
            translator._remember_context(f"source {index}", f"target {index}")

        self.assertEqual(translator.context, [
            ("source 2", "target 2"),
            ("source 3", "target 3"),
            ("source 4", "target 4"),
            ("source 5", "target 5"),
        ])

    def test_translation_memory_persists_cached_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "translation_cache.db"

            cache_translation(db, "openai", "en", "zh-TW", "hello", "你好")

            self.assertEqual(cached_translation(db, "openai", "en", "zh-TW", "hello"), "你好")
            self.assertIsNone(cached_translation(db, "google", "en", "zh-TW", "hello"))

    def test_translator_uses_persistent_translation_cache(self):
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
            with tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "openai"
                config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
                self.assertEqual(Translator(config).translate("hello", "en", "zh-TW"), "你好")
                self.assertEqual(Translator(config).translate("hello", "en", "zh-TW"), "你好")
        finally:
            providers_module.requests.post = original_post
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertEqual(len(calls), 1)

    def test_add_glossary_term_preserves_existing_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"boss": "王"}), encoding="utf-8")

            add_glossary_term(glossary, "cooldown", "冷卻")

            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {"boss": "王", "cooldown": "冷卻"})

    def test_gui_can_add_glossary_term(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("Add glossary term", self._add_glossary_term)', gui_source)
        self.assertIn('("Fix last translation", self._fix_last_translation)', gui_source)
        self.assertIn("last_source_text", gui_source)
        self.assertIn("simpledialog.askstring", gui_source)
        self.assertIn("add_glossary_term", gui_source)

    def test_local_provider_returns_text_without_cloud_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
            translator = Translator(config)

            self.assertEqual(translator.translate("hello", "auto", "zh-TW"), "hello")

    def test_local_provider_can_use_installed_argos_without_url(self):
        class Translation:
            def translate(self, text):
                return f"本機:{text}"

        class Language:
            def __init__(self, code):
                self.code = code

            def get_translation(self, target):
                return Translation()

        package = type(sys)("argostranslate")
        module = type(sys)("argostranslate.translate")
        package.translate = module
        module.get_installed_languages = lambda: [Language("en"), Language("zh")]
        original_package = sys.modules.get("argostranslate")
        original_module = sys.modules.get("argostranslate.translate")
        sys.modules["argostranslate"] = package
        sys.modules["argostranslate.translate"] = module
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "local"
                config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
                translator = Translator(config)

                self.assertEqual(translator.translate("hello", "en", "zh-TW"), "本機:hello")
                self.assertEqual(translator.last_confidence, 0.8)
        finally:
            if original_package is None:
                sys.modules.pop("argostranslate", None)
            else:
                sys.modules["argostranslate"] = original_package
            if original_module is None:
                sys.modules.pop("argostranslate.translate", None)
            else:
                sys.modules["argostranslate.translate"] = original_module

    def test_local_argos_translation_persists_cache_without_url(self):
        class Translation:
            def translate(self, text):
                return f"本機:{text}"

        class Language:
            def __init__(self, code):
                self.code = code

            def get_translation(self, target):
                return Translation()

        package = type(sys)("argostranslate")
        module = type(sys)("argostranslate.translate")
        package.translate = module
        module.get_installed_languages = lambda: [Language("en"), Language("zh")]
        original_package = sys.modules.get("argostranslate")
        original_module = sys.modules.get("argostranslate.translate")
        sys.modules["argostranslate"] = package
        sys.modules["argostranslate.translate"] = module
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "translation_cache.db"
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "local"
                config["translation_cache_path"] = str(db)

                self.assertEqual(Translator(config).translate("hello", "en", "zh-TW"), "本機:hello")
                self.assertEqual(cached_translation(db, "local", "en", "zh-TW", "hello"), "本機:hello")
        finally:
            if original_package is None:
                sys.modules.pop("argostranslate", None)
            else:
                sys.modules["argostranslate"] = original_package
            if original_module is None:
                sys.modules.pop("argostranslate.translate", None)
            else:
                sys.modules["argostranslate.translate"] = original_module

    def test_local_fallback_does_not_persist_original_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "translation_cache.db"
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["translation_cache_path"] = str(db)

            self.assertEqual(Translator(config).translate("hello", "auto", "zh-TW"), "hello")
            self.assertIsNone(cached_translation(db, "local", "auto", "zh-TW", "hello"))

    def test_translator_sets_confidence_for_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
            translator = Translator(config)

            translator.translate("hello", "auto", "zh-TW")

        self.assertEqual(translator.last_confidence, 0.3)

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
            with tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "local"
                config["local_translate_url"] = "http://127.0.0.1:5000/translate"
                config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
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

    def test_jsonl_log_exports_to_srt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jsonl = root / "session.jsonl"
            jsonl.write_text(
                "\n".join(
                    [
                        json.dumps({"direction": "speaker", "text": "hello", "translated_text": "你好"}, ensure_ascii=False),
                        json.dumps({"direction": "microphone", "text": "謝謝", "translated_text": "thanks"}, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            srt = export_jsonl_to_srt(jsonl, root / "exports" / "subtitles")

            self.assertEqual(srt, root / "exports" / "subtitles" / "session.srt")
            text = srt.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:03,000", text)
            self.assertIn("speaker: 你好", text)
            self.assertIn("00:00:03,000 --> 00:00:06,000", text)
            self.assertIn("microphone: thanks", text)
            self.assertEqual(srt_timestamp(3.25), "00:00:03,250")

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
            (app_models / "whisper-small").mkdir(parents=True)

            models = list_models(Path(tmp) / "missing", app_models)

            self.assertIn("small", models)
            self.assertIn("medium", models)
            self.assertIn("large-v3-turbo", models)

    def test_model_available_accepts_downloaded_model_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_models = Path(tmp) / "models"
            (app_models / "faster-whisper-medium").mkdir(parents=True)
            (app_models / "whisper-small").mkdir(parents=True)

            self.assertTrue(model_available("medium", Path(tmp) / "missing", app_models))
            self.assertTrue(model_available("small", Path(tmp) / "missing", app_models))
            self.assertFalse(model_available("large-v3-turbo", Path(tmp) / "missing", app_models))

    def test_model_install_message_shows_model_folder(self):
        message = model_install_message("medium", Path(r"C:\Users\me\.realtime-audio\models"))

        self.assertIn("medium", message)
        self.assertIn(r"C:\Users\me\.realtime-audio\models", message)
        self.assertIn("Download model", message)

    def test_start_checks_model_before_engine(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn("app_models = models_dir(self.config)", gui_source)
        self.assertIn('if not model_available(self.config["model"], self.repo_root / "_models", app_models):', gui_source)
        self.assertIn('messagebox.showerror("Model missing", model_install_message(self.config["model"], app_models))', gui_source)

    def test_package_script_builds_release_zip_with_readme(self):
        script = Path("scripts/package.ps1").read_text(encoding="utf-8")
        self.assertIn("RealtimeAudioTranslator-$Version-win-x64.zip", script)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-$Version.zip", script)
        self.assertNotIn("RuntimeCoreArchive", script)
        self.assertNotIn("CudaArchive", script)
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
        self.assertNotIn("release-output/*.7z", workflow)
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
        self.assertNotIn("兩個 runtime", notes)

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
            self.assertNotIn("兩個 runtime", text)
            for item in required:
                self.assertIn(item, text)

    def test_readme_mentions_push_to_talk(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("Push to talk", readme)
        self.assertIn("hold it to unmute TTS output", readme)

    def test_readme_and_release_notes_mention_virtual_mic_output_switch(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("Virtual mic output", text)

    def test_readme_and_release_notes_mention_confidence_status(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("信心", text)
            self.assertIn("本機/雲端", text)
            self.assertIn("費用", text)

    def test_readme_and_release_notes_mention_ai_orchestrator(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("AI 決策中樞", text)
            self.assertIn("Optimize settings", text)

    def test_readme_and_release_notes_mention_all_scenarios(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("客服", text)
            self.assertIn("自己說話", text)

    def test_readme_and_release_notes_mention_offline_light_mode(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("offline_light", text)
            self.assertIn("離線省資源", text)

    def test_readme_and_release_notes_mention_argos_offline_translate(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("Argos Translate", text)
            self.assertIn("離線模型", text)

    def test_readme_and_release_notes_mention_language_lock_hint(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("語言判斷", text)
            self.assertIn("Source language", text)

    def test_readme_and_release_notes_mention_check_updates(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("Check updates", text)
            self.assertIn("GitHub Releases", text)

    def test_readme_mentions_open_logs(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("Open logs", readme)
        self.assertIn("app.log", readme)
        self.assertIn("開啟紀錄資料夾", readme)

    def test_readme_mentions_open_app_folder(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("Open app folder", readme)
        self.assertIn("%USERPROFILE%\\.realtime-audio", readme)
        self.assertIn("settings.json", readme)
        self.assertIn("audio_devices.json", readme)
        self.assertIn("config\\glossary.json", readme)
        self.assertIn("models\\whisper-small", readme)
        self.assertIn("models\\translation", readme)
        self.assertIn("models\\tts", readme)

    def test_readme_and_release_notes_mention_subtitle_export(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("Export subtitles", text)
            self.assertIn("%USERPROFILE%\\.realtime-audio\\exports\\subtitles", text)

    def test_readme_and_release_notes_mention_add_glossary_term(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("Add glossary term", text)
            self.assertIn("Fix last translation", text)
            self.assertIn("術語", text)

    def test_readme_mentions_tts_test_provider(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("TTS test", readme)
        self.assertIn("Virtual mic test", readme)
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

    def test_find_device_ignores_empty_label(self):
        devices = [{"index": 7, "name": "Speakers", "input_channels": 0, "output_channels": 2, "hostapi": "WASAPI"}]
        with patch("realtime_audio_translator.audio.list_audio_devices", return_value=devices):
            self.assertIsNone(find_device("", want_output=True))
            self.assertEqual(find_device("Speakers", want_output=True), 7)

    def test_audio_devices_overlap_matches_short_and_full_names(self):
        self.assertTrue(audio_devices_overlap("CABLE Input", "CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"))
        self.assertFalse(audio_devices_overlap("Speakers", "CABLE Input"))

    def test_virtual_mic_recaptures_tts_matches_vb_cable_pair(self):
        self.assertTrue(virtual_mic_recaptures_tts("CABLE Output (VB-Audio Virtual Cable)", "CABLE Input (VB-Audio Virtual Cable)"))
        self.assertFalse(virtual_mic_recaptures_tts("Microphone", "CABLE Input"))

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

    def test_language_lock_uses_last_detected_language_only_from_auto(self):
        self.assertEqual(language_lock_value("auto", "en"), "en")
        self.assertEqual(language_lock_value("zh", "en"), "zh")
        self.assertEqual(language_lock_value("auto", ""), "auto")
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("Lock language", self._lock_language)', gui_source)

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

        self.assertIn('text="Download runtime files"', gui_source)
        self.assertIn('text="Fallback runtime source"', gui_source)
        self.assertIn("RUNTIME_RELEASE_URL", gui_source)
        self.assertIn("UPSTREAM_RUNTIME_RELEASE_URL", gui_source)
        self.assertIn('subprocess.run([str(runtime_dir(config) / "ffmpeg.exe"), "-version"]', gui_source)
        self.assertIn('config["last_ffmpeg_failed"]', gui_source)

    def test_import_runtime_refreshes_commands_json(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('refresh_commands(whisper_exe(target), APP_DIR / "commands.json")', gui_source)

    def test_provider_choices_are_fixed(self):
        self.assertEqual(PROVIDER_CHOICES, ("local", "google", "openai"))
        self.assertEqual(TTS_PROVIDER_CHOICES, ("local", "google", "openai"))

    def test_mode_notice_discloses_cloud_api_cost_risk(self):
        import realtime_audio_translator.gui as gui_module

        self.assertTrue(gui_module.cloud_activation_requires_confirmation("local", "local", "google", "local"))
        self.assertTrue(gui_module.cloud_activation_requires_confirmation("local", "google", "local", "openai"))
        self.assertFalse(gui_module.cloud_activation_requires_confirmation("local", "local", "local", "local"))
        self.assertFalse(gui_module.cloud_activation_requires_confirmation("google", "local", "local", "local"))

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
        self.assertTrue(record_logs_requires_confirmation(False, True))
        self.assertFalse(record_logs_requires_confirmation(True, True))
        self.assertFalse(record_logs_requires_confirmation(False, False))

        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("messagebox.askyesno", gui_source)
        self.assertIn("cloud_activation_requires_confirmation", gui_source)
        self.assertIn('config["cloud_api_enabled"] = cloud_enabled', gui_source)
        self.assertIn("record_logs_requires_confirmation", gui_source)
        self.assertIn("Enable conversation logs?", gui_source)

    def test_main_status_summary_shows_required_main_screen_state(self):
        config = DEFAULT_CONFIG.copy()
        config["scenario"] = "discord_chat"
        config["source_language"] = "en"
        config["target_language"] = "zh"
        config["speaker_device"] = "Speakers"
        config["microphone_device"] = "Microphone"
        config["tts_output_device"] = "CABLE Input"
        config["overlay_visible"] = True
        config["tts_enabled"] = True
        config["virtual_mic_enabled"] = False

        summary = main_status_summary(config)

        for text in ("目前場景：discord_chat", "輸入音源：Speakers / Microphone", "輸出音源：CABLE Input", "來源語言：en", "目標語言：zh", "字幕：開啟", "TTS：開啟", "虛擬麥克風：關閉"):
            self.assertIn(text, summary)

    def test_readme_and_release_notes_mention_cloud_api_confirmation(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("Google 或 OpenAI", text)
            self.assertIn("可能產生費用", text)

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
            self._write_wav(wav, 12000)
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
            self._write_wav(wav, 12000)
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

    def test_engine_records_empty_translation_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return ""

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

        self.assertTrue(engine.config["last_translation_empty"])

    def test_engine_remembers_last_translation_for_glossary_fix(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return "push mid"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "推中"

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
            engine._process_segments("me", Worker(wav))

        self.assertEqual(engine.config["last_source_text"], "push mid")
        self.assertEqual(engine.config["last_translated_text"], "推中")

    def test_engine_reports_confidence_status_after_successful_segment(self):
        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        class Transcriber:
            last_language = "en"
            last_language_probability = 0.92

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

        self.assertIn("speaker", statuses[-1])
        self.assertIn("本機免費模式", statuses[-1])
        self.assertIn("provider local", statuses[-1])
        self.assertIn("latency", statuses[-1])

    def test_engine_records_translation_confidence_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return "hello"

        class Translator:
            last_confidence = 0.3

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
            engine._process_segments("me", Worker(wav))

        self.assertEqual(engine.config["last_translation_confidence"], 0.3)

    def test_engine_records_language_confidence_for_diagnostics(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["source_language"] = "auto"
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            last_language = "en"
            last_language_probability = 0.42

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
            engine._process_segments("me", Worker(wav))

        self.assertEqual(engine.config["last_detected_language"], "en")
        self.assertEqual(engine.config["last_language_confidence"], 0.42)

    def test_engine_records_speech_speed_for_auto_tuning(self):
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        config["segment_seconds"] = 2.0
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, lambda status: None)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return "push mid now"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "推中"

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
            engine._process_segments("me", Worker(wav))

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
                self._write_wav(wav, 12000)
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
            self._write_wav(wav, 12000)
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
                self._write_wav(wav, 12000)
                engine.running = True
                engine.transcriber = Transcriber()
                engine.translator = Translator()
                engine.tts = TTS()
                engine._process_segments("me", Worker(wav))
        finally:
            engine_module.play_linear16 = original_play

        self.assertEqual(played, [])

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
            self._write_wav(wav, 12000)
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
            self._write_wav(wav, 12000)
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
        self.assertEqual(statuses[-1], "running; speaker capture skipped: matches TTS output")

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
        self.assertEqual(statuses[-1], "no audio devices; microphone capture skipped: matches virtual mic output")

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
        self.assertTrue(engine.config["last_asr_failed"])

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
