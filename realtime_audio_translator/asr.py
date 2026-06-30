import subprocess
import sys
import tempfile
from pathlib import Path

from .runtime import runtime_dir, whisper_exe


def add_xxl_data(repo_root: Path) -> None:
    data_path = repo_root / "_xxl_data"
    if data_path.exists() and str(data_path) not in sys.path:
        sys.path.insert(0, str(data_path))


class AudioTranscriber:
    def __init__(self, repo_root: Path, model_name: str, model_dir: Path, device: str = "cuda", compute_type: str = "auto", config: dict | None = None):
        add_xxl_data(repo_root)
        self.model_name = model_name
        self.model_dir = model_dir
        self.exe_path = whisper_exe(runtime_dir(config))
        self.model = None
        try:
            from faster_whisper import WhisperModel

            self.model = WhisperModel(self._model_path(), device=device, compute_type=compute_type, download_root=str(model_dir))
        except Exception:
            if not self.exe_path.exists():
                raise RuntimeError(f"Runtime missing: {self.exe_path}")

    def _model_path(self) -> str:
        for name in (self.model_name, f"faster-whisper-{self.model_name}"):
            path = self.model_dir / name
            if path.exists():
                return str(path)
        return self.model_name

    def transcribe(self, wav_path: Path, language: str | None = None) -> str:
        if language == "auto":
            language = None
        if self.model is None:
            return self._transcribe_with_exe(wav_path, language)
        segments, _ = self.model.transcribe(
            str(wav_path),
            language=language or None,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    def _transcribe_with_exe(self, wav_path: Path, language: str | None = None) -> str:
        if language == "auto":
            language = None
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
                "txt",
                "--beep_off",
            ]
            if language:
                command.extend(["--language", language])
            result = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout).strip())
            txt_files = list(out_dir.glob("*.txt"))
            return txt_files[0].read_text(encoding="utf-8", errors="replace").strip() if txt_files else ""
