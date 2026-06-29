import wave
from pathlib import Path

from .audio import find_device


def write_linear16_wav(path: Path, audio: bytes, samplerate: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(audio)
    return path


def play_linear16(audio: bytes, device_name: str = "CABLE Input", samplerate: int = 24000) -> None:
    import numpy as np
    import sounddevice as sd

    device = find_device(device_name, want_output=True)
    data = np.frombuffer(audio, dtype="int16")
    sd.play(data, samplerate=samplerate, device=device, blocking=True)
