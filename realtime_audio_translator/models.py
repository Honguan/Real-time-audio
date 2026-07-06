import os
import re
import subprocess
import wave
from pathlib import Path

from .config import APP_DIR


KNOWN_MODELS = ("small", "medium", "large-v3-turbo", "large-v2")


def models_dir(config: dict | None = None) -> Path:
    configured = (config or {}).get("models_path")
    return Path(os.path.expandvars(configured)).expanduser() if configured else APP_DIR / "models"


def model_path(model: str, local_models: Path, app_models: Path) -> Path | None:
    candidate = Path(os.path.expandvars(model)).expanduser()
    if candidate.exists():
        return candidate
    for root in (local_models, app_models):
        for name in (model, f"faster-whisper-{model}", f"whisper-{model}"):
            path = root / name
            if path.exists():
                return path
    return None


def model_available(model: str, local_models: Path, app_models: Path) -> bool:
    return model_path(model, local_models, app_models) is not None


def model_install_message(model: str, model_dir: Path) -> str:
    return (
        f"找不到模型：{model}\n"
        "請點「下載模型」，或把模型 zip 解壓到：\n"
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
    return subprocess.run(model_download_command(exe_path, probe, model, model_dir), check=False).returncode
