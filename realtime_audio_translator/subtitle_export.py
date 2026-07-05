import json
from pathlib import Path


def srt_timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def export_jsonl_to_srt(jsonl_path: Path, output_dir: Path, cue_seconds: float = 3.0) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{jsonl_path.stem}.srt"
    lines: list[str] = []
    index = 1
    for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        start = (index - 1) * cue_seconds
        end = start + cue_seconds
        text = row.get("translated_text") or row.get("text") or ""
        direction = row.get("direction") or "audio"
        lines.extend(
            [
                str(index),
                f"{srt_timestamp(start)} --> {srt_timestamp(end)}",
                f"{direction}: {text}",
                "",
            ]
        )
        index += 1
    output_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return output_path


def export_jsonl_to_txt(jsonl_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{jsonl_path.stem}.txt"
    lines: list[str] = []
    for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        text = row.get("translated_text") or row.get("text") or ""
        direction = row.get("direction") or "audio"
        lines.append(f"{direction}: {text}")
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    return output_path
