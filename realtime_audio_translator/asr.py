import sys
from pathlib import Path


def add_xxl_data(repo_root: Path) -> None:
    data_path = repo_root / "_xxl_data"
    if data_path.exists() and str(data_path) not in sys.path:
        sys.path.insert(0, str(data_path))


class AudioTranscriber:
    def __init__(self, repo_root: Path, model_name: str, model_dir: Path, device: str = "cuda", compute_type: str = "auto"):
        add_xxl_data(repo_root)
        from faster_whisper import WhisperModel

        self.model_name = model_name
        self.model_dir = model_dir
        self.model = WhisperModel(self._model_path(), device=device, compute_type=compute_type, download_root=str(model_dir))

    def _model_path(self) -> str:
        for name in (self.model_name, f"faster-whisper-{self.model_name}"):
            path = self.model_dir / name
            if path.exists():
                return str(path)
        return self.model_name

    def transcribe(self, wav_path: Path, language: str | None = None) -> str:
        segments, _ = self.model.transcribe(
            str(wav_path),
            language=language or None,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
