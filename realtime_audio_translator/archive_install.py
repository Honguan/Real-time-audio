import hashlib
import json
import locale
import ntpath
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath, PureWindowsPath


INSTALL_MANIFEST = "install_manifest.json"
MAX_ARCHIVE_FILES = 50_000
MAX_MEMBER_SIZE = 4 * 1024**3
MAX_TOTAL_SIZE = 20 * 1024**3
# PurePath.is_reserved was removed in Python 3.15; ntpath.isreserved exists from 3.13.
_is_reserved = getattr(ntpath, "isreserved", None) or (lambda name: PureWindowsPath(name).is_reserved())
# Windows tar prints listings in the ANSI code page, which differs from Python's UTF-8 mode (default in 3.15).
_TAR_ENCODING = locale.getencoding() if hasattr(locale, "getencoding") else locale.getpreferredencoding(False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_parts(name: str) -> tuple[str, ...]:
    normalized = str(name).replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(str(name))
    if not normalized or "\0" in normalized or posix.is_absolute() or windows.is_absolute() or windows.drive or _is_reserved(str(name)):
        raise RuntimeError(f"封存檔包含不安全路徑：{name}")
    if any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in posix.parts):
        raise RuntimeError(f"封存檔包含不安全路徑：{name}")
    return posix.parts


def _member_target(root: Path, name: str) -> Path:
    target = root.joinpath(*_safe_member_parts(name))
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"封存檔路徑超出安裝範圍：{name}") from exc
    return target


def _is_link_or_reparse(path: Path, info: os.stat_result | None = None) -> bool:
    info = info or path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def validate_tree(root: Path, max_files: int = MAX_ARCHIVE_FILES, max_total_size: int = MAX_TOTAL_SIZE) -> dict[str, int]:
    """Validate the tree and return regular files as {relative posix path: size}."""
    if not root.is_dir() or _is_link_or_reparse(root):
        raise RuntimeError(f"安裝根目錄不可為連結或 reparse point：{root}")
    files = {}
    total = 0
    prefix = len(str(root / "x")) - 1  # handles ".", drive roots and trailing separators like rglob does
    # rglob yields a directory before its contents and every entry is rejected if it is a link or
    # reparse point, so lexical containment is enough here; resolving each path costs ~25 s on 5k files.
    for path in root.rglob("*"):
        relative = str(path)[prefix:].replace("\\", "/")
        _safe_member_parts(relative)
        info = path.lstat()
        if _is_link_or_reparse(path, info):
            raise RuntimeError(f"安裝內容不可包含連結或 reparse point：{path.name}")
        if stat.S_ISREG(info.st_mode):
            files[relative] = info.st_size
            total += info.st_size
            if info.st_size > MAX_MEMBER_SIZE or len(files) > max_files or total > max_total_size:
                raise RuntimeError("封存檔超過允許的檔案數或容量")
    return files


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
        listing = subprocess.run(["tar", "-tf", "-"], stdin=snapshot, capture_output=True, encoding=_TAR_ENCODING, check=True)
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
        verbose = subprocess.run(["tar", "-tvf", "-"], stdin=snapshot, capture_output=True, encoding=_TAR_ENCODING, check=True)
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
        actual = validate_tree(root)
        actual.pop(INSTALL_MANIFEST, None)
        data = json.loads((root / INSTALL_MANIFEST).read_text(encoding="utf-8"))
        entries = data["files"]
        if data.get("version") != 1 or not isinstance(entries, list):
            return False
        expected = set()
        hash_checks = []
        for entry in entries:
            relative = "/".join(_safe_member_parts(entry["path"]))
            if relative in expected or actual.get(relative) != entry["size"]:
                return False
            expected.add(relative)
            if verify_hashes:
                hash_checks.append((root / relative, entry["sha256"]))
        if expected != actual.keys():
            return False
        if verify_hashes:
            executor = ThreadPoolExecutor(max_workers=4)
            try:
                hashes = executor.map(_sha256, (path for path, _ in hash_checks))
                if not all(digest == expected_hash for digest, (_, expected_hash) in zip(hashes, hash_checks)):
                    return False
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
        return True
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
