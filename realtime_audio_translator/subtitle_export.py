import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


def srt_timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _read_cues(jsonl_path: Path) -> list[tuple]:
    cues = []
    for line_number, raw in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("expected a JSON object")
            for key in ("translated_text", "text", "direction"):
                if row.get(key) is not None and not isinstance(row[key], str):
                    raise ValueError(f"{key} must be text")
            times = []
            for key in ("audio_start_seconds", "audio_end_seconds"):
                value = row.get(key)
                if isinstance(value, (int, float)):
                    value = float(value)
                    if not math.isfinite(value * 1000):
                        raise ValueError(f"{key} must be finite")
                else:
                    value = None
                times.append(value)
            text = row.get("translated_text") or row.get("text") or ""
            if text.strip():
                cues.append((*times, row.get("direction") or "audio", text))
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"{jsonl_path.name}: line {line_number}: {exc}") from exc
    if not cues:
        raise ValueError(f"{jsonl_path.name}: no subtitle text to export")
    if all(cue[0] is not None for cue in cues):
        cues.sort(key=lambda cue: cue[0])
    return cues


def _srt_text(cues: list[tuple], cue_seconds: float = 3.0) -> str:
    if not math.isfinite(cue_seconds * 1000) or cue_seconds <= 0:
        raise ValueError("cue_seconds must be finite and positive")
    lines: list[str] = []
    timed = all(cue[0] is not None for cue in cues)
    for offset, (recorded_start, recorded_end, direction, text) in enumerate(cues):
        start = recorded_start if timed else offset * cue_seconds
        next_start = cues[offset + 1][0] if timed and offset + 1 < len(cues) else None
        end = recorded_end if timed else None
        if end is None or end <= start:
            end = next_start if next_start is not None and next_start > start else start + cue_seconds
        lines.extend([
            str(offset + 1),
            f"{srt_timestamp(start)} --> {srt_timestamp(end)}",
            f"{direction}: {text}",
            "",
        ])
    return "\n".join(lines)


def _txt_text(cues: list[tuple]) -> str:
    return "".join(f"{direction}: {text}\n" for _, _, direction, text in cues)


def _write_export(output_path: Path, text: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".subtitle-", dir=output_path.parent) as temporary:
        staged = Path(temporary) / output_path.name
        staged.write_text(text, encoding="utf-8", newline="\n")
        staged.replace(output_path)
    return output_path


def export_jsonl_to_srt(jsonl_path: Path, output_dir: Path, cue_seconds: float = 3.0) -> Path:
    return _write_export(output_dir / f"{jsonl_path.stem}.srt", _srt_text(_read_cues(jsonl_path), cue_seconds))


def export_jsonl_to_txt(jsonl_path: Path, output_dir: Path) -> Path:
    return _write_export(output_dir / f"{jsonl_path.stem}.txt", _txt_text(_read_cues(jsonl_path)))


def export_subtitles(jsonl_path: Path, output_dir: Path) -> tuple[Path, Path]:
    cues = _read_cues(jsonl_path)
    srt, txt = _srt_text(cues), _txt_text(cues)
    output_dir.mkdir(parents=True, exist_ok=True)
    session = output_dir / f"{jsonl_path.stem}-{uuid4().hex}"
    with TemporaryDirectory(prefix=".subtitle-", dir=output_dir) as temporary:
        staged = Path(temporary) / "session"
        staged.mkdir()
        (staged / f"{jsonl_path.stem}.srt").write_text(srt, encoding="utf-8", newline="\n")
        (staged / f"{jsonl_path.stem}.txt").write_text(txt, encoding="utf-8", newline="\n")
        staged.rename(session)
    return session / f"{jsonl_path.stem}.srt", session / f"{jsonl_path.stem}.txt"
