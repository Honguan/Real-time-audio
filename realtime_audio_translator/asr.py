import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .runtime import runtime_dir, whisper_exe


DLL_DIRECTORIES = []


def add_runtime_dll_directory(runtime_root: Path) -> None:
    if runtime_root.exists() and hasattr(os, "add_dll_directory"):
        DLL_DIRECTORIES.append(os.add_dll_directory(str(runtime_root)))


def add_xxl_data(repo_root: Path, runtime_root: Path | None = None) -> None:
    for data_path in (repo_root / "_xxl_data", runtime_root / "_xxl_data" if runtime_root else None):
        if data_path and data_path.exists() and str(data_path) not in sys.path:
            sys.path.insert(0, str(data_path))


class AudioTranscriber:
    def __init__(self, repo_root: Path, model_name: str, model_dir: Path, device: str = "cuda", compute_type: str = "auto", config: dict | None = None):
        runtime_root = runtime_dir(config)
        add_runtime_dll_directory(runtime_root)
        add_xxl_data(repo_root, runtime_root)
        self.model_name = model_name
        self.model_dir = model_dir
        self.exe_path = whisper_exe(runtime_root)
        self.model = None
        self.last_language: str | None = None
        self.last_language_probability: float | None = None
        self.last_confidence: float | None = None
        try:
            from faster_whisper import WhisperModel

            self.model = WhisperModel(self._model_path(), device=device, compute_type=compute_type, download_root=str(model_dir))
        except Exception:
            if not self.exe_path.exists():
                raise RuntimeError(f"找不到 runtime：{self.exe_path}")

    def _model_path(self) -> str:
        for name in (self.model_name, f"faster-whisper-{self.model_name}"):
            path = self.model_dir / name
            if path.exists():
                return str(path)
        return self.model_name

    def transcribe(self, wav_path: Path, language: str | None = None) -> str:
        if language == "auto":
            language = None
        self.last_language_probability = None
        self.last_confidence = None
        if self.model is None:
            return self._transcribe_with_exe(wav_path, language)
        segments, info = self.model.transcribe(
            str(wav_path),
            language=language or None,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        self.last_language = getattr(info, "language", None) or language
        self.last_language_probability = getattr(info, "language_probability", None)
        texts = []
        confidences = []
        for segment in segments:
            texts.append(segment.text.strip())
            avg_logprob = getattr(segment, "avg_logprob", None)
            if avg_logprob is not None:
                try:
                    confidences.append(min(1.0, max(0.0, math.exp(float(avg_logprob)))))
                except Exception:
                    pass
        self.last_confidence = sum(confidences) / len(confidences) if confidences else None
        return " ".join(text for text in texts if text).strip()

    def _transcribe_with_exe(self, wav_path: Path, language: str | None = None) -> str:
        if language == "auto":
            language = None
        self.last_language = language
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            command = [
                str(self.exe_path),
                str(wav_path),
                "--model",
                self.model_name,
                "--model_dir",
                str(self.model_dir),
                "--output_dir",
                str(out_dir),
                "--output_format",
                "json",
                "--beep_off",
            ]
            if language:
                command.extend(["--language", language])
            result = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout).strip())
            json_files = list(out_dir.glob("*.json"))
            if not json_files:
                return ""
            data = json.loads(json_files[0].read_text(encoding="utf-8", errors="replace"))
            self.last_language = data.get("language") or language
            return str(data.get("text") or "").strip()
