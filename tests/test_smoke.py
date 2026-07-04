import unittest


class SmokeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
