import json
import urllib.request
from pathlib import Path

from packaging.version import Version

from . import __version__


RELEASES_URL = "https://github.com/Honguan/Real-time-audio/releases"
LATEST_RELEASE_API = "https://api.github.com/repos/Honguan/Real-time-audio/releases/latest"


def current_version(app_root: Path | None = None) -> str:
    if app_root:
        version_file = app_root / "release_version.txt"
        if version_file.exists():
            return version_file.read_text(encoding="utf-8").strip()
    return f"v{__version__}"


def latest_release_tag(timeout: float = 5.0) -> str:
    with urllib.request.urlopen(LATEST_RELEASE_API, timeout=timeout) as response:
        return latest_release_tag_from_json(response.read())


def latest_release_tag_from_json(data: bytes) -> str:
    return str(json.loads(data.decode("utf-8"))["tag_name"])


def is_newer_version(latest: str, current: str, *, allow_prerelease: bool = False) -> bool:
    left = Version(latest)
    right = Version(current)
    return left > right and (allow_prerelease or not left.is_prerelease)


def release_update_message(current: str, latest: str) -> str:
    if is_newer_version(latest, current):
        return f"有新版本可下載：{latest}（{RELEASES_URL}）"
    return f"已是最新版本：{current}"
