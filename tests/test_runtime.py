import tempfile
import unittest
from pathlib import Path

from realtime_audio_translator.runtime import install_runtime_from, runtime_status, whisper_exe


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

    def test_install_runtime_from_copies_extracted_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (source / "cublas64_12.dll").write_text("dll", encoding="utf-8")

            install_runtime_from(source, target)

            self.assertTrue((target / "faster-whisper-xxl.exe").exists())
            self.assertTrue((target / "cublas64_12.dll").exists())


if __name__ == "__main__":
    unittest.main()
