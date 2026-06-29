import unittest


class SmokeTests(unittest.TestCase):
    def test_runtime_modules_import(self):
        from realtime_audio_translator import asr, audio, engine, gui, paths, tts

        self.assertTrue(hasattr(asr, "AudioTranscriber"))
        self.assertTrue(hasattr(audio, "list_audio_devices"))
        self.assertTrue(hasattr(engine, "RealtimeEngine"))
        self.assertTrue(hasattr(gui, "TranslatorApp"))
        self.assertTrue(hasattr(paths, "resource_path"))
        self.assertTrue(hasattr(tts, "play_linear16"))


if __name__ == "__main__":
    unittest.main()
