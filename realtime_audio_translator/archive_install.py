import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath


INSTALL_MANIFEST = "install_manifest.json"
MAX_ARCHIVE_FILES = 50_000
MAX_MEMBER_SIZE = 4 * 1024**3
MAX_TOTAL_SIZE = 20 * 1024**3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _member_target(root: Path, name: str) -> Path:
    normalized = str(name).replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(str(name))
    if not normalized or "\0" in normalized or posix.is_absolute() or windows.is_absolute() or windows.drive or windows.is_reserved():
        raise RuntimeError(f"封存檔包含不安全路徑：{name}")
    if any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in posix.parts):
        raise RuntimeError(f"封存檔包含不安全路徑：{name}")
    target = root.joinpath(*posix.parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"封存檔路徑超出安裝範圍：{name}") from exc
    return target


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def validate_tree(root: Path, max_files: int = MAX_ARCHIVE_FILES, max_total_size: int = MAX_TOTAL_SIZE) -> None:
    if not root.is_dir() or _is_link_or_reparse(root):
        raise RuntimeError(f"安裝根目錄不可為連結或 reparse point：{root}")
    count = 0
    total = 0
    for path in root.rglob("*"):
        _member_target(root, path.relative_to(root).as_posix())
        if _is_link_or_reparse(path):
            raise RuntimeError(f"安裝內容不可包含連結或 reparse point：{path.name}")
        if path.is_file():
            count += 1
            size = path.stat().st_size
            total += size
            if size > MAX_MEMBER_SIZE or count > max_files or total > max_total_size:
                raise RuntimeError("封存檔超過允許的檔案數或容量")


def safe_extract_zip(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    validate_tree(destination)
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise RuntimeError("封存檔超過允許的檔案數")
        total = 0
        seen = set()
        for info in infos:
            target = _member_target(destination, info.filename)
            relative = target.relative_to(destination).as_posix().casefold()
            if relative in seen:
                raise RuntimeError(f"封存檔包含重複路徑：{info.filename}")
            seen.add(relative)
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR) or stat.S_ISLNK(mode):
                raise RuntimeError(f"封存檔包含不允許的連結或特殊檔案：{info.filename}")
            total += info.file_size
            if info.file_size > MAX_MEMBER_SIZE or total > MAX_TOTAL_SIZE:
                raise RuntimeError("封存檔超過允許的容量")
        for info in infos:
            target = _member_target(destination, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info) as source, target.open("wb") as output:
                while block := source.read(1024 * 1024):
                    written += len(block)
                    if written > info.file_size or written > MAX_MEMBER_SIZE:
                        raise RuntimeError(f"封存檔成員大小不符：{info.filename}")
                    output.write(block)
            if written != info.file_size:
                raise RuntimeError(f"封存檔成員大小不符：{info.filename}")
    validate_tree(destination)


def safe_extract_archive(archive_path: Path, destination: Path) -> None:
    if zipfile.is_zipfile(archive_path):
        safe_extract_zip(archive_path, destination)
        return
    destination.mkdir(parents=True, exist_ok=True)
    validate_tree(destination)
    with tempfile.TemporaryFile(dir=destination.parent) as snapshot, archive_path.open("rb") as archive:
        shutil.copyfileobj(archive, snapshot)
        snapshot.seek(0)
        listing = subprocess.run(["tar", "-tf", "-"], stdin=snapshot, capture_output=True, text=True, check=True)
        names = listing.stdout.splitlines()
        if not names or len(names) > MAX_ARCHIVE_FILES:
            raise RuntimeError("封存檔沒有內容或超過允許的檔案數")
        seen = set()
        for name in names:
            relative = _member_target(destination, name).relative_to(destination).as_posix().casefold()
            if relative in seen:
                raise RuntimeError(f"封存檔包含重複路徑：{name}")
            seen.add(relative)
        snapshot.seek(0)
        verbose = subprocess.run(["tar", "-tvf", "-"], stdin=snapshot, capture_output=True, text=True, check=True)
        entries = [line for line in verbose.stdout.splitlines() if line.strip()]
        if len(entries) != len(names) or any(line.lstrip()[:1] not in ("-", "d") for line in entries):
            raise RuntimeError("封存檔包含連結或無法驗證的特殊檔案")
        total = 0
        for line in entries:
            parts = line.split()
            if len(parts) > 4 and all(part.isdigit() for part in parts[1:4]):
                size_text = parts[4]
            elif len(parts) > 2 and parts[2].isdigit():
                size_text = parts[2]
            else:
                raise RuntimeError("無法驗證封存檔成員大小")
            size = int(size_text)
            total += size
            if size > MAX_MEMBER_SIZE or total > MAX_TOTAL_SIZE:
                raise RuntimeError("封存檔超過允許的容量")
        snapshot.seek(0)
        subprocess.run(["tar", "-xf", "-", "-C", str(destination)], stdin=snapshot, check=True)
    validate_tree(destination)


def write_install_manifest(root: Path) -> Path:
    validate_tree(root)
    manifest = root / INSTALL_MANIFEST
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == manifest:
            continue
        entries.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": _sha256(path)})
    temporary = root.parent / f".{root.name}-{INSTALL_MANIFEST}.tmp"
    try:
        temporary.write_text(json.dumps({"version": 1, "files": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, manifest)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def verify_install_manifest(root: Path, verify_hashes: bool = False) -> bool:
    try:
        validate_tree(root)
        data = json.loads((root / INSTALL_MANIFEST).read_text(encoding="utf-8"))
        entries = data["files"]
        if data.get("version") != 1 or not isinstance(entries, list):
            return False
        expected = set()
        for entry in entries:
            path = _member_target(root, entry["path"])
            relative = path.relative_to(root).as_posix()
            if relative in expected or not path.is_file() or _is_link_or_reparse(path) or path.stat().st_size != entry["size"]:
                return False
            expected.add(relative)
            if verify_hashes and _sha256(path) != entry["sha256"]:
                return False
        manifest = root / INSTALL_MANIFEST
        actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path != manifest}
        return expected == actual
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        return False


def atomic_replace_tree(staging: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_root = Path(tempfile.mkdtemp(prefix=f".{target.name}-backup-", dir=target.parent))
    backup = backup_root / target.name
    had_target = target.exists()
    safe_to_clean_backup = not had_target
    try:
        if had_target:
            target.replace(backup)
        try:
            staging.replace(target)
        except Exception:
            if had_target and backup.exists() and not target.exists():
                backup.replace(target)
                safe_to_clean_backup = True
            raise
        safe_to_clean_backup = True
    finally:
        if safe_to_clean_backup:
            shutil.rmtree(backup_root, ignore_errors=True)
