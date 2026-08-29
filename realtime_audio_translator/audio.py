import audioop
import json
import queue
import threading
import time
import wave
from collections import deque
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


@dataclass
class AudioSegment:
    pcm: bytes
    sample_rate: int
    timing: dict[str, float] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return len(self.pcm) / (2 * self.sample_rate) if self.sample_rate > 0 else 0.0


@dataclass(frozen=True)
class SegmentationPolicy:
    frame_seconds: float
    pre_roll_seconds: float
    hangover_seconds: float
    overlap_seconds: float
    max_utterance_seconds: float


SEGMENTATION_POLICIES = {
    "low_latency": SegmentationPolicy(0.04, 0.12, 0.16, 0.08, 1.5),
    "balanced": SegmentationPolicy(0.05, 0.20, 0.30, 0.10, 2.0),
    "quality": SegmentationPolicy(0.08, 0.32, 0.48, 0.16, 3.0),
    "offline_light": SegmentationPolicy(0.10, 0.20, 0.40, 0.10, 2.5),
}


def segmentation_policy(mode: str, max_utterance_seconds=None) -> SegmentationPolicy:
    policy = SEGMENTATION_POLICIES.get(str(mode), SEGMENTATION_POLICIES["balanced"])
    try:
        maximum = min(3.0, max(policy.frame_seconds, float(max_utterance_seconds)))
    except (TypeError, ValueError):
        return policy
    return SegmentationPolicy(policy.frame_seconds, policy.pre_roll_seconds, policy.hangover_seconds, policy.overlap_seconds, maximum)


class UtteranceSegmenter:
    def __init__(self, policy: SegmentationPolicy, threshold: float):
        self.policy = policy
        self.threshold = threshold
        self._pre_roll = deque(maxlen=max(1, round(policy.pre_roll_seconds / policy.frame_seconds)))
        self._hangover_frames = max(1, round(policy.hangover_seconds / policy.frame_seconds))
        self._overlap_frames = max(0, round(policy.overlap_seconds / policy.frame_seconds))
        self._max_frames = max(1, round(policy.max_utterance_seconds / policy.frame_seconds))
        self._frames: list[AudioSegment] = []
        self._silence_frames = 0
        self._leading_overlap_frames = 0

    def push(self, frame: AudioSegment) -> AudioSegment | None:
        vad_started = time.perf_counter()
        speech = audio_segment_active(frame, self.threshold)
        frame.timing["_vad_seconds"] = time.perf_counter() - vad_started
        if not self._frames:
            if not speech:
                self._pre_roll.append(frame)
                return None
            self._frames = [*self._pre_roll, frame]
            self._pre_roll.clear()
        else:
            self._frames.append(frame)
        self._silence_frames = 0 if speech else self._silence_frames + 1
        if len(self._frames) >= self._max_frames:
            utterance = self._utterance()
            retained = self._frames[-self._overlap_frames:] if self._overlap_frames else []
            if retained and self._silence_frames < len(retained):
                self._frames = list(retained)
                self._silence_frames = min(self._silence_frames, len(retained))
                self._leading_overlap_frames = len(retained)
            else:
                self._pre_roll.extend(retained)
                self._frames = []
                self._silence_frames = 0
                self._leading_overlap_frames = 0
            return utterance
        if self._silence_frames >= self._hangover_frames:
            utterance = self._utterance()
            silence_tail = min(self._silence_frames, self._pre_roll.maxlen)
            self._pre_roll.extend(self._frames[-silence_tail:])
            self._frames = []
            self._silence_frames = 0
            self._leading_overlap_frames = 0
            return utterance
        return None

    def flush(self) -> AudioSegment | None:
        if not self._frames:
            return None
        if self._leading_overlap_frames >= len(self._frames):
            self._frames = []
            self._silence_frames = 0
            self._leading_overlap_frames = 0
            return None
        utterance = self._utterance()
        self._frames = []
        self._silence_frames = 0
        self._leading_overlap_frames = 0
        return utterance

    def _utterance(self) -> AudioSegment:
        utterance = self._join(self._frames)
        if self._leading_overlap_frames:
            utterance.timing["overlap_seconds"] = self._leading_overlap_frames * self.policy.frame_seconds
        return utterance

    @staticmethod
    def _join(frames: list[AudioSegment]) -> AudioSegment:
        timing = dict(frames[0].timing)
        timing.update({key: value for key, value in frames[-1].timing.items() if "completed" in key})
        timing["_vad_seconds"] = sum(frame.timing.get("_vad_seconds", 0.0) for frame in frames)
        timing["vad_started_at"] = timing.get("capture_started_at", time.time())
        timing["vad_completed_at"] = timing.get("capture_completed_at", time.time())
        return AudioSegment(b"".join(frame.pcm for frame in frames), frames[0].sample_rate, timing)


def audio_segment_active(segment: AudioSegment | Path, threshold: float) -> bool:
    threshold = min(1.0, max(0.0, float(threshold)))
    if threshold == 0:
        return True
    if isinstance(segment, AudioSegment):
        frames = segment.pcm
        sample_width = 2
    else:
        with wave.open(str(segment), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            sample_width = handle.getsampwidth()
    if not frames:
        return False
    peak = float(2 ** (8 * sample_width - 1))
    return audioop.rms(frames, sample_width) / peak >= threshold


def write_audio_segment(path: Path, segment: AudioSegment) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(segment.sample_rate)
        handle.writeframes(segment.pcm)
    return path


def _mono_segment(data, sample_rate: int, channels: int, timing: dict[str, float] | None = None) -> AudioSegment:
    import numpy as np

    samples = np.frombuffer(data, dtype=np.int16) if isinstance(data, bytes) else np.asarray(data, dtype=np.int16).reshape(-1)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return AudioSegment(samples.tobytes(), sample_rate, dict(timing or {}))


def audio_frame_stream(device_index: int, loopback: bool, cancel_event, frame_seconds: float):
    if loopback:
        output = next((candidate for candidate in list_audio_devices() if candidate["index"] == device_index), None)
        if output is None:
            raise DeviceResolutionError("已選擇的輸出裝置已不可用")
        yield from _loopback_frame_stream(output, cancel_event, frame_seconds)
        return
    sd = _sd()
    device = sd.query_devices(device_index)
    sample_rate = int(device.get("default_samplerate") or 48000)
    channels = max(1, min(int(device["max_input_channels"]), 2))
    frame_count = max(1, round(sample_rate * frame_seconds))
    with sd.InputStream(samplerate=sample_rate, blocksize=frame_count, channels=channels, dtype="int16", device=device_index) as stream:
        while not cancel_event.is_set():
            started_at = time.time()
            started_perf = time.perf_counter()
            data, overflowed = stream.read(frame_count)
            if overflowed:
                raise CaptureOverflowError("PortAudio input overflow")
            yield _mono_segment(data, sample_rate, channels, {
                "capture_started_at": started_at,
                "_capture_started_perf": started_perf,
                "capture_completed_at": time.time(),
                "_capture_completed_perf": time.perf_counter(),
            })


def _loopback_frame_stream(output_device: dict, cancel_event, frame_seconds: float):
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as audio:
        output = _pyaudio_output_for_device(audio, output_device)
        loopback = audio.get_wasapi_loopback_analogue_by_dict(output)
        if loopback is None:
            raise DeviceResolutionError(f"找不到輸出裝置「{output_device['name']}」的 WASAPI loopback")
        sample_rate = int(loopback["defaultSampleRate"])
        channels = max(1, int(loopback["maxInputChannels"]))
        frame_count = max(1, round(sample_rate * frame_seconds))
        with audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            input_device_index=loopback["index"],
            frames_per_buffer=frame_count,
        ) as stream:
            while not cancel_event.is_set():
                started_at = time.time()
                started_perf = time.perf_counter()
                try:
                    data = stream.read(frame_count, exception_on_overflow=True)
                except OSError as exc:
                    if -9981 in exc.args or "overflow" in str(exc).lower():
                        raise CaptureOverflowError("PortAudio input overflow") from exc
                    raise
                yield _mono_segment(data, sample_rate, channels, {
                    "capture_started_at": started_at,
                    "_capture_started_perf": started_perf,
                    "capture_completed_at": time.time(),
                    "_capture_completed_perf": time.perf_counter(),
                })


def capture_audio(device_index: int, seconds: float, loopback: bool = False, cancel_event=None) -> AudioSegment:
    sd = _sd()
    device = sd.query_devices(device_index)
    if loopback:
        output = next((candidate for candidate in list_audio_devices() if candidate["index"] == device_index), None)
        if output is None:
            raise DeviceResolutionError("已選擇的輸出裝置已不可用")
        return _capture_loopback_audio(output, seconds, cancel_event)
    samplerate = int(device.get("default_samplerate") or 48000)
    channels = max(1, min(int(device["max_input_channels"]), 2))
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
    return _mono_segment(data, samplerate, channels)


def capture_wav(path: Path, device_index: int, seconds: float, loopback: bool = False, cancel_event=None) -> Path:
    return write_audio_segment(path, capture_audio(device_index, seconds, loopback, cancel_event))


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


def _capture_loopback_audio(output_device: dict, seconds: float, cancel_event=None) -> AudioSegment:
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
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError
    return _mono_segment(b"".join(chunks), samplerate, channels)


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


def discard_audio_segment(segment: AudioSegment | Path) -> bool:
    if isinstance(segment, AudioSegment):
        return True
    try:
        segment.unlink(missing_ok=True)
        return True
    except OSError:
        return False


class SegmentWorker:
    def __init__(self, device_index: int, loopback: bool, threshold: float, policy: SegmentationPolicy, cancel_event=None, health_callback=None):
        self.device_index = device_index
        self.loopback = loopback
        self.threshold = threshold
        self.policy = policy
        self._cancel = cancel_event or threading.Event()
        self._stopped = False
        self._health_callback = health_callback
        self._queue_lock = threading.Lock()
        self.queue: queue.Queue[AudioSegment] = queue.Queue(maxsize=MAX_PENDING_SEGMENTS)
        self.dropped_segments = 0
        self.max_queue_depth = 0
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

    def take_timing(self, captured: AudioSegment) -> dict[str, float]:
        return captured.timing

    def _discard_pending(self) -> int:
        discarded = 0
        while True:
            try:
                pending = self.queue.get_nowait()
                discard_audio_segment(pending)
                discarded += 1
            except queue.Empty:
                return discarded

    def _enqueue(self, captured: AudioSegment, timing: dict[str, float] | None = None) -> None:
        with self._queue_lock:
            if self._stopped or self._cancel.is_set():
                discard_audio_segment(captured)
                return
            while True:
                try:
                    timing = dict(timing or {})
                    timing["enqueued_at"] = time.time()
                    timing["_enqueued_perf"] = time.perf_counter()
                    captured.timing.update(timing)
                    self.queue.put_nowait(captured)
                    self.max_queue_depth = max(self.max_queue_depth, self.queue.qsize())
                    return
                except queue.Full:
                    try:
                        dropped = self.queue.get_nowait()
                        discard_audio_segment(dropped)
                        self.dropped_segments += 1
                    except queue.Empty:
                        continue

    def run(self, prefix: str) -> None:
        failures = 0
        self._emit_health(prefix, "capturing")
        while not self._stopped:
            segmenter = UtteranceSegmenter(self.policy, self.threshold)
            try:
                for frame in audio_frame_stream(self.device_index, self.loopback, self._cancel, self.policy.frame_seconds):
                    utterance = segmenter.push(frame)
                    if utterance is not None:
                        self._enqueue(utterance, utterance.timing)
                    if failures:
                        self._emit_health(prefix, "capturing")
                        failures = 0
                if not self._stopped and not self._cancel.is_set():
                    trailing = segmenter.flush()
                    if trailing is not None:
                        self._enqueue(trailing, trailing.timing)
                return
            except Exception as exc:
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
