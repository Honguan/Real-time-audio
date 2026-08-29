import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


_STOP = object()
CONTENT_MODES = {"both", "original", "translation", "none"}


def _managed_conversation_files(log_dir: Path) -> list[Path]:
    files = []
    if not log_dir.is_dir():
        return files
    for path in log_dir.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix not in {".jsonl", ".md"}:
            continue
        try:
            if path.suffix == ".md":
                with path.open("r", encoding="utf-8") as handle:
                    managed = handle.readline().rstrip() == f"# Conversation {path.stem}"
            else:
                with path.open("r", encoding="utf-8") as handle:
                    first = next((line for line in handle if line.strip()), "")
                row = json.loads(first)
                required = {"session_id", "timestamp", "direction", "source_language", "target_language", "provider"}
                managed = isinstance(row, dict) and required <= row.keys() and row["session_id"] == path.stem
            if managed:
                files.append(path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return files


def conversation_log_usage(log_dir: Path) -> int:
    return sum(path.stat().st_size for path in _managed_conversation_files(log_dir))


def enforce_log_retention(log_dir: Path, retention_days: int, max_bytes: int, now: float | None = None) -> None:
    files = _managed_conversation_files(log_dir)
    groups: dict[str, list[Path]] = {}
    for path in files:
        groups.setdefault(path.stem, []).append(path)
    current_time = time.time() if now is None else now
    if retention_days > 0:
        cutoff = current_time - retention_days * 86400
        for stem, paths in list(groups.items()):
            if max(path.stat().st_mtime for path in paths) < cutoff:
                for path in paths:
                    path.unlink(missing_ok=True)
                groups.pop(stem)
    if max_bytes <= 0:
        return
    ordered = sorted(groups.values(), key=lambda paths: max(path.stat().st_mtime for path in paths))
    total = sum(path.stat().st_size for paths in ordered for path in paths)
    while total > max_bytes and ordered:
        for path in ordered.pop(0):
            try:
                total -= path.stat().st_size
            except OSError:
                pass
            path.unlink(missing_ok=True)


class ConversationLog:
    def __init__(
        self,
        log_dir: Path,
        session_id: str | None = None,
        retention_days: int = 7,
        max_mb: int = 100,
        content_mode: str = "both",
        queue_size: int = 256,
    ):
        if content_mode not in CONTENT_MODES:
            raise ValueError(f"不支援的對話紀錄內容模式：{content_mode}")
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = log_dir
        self.session_id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.jsonl_path = log_dir / f"{self.session_id}.jsonl"
        self.md_path = log_dir / f"{self.session_id}.md"
        self.retention_days = max(0, int(retention_days))
        self.max_bytes = max(0, int(max_mb)) * 1024 * 1024
        self.content_mode = content_mode
        self.policy = (self.log_dir, self.retention_days, self.max_bytes, self.content_mode)
        self.dropped_records = 0
        self.error: Exception | None = None
        self._queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._closed = False
        self._stop_enqueued = False
        self._close_lock = threading.Lock()
        enforce_log_retention(self.log_dir, self.retention_days, self.max_bytes)
        self._thread = threading.Thread(target=self._run, name=f"conversation-log-{self.session_id}", daemon=True)
        self._thread.start()

    def append(
        self,
        direction: str,
        source_language: str,
        target_language: str,
        text: str,
        translated_text: str,
        provider: str,
        latency_seconds: float | None = None,
        performance: dict | None = None,
        timestamps: dict | None = None,
    ) -> None:
        row = {
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "source_language": source_language,
            "target_language": target_language,
            "provider": provider,
        }
        if self.content_mode in {"both", "original"}:
            row["text"] = text
        if self.content_mode in {"both", "translation"}:
            row["translated_text"] = translated_text
        if latency_seconds is not None:
            row["latency_seconds"] = latency_seconds
        if performance:
            row["performance"] = performance
        if timestamps:
            row["timestamps"] = timestamps
        with self._close_lock:
            if self._closed:
                return
            self._enqueue(row)

    def _enqueue(self, row: dict) -> None:
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self.dropped_records += 1
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(row)
            except queue.Full:
                self.dropped_records += 1

    def close(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._close_lock:
            self._closed = True
            if not self._stop_enqueued:
                try:
                    self._queue.put(_STOP, timeout=max(0.0, deadline - time.monotonic()))
                except queue.Full:
                    return False
                self._stop_enqueued = True
        self._thread.join(max(0.0, deadline - time.monotonic()))
        return not self._thread.is_alive()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                self._write(item)
                enforce_log_retention(self.log_dir, self.retention_days, self.max_bytes)
            except Exception as exc:
                self.error = exc
            finally:
                self._queue.task_done()

    def _write(self, row: dict) -> None:
        with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        if not self.md_path.exists():
            self.md_path.write_text(f"# Conversation {self.session_id}\n\ncreated: {row['timestamp']}\n\n", encoding="utf-8", newline="\n")
        with self.md_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"## {row['timestamp']} {row['direction']}\n\n")
            handle.write(f"- provider: {row['provider']}\n")
            if "text" in row:
                handle.write(f"- {row['source_language']}: {row['text']}\n")
            if "translated_text" in row:
                handle.write(f"- {row['target_language']}: {row['translated_text']}\n")
            if "latency_seconds" in row:
                handle.write(f"- latency: {row['latency_seconds']:.2f}s\n")
            if "performance" in row:
                handle.write(f"- performance: {json.dumps(row['performance'], ensure_ascii=False)}\n")
            handle.write("\n")
