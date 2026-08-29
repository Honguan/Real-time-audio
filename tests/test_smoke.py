import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from realtime_audio_translator.smoke import run_smoke_test


class SmokeTests(unittest.TestCase):
    def test_packaged_entrypoint_uses_absolute_import(self):
        entrypoint = (Path(__file__).parents[1] / "realtime_audio_translator" / "__main__.py").read_text(encoding="utf-8")

        self.assertIn("from realtime_audio_translator.gui import main", entrypoint)
        self.assertIn("--smoke-test", entrypoint)

    def test_runtime_modules_import(self):
        from realtime_audio_translator import ai_diagnostics, asr, audio, engine, gui, paths, runtime, scenario_manager, tts

        self.assertTrue(hasattr(ai_diagnostics, "collect_diagnostics"))
        self.assertTrue(hasattr(asr, "AudioTranscriber"))
        self.assertTrue(hasattr(audio, "list_audio_devices"))
        self.assertTrue(hasattr(engine, "RealtimeEngine"))
        self.assertTrue(hasattr(gui, "TranslatorApp"))
        self.assertTrue(hasattr(paths, "resource_path"))
        self.assertTrue(hasattr(runtime, "runtime_status"))
        self.assertTrue(hasattr(scenario_manager, "apply_scenario"))
        self.assertTrue(hasattr(tts, "play_linear16"))

    def test_smoke_test_returns_machine_readable_success(self):
        with TemporaryDirectory() as tmp:
            result = run_smoke_test(Path(tmp), require_frozen=False)

        self.assertEqual(result["status"], "ok", result.get("error"))
        self.assertEqual(result["checks"], ["imports", "portaudio", "tk", "resources", "config", "bundle"])


if __name__ == "__main__":
    unittest.main()
