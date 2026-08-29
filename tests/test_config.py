import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from realtime_audio_translator.commands import command_choices, parse_help_options
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config, save_config_state
from realtime_audio_translator.ai_memory import add_glossary_term, cache_translation, cached_translation
from realtime_audio_translator.app_log import append_app_log
from realtime_audio_translator.gui import LANGUAGE_CHOICES, PERFORMANCE_CHOICES, PROVIDER_CHOICES, TARGET_LANGUAGE_CHOICES, TTS_PROVIDER_CHOICES, TranslatorApp, diagnostic_action_label, diagnostic_actions, first_diagnostic_action, first_run_setup_action, first_run_wizard_needed, format_overlay_line, language_lock_value, latency_seconds_value, main_status_summary, mode_notice, overlay_clipboard_text, overlay_font_size_value, overlay_hold_seconds_value, overlay_opacity_value, overlay_visibility_action, performance_segment_seconds, setup_guide_actions, status_message_is_error, subtitle_updates_allowed, swap_language_values, troubleshooting_action, visible_button_texts, visible_setting_keys
from realtime_audio_translator.logbook import ConversationLog
from realtime_audio_translator.release_updater import RELEASES_URL, current_version, is_newer_version, latest_release_tag_from_json, release_update_message


class ConfigTests(unittest.TestCase):
    def test_interrupted_config_write_preserves_last_complete_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            save_config(root, config)
            config["target_language"] = "ja"

            def interrupted_dump(_value, handle, **_kwargs):
                handle.write('{"target_language":')
                raise OSError("simulated crash")

            with patch("realtime_audio_translator.config.json.dump", side_effect=interrupted_dump):
                with self.assertRaisesRegex(OSError, "simulated crash"):
                    save_config(root, config)

            self.assertEqual(load_config(root)["target_language"], DEFAULT_CONFIG["target_language"])

    def test_concurrent_config_saves_leave_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def save(index):
                config = DEFAULT_CONFIG.copy()
                config["tts_volume"] = index % 101
                save_config(root, config)

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(save, range(80)))

            document = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            self.assertIn(document["settings"]["tts_volume"], range(101))

    def test_config_uses_one_authoritative_settings_file_and_separates_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["last_error"] = "runtime failed"
            config["last_asr_model_score"] = -0.4
            config["last_provider_quality_signal"] = 0.73
            config["last_translation_heuristic_warning"] = "快取結果，未重新評分"

            save_config(root, config)
            save_config_state(root, config, {
                "last_error",
                "last_asr_model_score",
                "last_provider_quality_signal",
                "last_translation_heuristic_warning",
            })

            settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
            state = json.loads((root / "config" / "state.json").read_text(encoding="utf-8"))
            self.assertFalse((root / "config.json").exists())
            self.assertNotIn("last_error", settings["settings"])
            self.assertEqual(state["diagnostics"]["last_error"], "runtime failed")
            self.assertEqual(load_config(root)["last_asr_model_score"], -0.4)
            self.assertEqual(load_config(root)["last_provider_quality_signal"], 0.73)
            self.assertEqual(load_config(root)["last_translation_heuristic_warning"], "快取結果，未重新評分")

    def test_concurrent_disjoint_state_updates_are_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_config(root, DEFAULT_CONFIG.copy())
            error_state = DEFAULT_CONFIG.copy()
            error_state["last_error"] = "runtime failed"
            metric_state = DEFAULT_CONFIG.copy()
            metric_state["last_cuda_devices"] = 2

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(save_config_state, root, error_state, {"last_error"}),
                    executor.submit(save_config_state, root, metric_state, {"last_cuda_devices"}),
                ]
                for future in futures:
                    future.result()

            config = load_config(root)
            self.assertEqual(config["last_error"], "runtime failed")
            self.assertEqual(config["last_cuda_devices"], 2)

    def test_load_config_removes_obsolete_confidence_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_config(root, DEFAULT_CONFIG.copy())
            state_path = root / "config" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["session_metrics"].update({
                "last_language_confidence": 0.8,
                "last_asr_confidence": 0.7,
                "last_translation_confidence": 1.0,
            })
            state_path.write_text(json.dumps(state), encoding="utf-8")

            config = load_config(root)
            rewritten = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertIsNone(config["last_language_model_score"])
            self.assertIsNone(config["last_asr_model_score"])
            self.assertIsNone(config["last_provider_quality_signal"])
            self.assertNotIn("last_language_confidence", rewritten["session_metrics"])
            self.assertNotIn("last_asr_confidence", rewritten["session_metrics"])
            self.assertNotIn("last_translation_confidence", rewritten["session_metrics"])

    def test_load_config_migrates_legacy_settings_and_drops_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            settings_path = root / "config" / "settings.json"
            settings_path.write_text(
                json.dumps({"target_language": "ja", "last_error": "legacy", "unknown_setting": True}),
                encoding="utf-8",
            )

            config = load_config(root)
            document = json.loads(settings_path.read_text(encoding="utf-8"))

            self.assertEqual(config["target_language"], "ja")
            self.assertEqual(config["last_error"], "legacy")
            self.assertNotIn("unknown_setting", config)
            self.assertEqual(document["schema_version"], 1)
            self.assertNotIn("unknown_setting", document["settings"])

    def test_load_config_migrates_existing_mirrors_from_legacy_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(json.dumps({"target_language": "ko"}), encoding="utf-8")
            (root / "config.json").write_text(json.dumps({"target_language": "ja"}), encoding="utf-8")

            config = load_config(root)

            self.assertEqual(config["target_language"], "ja")
            self.assertFalse((root / "config.json").exists())

    def test_load_config_validates_setting_types_ranges_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(
                json.dumps({"record_logs": "yes", "tts_volume": 101, "segment_seconds": -1, "runtime_dir": "relative", "provider": "unknown", "conversation_log_retention_days": -1, "conversation_log_max_mb": 20000, "conversation_log_content": "everything", "last_ffmpeg_failed": "yes"}),
                encoding="utf-8",
            )

            config = load_config(root)

            self.assertEqual(config["record_logs"], DEFAULT_CONFIG["record_logs"])
            self.assertEqual(config["tts_volume"], DEFAULT_CONFIG["tts_volume"])
            self.assertEqual(config["segment_seconds"], DEFAULT_CONFIG["segment_seconds"])
            self.assertEqual(config["runtime_dir"], DEFAULT_CONFIG["runtime_dir"])
            self.assertEqual(config["provider"], DEFAULT_CONFIG["provider"])
            self.assertEqual(config["conversation_log_retention_days"], DEFAULT_CONFIG["conversation_log_retention_days"])
            self.assertEqual(config["conversation_log_max_mb"], DEFAULT_CONFIG["conversation_log_max_mb"])
            self.assertEqual(config["conversation_log_content"], DEFAULT_CONFIG["conversation_log_content"])
            self.assertEqual(config["last_ffmpeg_failed"], DEFAULT_CONFIG["last_ffmpeg_failed"])

    def test_load_config_rejects_future_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(
                json.dumps({"schema_version": 99, "settings": {}}), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_config(root)

    def test_load_config_does_not_downgrade_future_schema_to_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            settings_path = root / "config" / "settings.json"
            future = {"schema_version": 99, "settings": {"target_language": "ko"}}
            settings_path.write_text(json.dumps(future), encoding="utf-8")
            (root / "config.json").write_text(json.dumps({"target_language": "ja"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_config(root)

            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8")), future)
            self.assertTrue((root / "config.json").exists())

    def test_load_config_recovers_previous_settings_from_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            save_config(root, config)
            save_config_state(root, config, {"last_error"})
            config["target_language"] = "ja"
            config["last_error"] = "new error"
            save_config(root, config)
            save_config_state(root, config, {"last_error"})
            (root / "config" / "settings.json").write_text("{broken", encoding="utf-8")
            (root / "config" / "state.json").write_text(
                json.dumps({"schema_version": 1, "session_metrics": [], "diagnostics": {}}), encoding="utf-8"
            )

            recovered = load_config(root)

            self.assertEqual(recovered["target_language"], DEFAULT_CONFIG["target_language"])
            json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))

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
            self.assertEqual(json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))["settings"]["target_language"], "ja")
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
        self.assertIn("有新版本可下載", release_update_message("v0.1.0", "v0.2.0"))
        self.assertIn("v0.2.0", release_update_message("v0.1.0", "v0.2.0"))
        self.assertIn("已是最新版本", release_update_message("v0.1.0", "v0.1.0"))

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

    def test_load_config_creates_public_settings_from_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").unlink(missing_ok=True)
            (root / "config.json").write_text(json.dumps({"target_language": "ja"}), encoding="utf-8")

            self.assertEqual(load_config(root)["target_language"], "ja")
            self.assertEqual(json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))["settings"]["target_language"], "ja")
            self.assertFalse((root / "config.json").exists())

    def test_load_config_rejects_invalid_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(json.dumps({"source_language": "bad", "target_language": "auto"}), encoding="utf-8")

            config = load_config(root)
            self.assertEqual(config["source_language"], DEFAULT_CONFIG["source_language"])
            self.assertEqual(config["target_language"], DEFAULT_CONFIG["target_language"])

    def test_load_config_repairs_identical_fixed_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            (root / "config" / "settings.json").write_text(
                json.dumps({"source_language": "en", "target_language": "en"}), encoding="utf-8"
            )

            config = load_config(root)

            self.assertEqual(config["source_language"], DEFAULT_CONFIG["source_language"])
            self.assertEqual(config["target_language"], DEFAULT_CONFIG["target_language"])

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

    def test_load_config_uses_public_runtime_path_when_runtime_dir_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            runtime = root / "custom-runtime"
            (root / "config" / "settings.json").write_text(
                json.dumps({"runtime_path": str(runtime)}), encoding="utf-8"
            )

            self.assertEqual(load_config(root)["runtime_dir"], str(runtime))

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
            config["cloud_api_enabled"] = True
            config["runtime_dir"] = str(root / "runtime" / "cuda12")

            save_config(root, config)

            saved = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))["settings"]
            self.assertEqual(saved["ui_mode"], "advanced")
            self.assertEqual(saved["asr_model"], "medium")
            self.assertEqual(saved["translation_engine"], "openai")
            self.assertEqual(saved["tts_engine"], "system")
            self.assertEqual(saved["runtime_path"], str(root / "runtime" / "cuda12"))
            self.assertTrue(saved["save_conversation_history"])
            self.assertFalse(saved["subtitle_always_on_top"])

            config["target_language"] = "auto"
            config["source_language"] = "bad"
            save_config(root, config)
            saved = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))["settings"]
            self.assertEqual(saved["source_language"], DEFAULT_CONFIG["source_language"])
            self.assertEqual(saved["target_language"], DEFAULT_CONFIG["target_language"])

    def test_save_config_blocks_cloud_without_public_confirmation_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "openai"
            config["tts_provider"] = "google"
            config["cloud_api_enabled"] = False

            save_config(root, config)
            saved = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))["settings"]
            self.assertEqual(saved["provider"], "local")
            self.assertEqual(saved["tts_provider"], "local")
            self.assertEqual(saved["translation_engine"], "local")
            self.assertEqual(saved["tts_engine"], "system")

    def test_save_config_rejects_identical_fixed_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["source_language"] = "ja"
            config["target_language"] = "ja"

            with self.assertRaisesRegex(ValueError, "來源與目標語言不可相同"):
                save_config(Path(tmp), config)

    def test_ensure_glossary_file_creates_parent_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "nested" / "glossary.json"
            self.assertEqual(ensure_glossary_file(glossary), glossary)
            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {})

            glossary.write_text(json.dumps({"mid lane": "中路"}), encoding="utf-8")
            ensure_glossary_file(glossary)
            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {"mid lane": "中路"})

    def test_clear_logs_and_cache_keep_app_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom_logs = root / "custom-logs"
            ensure_app_dirs(root)
            (root / "logs" / "app.log").write_text("app event", encoding="utf-8")
            root_log = ConversationLog(root / "logs", "session")
            root_log.append("me", "en", "zh", "hello", "你好", "local")
            self.assertTrue(root_log.close())
            default_log = ConversationLog(root / "logs" / "conversations", "default-session")
            default_log.append("me", "en", "zh", "hello", "你好", "local")
            self.assertTrue(default_log.close())
            (root / "exports" / "subtitles" / "session.srt").write_text("secret", encoding="utf-8")
            custom_logs.mkdir()
            custom_log = ConversationLog(custom_logs, "session")
            custom_log.append("me", "en", "zh", "hello", "你好", "local")
            self.assertTrue(custom_log.close())
            (custom_logs / "notes.txt").write_text("keep", encoding="utf-8")
            (custom_logs / "notes.md").write_text("keep", encoding="utf-8")
            (custom_logs / "data.jsonl").write_text('{"unrelated": true}\n', encoding="utf-8")
            (custom_logs / "unrelated").mkdir()
            (custom_logs / "unrelated" / "keep.jsonl").write_text("keep", encoding="utf-8")
            (root / "cache" / "audio" / "clip.wav").write_bytes(b"audio")
            (root / "cache" / "temp_audio" / "clip.wav").write_bytes(b"audio")
            cache_translation(root / "cache" / "translation_cache.db", "root-entry", "local", "en", "zh", "hello", "你好")

            custom_cache = root / "custom-cache.db"
            cache_translation(custom_cache, "custom-entry", "local", "en", "zh", "hello", "custom")

            clear_logs(root)
            clear_logs(root, custom_logs)
            clear_cache(root, custom_cache)

            self.assertEqual(sorted(path.name for path in (root / "logs").iterdir()), ["app.log", "conversations"])
            self.assertEqual((root / "logs" / "app.log").read_text(encoding="utf-8"), "")
            self.assertEqual(list((root / "logs" / "conversations").iterdir()), [])
            self.assertEqual(list((root / "exports" / "subtitles").iterdir()), [])
            self.assertEqual(sorted(path.name for path in custom_logs.iterdir()), ["data.jsonl", "notes.md", "notes.txt", "unrelated"])
            self.assertEqual((custom_logs / "notes.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual((custom_logs / "notes.md").read_text(encoding="utf-8"), "keep")
            self.assertTrue((custom_logs / "data.jsonl").exists())
            self.assertTrue((custom_logs / "unrelated" / "keep.jsonl").exists())
            self.assertEqual(list((root / "cache" / "audio").iterdir()), [])
            self.assertEqual(list((root / "cache" / "temp_audio").iterdir()), [])
            self.assertIsNone(cached_translation(root / "cache" / "translation_cache.db", "root-entry"))
            self.assertFalse(custom_cache.exists())

    def test_log_cleanup_rejects_root_relative_and_reparse_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ensure_app_dirs(root)

            for unsafe in (root, root.parent, Path("."), Path(root.anchor)):
                with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                    log_files_to_clear(root, unsafe)

            with patch("realtime_audio_translator.config._has_reparse_point", return_value=True):
                with self.assertRaisesRegex(ValueError, "符號連結或 junction"):
                    log_files_to_clear(root, root / "linked-logs")

    def test_reparse_point_detection_uses_windows_file_attribute(self):
        fake_path = unittest.mock.MagicMock(spec=Path)
        fake_path.parents = ()
        fake_path.is_symlink.return_value = False
        fake_path.lstat.return_value.st_file_attributes = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 1024)

        self.assertTrue(_has_reparse_point(fake_path))

    def test_external_log_cleanup_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            external = root.parent / f"{root.name}-external-logs"
            external.mkdir()
            self.addCleanup(lambda: __import__("shutil").rmtree(external, ignore_errors=True))
            log = ConversationLog(external, "session")
            log.append("me", "en", "zh", "hello", "你好", "local")
            self.assertTrue(log.close())
            (external / "notes.txt").write_text("keep", encoding="utf-8")
            app = TranslatorApp.__new__(TranslatorApp)
            app.config = {"log_dir": str(external)}
            app._save = lambda: None

            with patch("realtime_audio_translator.gui.APP_DIR", root), patch("realtime_audio_translator.gui.messagebox.askyesno", return_value=False) as confirm:
                self.assertIsNone(app._confirm_log_cleanup())

            self.assertIn(str(external), confirm.call_args.args[1])
            self.assertIn("2 個", confirm.call_args.args[1])
            self.assertTrue((external / "session.jsonl").exists())

            clear_logs(root, external)

            self.assertFalse((external / "session.jsonl").exists())
            self.assertFalse((external / "session.md").exists())
            self.assertEqual((external / "notes.txt").read_text(encoding="utf-8"), "keep")

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

    def test_command_choices_reads_commands_json_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commands.json"
            path.write_text(json.dumps({"device": {"choices": ["cuda", "cpu"]}}), encoding="utf-8")

            self.assertEqual(command_choices(path, "device"), ["cuda", "cpu"])
            self.assertEqual(command_choices(path, "missing"), [])


if __name__ == "__main__":
    unittest.main()
