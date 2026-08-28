import io
import stat
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from realtime_audio_translator.archive_install import atomic_replace_tree, safe_extract_archive, safe_extract_zip, verify_install_manifest, write_install_manifest


class ArchiveInstallTests(unittest.TestCase):
    def test_zip_extraction_rejects_escape_paths_and_links(self):
        cases = {
            "parent": "../escape.txt",
            "absolute": "/escape.txt",
            "drive": "C:/escape.txt",
            "unc": r"\\server\share\escape.txt",
            "device": "CON",
            "trailing": "package/file. ",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label, member in cases.items():
                with self.subTest(label=label):
                    archive_path = root / f"{label}.zip"
                    destination = root / f"stage-{label}"
                    with zipfile.ZipFile(archive_path, "w") as archive:
                        archive.writestr(member, "escape")
                    with self.assertRaises(RuntimeError):
                        safe_extract_zip(archive_path, destination)

            link_archive = root / "link.zip"
            link = zipfile.ZipInfo("package/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(link_archive, "w") as archive:
                archive.writestr(link, "../../escape.txt")
            with self.assertRaises(RuntimeError):
                safe_extract_zip(link_archive, root / "stage-link")

            self.assertFalse((root / "escape.txt").exists())

    def test_zip_extraction_accepts_regular_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "model.zip"
            destination = root / "stage"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("package/model/data.bin", b"model")

            safe_extract_zip(archive_path, destination)

            self.assertEqual((destination / "package" / "model" / "data.bin").read_bytes(), b"model")

    def test_tar_extraction_rejects_escape_and_link_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label, member in (("parent", "../escape.txt"), ("drive", "C:/escape.txt")):
                with self.subTest(label=label):
                    archive_path = root / f"{label}.tar"
                    with tarfile.open(archive_path, "w") as archive:
                        info = tarfile.TarInfo(member)
                        info.size = 6
                        archive.addfile(info, io.BytesIO(b"escape"))
                    with self.assertRaises(RuntimeError):
                        safe_extract_archive(archive_path, root / f"stage-{label}")

            link_archive = root / "link.tar"
            with tarfile.open(link_archive, "w") as archive:
                link = tarfile.TarInfo("package/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../escape.txt"
                archive.addfile(link)
            with self.assertRaises(RuntimeError):
                safe_extract_archive(link_archive, root / "stage-link")

            self.assertFalse((root / "escape.txt").exists())

    def test_tar_extraction_accepts_regular_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "runtime.tar"
            destination = root / "stage"
            with tarfile.open(archive_path, "w") as archive:
                info = tarfile.TarInfo("runtime/file.bin")
                info.size = 7
                archive.addfile(info, io.BytesIO(b"runtime"))

            safe_extract_archive(archive_path, destination)

            self.assertEqual((destination / "runtime" / "file.bin").read_bytes(), b"runtime")

    def test_tar_validation_and_extraction_share_one_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "runtime.tar"
            destination = root / "stage"
            with tarfile.open(archive_path, "w") as archive:
                info = tarfile.TarInfo("runtime/file.bin")
                info.size = 7
                archive.addfile(info, io.BytesIO(b"runtime"))
            calls = []
            original_run = subprocess.run

            def observe(command, **kwargs):
                calls.append((command, id(kwargs.get("stdin"))))
                return original_run(command, **kwargs)

            with patch("realtime_audio_translator.archive_install.subprocess.run", side_effect=observe):
                safe_extract_archive(archive_path, destination)

            self.assertEqual(len(calls), 3)
            self.assertEqual(len({stdin_id for _command, stdin_id in calls}), 1)
            self.assertTrue(all("-" in command and str(archive_path) not in command for command, _stdin_id in calls))

    def test_manifest_detects_changed_or_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload.bin"
            payload.write_bytes(b"working")
            write_install_manifest(root)
            self.assertTrue(verify_install_manifest(root, verify_hashes=True))

            payload.write_bytes(b"tampered")
            self.assertFalse(verify_install_manifest(root, verify_hashes=True))

            payload.write_bytes(b"working")
            nested = root / "nested" / "install_manifest.json"
            nested.parent.mkdir()
            nested.write_text("unexpected", encoding="utf-8")
            self.assertFalse(verify_install_manifest(root, verify_hashes=True))

    def test_failed_tree_swap_restores_existing_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "runtime"
            staging = root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "version.txt").write_text("working", encoding="utf-8")
            (staging / "version.txt").write_text("new", encoding="utf-8")
            original_replace = Path.replace

            def fail_staging_replace(path, destination):
                if path == staging:
                    raise OSError("swap failed")
                return original_replace(path, destination)

            with patch.object(Path, "replace", new=fail_staging_replace):
                with self.assertRaisesRegex(OSError, "swap failed"):
                    atomic_replace_tree(staging, target)

            self.assertEqual((target / "version.txt").read_text(encoding="utf-8"), "working")


if __name__ == "__main__":
    unittest.main()
