import audioop
import json
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
                "default_samplerate": float(device["default_samplerate"]),
            }
        )
    return devices


def format_device_label(device: dict) -> str:
    return f"{device['name']} [{device['hostapi']} · #{device['index']}]"


class DeviceResolutionError(ValueError):
    pass


DEVICE_ID_FIELDS = ("index", "name", "hostapi", "input_channels", "output_channels", "default_samplerate")


def device_identity(device: dict) -> str:
    descriptor = {key: device[key] for key in DEVICE_ID_FIELDS}
    return "portaudio:" + json.dumps(descriptor, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def device_descriptor(identity: str) -> dict:
    if not str(identity).startswith("portaudio:"):
        raise DeviceResolutionError("音訊裝置設定無效，請重新選擇裝置")
    try:
        descriptor = json.loads(str(identity).removeprefix("portaudio:"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise DeviceResolutionError("音訊裝置設定無效，請重新選擇裝置") from exc
    if not isinstance(descriptor, dict) or any(key not in descriptor for key in DEVICE_ID_FIELDS):
        raise DeviceResolutionError("音訊裝置設定不完整，請重新選擇裝置")
    try:
        descriptor["index"] = int(descriptor["index"])
        descriptor["input_channels"] = int(descriptor["input_channels"])
        descriptor["output_channels"] = int(descriptor["output_channels"])
        descriptor["default_samplerate"] = float(descriptor["default_samplerate"])
        descriptor["name"] = str(descriptor["name"])
        descriptor["hostapi"] = str(descriptor["hostapi"])
    except (TypeError, ValueError) as exc:
        raise DeviceResolutionError("音訊裝置設定無效，請重新選擇裝置") from exc
    return descriptor


def _device_signature(device: dict) -> tuple:
    return tuple(device[key] for key in DEVICE_ID_FIELDS if key not in {"index", "default_samplerate"})


def find_device(identity: str, want_output: bool, devices: list[dict] | None = None) -> int:
    if not identity:
        try:
            default = _sd().query_devices(kind="output" if want_output else "input")
            return int(default["index"])
        except Exception as exc:
            raise DeviceResolutionError("找不到系統預設音訊裝置") from exc
    saved = device_descriptor(identity)
    try:
        available = devices if devices is not None else list_audio_devices()
    except Exception as exc:
        raise DeviceResolutionError("無法讀取音訊裝置清單") from exc
    candidates = [
        device for device in available
        if device["output_channels" if want_output else "input_channels"] > 0
    ]
    exact = next((device for device in candidates if device["index"] == saved["index"] and _device_signature(device) == _device_signature(saved)), None)
    if exact:
        return int(exact["index"])
    matches = [device for device in candidates if _device_signature(device) == _device_signature(saved)]
    if len(matches) == 1:
        return int(matches[0]["index"])
    if len(matches) > 1:
        raise DeviceResolutionError(f"音訊裝置「{saved['name']}」有多個相同端點，請重新選擇")
    raise DeviceResolutionError(f"找不到已儲存的音訊裝置「{saved['name']}」，請重新連接或重新選擇")


def same_device_identity(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return device_descriptor(left) == device_descriptor(right)
    except DeviceResolutionError:
        return False


def virtual_mic_recaptures_tts(microphone_device: str, tts_output_device: str) -> bool:
    return same_device_identity(microphone_device, tts_output_device)


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


def capture_wav(path: Path, device_index: int, seconds: float, loopback: bool = False, cancel_event=None) -> Path:
    import numpy as np

    sd = _sd()
    device = sd.query_devices(device_index)
    if loopback:
        output = next((candidate for candidate in list_audio_devices() if candidate["index"] == device_index), None)
        if output is None:
            raise DeviceResolutionError("已選擇的輸出裝置已不可用")
        return _capture_loopback_wav(path, output, seconds, cancel_event)
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


def _pyaudio_output_for_device(audio, output_device: dict) -> dict:
    outputs = []
    for index in range(audio.get_device_count()):
        candidate = audio.get_device_info_by_index(index)
        if int(candidate.get("maxOutputChannels", 0)) <= 0:
            continue
        hostapi = audio.get_host_api_info_by_index(int(candidate["hostApi"]))["name"]
        if (
            str(candidate["name"]) == str(output_device["name"])
            and str(hostapi) == str(output_device["hostapi"])
            and int(candidate["maxOutputChannels"]) == int(output_device["output_channels"])
            and float(candidate["defaultSampleRate"]) == float(output_device["default_samplerate"])
        ):
            outputs.append(candidate)
    if len(outputs) != 1:
        reason = "有多個相同端點" if outputs else "不存在"
        raise DeviceResolutionError(f"無法對應 WASAPI loopback：輸出裝置「{output_device['name']}」{reason}")
    return outputs[0]


def _capture_loopback_wav(path: Path, output_device: dict, seconds: float, cancel_event=None) -> Path:
    import numpy as np
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as audio:
        output = _pyaudio_output_for_device(audio, output_device)
        loopback = audio.get_wasapi_loopback_analogue_by_dict(output)
        if loopback is None:
            raise DeviceResolutionError(f"找不到輸出裝置「{output_device['name']}」的 WASAPI loopback")
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
