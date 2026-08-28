import queue
import wave
from pathlib import Path


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
        return self.text


class StoppingTranslator:
    def __init__(self, engine, text: str, **state):
        self.engine = engine
        self.text = text
        self.__dict__.update(state)

    def translate(self, text, source_language, target_language):
        self.engine.running = False
        return self.text
