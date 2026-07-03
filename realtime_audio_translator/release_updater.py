import json
import re
import urllib.request
from pathlib import Path

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


def is_newer_version(latest: str, current: str) -> bool:
    left = _version_parts(latest)
    right = _version_parts(current)
    length = max(len(left), len(right))
    return left + (0,) * (length - len(left)) > right + (0,) * (length - len(right))


def release_update_message(current: str, latest: str) -> str:
    if is_newer_version(latest, current):
        return f"new version available: {latest} ({RELEASES_URL})"
    return f"already latest: {current}"


def _version_parts(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))
