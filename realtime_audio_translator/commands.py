import json
import re
import subprocess
from pathlib import Path


OPTION_RE = re.compile(r"^\s+--([a-zA-Z0-9_-]+)(?:[^\n,]*)(?:,\s*(-[a-zA-Z0-9_-]+))?")
CHOICES_RE = re.compile(r"\{([^{}]+)\}")


def parse_help_options(help_text: str) -> dict:
    options: dict[str, dict] = {}
    for line in help_text.splitlines():
        match = OPTION_RE.match(line)
        if not match:
            continue
        name, alias = match.groups()
        choices_match = CHOICES_RE.search(line)
        options[name] = {
            "aliases": [alias] if alias else [],
            "choices": [item.strip() for item in choices_match.group(1).split(",")] if choices_match else [],
            "flag": " " not in line.strip().split(",", 1)[0],
        }
    return options


def refresh_commands(exe_path: Path, out_path: Path) -> dict:
    result = subprocess.run(
        [str(exe_path), "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    options = parse_help_options(result.stdout + result.stderr)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(options, handle, ensure_ascii=False, indent=2)
    return options
