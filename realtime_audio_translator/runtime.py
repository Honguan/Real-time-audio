import shutil
from pathlib import Path

from .config import APP_DIR


DEFAULT_RUNTIME_DIR = APP_DIR / "runtime" / "cuda12"
WHISPER_EXE = "faster-whisper-xxl.exe"
REQUIRED_RUNTIME_ITEMS = (WHISPER_EXE, "ffmpeg.exe", "_xxl_data")
CUDA_HINTS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
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
    for name in REQUIRED_RUNTIME_ITEMS:
        if not (root / name).exists():
            missing.append(name)
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
        "尚未找到語音辨識 runtime。\n"
        f"請到 {RUNTIME_RELEASE_URL} 下載 Faster-Whisper-XXL Windows runtime，\n"
        f"並下載 {CUDA_PACKAGE_NAME}。\n"
        f"兩個都解壓到：\n{root}\n"
        "或點選「選擇 runtime 資料夾」手動指定位置。\n"
        f"資料夾需要直接包含：{', '.join(REQUIRED_RUNTIME_ITEMS)}。\n"
        f"CUDA12 建議包含：{', '.join(CUDA_HINTS)}。"
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
