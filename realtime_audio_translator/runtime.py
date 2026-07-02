import shutil
from pathlib import Path

from .config import APP_DIR


DEFAULT_RUNTIME_DIR = APP_DIR / "runtime"
WHISPER_EXE = "faster-whisper-xxl.exe"
CUDA_HINTS = ("cublas64_12.dll", "cudnn64_9.dll")
RUNTIME_RELEASE_URL = "https://github.com/Purfview/whisper-standalone-win/releases"
CUDA_PACKAGE_NAME = "cuBLAS.and.cuDNN_CUDA12_win_v3.7z"


def runtime_dir(config: dict | None = None) -> Path:
    configured = (config or {}).get("runtime_dir")
    return Path(configured).expanduser() if configured else DEFAULT_RUNTIME_DIR


def whisper_exe(root: Path = DEFAULT_RUNTIME_DIR) -> Path:
    return root / WHISPER_EXE


def runtime_status(root: Path = DEFAULT_RUNTIME_DIR) -> dict:
    missing = []
    warnings = []
    if not whisper_exe(root).exists():
        missing.append(WHISPER_EXE)
    for name in CUDA_HINTS:
        if not any(root.rglob(name)):
            warnings.append(name)
    return {
        "ready": not missing,
        "path": str(root),
        "missing": missing,
        "warnings": warnings,
        "release_url": RUNTIME_RELEASE_URL,
        "cuda_package": CUDA_PACKAGE_NAME,
    }


def runtime_install_message(root: Path = DEFAULT_RUNTIME_DIR) -> str:
    return (
        f"Put {WHISPER_EXE} in {root}.\n"
        f"Runtime: {RUNTIME_RELEASE_URL}\n"
        f"CUDA12: {CUDA_PACKAGE_NAME}"
    )


def install_runtime_from(source: Path, target: Path = DEFAULT_RUNTIME_DIR) -> Path:
    exe = whisper_exe(source)
    if not exe.exists():
        matches = list(source.rglob(WHISPER_EXE))
        if not matches:
            raise FileNotFoundError(exe)
        source = matches[0].parent
    if source.resolve() == target.resolve():
        return target
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(child, destination)
    return target
