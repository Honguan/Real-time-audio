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
    cues = []
    for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        text = row.get("translated_text") or row.get("text") or ""
        direction = row.get("direction") or "audio"
        cues.append((row.get("audio_start_seconds"), row.get("audio_end_seconds"), direction, text))
    timed = cues and all(isinstance(cue[0], (int, float)) for cue in cues)
    if timed:
        cues.sort(key=lambda cue: cue[0])
    for offset, (recorded_start, recorded_end, direction, text) in enumerate(cues):
        start = float(recorded_start) if timed else offset * cue_seconds
        next_start = float(cues[offset + 1][0]) if timed and offset + 1 < len(cues) else None
        end = float(recorded_end) if timed and isinstance(recorded_end, (int, float)) else None
        if end is None or end <= start:
            end = next_start if next_start is not None and next_start > start else start + cue_seconds
        index = offset + 1
        lines.extend(
            [
                str(index),
                f"{srt_timestamp(start)} --> {srt_timestamp(end)}",
                f"{direction}: {text}",
                "",
            ]
        )
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
