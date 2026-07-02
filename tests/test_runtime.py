import tempfile
import unittest
from pathlib import Path

from realtime_audio_translator.runtime import install_runtime_from, runtime_install_message, runtime_status, whisper_exe


class RuntimeTests(unittest.TestCase):
    def test_runtime_status_reports_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = runtime_status(Path(tmp))
            self.assertFalse(status["ready"])
            self.assertIn("faster-whisper-xxl.exe", status["missing"])

    def test_runtime_install_message_includes_release_and_cuda12_package(self):
        message = runtime_install_message(Path("runtime"))

        self.assertIn("runtime", message)
        self.assertIn("https://github.com/Purfview/whisper-standalone-win/releases", message)
        self.assertIn("cuBLAS.and.cuDNN_CUDA12_win_v3.7z", message)

    def test_gui_runtime_missing_prompts_use_runtime_install_message(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertNotIn('f"Put faster-whisper-xxl.exe in {exe.parent}"', gui_source)
        self.assertNotIn("Runtime missing: {', '.join(status['missing'])}", gui_source)

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

    def test_install_runtime_from_accepts_parent_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "download"
            nested = source / "Faster-Whisper-XXL"
            target = root / "target"
            nested.mkdir(parents=True)
            (nested / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")

            install_runtime_from(source, target)

            self.assertTrue((target / "faster-whisper-xxl.exe").exists())

    def test_install_runtime_from_accepts_existing_runtime_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            runtime.mkdir()
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")

            self.assertEqual(install_runtime_from(runtime, runtime), runtime)


if __name__ == "__main__":
    unittest.main()
