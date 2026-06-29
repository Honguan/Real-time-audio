import subprocess
import wave
from pathlib import Path


KNOWN_MODELS = ("medium", "large-v3-turbo", "large-v2")


def list_models(local_models: Path, app_models: Path) -> list[str]:
    found: set[str] = set()
    for root in (local_models, app_models):
        if not root.exists():
            continue
        for path in root.iterdir():
            if path.is_dir():
                found.add(path.name.replace("faster-whisper-", ""))
    return sorted(found or KNOWN_MODELS)


def recommend_model(cuda_devices: int, vram_gb: int, prefer_quality: bool = False) -> str:
    if cuda_devices < 1:
        return "medium"
    if prefer_quality and vram_gb >= 8:
        return "large-v2"
    return "large-v3-turbo" if vram_gb >= 4 else "medium"


def download_model(exe_path: Path, model: str, model_dir: Path) -> int:
    model_dir.mkdir(parents=True, exist_ok=True)
    probe = model_dir / "probe.wav"
    if not probe.exists():
        with wave.open(str(probe), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\0\0" * 16000)
    command = [
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
    return subprocess.run(command, check=False).returncode
