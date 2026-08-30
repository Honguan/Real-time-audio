import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from realtime_audio_translator.archive_install import verify_install_manifest
from realtime_audio_translator.runtime import runtime_status


ROOT = Path(__file__).parents[1]


class InstallerTests(unittest.TestCase):
    def _powershell(self, script: str, *args: str) -> None:
        subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(ROOT / "scripts" / script), *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_hardware_detection_selects_only_needed_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "hardware.txt"
            self._powershell(
                "detect_installer_hardware.ps1",
                "-OutputPath", str(output),
                "-NvidiaSmiOutput", "6144\n8192",
                "-SoundDeviceNames", "CABLE Input (VB-Audio Virtual Cable)",
            )
            cuda = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8-sig").splitlines())
            self._powershell("detect_installer_hardware.ps1", "-OutputPath", str(output))
            cpu = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8-sig").splitlines())

        self.assertEqual(cuda, {"runtime": "cuda", "gpu_count": "2", "vram_gb": "8", "vb_cable": "true"})
        self.assertEqual(cpu, {"runtime": "cpu", "gpu_count": "0", "vram_gb": "0", "vb_cable": "false"})

    def test_runtime_normalization_handles_nested_upstream_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            core = root / "download" / "core"
            cuda = root / "download" / "cuda"
            (core / "_xxl_data").mkdir(parents=True)
            cuda.mkdir(parents=True)
            for path in (core / "faster-whisper-xxl.exe", core / "ffmpeg.exe", core / "_xxl_data" / "model.bin"):
                path.write_bytes(b"test")
            for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"):
                (cuda / name).write_bytes(b"test")

            self._powershell("normalize_installer_runtime.ps1", "-RuntimeRoot", str(root), "-Device", "cuda")

            for name in ("faster-whisper-xxl.exe", "ffmpeg.exe", "_xxl_data", "cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"):
                self.assertTrue((root / name).exists(), name)
            self.assertTrue(verify_install_manifest(root, verify_hashes=True))
            self.assertTrue(runtime_status(root, "cpu", verify_hashes=True)["ready"])

    def test_installer_builder_returns_only_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            output = root / "output"
            dist.mkdir()
            (dist / "RealtimeAudioTranslator.exe").write_bytes(b"app")
            compiler = root / "fake-iscc.ps1"
            compiler.write_text(
                "$out = ($args | Where-Object { $_ -like '/DOutputDir=*' }).Substring(12)\n"
                "$tag = ($args | Where-Object { $_ -like '/DReleaseTag=*' }).Substring(13)\n"
                "New-Item -ItemType Directory -Path $out -Force | Out-Null\n"
                "Set-Content -LiteralPath (Join-Path $out \"RealtimeAudioTranslator-$tag-setup.exe\") -Value app\n"
                "Write-Output 'compiler log'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            expected = output / "RealtimeAudioTranslator-v1.2.3-setup.exe"
            command = (
                f"$result = & '{ROOT / 'scripts' / 'build_installer.ps1'}' -Version v1.2.3 "
                f"-IsccPath '{compiler}' -DistDir '{dist}' -ReleaseDir '{root}' -OutputDir '{output}'; "
                f"if (@($result).Count -ne 1 -or $result -ne '{expected}') {{ throw 'Unexpected builder output' }}"
            )
            result = subprocess.run(["pwsh", "-NoProfile", "-Command", command], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_installer_and_workflows_enforce_safety_and_signing(self):
        lock = json.loads((ROOT / "release-lock.json").read_text(encoding="utf-8"))
        installer = (ROOT / "installer" / "RealtimeAudioTranslator.iss").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertEqual(lock["installer"]["builder"]["version"], "7.1.0")
        self.assertRegex(lock["installer"]["builder"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("Components: runtime\\core", installer)
        self.assertIn("Components: runtime\\cuda", installer)
        self.assertIn("external download extractarchive", installer)
        self.assertIn("uninsneveruninstall", installer)
        self.assertIn("CPU 模式不下載 CUDA DLL", installer)
        self.assertIn("{param:TYPE|}", installer)
        self.assertIn("https://vb-audio.com/Cable/", installer)
        self.assertIn("MB_DEFBUTTON2", installer)
        self.assertIn("SuppressibleMsgBox", installer)
        self.assertNotIn("DelTree(UserRoot,", installer)
        for secret in ("WINDOWS_SIGNING_CERTIFICATE_BASE64", "WINDOWS_SIGNING_CERTIFICATE_PASSWORD"):
            self.assertIn(f"secrets.{secret}", release)
        self.assertGreaterEqual(release.count("./scripts/sign_windows.ps1"), 2)
        self.assertIn("dist-release/*.exe", release)
        self.assertIn("Build and test app-only installer", ci)
        self.assertIn("/TYPE=app", ci)
        self.assertIn("unrelated-installer-test.txt", ci)


if __name__ == "__main__":
    unittest.main()
