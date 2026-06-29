import tempfile
import unittest
from pathlib import Path

from realtime_audio_translator.runtime import runtime_status, whisper_exe


class RuntimeTests(unittest.TestCase):
    def test_runtime_status_reports_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = runtime_status(Path(tmp))
            self.assertFalse(status["ready"])
            self.assertIn("faster-whisper-xxl.exe", status["missing"])

    def test_whisper_exe_uses_runtime_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "faster-whisper-xxl.exe"
            exe.write_text("", encoding="utf-8")
            self.assertEqual(whisper_exe(root), exe)


if __name__ == "__main__":
    unittest.main()
