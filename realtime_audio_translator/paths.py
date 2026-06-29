import sys
from pathlib import Path


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path.cwd()))


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)
