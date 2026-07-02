import tempfile
import unittest
import subprocess
import zipfile
from pathlib import Path

from realtime_audio_translator.runtime import DEFAULT_RUNTIME_DIR, install_runtime_from, runtime_install_message, runtime_status, whisper_exe


class RuntimeTests(unittest.TestCase):
    def test_runtime_status_reports_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = runtime_status(Path(tmp))
            self.assertFalse(status["ready"])
            self.assertIn("faster-whisper-xxl.exe", status["missing"])

    def test_default_runtime_dir_uses_cuda12_folder(self):
        self.assertEqual(DEFAULT_RUNTIME_DIR.name, "cuda12")
        self.assertEqual(DEFAULT_RUNTIME_DIR.parent.name, "runtime")

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

    def test_package_script_is_zip_only(self):
        script = (Path(__file__).parents[1] / "scripts" / "package.ps1").read_text(encoding="utf-8")

        self.assertNotIn("iscc", script.lower())
        self.assertNotIn("Inno Setup", script)
        self.assertNotIn("RealtimeAudioTranslatorSetup", script)
        self.assertNotIn(".iss", script)

    def test_package_script_creates_app_runtime_zips_and_checksums(self):
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            dist = work / "RealtimeAudioTranslator"
            runtime = work / "runtime"
            out = work / "release"
            (dist / "_internal").mkdir(parents=True)
            (dist / "RealtimeAudioTranslator.exe").write_text("app", encoding="utf-8")
            (dist / "_internal" / "lib.txt").write_text("lib", encoding="utf-8")
            (runtime / "_xxl_data").mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("fw", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data" / "data.txt").write_text("data", encoding="utf-8")
            (runtime / "cublas64_12.dll").write_text("cuda", encoding="utf-8")

            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(root / "scripts" / "package.ps1"),
                    "-SkipBuild",
                    "-Version",
                    "v0.0.0-test",
                    "-OutputDir",
                    str(out),
                    "-DistDir",
                    str(dist),
                    "-RuntimeSource",
                    str(runtime),
                ],
                cwd=root,
                check=True,
            )

            app_zip = out / "RealtimeAudioTranslator-v0.0.0-test-win-x64.zip"
            runtime_zip = out / "RealtimeAudioTranslator-runtime-cuda12-v0.0.0-test.zip"
            self.assertTrue(app_zip.exists())
            self.assertTrue(runtime_zip.exists())
            self.assertTrue((out / "SHA256SUMS.txt").exists())
            self.assertFalse(list(out.glob("*.bin")))
            self.assertFalse((out / "RealtimeAudioTranslatorSetup.exe").exists())

            with zipfile.ZipFile(app_zip) as archive:
                self.assertIn("RealtimeAudioTranslator.exe", archive.namelist())
                self.assertIn("_internal/lib.txt", archive.namelist())
                self.assertIn("assets/icon.png", archive.namelist())
                self.assertIn("README_QUICK_START_zh-TW.txt", archive.namelist())
                self.assertIn("release_version.txt", archive.namelist())
            with zipfile.ZipFile(runtime_zip) as archive:
                self.assertIn("faster-whisper-xxl.exe", archive.namelist())
                self.assertIn("ffmpeg.exe", archive.namelist())
                self.assertIn("cublas64_12.dll", archive.namelist())
                self.assertIn("_xxl_data/data.txt", archive.namelist())
                self.assertIn("runtime_manifest.json", archive.namelist())

    def test_package_script_rejects_incomplete_runtime_source(self):
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            dist = work / "RealtimeAudioTranslator"
            runtime = work / "runtime"
            out = work / "release"
            (dist / "_internal").mkdir(parents=True)
            (dist / "RealtimeAudioTranslator.exe").write_text("app", encoding="utf-8")
            runtime.mkdir()
            (runtime / "faster-whisper-xxl.exe").write_text("fw", encoding="utf-8")

            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(root / "scripts" / "package.ps1"),
                    "-SkipBuild",
                    "-Version",
                    "v0.0.0-test",
                    "-OutputDir",
                    str(out),
                    "-DistDir",
                    str(dist),
                    "-RuntimeSource",
                    str(runtime),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ffmpeg.exe", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
