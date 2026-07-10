import audioop
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


def virtual_mic_recaptures_tts(microphone_device: str, tts_output_device: str) -> bool:
    microphone = device_name_from_label(microphone_device).lower()
    output = device_name_from_label(tts_output_device).lower()
    return "cable output" in microphone and "cable input" in output


def loopback_device_for_output(loopback_devices, output_name: str):
    output = device_name_from_label(output_name).lower().strip()
    if not output:
        return None
    return next((device for device in loopback_devices if output in str(device.get("name", "")).lower()), None)


def audio_segment_active(path: Path, threshold: float) -> bool:
    threshold = min(1.0, max(0.0, float(threshold)))
    if threshold == 0:
        return True
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        if not frames:
            return False
        peak = float(2 ** (8 * handle.getsampwidth() - 1))
        return audioop.rms(frames, handle.getsampwidth()) / peak >= threshold


def find_device(name_part: str, want_output: bool) -> int | None:
    needle = device_name_from_label(name_part).lower().strip()
    if not needle:
        return None
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
    if loopback:
        return _capture_loopback_wav(path, device, seconds)
    samplerate = int(device.get("default_samplerate") or 48000)
    channels = int(device["max_input_channels"])
    channels = max(1, min(channels, 2))
    frames = int(samplerate * seconds)
    data = sd.rec(frames, samplerate=samplerate, channels=channels, dtype="int16", device=device_index)
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


def _capture_loopback_wav(path: Path, output_device: dict, seconds: float) -> Path:
    import numpy as np
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as audio:
        loopback = loopback_device_for_output(audio.get_loopback_device_info_generator(), output_device["name"])
        if loopback is None:
            raise RuntimeError(f"找不到喇叭的 WASAPI loopback 裝置：{output_device['name']}")
        samplerate = int(loopback["defaultSampleRate"])
        channels = max(1, int(loopback["maxInputChannels"]))
        frames = int(samplerate * seconds)
        chunks = []
        with audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=samplerate,
            input=True,
            input_device_index=loopback["index"],
            frames_per_buffer=min(1024, max(1, frames)),
        ) as stream:
            while frames > 0:
                count = min(1024, frames)
                chunks.append(stream.read(count, exception_on_overflow=False))
                frames -= count
    data = np.frombuffer(b"".join(chunks), dtype=np.int16)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1).astype(np.int16)
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
