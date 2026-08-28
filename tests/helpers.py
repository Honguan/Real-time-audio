import queue
import wave
from pathlib import Path

from realtime_audio_translator.asr import TranscriptionResult
from realtime_audio_translator.providers import TranslationResult


def write_wav(path: Path, sample: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(sample.to_bytes(2, "little", signed=True) * 1600)


class QueuedWorker:
    def __init__(self, wav: Path):
        self.queue = queue.Queue()
        self.queue.put(wav)


class StaticTranscriber:
    def __init__(self, text: str, **state):
        self.text = text
        self.__dict__.update(state)

    def transcribe(self, wav, source_language):
        return TranscriptionResult(
            self.text,
            getattr(self, "last_language", source_language),
            getattr(self, "last_language_probability", None),
            getattr(self, "last_confidence", None),
        )


class StoppingTranslator:
    def __init__(self, engine, text: str, **state):
        self.engine = engine
        self.text = text
        self.__dict__.update(state)

    def translate(self, text, source_language, target_language):
        self.engine.running = False
        return TranslationResult(self.text, getattr(self, "last_confidence", 0.8))
