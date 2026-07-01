import json
from datetime import datetime, timezone
from pathlib import Path


class ConversationLog:
    def __init__(self, log_dir: Path, session_id: str | None = None):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.jsonl_path = log_dir / f"{self.session_id}.jsonl"
        self.md_path = log_dir / f"{self.session_id}.md"
        if not self.md_path.exists():
            created = datetime.now(timezone.utc).isoformat()
            self.md_path.write_text(f"# Conversation {self.session_id}\n\ncreated: {created}\n\n", encoding="utf-8", newline="\n")

    def append(
        self,
        direction: str,
        source_language: str,
        target_language: str,
        text: str,
        translated_text: str,
        provider: str,
        latency_seconds: float | None = None,
    ) -> None:
        row = {
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "source_language": source_language,
            "target_language": target_language,
            "text": text,
            "translated_text": translated_text,
            "provider": provider,
        }
        if latency_seconds is not None:
            row["latency_seconds"] = latency_seconds
        with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        with self.md_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"## {row['timestamp']} {direction}\n\n")
            handle.write(f"- provider: {provider}\n")
            handle.write(f"- {source_language}: {text}\n")
            handle.write(f"- {target_language}: {translated_text}\n\n")
            if latency_seconds is not None:
                handle.write(f"- latency: {latency_seconds:.2f}s\n\n")
