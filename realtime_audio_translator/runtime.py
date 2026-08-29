import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .archive_install import INSTALL_MANIFEST, atomic_replace_tree, safe_extract_archive, validate_tree, verify_install_manifest, write_install_manifest
from .config import APP_DIR


DEFAULT_RUNTIME_DIR = APP_DIR / "runtime" / "cuda12"
WHISPER_EXE = "faster-whisper-xxl.exe"
REQUIRED_RUNTIME_ITEMS = (WHISPER_EXE, "ffmpeg.exe", "_xxl_data")
REQUIRED_CUDA_ITEMS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
RUNTIME_RELEASE_URL = "https://github.com/Honguan/Real-time-audio/releases"
UPSTREAM_RUNTIME_RELEASE_URL = "https://github.com/Purfview/whisper-standalone-win/releases"
CUDA_PACKAGE_NAME = "cuBLAS.and.cuDNN_CUDA12_win_v3.7z"
LATEST_RELEASE_API = "https://api.github.com/repos/Honguan/Real-time-audio/releases/latest"


def runtime_dir(config: dict | None = None) -> Path:
    configured = (config or {}).get("runtime_dir") or (config or {}).get("runtime_path")
    return Path(os.path.expandvars(configured)).expanduser() if configured else DEFAULT_RUNTIME_DIR


def whisper_exe(root: Path = DEFAULT_RUNTIME_DIR) -> Path:
    return root / WHISPER_EXE


def _cuda_probe(root: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run([str(whisper_exe(root)), "--checkcuda"], capture_output=True, text=True, timeout=5, check=False)
    except Exception as exc:
        return False, str(exc)
    output = (result.stdout + result.stderr).strip()
    count = re.search(r"CUDA devices?\s*:\s*(\d+)", output, re.IGNORECASE)
    indexed = re.findall(r"CUDA device\s+\d+\s*:", output, re.IGNORECASE)
    ready = result.returncode == 0 and (int(count.group(1)) > 0 if count else bool(indexed))
    return ready, output or f"--checkcuda 結束碼 {result.returncode}"


def runtime_status(root: Path = DEFAULT_RUNTIME_DIR, device: str = "auto", compute_type: str = "auto", verify_hashes: bool = False) -> dict:
    cpu_missing = []
    for name in (WHISPER_EXE, "ffmpeg.exe"):
        if not (root / name).is_file():
            cpu_missing.append(name)
    if not (root / "_xxl_data").is_dir():
        cpu_missing.append("_xxl_data")
    manifest_valid = verify_install_manifest(root, verify_hashes=verify_hashes)
    if not manifest_valid:
        cpu_missing.append(INSTALL_MANIFEST)
    cuda_missing = list(cpu_missing)
    for name in REQUIRED_CUDA_ITEMS:
        if not any(root.rglob(name)):
            cuda_missing.append(name)
    cuda_probe_ready = False
    cuda_probe_output = "尚未執行：CUDA runtime 檔案不完整"
    if not cuda_missing:
        cuda_probe_ready, cuda_probe_output = _cuda_probe(root)
        if not cuda_probe_ready:
            cuda_missing.append("CUDA --checkcuda probe")
    cpu_ready = not cpu_missing
    cuda_ready = not cuda_missing and cuda_probe_ready
    selected = str(device or "auto").lower()
    ready = cuda_ready if selected == "cuda" else cpu_ready if selected == "cpu" else cpu_ready or cuda_ready
    missing = cuda_missing if selected == "cuda" else cpu_missing
    return {
        "ready": ready,
        "device": selected,
        "compute_type": str(compute_type or "auto"),
        "cpu_ready": cpu_ready,
        "cuda_ready": cuda_ready,
        "cpu_missing": cpu_missing,
        "cuda_missing": cuda_missing,
        "cuda_probe_output": cuda_probe_output,
        "path": str(root),
        "missing": missing,
        "release_url": RUNTIME_RELEASE_URL,
    }


def runtime_install_message(root: Path = DEFAULT_RUNTIME_DIR, device: str = "cuda") -> str:
    cuda = str(device).lower() == "cuda"
    packages = "兩個 runtime 壓縮檔" if cuda else "CPU runtime 核心壓縮檔"
    dll_line = "RealtimeAudioTranslator-runtime-cuda12-dlls-<version>.zip\n" if cuda else ""
    return (
        f"尚未找到可供 {'CUDA' if cuda else 'CPU'} 使用的語音辨識 runtime。\n"
        f"請到 {RUNTIME_RELEASE_URL} 下載{packages}：\n"
        "RealtimeAudioTranslator-runtime-cuda12-core-<version>.7z\n"
        f"{dll_line}"
        "下載後請用程式內的「手動匯入 runtime」安裝；程式會驗證內容後安全替換。\n"
        f"安裝位置：\n{root}\n"
        f"備用來源：{UPSTREAM_RUNTIME_RELEASE_URL} 的 Faster-Whisper-XXL Windows runtime 和 {CUDA_PACKAGE_NAME}。\n"
        f"資料夾需要直接包含：{', '.join(REQUIRED_RUNTIME_ITEMS)}。\n"
        + (f"CUDA 模式必須包含：{', '.join(REQUIRED_CUDA_ITEMS)}，並通過 `--checkcuda`。" if cuda else "CPU 模式不需要 CUDA DLL。")
    )


def runtime_assets_from_json(data: bytes, device: str = "cuda") -> list[tuple[str, str, str]]:
    assets = json.loads(data.decode("utf-8"))["assets"]
    cuda = str(device).lower() == "cuda"
    selected = [
        (asset["name"], asset["browser_download_url"], str(asset.get("digest", "")).removeprefix("sha256:"))
        for asset in assets
        if "runtime-cuda12-core-" in asset["name"] or (cuda and "runtime-cuda12-dlls-" in asset["name"])
    ]
    expected = 2 if cuda else 1
    if len(selected) != expected or any(not digest for _name, _url, digest in selected):
        raise RuntimeError(f"最新版 Release 找不到完整的 {'CUDA12' if cuda else 'CPU'} runtime 檔案")
    return sorted(selected)


def download_runtime(target: Path = DEFAULT_RUNTIME_DIR, progress=None, device: str = "cuda") -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    required_space = (10 if str(device).lower() == "cuda" else 5) * 1024**3
    if shutil.disk_usage(target.parent).free < required_space:
        raise RuntimeError(f"runtime 安裝需要至少 {required_space // 1024**3}GB 可用磁碟空間")
    request = urllib.request.Request(LATEST_RELEASE_API, headers={"User-Agent": "RealtimeAudioTranslator"})
    with urllib.request.urlopen(request, timeout=30) as response:
        assets = runtime_assets_from_json(response.read(), device)
    with tempfile.TemporaryDirectory(prefix=f".{target.name}-install-", dir=target.parent) as temp:
        staging = Path(temp) / "runtime"
        staging.mkdir()
        for name, url, expected_digest in assets:
            if Path(name).name != name or "/" in name or "\\" in name:
                raise RuntimeError(f"runtime 檔名不安全：{name}")
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
            safe_extract_archive(archive, staging)
        missing = _required_runtime_items_missing(staging)
        if missing:
            raise RuntimeError("runtime 安裝不完整，缺少：" + ", ".join(missing))
        write_install_manifest(staging)
        if not verify_install_manifest(staging, verify_hashes=True):
            raise RuntimeError("runtime 安裝 manifest 驗證失敗")
        atomic_replace_tree(staging, target)
    return target


def install_runtime_from(source: Path, target: Path = DEFAULT_RUNTIME_DIR) -> Path:
    validate_tree(source)
    exe = whisper_exe(source)
    if not exe.exists():
        matches = list(source.rglob(WHISPER_EXE))
        if not matches:
            raise FileNotFoundError(exe)
        source = matches[0].parent
        validate_tree(source)
    missing = _required_runtime_items_missing(source)
    if missing:
        raise RuntimeError("runtime 安裝不完整，缺少：" + ", ".join(missing))
    if source.resolve() == target.resolve():
        validate_tree(target)
        write_install_manifest(target)
        return target
    validate_tree(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target.name}-install-", dir=target.parent) as temp:
        staging = Path(temp) / "runtime"
        shutil.copytree(source, staging)
        missing = _required_runtime_items_missing(staging)
        if missing:
            raise RuntimeError("runtime 安裝不完整，缺少：" + ", ".join(missing))
        write_install_manifest(staging)
        if not verify_install_manifest(staging, verify_hashes=True):
            raise RuntimeError("runtime 安裝 manifest 驗證失敗")
        atomic_replace_tree(staging, target)
    return target


def _required_runtime_items_missing(root: Path) -> list[str]:
    missing = [name for name in (WHISPER_EXE, "ffmpeg.exe") if not (root / name).is_file()]
    if not (root / "_xxl_data").is_dir():
        missing.append("_xxl_data")
    return missing
