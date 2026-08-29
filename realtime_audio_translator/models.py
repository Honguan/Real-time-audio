import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .archive_install import atomic_replace_tree, validate_tree
from .config import APP_DIR


KNOWN_MODELS = ("small", "medium", "large-v3-turbo", "large-v2")
MODEL_MARKER = ".model-installed.json"
MODEL_READY = "ready"
MODEL_MISSING = "missing"
MODEL_INVALID = "invalid"
REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")
MODEL_REPOSITORIES = {
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3-turbo": "dropbox-dash/faster-whisper-large-v3-turbo",
}
MODEL_API = "https://huggingface.co/api/models"


class ModelDownloadCancelled(RuntimeError):
    pass


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.sha1() if algorithm == "git-sha1" else hashlib.sha256()
    if algorithm == "git-sha1":
        digest.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _marker_data(path: Path) -> dict | None:
    try:
        data = json.loads((path / MODEL_MARKER).read_text(encoding="utf-8"))
        return data if data.get("version") == 2 and isinstance(data.get("files"), list) else None
    except (OSError, AttributeError, json.JSONDecodeError):
        return None


def verify_model_integrity(path: Path, verify_hashes: bool = False) -> bool:
    data = _marker_data(path)
    if data is None:
        return False
    try:
        names = {entry["path"] for entry in data["files"]}
        metadata_valid = data.get("model") in MODEL_REPOSITORIES and "/" in data.get("repository", "") and re.fullmatch(r"[0-9a-f]{40}", data.get("revision", ""))
        files_valid = len(names) == len(data["files"]) and {*REQUIRED_MODEL_FILES} <= names and any(name.startswith("vocabulary.") for name in names)
        if not metadata_valid or not files_valid:
            return False
        for entry in data["files"]:
            name = entry["path"]
            digest_length = 64 if entry.get("algorithm") == "sha256" else 40
            if entry.get("algorithm") not in {"sha256", "git-sha1"} or not re.fullmatch(rf"[0-9a-f]{{{digest_length}}}", entry.get("digest", "")) or not isinstance(entry.get("size"), int) or entry["size"] <= 0 or Path(name).name != name or name not in {*REQUIRED_MODEL_FILES, "preprocessor_config.json", "vocabulary.json", "vocabulary.txt"}:
                return False
            installed = path / name
            if not installed.is_file() or installed.stat().st_size != entry["size"]:
                return False
            if verify_hashes and _digest(installed, entry["algorithm"]) != entry["digest"]:
                return False
        return True
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _model_path_status(path: Path) -> str:
    try:
        if not path.exists():
            return MODEL_MISSING
        if not path.is_dir():
            return MODEL_INVALID
        if any(path.glob("*.partial")):
            return MODEL_INVALID
        required = [path / name for name in REQUIRED_MODEL_FILES]
        vocabularies = [candidate for candidate in path.glob("vocabulary.*") if candidate.is_file()]
        if any(not candidate.is_file() or candidate.stat().st_size == 0 for candidate in required) or not vocabularies or all(candidate.stat().st_size == 0 for candidate in vocabularies):
            return MODEL_INVALID
        metadata = [json.loads(candidate.read_text(encoding="utf-8")) for candidate in (path / "config.json", path / "tokenizer.json")]
        if not all(isinstance(document, dict) and document for document in metadata):
            return MODEL_INVALID
        marker = path / MODEL_MARKER
        if marker.exists() and not verify_model_integrity(path):
            return MODEL_INVALID
        return MODEL_READY
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return MODEL_INVALID


def _model_candidates(model: str, local_models: Path, app_models: Path):
    candidate = Path(os.path.expandvars(model)).expanduser()
    yield candidate
    for root in (local_models, app_models):
        for name in (model, f"faster-whisper-{model}", f"whisper-{model}"):
            path = root / name
            if path != candidate:
                yield path


def models_dir(config: dict | None = None) -> Path:
    configured = (config or {}).get("models_path")
    return Path(os.path.expandvars(configured)).expanduser() if configured else APP_DIR / "models"


def model_path(model: str, local_models: Path, app_models: Path) -> Path | None:
    if not model:
        return None
    return next((path for path in _model_candidates(model, local_models, app_models) if _model_path_status(path) == MODEL_READY), None)


def model_status(model: str, local_models: Path, app_models: Path) -> str:
    if not model:
        return MODEL_MISSING
    statuses = [_model_path_status(path) for path in _model_candidates(model, local_models, app_models)]
    return MODEL_READY if MODEL_READY in statuses else MODEL_INVALID if MODEL_INVALID in statuses else MODEL_MISSING


def model_available(model: str, local_models: Path, app_models: Path) -> bool:
    return model_path(model, local_models, app_models) is not None


def model_install_message(model: str, model_dir: Path) -> str:
    return (
        f"找不到完整模型：{model}\n"
        "請點「下載模型」重新下載，或把完整模型 zip 解壓到：\n"
        f"{model_dir}"
    )


def list_models(local_models: Path, app_models: Path) -> list[str]:
    found: set[str] = set(KNOWN_MODELS)
    for root in (local_models, app_models):
        if not root.exists():
            continue
        for path in root.iterdir():
            if path.is_dir():
                found.add(path.name.replace("faster-whisper-", "").replace("whisper-", ""))
    return sorted(found)


def recommend_model(cuda_devices: int, vram_gb: int, prefer_quality: bool = False) -> str:
    if cuda_devices < 1:
        return "medium"
    if prefer_quality and vram_gb >= 8:
        return "large-v2"
    return "large-v3-turbo" if vram_gb >= 4 else "medium"


def cuda_hardware_from_check_output(text: str) -> tuple[int, int]:
    devices = text.count("CUDA device")
    memory_mb = [int(value) for value in re.findall(r"(\d+)\s*MB", text, flags=re.IGNORECASE)]
    memory_gb = [int(value) for value in re.findall(r"(\d+)\s*GB", text, flags=re.IGNORECASE)]
    vram_gb = max(memory_gb or [mb // 1024 for mb in memory_mb] or [4 if devices else 0])
    return devices, vram_gb


def model_manifest(model: str, session=requests) -> dict:
    repository = MODEL_REPOSITORIES.get(model)
    if repository is None:
        raise RuntimeError(f"不支援自動下載模型：{model}")
    response = session.get(f"{MODEL_API}/{repository}", params={"blobs": "true"}, timeout=(10, 30))
    response.raise_for_status()
    data = response.json()
    resolved_repository = str(data.get("id") or "")
    revision = str(data.get("sha") or "")
    siblings = {entry.get("rfilename"): entry for entry in data.get("siblings", [])}
    names = [*REQUIRED_MODEL_FILES]
    names.extend(name for name in ("preprocessor_config.json", "vocabulary.json", "vocabulary.txt") if name in siblings)
    if resolved_repository != repository or not re.fullmatch(r"[0-9a-f]{40}", revision) or any(name not in siblings for name in REQUIRED_MODEL_FILES) or not any(name.startswith("vocabulary.") for name in names):
        raise RuntimeError(f"模型 manifest 不完整：{model}")
    files = []
    for name in names:
        entry = siblings[name]
        lfs = entry.get("lfs") or {}
        digest = str(lfs.get("sha256") or entry.get("blobId") or "")
        algorithm = "sha256" if lfs.get("sha256") else "git-sha1"
        size = int(entry.get("size") or lfs.get("size") or 0)
        if size <= 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", digest):
            raise RuntimeError(f"模型 manifest 缺少大小或雜湊：{name}")
        files.append({
            "path": name,
            "size": size,
            "algorithm": algorithm,
            "digest": digest,
            "url": f"https://huggingface.co/{quote(resolved_repository, safe='/')}/resolve/{quote(revision, safe='')}/{quote(name, safe='')}",
        })
    return {"version": 2, "model": model, "repository": resolved_repository, "revision": revision, "files": files}


def _progress_text(model: str, downloaded: int, total: int, speed: float) -> str:
    percent = 100.0 * downloaded / total if total else 0.0
    return f"正在下載模型 {model}：{percent:.1f}%（{downloaded / 1024**2:.1f}/{total / 1024**2:.1f} MB，{speed / 1024**2:.1f} MB/s）"


def _download_file(model: str, entry: dict, staging: Path, downloaded_before: int, total: int, started: float, network_bytes: list[int], progress, cancel_event, session) -> int:
    final = staging / entry["path"]
    partial = staging / f"{entry['path']}.partial"
    if final.is_file() and final.stat().st_size == entry["size"] and _digest(final, entry["algorithm"]) == entry["digest"]:
        return entry["size"]
    final.unlink(missing_ok=True)
    if partial.is_file() and partial.stat().st_size == entry["size"]:
        if _digest(partial, entry["algorithm"]) == entry["digest"]:
            os.replace(partial, final)
            return entry["size"]
        partial.unlink()
    if partial.exists() and partial.stat().st_size > entry["size"]:
        partial.unlink()
    existing = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    with session.get(entry["url"], headers=headers, stream=True, timeout=(10, 30)) as response:
        response.raise_for_status()
        append = existing > 0 and response.status_code == 206
        if not append:
            existing = 0
        written = existing
        with partial.open("ab" if append else "wb") as output:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if cancel_event is not None and cancel_event.is_set():
                    raise ModelDownloadCancelled("模型下載已取消；可稍後重試")
                if not block:
                    continue
                output.write(block)
                written += len(block)
                network_bytes[0] += len(block)
                if written > entry["size"]:
                    raise RuntimeError(f"模型檔案大小超出 manifest：{entry['path']}")
                if progress:
                    current = downloaded_before + written
                    progress(_progress_text(model, current, total, network_bytes[0] / max(time.perf_counter() - started, 0.001)))
    if written != entry["size"] or _digest(partial, entry["algorithm"]) != entry["digest"]:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"模型檔案完整性驗證失敗：{entry['path']}")
    os.replace(partial, final)
    return written


def download_model(model: str, model_dir: Path, progress=None, cancel_event=None, session=requests) -> Path:
    if cancel_event is not None and cancel_event.is_set():
        raise ModelDownloadCancelled("模型下載已取消；可稍後重試")
    if progress:
        progress(f"正在取得模型 {model} 的版本與檔案資訊")
    manifest = model_manifest(model, session)
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / f"faster-whisper-{model}"
    staging = model_dir / f".{target.name}.partial"
    staging.mkdir(exist_ok=True)
    validate_tree(staging)
    total = sum(entry["size"] for entry in manifest["files"])
    reusable = sum(min(max([candidate.stat().st_size for candidate in (staging / entry["path"], staging / f"{entry['path']}.partial") if candidate.is_file()] or [0]), entry["size"]) for entry in manifest["files"])
    required = max(0, total - reusable)
    free = shutil.disk_usage(model_dir).free
    if free < required:
        raise RuntimeError(f"模型下載空間不足：需要 {required / 1024**3:.1f} GB，可用 {free / 1024**3:.1f} GB")
    downloaded = 0
    started = time.perf_counter()
    network_bytes = [0]
    for entry in manifest["files"]:
        if cancel_event is not None and cancel_event.is_set():
            raise ModelDownloadCancelled("模型下載已取消；可稍後重試")
        downloaded += _download_file(model, entry, staging, downloaded, total, started, network_bytes, progress, cancel_event, session)
    allowed = {entry["path"] for entry in manifest["files"]}
    for stale in staging.iterdir():
        if stale.name in allowed | {MODEL_MARKER}:
            continue
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink()
    marker = staging / MODEL_MARKER
    marker.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if not verify_model_integrity(staging, verify_hashes=True):
        raise RuntimeError("模型安裝 manifest 驗證失敗")
    atomic_replace_tree(staging, target)
    if progress:
        progress(f"模型 {model} 下載完成；版本 {manifest['revision'][:12]}，SHA 完整性驗證成功")
    return target
