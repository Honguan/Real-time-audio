import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .config import APP_DIR


DEFAULT_RUNTIME_DIR = APP_DIR / "runtime" / "cuda12"
WHISPER_EXE = "faster-whisper-xxl.exe"
REQUIRED_RUNTIME_ITEMS = (WHISPER_EXE, "ffmpeg.exe", "_xxl_data")
CUDA_HINTS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
RUNTIME_RELEASE_URL = "https://github.com/Honguan/Real-time-audio/releases"
UPSTREAM_RUNTIME_RELEASE_URL = "https://github.com/Purfview/whisper-standalone-win/releases"
CUDA_PACKAGE_NAME = "cuBLAS.and.cuDNN_CUDA12_win_v3.7z"
LATEST_RELEASE_API = "https://api.github.com/repos/Honguan/Real-time-audio/releases/latest"


def runtime_dir(config: dict | None = None) -> Path:
    configured = (config or {}).get("runtime_dir") or (config or {}).get("runtime_path")
    return Path(os.path.expandvars(configured)).expanduser() if configured else DEFAULT_RUNTIME_DIR


def whisper_exe(root: Path = DEFAULT_RUNTIME_DIR) -> Path:
    return root / WHISPER_EXE


def runtime_status(root: Path = DEFAULT_RUNTIME_DIR) -> dict:
    missing = []
    warnings = []
    for name in (WHISPER_EXE, "ffmpeg.exe"):
        if not (root / name).is_file():
            missing.append(name)
    if not (root / "_xxl_data").is_dir():
        missing.append("_xxl_data")
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
        f"請到 {RUNTIME_RELEASE_URL} 下載兩個 runtime 壓縮檔：\n"
        "RealtimeAudioTranslator-runtime-cuda12-core-<version>.7z\n"
        "RealtimeAudioTranslator-runtime-cuda12-dlls-<version>.zip\n"
        f"兩個都解壓到：\n{root}\n"
        "或點選「選擇 runtime 資料夾」手動指定位置。\n"
        f"備用來源：{UPSTREAM_RUNTIME_RELEASE_URL} 的 Faster-Whisper-XXL Windows runtime 和 {CUDA_PACKAGE_NAME}。\n"
        f"資料夾需要直接包含：{', '.join(REQUIRED_RUNTIME_ITEMS)}。\n"
        f"CUDA12 建議包含：{', '.join(CUDA_HINTS)}。"
    )


def runtime_assets_from_json(data: bytes) -> list[tuple[str, str, str]]:
    assets = json.loads(data.decode("utf-8"))["assets"]
    selected = [
        (asset["name"], asset["browser_download_url"], str(asset.get("digest", "")).removeprefix("sha256:"))
        for asset in assets
        if "runtime-cuda12-core-" in asset["name"] or "runtime-cuda12-dlls-" in asset["name"]
    ]
    if len(selected) != 2 or any(not digest for _name, _url, digest in selected):
        raise RuntimeError("最新版 Release 找不到完整的 CUDA12 runtime 檔案")
    return sorted(selected)


def download_runtime(target: Path = DEFAULT_RUNTIME_DIR, progress=None) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target).free < 10 * 1024**3:
        raise RuntimeError("runtime 安裝需要至少 10GB 可用磁碟空間")
    request = urllib.request.Request(LATEST_RELEASE_API, headers={"User-Agent": "RealtimeAudioTranslator"})
    with urllib.request.urlopen(request, timeout=30) as response:
        assets = runtime_assets_from_json(response.read())
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        for name, url, expected_digest in assets:
            if progress:
                progress(f"正在下載 {name}")
            archive = Path(temp) / name
            request = urllib.request.Request(url, headers={"User-Agent": "RealtimeAudioTranslator"})
            with urllib.request.urlopen(request, timeout=30) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            digest = hashlib.sha256()
            with archive.open("rb") as downloaded:
                for block in iter(lambda: downloaded.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest().lower() != expected_digest.lower():
                raise RuntimeError(f"{name} SHA-256 驗證失敗")
            if progress:
                progress(f"正在解壓 {name}")
            subprocess.run(["tar", "-xf", str(archive), "-C", str(target)], check=True)
    status = runtime_status(target)
    if not status["ready"]:
        raise RuntimeError("runtime 安裝不完整，缺少：" + ", ".join(status["missing"]))
    return target


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
