import unittest
import tempfile
import ast
from pathlib import Path
from unittest.mock import patch

from realtime_audio_translator.ai_confidence import build_quality_snapshot, format_quality_status
from realtime_audio_translator.app_services import AudioDiagnosticsService
from realtime_audio_translator.config import DEFAULT_CONFIG
from realtime_audio_translator.diagnostics import DiagnosticIssue, collect_diagnostics
from realtime_audio_translator.engine import RealtimeEngine, direction_label
from realtime_audio_translator.gui import SETTING_ROWS, TranslatorApp, setup_guide_message
from realtime_audio_translator.localization import ENGLISH, SUPPORTED_LANGUAGES, missing_translation_keys, normalize_language, translate, translate_known
from realtime_audio_translator.models import _progress_text
from realtime_audio_translator.release_updater import release_update_message


class LocalizationTests(unittest.TestCase):
    def test_supported_catalog_translates_core_ui(self):
        core_messages = {label for label, _key in SETTING_ROWS} | {"設定精靈", "一鍵診斷", "開始", "停止", "離開", "就緒"}

        self.assertFalse(missing_translation_keys(core_messages, "en"))
        self.assertEqual(translate("en", "來源語言"), "Source language")
        self.assertEqual(translate("zh-TW", "來源語言"), "來源語言")

    def test_missing_translation_keys_are_detected(self):
        self.assertEqual(missing_translation_keys({"不存在的訊息"}, "en"), {"不存在的訊息"})

    def test_all_literal_translation_calls_have_english_keys(self):
        messages = set()
        root = Path(__file__).parents[1] / "realtime_audio_translator"
        for name in ("gui.py", "engine.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            messages.update(
                call.args[0].value
                for call in ast.walk(tree)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_t"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            )

        self.assertFalse(missing_translation_keys(messages, "en"))

    def test_user_facing_gui_calls_do_not_bypass_localization(self):
        source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        bypasses = []
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            segment = ast.get_source_segment(source, call) or ""
            if call.func.attr in {"set", "askyesno", "showerror", "showinfo", "askstring"} and any("\u4e00" <= character <= "\u9fff" for character in segment) and "_t(" not in segment:
                bypasses.append((call.lineno, segment))

        self.assertFalse(bypasses, bypasses)

    def test_invalid_language_falls_back_to_traditional_chinese(self):
        self.assertEqual(SUPPORTED_LANGUAGES, ("zh-TW", "en"))
        self.assertEqual(normalize_language("bad"), "zh-TW")
        self.assertEqual(translate("bad", "就緒"), "就緒")
        self.assertEqual(translate_known("en", "來源與目標語言不可相同"), "Source and target languages must differ")
        self.assertIn("介面語言", ENGLISH)

    def test_english_diagnostics_do_not_mix_traditional_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG | {"app_language": "en", "runtime_dir": str(Path(tmp) / "runtime"), "model": "missing-model"}
            issues = collect_diagnostics(config, Path(tmp))

        self.assertTrue(issues)
        messages = " ".join(f"{issue.title} {issue.detail} {issue.fix}" for issue in issues)
        self.assertFalse(any("\u4e00" <= character <= "\u9fff" for character in messages), messages)

    def test_engine_status_uses_app_language(self):
        engine = RealtimeEngine.__new__(RealtimeEngine)
        engine.config = {"app_language": "en"}

        self.assertEqual(direction_label("speaker", "en"), "Speaker")
        self.assertEqual(engine._t("已停止"), "Stopped")

    def test_english_async_status_sources_do_not_mix_traditional_chinese(self):
        config = DEFAULT_CONFIG | {"app_language": "en", "provider": "google", "google_service_account_json": "credentials.json"}
        snapshot = build_quality_snapshot(config, "en", "zh", asr_latency_seconds=0.2, translation_latency_seconds=0.1)
        with patch("realtime_audio_translator.app_services.google_access_token"):
            statuses = [
                AudioDiagnosticsService(Path()).test_api(config),
                release_update_message("v0.1.0", "v0.2.0", "en"),
                _progress_text("medium", 50, 100, 10, "en"),
                format_quality_status(snapshot, advanced=True, language="en"),
            ]

        self.assertTrue(all(statuses))
        self.assertFalse(any("\u4e00" <= character <= "\u9fff" for status in statuses for character in status), statuses)

    def test_english_diagnostic_dialog_uses_localized_headings(self):
        app = TranslatorApp.__new__(TranslatorApp)
        app.config = DEFAULT_CONFIG | {"app_language": "en"}
        app._config_from_vars = lambda: app.config
        issue = DiagnosticIssue("model_missing", "info", "Speech recognition model not found", "Detected by diagnostic check: model_missing.", "Use the recommended action: download model.", "download_model")

        message = app._diagnostic_message([issue])

        self.assertIn("Issue: Speech recognition model not found", message)
        self.assertIn("[Information]", message)
        self.assertIn("Quick fix: Download model", message)
        self.assertFalse(any("\u4e00" <= character <= "\u9fff" for character in message), message)

    def test_english_setup_guide_is_complete(self):
        guide = setup_guide_message("en")
        self.assertIn("Run diagnostics", guide)
        self.assertIn("virtual microphone", guide)
        self.assertFalse(any("\u4e00" <= character <= "\u9fff" for character in guide), guide)


if __name__ == "__main__":
    unittest.main()
