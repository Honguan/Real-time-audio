import json
import os
import re
import subprocess
import wave
from pathlib import Path

from .config import APP_DIR


KNOWN_MODELS = ("small", "medium", "large-v3-turbo", "large-v2")
MODEL_MARKER = ".model-installed.json"
MODEL_READY = "ready"
MODEL_MISSING = "missing"
MODEL_INVALID = "invalid"
REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")


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
        if marker.exists():
            data = json.loads(marker.read_text(encoding="utf-8"))
            sizes = data.get("sizes")
            expected_names = {*REQUIRED_MODEL_FILES, *(candidate.name for candidate in vocabularies)}
            if data.get("version") != 1 or not isinstance(sizes, dict) or set(sizes) != expected_names or any((path / name).stat().st_size != size for name, size in sizes.items()):
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


def model_download_command(exe_path: Path, probe: Path, model: str, model_dir: Path) -> list[str]:
    return [
        str(exe_path),
        str(probe),
        "--model",
        model,
        "--model_dir",
        str(model_dir),
        "--output_format",
        "txt",
        "--beep_off",
    ]


def download_model(exe_path: Path, model: str, model_dir: Path) -> int:
    if not exe_path.exists():
        raise FileNotFoundError(exe_path)
    model_dir.mkdir(parents=True, exist_ok=True)
    probe = model_dir / "probe.wav"
    if not probe.exists():
        with wave.open(str(probe), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\0\0" * 16000)
    code = subprocess.run(model_download_command(exe_path, probe, model, model_dir), check=False).returncode
    if code == 0:
        installed = model_path(model, model_dir, model_dir)
        if installed is None:
            raise RuntimeError(f"模型下載結果不完整：{model}")
        sizes = {path.name: path.stat().st_size for path in [*(installed / name for name in REQUIRED_MODEL_FILES), *installed.glob("vocabulary.*")] if path.is_file()}
        temporary = installed / f"{MODEL_MARKER}.tmp"
        try:
            temporary.write_text(json.dumps({"version": 1, "model": model, "sizes": sizes}, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, installed / MODEL_MARKER)
        finally:
            temporary.unlink(missing_ok=True)
    return code
