import json
from datetime import datetime, timezone
from pathlib import Path


def append_app_log(root: Path, event: str, **fields: object) -> Path:
    path = root / "logs" / "app.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return path
