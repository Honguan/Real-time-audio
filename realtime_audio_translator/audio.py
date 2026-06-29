import queue
import wave
from pathlib import Path


def _sd():
    import sounddevice as sd

    return sd


def list_audio_devices() -> list[dict]:
    sd = _sd()
    hostapis = sd.query_hostapis()
    devices = []
    for index, device in enumerate(sd.query_devices()):
        devices.append(
            {
                "index": index,
                "name": device["name"],
                "input_channels": int(device["max_input_channels"]),
                "output_channels": int(device["max_output_channels"]),
                "hostapi": hostapis[device["hostapi"]]["name"],
            }
        )
    return devices


def format_device_label(device: dict) -> str:
    return f"{device['name']} [{device['hostapi']}]"


def device_name_from_label(label: str) -> str:
    return label.rsplit(" [", 1)[0]


def find_device(name_part: str, want_output: bool) -> int | None:
    needle = device_name_from_label(name_part).lower()
    for device in list_audio_devices():
        if needle in device["name"].lower():
            if want_output and device["output_channels"] > 0:
                return device["index"]
            if not want_output and device["input_channels"] > 0:
                return device["index"]
    return None


def capture_wav(path: Path, device_index: int, seconds: float, loopback: bool = False) -> Path:
    import numpy as np

    sd = _sd()
    device = sd.query_devices(device_index)
    samplerate = int(device.get("default_samplerate") or 48000)
    channels = int(device["max_output_channels"] if loopback else device["max_input_channels"])
    channels = max(1, min(channels, 2))
    extra = None
    if loopback:
        extra = sd.WasapiSettings(loopback=True)
    frames = int(samplerate * seconds)
    data = sd.rec(frames, samplerate=samplerate, channels=channels, dtype="int16", device=device_index, extra_settings=extra)
    sd.wait()
    if channels > 1:
        data = data.mean(axis=1).astype(np.int16)
        channels = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(data.tobytes())
    return path


class SegmentWorker:
    def __init__(self, cache_dir: Path, device_index: int, seconds: float, loopback: bool):
        self.cache_dir = cache_dir
        self.device_index = device_index
        self.seconds = seconds
        self.loopback = loopback
        self._stopped = False
        self.queue: queue.Queue[Path] = queue.Queue()

    def stop(self) -> None:
        self._stopped = True

    def run(self, prefix: str) -> None:
        count = 0
        while not self._stopped:
            path = self.cache_dir / f"{prefix}-{count:06d}.wav"
            try:
                self.queue.put(capture_wav(path, self.device_index, self.seconds, self.loopback))
            except Exception:
                self._stopped = True
                raise
            count += 1
