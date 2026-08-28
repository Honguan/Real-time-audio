import audioop
import queue
import threading
import time
import wave
from dataclasses import dataclass, field
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


def capture_wav(path: Path, device_index: int, seconds: float, loopback: bool = False, cancel_event=None) -> Path:
    import numpy as np

    sd = _sd()
    device = sd.query_devices(device_index)
    if loopback:
        return _capture_loopback_wav(path, device, seconds, cancel_event)
    samplerate = int(device.get("default_samplerate") or 48000)
    channels = int(device["max_input_channels"])
    channels = max(1, min(channels, 2))
    frames = int(samplerate * seconds)
    data = sd.rec(frames, samplerate=samplerate, channels=channels, dtype="int16", device=device_index)
    if cancel_event is not None and cancel_event.wait(seconds):
        sd.stop()
        raise InterruptedError
    status = sd.wait(ignore_errors=False)
    if getattr(status, "input_overflow", False):
        raise CaptureOverflowError("PortAudio input overflow")
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError
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


def _capture_loopback_wav(path: Path, output_device: dict, seconds: float, cancel_event=None) -> Path:
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
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError
                count = min(1024, frames)
                chunks.append(stream.read(count, exception_on_overflow=True))
                frames -= count
    data = np.frombuffer(b"".join(chunks), dtype=np.int16)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError
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


MAX_PENDING_SEGMENTS = 3
CAPTURE_RETRY_DELAYS = (0.1, 0.25, 0.5)


class CaptureOverflowError(OSError):
    pass


@dataclass(frozen=True)
class WorkerHealth:
    direction: str
    state: str
    error_code: str = ""
    message: str = ""
    failure_timestamp: float | None = None
    attempt: int = 0
    error: BaseException | None = field(default=None, compare=False, repr=False)


def capture_error_code(error: BaseException) -> str:
    if isinstance(error, CaptureOverflowError):
        return "audio_overflow"
    if type(error).__name__ == "PortAudioError":
        code = error.args[1] if len(error.args) > 1 else "unknown"
        return f"portaudio_{code}"
    if isinstance(error, OSError):
        return "audio_io_error"
    return "capture_fatal"


def discard_audio_segment(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


class SegmentWorker:
    def __init__(self, cache_dir: Path, device_index: int, seconds: float, loopback: bool, cancel_event=None, health_callback=None):
        self.cache_dir = cache_dir
        self.device_index = device_index
        self.seconds = seconds
        self.loopback = loopback
        self._cancel = cancel_event or threading.Event()
        self._stopped = False
        self._health_callback = health_callback
        self._queue_lock = threading.Lock()
        self.queue: queue.Queue[Path] = queue.Queue(maxsize=MAX_PENDING_SEGMENTS)
        self.dropped_segments = 0
        self.health = WorkerHealth("", "stopped")

    def _emit_health(self, direction: str, state: str, error: BaseException | None = None, attempt: int = 0) -> None:
        self.health = WorkerHealth(
            direction,
            state,
            capture_error_code(error) if error else "",
            str(error) if error else "",
            time.time() if error else None,
            attempt,
            error,
        )
        if self._health_callback:
            try:
                self._health_callback(self.health)
            except Exception:
                pass

    def stop(self) -> None:
        self._stopped = True
        self._cancel.set()
        with self._queue_lock:
            self._discard_pending()

    def discard_pending(self) -> int:
        with self._queue_lock:
            return self._discard_pending()

    def _discard_pending(self) -> int:
        discarded = 0
        while True:
            try:
                discard_audio_segment(self.queue.get_nowait())
                discarded += 1
            except queue.Empty:
                return discarded

    def _enqueue(self, captured: Path) -> None:
        with self._queue_lock:
            if self._stopped or self._cancel.is_set():
                discard_audio_segment(captured)
                return
            while True:
                try:
                    self.queue.put_nowait(captured)
                    return
                except queue.Full:
                    try:
                        discard_audio_segment(self.queue.get_nowait())
                        self.dropped_segments += 1
                    except queue.Empty:
                        continue

    def run(self, prefix: str) -> None:
        count = 0
        failures = 0
        self._emit_health(prefix, "capturing")
        while not self._stopped:
            path = self.cache_dir / f"{prefix}-{count:06d}.wav"
            try:
                captured = capture_wav(path, self.device_index, self.seconds, self.loopback, self._cancel)
                if self._cancel.is_set():
                    discard_audio_segment(captured)
                    return
                self._enqueue(captured)
                if failures:
                    self._emit_health(prefix, "capturing")
                    failures = 0
            except InterruptedError:
                discard_audio_segment(path)
                return
            except Exception as exc:
                discard_audio_segment(path)
                failures += 1
                recoverable = isinstance(exc, OSError) or type(exc).__name__ == "PortAudioError"
                if recoverable and failures <= len(CAPTURE_RETRY_DELAYS):
                    self._emit_health(prefix, "degraded", exc, failures)
                    if self._cancel.wait(CAPTURE_RETRY_DELAYS[failures - 1]):
                        return
                    self._emit_health(prefix, "recovering", exc, failures)
                    continue
                self._stopped = True
                with self._queue_lock:
                    self._discard_pending()
                self._emit_health(prefix, "failed", exc, failures)
                return
            count += 1
