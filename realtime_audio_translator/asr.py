import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .audio import AudioSegment, write_audio_segment
from .models import model_path
from .runtime import runtime_dir, runtime_status, whisper_exe


DLL_DIRECTORIES = []


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None
    language_probability: float | None = None
    model_score: float | None = None


def add_runtime_dll_directory(runtime_root: Path) -> None:
    if runtime_root.exists() and hasattr(os, "add_dll_directory"):
        DLL_DIRECTORIES.append(os.add_dll_directory(str(runtime_root)))


def add_xxl_data(repo_root: Path, runtime_root: Path | None = None) -> None:
    for data_path in (repo_root / "_xxl_data", runtime_root / "_xxl_data" if runtime_root else None):
        if data_path and data_path.exists() and str(data_path) not in sys.path:
            sys.path.insert(0, str(data_path))


class AudioTranscriber:
    def __init__(self, repo_root: Path, model_name: str, model_dir: Path, device: str = "cuda", compute_type: str = "auto", config: dict | None = None, cancel_event: threading.Event | None = None):
        runtime_root = runtime_dir(config)
        if runtime_root.exists() and any(runtime_root.iterdir()) and not runtime_status(runtime_root, device, compute_type, verify_hashes=True)["ready"]:
            raise RuntimeError(f"runtime 未通過完整性驗證：{runtime_root}")
        add_runtime_dll_directory(runtime_root)
        add_xxl_data(repo_root, runtime_root)
        local_model = model_path(model_name, model_dir, repo_root / "_models") or model_path(model_name, repo_root / "models", model_dir)
        self.model_name = local_model.name.removeprefix("faster-whisper-") if local_model else model_name
        self.model_dir = local_model.parent if local_model else model_dir
        self.device = device
        self.compute_type = compute_type
        self.exe_path = whisper_exe(runtime_root)
        self.model = None
        self._inference_lock = threading.Lock()
        self.cancel_event = cancel_event
        try:
            from faster_whisper import WhisperModel

            self.model = WhisperModel(self._model_path(), device=device, compute_type=compute_type, download_root=str(model_dir))
        except Exception:
            if not self.exe_path.exists():
                raise RuntimeError(f"找不到 runtime：{self.exe_path}")

    def _model_path(self) -> str:
        local_model = model_path(self.model_name, self.model_dir, self.model_dir)
        return str(local_model.resolve()) if local_model else self.model_name

    def transcribe(self, audio: AudioSegment | Path, language: str | None = None) -> TranscriptionResult:
        if language == "auto":
            language = None
        with self._inference_lock:
            if self.cancel_event is not None and self.cancel_event.is_set():
                return TranscriptionResult("", language)
            if self.model is None:
                return self._transcribe_with_exe(audio, language, self.cancel_event)
            model_audio = str(audio) if isinstance(audio, Path) else self._model_audio(audio)
            segments, info = self.model.transcribe(
                model_audio,
                language=language or None,
                vad_filter=True,
                beam_size=1,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            texts = []
            model_scores = []
            for segment in segments:
                texts.append(segment.text.strip())
                avg_logprob = getattr(segment, "avg_logprob", None)
                if avg_logprob is not None:
                    try:
                        model_scores.append(float(avg_logprob))
                    except Exception:
                        pass
            return TranscriptionResult(
                " ".join(text for text in texts if text).strip(),
                getattr(info, "language", None) or language,
                getattr(info, "language_probability", None),
                sum(model_scores) / len(model_scores) if model_scores else None,
            )

    @staticmethod
    def _model_audio(audio: AudioSegment):
        import numpy as np

        samples = np.frombuffer(audio.pcm, dtype="<i2").astype(np.float32)
        if audio.sample_rate != 16000 and samples.size:
            output_size = max(1, (samples.size - 1) * 16000 // audio.sample_rate + 1)
            positions = np.arange(output_size, dtype=np.float64) * audio.sample_rate / 16000
            samples = np.interp(positions, np.arange(samples.size), samples).astype(np.float32)
        return samples / 32768.0

    def _transcribe_with_exe(self, audio: AudioSegment | Path, language: str | None = None, cancel_event: threading.Event | None = None) -> TranscriptionResult:
        if language == "auto":
            language = None
        if cancel_event is not None and cancel_event.is_set():
            return TranscriptionResult("", language)
        local_model = model_path(self.model_name, self.model_dir, self.model_dir)
        if local_model and local_model.name != f"faster-whisper-{self.model_name}":
            raise RuntimeError(f"EXE 模式的模型目錄需以 faster-whisper- 開頭：{local_model}")
        with tempfile.TemporaryDirectory(prefix="realtime-audio-asr-") as tmp:
            out_dir = Path(tmp)
            wav_path = audio if isinstance(audio, Path) else write_audio_segment(out_dir / "segment.wav", audio)
            command = [
                str(self.exe_path),
                str(wav_path),
                "--model",
                self.model_name,
                "--model_dir",
                str(self.model_dir),
                "--device",
                self.device,
                "--compute_type",
                self.compute_type,
                "--output_dir",
                str(out_dir),
                "--output_format",
                "json",
                "--beep_off",
            ]
            if language:
                command.extend(["--language", language])
            if cancel_event is None:
                result = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
            else:
                # File-backed output cannot block cancellation on a full pipe.
                with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout, tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr:
                    with subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
                        try:
                            while process.poll() is None:
                                if cancel_event.wait(0.05):
                                    return TranscriptionResult("", language)
                        finally:
                            if process.poll() is None:
                                process.kill()
                            process.wait()
                    if cancel_event.is_set():
                        return TranscriptionResult("", language)
                    stdout.seek(0)
                    stderr.seek(0)
                    result = subprocess.CompletedProcess(command, process.returncode, stdout.read(), stderr.read())
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout).strip())
            json_files = list(out_dir.glob("*.json"))
            if not json_files:
                return TranscriptionResult("", language)
            data = json.loads(json_files[0].read_text(encoding="utf-8", errors="replace"))
            return TranscriptionResult(str(data.get("text") or "").strip(), data.get("language") or language)
