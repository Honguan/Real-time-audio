import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .asr import AudioTranscriber
from .audio import AudioSegment, DeviceResolutionError, SegmentWorker, WorkerHealth, discard_audio_segment, find_device, same_device_identity, segmentation_policy, virtual_mic_recaptures_tts
from .ai_confidence import build_confidence_snapshot, format_confidence_status
from .config import APP_DIR, DEFAULT_CONFIG, STATE_KEYS, TARGET_LANGUAGE_CHOICES, save_config_state, validate_language_pair
from .logbook import ConversationLog
from .models import models_dir
from .performance import FIRST_SUBTITLE_P50, FIRST_SUBTITLE_P95, FIRST_SUBTITLE_P99, LatencyWindow
from .providers import TextToSpeech, Translator
from .tts import play_linear16


OverlayCallback = Callable[[str, str], None]
StatusCallback = Callable[[str], None]


@dataclass
class PipelineContext:
    config: dict
    translator: Translator
    tts: TextToSpeech
    metrics: dict = field(default_factory=dict)


@dataclass
class TtsTask:
    direction: str
    translated: str
    target: str
    device: str
    session: str | None
    timing: dict
    cancel: threading.Event


def direction_label(direction: str) -> str:
    return "喇叭" if direction == "speaker" else "麥克風"


def drain_queue(items) -> int:
    removed = 0
    while True:
        try:
            item = items.get_nowait()
            if isinstance(item, Path):
                discard_audio_segment(item)
            removed += 1
        except queue.Empty:
            return removed


def overlay_text_from_config(original: str, translated: str, source_language: str, target_language: str, config: dict) -> str:
    lines = []
    if config.get("show_original_text"):
        lines.append(f"{source_language}: {original}" if config.get("show_language_labels") else original)
    if config.get("show_translated_text", True):
        lines.append(f"{target_language}: {translated}" if config.get("show_language_labels") else translated)
    return "\n".join(line for line in lines if line)


def audio_devices_overlap(left: str, right: str) -> bool:
    return same_device_identity(left, right)


def safe_target_language(language: str, fallback: str) -> str:
    return language if language in TARGET_LANGUAGE_CHOICES else fallback


def trim_transcript_overlap(previous: str, current: str) -> str:
    if not previous or not current:
        return current
    use_words = " " in previous or " " in current
    left = previous.split() if use_words else list(previous)
    right = current.split() if use_words else list(current)
    for count in range(min(len(left), len(right)), 0, -1):
        if [part.casefold() for part in left[-count:]] == [part.casefold() for part in right[:count]]:
            remainder = right[count:]
            return " ".join(remainder) if use_words else "".join(remainder)
    return current


class RealtimeEngine:
    def __init__(self, repo_root: Path, config: dict, overlay: OverlayCallback, status: StatusCallback, state_root: Path | None = None):
        self.repo_root = repo_root
        self.config = config
        self.overlay = overlay
        self.status = status
        self.state_root = state_root
        self.running = False
        self._closed = False
        self.paused = False
        self.muted = bool(config.get("start_muted", False))
        self._session: str | None = None
        self._cancel = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._callback_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._tts_queue_lock = threading.Lock()
        self._tts_cancel = threading.Event()
        self._tts_queues: dict[str, queue.Queue] = {}
        self._latencies = LatencyWindow()
        self._subtitle_latencies = LatencyWindow()
        self.threads: list[threading.Thread] = []
        self.workers: list[SegmentWorker] = []
        self.capture_health: dict[str, WorkerHealth] = {}
        self._active_directions: set[str] = set()
        self._starting_directions = False
        self.log = ConversationLog(Path(config.get("log_dir") or APP_DIR / "logs")) if config.get("record_logs") else None
        self.pipelines: dict[str, PipelineContext] = {}
        self.transcriber: AudioTranscriber | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.running or self._closed:
                return
            self._session = uuid.uuid4().hex
            session = self._session
            self._cancel = threading.Event()
            self._tts_cancel = threading.Event()
            with self._tts_queue_lock:
                self._tts_queues = {}
            self.running = True
        try:
            validate_language_pair(self.config)
        except ValueError as exc:
            self.running = False
            if self._session == session:
                self.status(str(exc))
            return
        session_counters = {"last_provider_error_count": 0, "last_rate_limit_count": 0, "last_max_queue_depth": 0, "last_dropped_segments": 0}
        self.config.update(session_counters)
        self._save_state(set(session_counters))
        model_load_started = time.perf_counter()
        try:
            transcriber = AudioTranscriber(
                self.repo_root,
                self.config["model"],
                models_dir(self.config),
                self.config["device"],
                self.config["compute_type"],
                self.config,
            )
        except Exception as exc:
            if not self._session_active(session):
                return
            self.running = False
            self.config["last_asr_failed"] = True
            self._save_state({"last_asr_failed"})
            message = str(exc)
            if message.startswith("Runtime missing: "):
                message = "找不到 runtime：" + message.removeprefix("Runtime missing: ")
            self.status(message)
            return
        if not self._session_active(session):
            return
        self.transcriber = transcriber
        self.config["last_model_load_seconds"] = time.perf_counter() - model_load_started
        self.config["last_asr_failed"] = False
        self._save_state({"last_asr_failed", "last_model_load_seconds"})
        started = []
        self._starting_directions = True
        skipped_feedback = False
        skipped_mic_feedback = False
        if self.config.get("speaker_enabled", True):
            if self.config.get("tts_enabled", True) and audio_devices_overlap(self.config.get("speaker_device", ""), self.config.get("tts_output_device", "")):
                skipped_feedback = True
            else:
                started.append(self._start_direction("speaker", self.config.get("speaker_device", ""), True))
        if self.config.get("microphone_enabled", True):
            if self.config.get("tts_enabled", True) and self.config.get("virtual_mic_enabled", False) and virtual_mic_recaptures_tts(self.config.get("microphone_device", ""), self.config.get("virtual_mic_input_device", "")):
                skipped_mic_feedback = True
            else:
                started.append(self._start_direction("me", self.config.get("microphone_device", ""), False))
        self._starting_directions = False
        if self._all_captures_failed():
            self.running = False
            self._cancel.set()
            self._tts_cancel.set()
            self._discard_tts_queue()
        skips = []
        if skipped_feedback:
            skips.append("喇叭擷取已略過：和 TTS 輸出相同")
        if skipped_mic_feedback:
            skips.append("麥克風擷取已略過：和虛擬麥克風輸出相同")
        if any(started) and self.running:
            if self._session_active(session):
                self._publish_capture_status(session, f"；{'；'.join(skips)}" if skips else "")
        elif self._session == session:
            self.running = False
            if not self._cancel.is_set():
                self.status("沒有可用音訊裝置" + (f"；{'；'.join(skips)}" if skips else ""))

    def stop(self, join_timeout: float = 5.0) -> str:
        deadline = time.monotonic() + max(0.0, join_timeout)
        with self._lifecycle_lock:
            self.running = False
            self._closed = True
            self._cancel.set()
            self._tts_cancel.set()
            workers = list(self.workers)
            threads = list(self.threads)
        self._discard_tts_queue()
        callback_stopped = self._callback_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        if callback_stopped:
            self._callback_lock.release()
        for worker in workers:
            worker.stop()
            drain_queue(worker.queue)
        current = threading.current_thread()
        for thread in threads:
            if thread is not current:
                thread.join(max(0.0, deadline - time.monotonic()))
        alive = [thread for thread in threads if thread.is_alive()]
        with self._lifecycle_lock:
            self.threads = [thread for thread in self.threads if thread not in threads or thread in alive]
            if not alive:
                self.workers = [worker for worker in self.workers if worker not in workers]
        pending = len(alive) + (not callback_stopped)
        message = f"停止逾時：仍有 {pending} 個背景工作" if pending else "已停止"
        self.status(message)
        return message

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.status("已暫停" if paused else self._capture_status("執行中"))

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self._tts_cancel.set()
            self._discard_tts_queue()
        elif self._tts_cancel.is_set():
            self._tts_cancel = threading.Event()
        self.status("已靜音" if muted else self._capture_status("執行中"))

    def _capture_status(self, healthy: str) -> str:
        with self._health_lock:
            failed = [direction_label(direction) for direction, health in self.capture_health.items() if health.state == "failed"]
            recovering = [direction_label(direction) for direction, health in self.capture_health.items() if health.state in {"degraded", "recovering"}]
        if failed:
            return f"部分可用；{'、'.join(failed)}擷取失敗；請停止後重新啟動"
        if recovering:
            return f"音訊降級；{'、'.join(recovering)}擷取恢復中"
        return healthy

    def _publish_capture_status(self, session: str | None, suffix: str = "") -> None:
        with self._callback_lock:
            if self._session_active(session):
                self.status(self._capture_status("執行中") + suffix)

    def _start_direction(self, direction: str, device_hint: str, loopback: bool) -> bool:
        try:
            device = find_device(device_hint, want_output=loopback)
        except (DeviceResolutionError, OSError) as exc:
            self.status(f"{direction_label(direction)}：{exc}")
            return False
        session = self._session
        worker = SegmentWorker(
            device,
            loopback,
            float(self.config.get("speech_threshold", 0.01)),
            segmentation_policy(self.config.get("performance_mode", "balanced"), self.config.get("segment_seconds")),
            self._cancel,
            lambda event: self._capture_health_changed(event, session),
        )
        self._pipeline(direction)
        capture_thread = threading.Thread(target=worker.run, args=(direction,), daemon=True)
        process_thread = threading.Thread(target=self._process_segments, args=(direction, worker, session), daemon=True)
        with self._lifecycle_lock:
            if not self._session_active(session):
                return False
            self.workers.append(worker)
            with self._health_lock:
                self._active_directions.add(direction)
            self.threads.extend([capture_thread, process_thread])
            capture_thread.start()
            process_thread.start()
        return True

    def _all_captures_failed(self) -> bool:
        with self._health_lock:
            return bool(self._active_directions) and all(
                self.capture_health.get(direction, WorkerHealth(direction, "capturing")).state == "failed"
                for direction in self._active_directions
            )

    def _capture_health_changed(self, health: WorkerHealth, session: str | None) -> None:
        if not self._session_active(session):
            return
        with self._health_lock:
            previous = self.capture_health.get(health.direction)
            self.capture_health[health.direction] = health
        label = direction_label(health.direction)
        if health.state == "degraded":
            self._publish(session, self.status, f"{label}擷取暫時失敗 [{health.error_code}]：{health.message}；第 {health.attempt} 次重試")
        elif health.state == "recovering":
            self._publish(session, self.status, f"{label}擷取恢復中；第 {health.attempt} 次重試")
        elif health.state == "capturing" and previous and previous.state != "capturing":
            self._publish(session, self.status, f"{label}擷取已恢復")
        elif health.state == "failed":
            self._publish(session, self.status, f"{label}擷取失敗 [{health.error_code}]：{health.message}；請停止後重新啟動")
            if not self._starting_directions and self._all_captures_failed():
                self.running = False
                self._cancel.set()
                self._tts_cancel.set()
                self._discard_tts_queue()

    def _session_active(self, session: str | None) -> bool:
        return session is None or (self.running and session == self._session and not self._cancel.is_set())

    def _pipeline(self, direction: str) -> PipelineContext:
        if direction not in self.pipelines:
            config = {key: value for key, value in self.config.items() if key not in STATE_KEYS}
            self.pipelines[direction] = PipelineContext(config, Translator(config), TextToSpeech(config))
        return self.pipelines[direction]

    def _publish(self, session: str | None, callback: Callable, *args) -> None:
        with self._callback_lock:
            if self._session_active(session):
                callback(*args)

    def _process_segments(self, direction: str, worker: SegmentWorker, session: str | None = None) -> None:
        assert self.transcriber is not None
        pipeline = self._pipeline(direction)
        config = pipeline.config
        translator = pipeline.translator
        metrics = pipeline.metrics
        source = "auto" if direction == "speaker" else config["source_language"]
        fallback_source = safe_target_language(config["target_language"], DEFAULT_CONFIG["target_language"]) if direction == "speaker" else source
        target = safe_target_language(
            config["source_language"] if direction == "speaker" else config["target_language"],
            DEFAULT_CONFIG["source_language"] if direction == "speaker" else DEFAULT_CONFIG["target_language"],
        )
        while self.running and self._session_active(session) and not getattr(worker, "_stopped", False):
            if self.paused:
                worker.discard_pending() if hasattr(worker, "discard_pending") else drain_queue(worker.queue)
                time.sleep(0.1)
                continue
            try:
                wav = worker.queue.get(timeout=0.5)
            except Exception:
                continue
            try:
                if not config.get("speaker_enabled" if direction == "speaker" else "microphone_enabled", True):
                    continue
                dequeued_perf = time.perf_counter()
                timing = worker.take_timing(wav) if hasattr(worker, "take_timing") else {}
                timing["dequeued_at"] = time.time()
                previous_dropped = metrics.get("last_dropped_segments", 0)
                queue_depth = worker.queue.qsize()
                dropped_segments = getattr(worker, "dropped_segments", 0)
                if "_capture_completed_perf" in timing:
                    processing_lag = max(0.0, dequeued_perf - timing["_capture_completed_perf"])
                else:
                    try:
                        processing_lag = max(0.0, time.time() - wav.stat().st_mtime)
                    except (AttributeError, OSError):
                        processing_lag = 0.0
                backlog_metrics = {
                    "last_queue_depth": queue_depth,
                    "last_max_queue_depth": max(int(metrics.get("last_max_queue_depth", 0)), int(getattr(worker, "max_queue_depth", queue_depth))),
                    "last_dropped_segments": dropped_segments,
                    "last_processing_lag_seconds": processing_lag,
                    "last_capture_seconds": max(0.0, timing.get("_capture_completed_perf", dequeued_perf) - timing.get("_capture_started_perf", dequeued_perf)),
                    "last_queue_wait_seconds": max(0.0, dequeued_perf - timing.get("_enqueued_perf", dequeued_perf - processing_lag)),
                }
                self._record_metrics(direction, backlog_metrics, set(backlog_metrics))
                if dropped_segments > previous_dropped or queue_depth >= max(1, worker.queue.maxsize - 1):
                    self._publish(session, self.status, f"{direction_label(direction)}：處理落後，已略過 {dropped_segments} 段；佇列 {queue_depth}/{worker.queue.maxsize}")
                started = time.perf_counter()
                cpu_started = time.thread_time()
                vad_latency = timing.get("_vad_seconds", 0.0)
                asr_started = time.perf_counter()
                timing["asr_started_at"] = time.time()
                transcription = self.transcriber.transcribe(wav, source)
                timing["asr_completed_at"] = time.time()
                if not self._session_active(session):
                    return
                raw_text = transcription.text
                text = trim_transcript_overlap(metrics.get("_last_asr_text", ""), raw_text) if timing.get("overlap_seconds", 0) else raw_text
                metrics["_last_asr_text"] = raw_text
                asr_latency = time.perf_counter() - asr_started
                if not text:
                    continue
                try:
                    segment_seconds = max(wav.duration_seconds if isinstance(wav, AudioSegment) else float(config.get("segment_seconds", 2.0)), 0.1)
                except Exception:
                    segment_seconds = 2.0
                clean_text = text.strip()
                speech_units = len(clean_text.split()) if " " in clean_text else len(clean_text)
                metrics["last_speech_units_per_second"] = speech_units / segment_seconds
                state_keys = {"last_speech_units_per_second"}
                detected_source = transcription.language if source == "auto" else None
                source_for_output = detected_source or fallback_source
                language_confidence = transcription.language_probability
                if detected_source:
                    metrics["last_detected_language"] = detected_source
                    state_keys.add("last_detected_language")
                if language_confidence is not None:
                    metrics["last_language_confidence"] = language_confidence
                    state_keys.add("last_language_confidence")
                asr_confidence = transcription.confidence
                if asr_confidence is not None:
                    metrics["last_asr_confidence"] = asr_confidence
                    state_keys.add("last_asr_confidence")
                translation_confidence = None
                translation_latency = None
                tts_latency = None
                translation_failed = False
                try:
                    translation_started = time.perf_counter()
                    timing["translation_started_at"] = time.time()
                    translation = translator.translate(text, source_for_output, target)
                    timing["translation_completed_at"] = time.time()
                    if not self._session_active(session):
                        return
                    translated = translation.text
                    translation_latency = time.perf_counter() - translation_started
                    translation_confidence = translation.confidence
                    if translation_confidence is not None:
                        metrics["last_translation_confidence"] = translation_confidence
                        state_keys.add("last_translation_confidence")
                except Exception as exc:
                    timing["translation_completed_at"] = time.time()
                    translation_latency = time.perf_counter() - translation_started
                    if not self._session_active(session):
                        return
                    self._record_provider_error(direction, exc)
                    translated = ""
                    translation_failed = True
                    self._publish(session, self.status, f"{direction_label(direction)}：翻譯失敗：{exc}")
                metrics["last_translation_empty"] = not translation_failed and not bool(str(translated).strip())
                state_keys.add("last_translation_empty")
                if not translation_failed:
                    metrics["last_source_text"] = text
                    metrics["last_translated_text"] = translated
                    state_keys.update({"last_source_text", "last_translated_text"})
                if translation_failed:
                    overlay_text = f"{source_for_output}: {text}" if config.get("show_language_labels") else text
                else:
                    overlay_text = overlay_text_from_config(text, translated, source_for_output, target, config)
                if direction == "speaker":
                    if not self._session_active(session):
                        return
                    self._publish(session, self.overlay, overlay_text, "")
                else:
                    if not self._session_active(session):
                        return
                    self._publish(session, self.overlay, "", overlay_text)
                timing["subtitle_published_at"] = time.time()
                subtitle_latency = max(0.0, time.perf_counter() - timing.get("_capture_started_perf", started))
                with self._metrics_lock:
                    self._subtitle_latencies.add(subtitle_latency)
                    subtitle_percentiles = self._subtitle_latencies.snapshot((FIRST_SUBTITLE_P50, FIRST_SUBTITLE_P95, FIRST_SUBTITLE_P99))
                subtitle_metrics = {"last_first_subtitle_latency_seconds": subtitle_latency, **subtitle_percentiles}
                metrics.update(subtitle_metrics)
                state_keys.update(subtitle_metrics)
                if direction == "speaker" and config.get("tts_enabled", True) and config.get("speaker_tts_enabled", False) and not self.muted and translated and not translation_failed:
                    self._enqueue_tts(direction, translated, target, config.get("speaker_tts_output_device", ""), session, timing)
                elif direction != "speaker" and config.get("tts_enabled", True) and config.get("virtual_mic_enabled", False) and not self.muted and translated and not translation_failed:
                    self._enqueue_tts(direction, translated, target, config.get("tts_output_device", ""), session, timing)
                if not self._session_active(session):
                    return
                latency = time.perf_counter() - started
                end_to_end = max(latency, time.perf_counter() - timing.get("_capture_started_perf", started))
                with self._metrics_lock:
                    self._latencies.add(end_to_end)
                    latency_percentiles = self._latencies.snapshot()
                stage_metrics = {
                    "last_vad_seconds": vad_latency,
                    "last_asr_latency_seconds": asr_latency,
                    "last_translation_latency_seconds": translation_latency or 0.0,
                    "last_end_to_end_latency_seconds": end_to_end,
                    "last_real_time_factor": latency / segment_seconds,
                    "last_cpu_percent": 100.0 * (time.thread_time() - cpu_started) / max(latency, 0.001),
                    **latency_percentiles,
                }
                metrics.update(stage_metrics)
                state_keys.update(stage_metrics)
                metrics["last_latency_seconds"] = latency
                state_keys.add("last_latency_seconds")
                if not self._session_active(session):
                    return
                self._record_metrics(direction, metrics, state_keys)
                if not self._session_active(session):
                    return
                if self.log and not translation_failed:
                    with self._log_lock:
                        public_timing = {key: value for key, value in timing.items() if not key.startswith("_")}
                        performance_keys = set(backlog_metrics) | set(subtitle_metrics) | set(stage_metrics) | {"last_provider_error_count", "last_rate_limit_count"}
                        performance = {key: metrics[key] for key in performance_keys if key in metrics}
                        performance.update({key: self.config[key] for key in ("last_cuda_devices", "last_vram_gb", "last_model_load_seconds") if self.config.get(key) not in (None, "")})
                        self.log.append(direction, source_for_output, target, text, translated, config["provider"], latency_seconds=latency, performance=performance, timestamps=public_timing)
                    if not self._session_active(session):
                        return
                if not translation_failed:
                    snapshot = build_confidence_snapshot(
                        {**config, **metrics},
                        source_for_output,
                        target,
                        asr_latency_seconds=asr_latency,
                        translation_latency_seconds=translation_latency,
                        tts_latency_seconds=tts_latency,
                        language_confidence=language_confidence,
                        asr_confidence=asr_confidence,
                        translation_confidence=translation_confidence,
                    )
                    self._publish(session, self.status, f"{direction_label(direction)}延遲 {latency:.2f} 秒；{format_confidence_status(snapshot, bool(config.get('advanced_mode')))}")
            except Exception as exc:
                self._publish(session, self.status, f"{direction_label(direction)}：{exc}")
            finally:
                discard_audio_segment(wav)

    def _enqueue_tts(self, direction: str, translated: str, target: str, tts_device: str, session: str | None, timing: dict) -> None:
        if session is None:
            self._speak_translation(direction, translated, target, tts_device, session, timing, self._tts_cancel)
            return
        task = TtsTask(direction, translated, target, tts_device, session, dict(timing), self._tts_cancel)
        try:
            key = f"device:{find_device(tts_device, want_output=True)}"
        except (DeviceResolutionError, OSError):
            key = f"unresolved:{tts_device}"
        with self._lifecycle_lock:
            if not self._session_active(session):
                return
            with self._tts_queue_lock:
                tasks = self._tts_queues.get(key)
                if tasks is None:
                    tasks = queue.Queue(maxsize=1)
                    self._tts_queues[key] = tasks
                    thread = threading.Thread(target=self._run_tts_queue, args=(tasks, session), daemon=True)
                    self.threads.append(thread)
                    thread.start()
                if tasks.full():
                    try:
                        tasks.get_nowait()
                        tasks.task_done()
                    except queue.Empty:
                        pass
                tasks.put_nowait(task)
                depth = sum(items.qsize() for items in self._tts_queues.values())
        self._record_metrics(direction, {"last_tts_queue_depth": depth}, {"last_tts_queue_depth"})

    def _run_tts_queue(self, tasks: queue.Queue, session: str) -> None:
        while self._session_active(session):
            try:
                task = tasks.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                depth = self._tts_queue_depth()
                self._record_metrics(task.direction, {"last_tts_queue_depth": depth}, {"last_tts_queue_depth"})
                if not task.cancel.is_set() and not self.muted and self._session_active(task.session):
                    self._speak_translation(task.direction, task.translated, task.target, task.device, task.session, task.timing, task.cancel)
            finally:
                tasks.task_done()

    def _tts_queue_depth(self) -> int:
        with self._tts_queue_lock:
            return sum(items.qsize() for items in self._tts_queues.values())

    def _discard_tts_queue(self) -> None:
        with self._tts_queue_lock:
            for tasks in self._tts_queues.values():
                while True:
                    try:
                        tasks.get_nowait()
                        tasks.task_done()
                    except queue.Empty:
                        break
        with self._metrics_lock:
            for pipeline in self.pipelines.values():
                pipeline.metrics["last_tts_queue_depth"] = 0
            self.config["last_tts_queue_depth"] = 0
            self._save_state({"last_tts_queue_depth"})

    def _speak_translation(self, direction: str, translated: str, target: str, tts_device: str, session: str | None, timing: dict, cancel_event: threading.Event) -> float | None:
        tts_started = time.perf_counter()
        synthesis_seconds = 0.0
        playback_seconds = 0.0
        pipeline = self._pipeline(direction)
        config = pipeline.config
        tts = pipeline.tts
        try:
            if config.get("tts_provider") == "local":
                local_started = time.perf_counter()
                local_started_at = time.time()
                if session is None:
                    local_timing = tts.speak_local(translated, tts_device)
                else:
                    local_timing = tts.speak_local(translated, tts_device, cancel_event)
                if isinstance(local_timing, dict):
                    timing.update({key: value for key, value in local_timing.items() if key.endswith("_at")})
                    synthesis_seconds = float(local_timing.get("last_tts_synthesis_seconds", 0.0))
                    playback_seconds = float(local_timing.get("last_tts_playback_seconds", 0.0))
                else:
                    timing.update({"tts_synthesis_started_at": local_started_at, "tts_synthesis_completed_at": local_started_at, "tts_playback_started_at": local_started_at, "tts_playback_completed_at": time.time()})
                    playback_seconds = time.perf_counter() - local_started
            elif config.get("tts_provider") == "openai":
                synthesis_started = time.perf_counter()
                timing["tts_synthesis_started_at"] = time.time()
                audio = self._cancellable_tts_synthesis(lambda: tts.synthesize_openai_linear16(translated), cancel_event)
                if audio is None:
                    return None
                synthesis_seconds = time.perf_counter() - synthesis_started
                timing["tts_synthesis_completed_at"] = time.time()
                if not self._session_active(session):
                    return None
                playback_started = time.perf_counter()
                timing["tts_playback_started_at"] = time.time()
                play_linear16(audio, tts_device) if session is None else play_linear16(audio, tts_device, cancel_event=cancel_event)
                playback_seconds = time.perf_counter() - playback_started
                timing["tts_playback_completed_at"] = time.time()
            else:
                synthesis_started = time.perf_counter()
                timing["tts_synthesis_started_at"] = time.time()
                audio = self._cancellable_tts_synthesis(lambda: tts.synthesize_google_linear16(translated, target), cancel_event)
                if audio is None:
                    return None
                synthesis_seconds = time.perf_counter() - synthesis_started
                timing["tts_synthesis_completed_at"] = time.time()
                if not self._session_active(session):
                    return None
                playback_started = time.perf_counter()
                timing["tts_playback_started_at"] = time.time()
                play_linear16(audio, tts_device) if session is None else play_linear16(audio, tts_device, cancel_event=cancel_event)
                playback_seconds = time.perf_counter() - playback_started
                timing["tts_playback_completed_at"] = time.time()
            if not self._session_active(session):
                return None
            tts_failed = False
        except Exception as exc:
            if not self._session_active(session):
                return None
            now = time.time()
            if "tts_synthesis_started_at" in timing:
                timing.setdefault("tts_synthesis_completed_at", now)
            if "tts_playback_started_at" in timing:
                timing.setdefault("tts_playback_completed_at", now)
            tts_failed = True
            self._record_provider_error(direction, exc)
            self._publish(session, self.status, f"{direction_label(direction)}：TTS 失敗：{exc}")
        tts_metrics = {
            "last_tts_failed": tts_failed,
            "last_tts_latency_seconds": time.perf_counter() - tts_started,
            "last_tts_synthesis_seconds": synthesis_seconds,
            "last_tts_playback_seconds": playback_seconds,
            "last_tts_queue_depth": self._tts_queue_depth(),
        }
        self._record_metrics(direction, tts_metrics, set(tts_metrics))
        return time.perf_counter() - tts_started

    @staticmethod
    def _cancellable_tts_synthesis(synthesize: Callable[[], bytes], cancel_event: threading.Event) -> bytes | None:
        result = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                result.put((True, synthesize()))
            except Exception as exc:
                result.put((False, exc))

        # ponytail: requests cannot interrupt in-flight I/O; the daemon exits at the provider timeout.
        threading.Thread(target=run, daemon=True).start()
        while not cancel_event.wait(0.05):
            try:
                succeeded, value = result.get_nowait()
            except queue.Empty:
                continue
            if succeeded:
                return value
            raise value
        return None

    def _record_provider_error(self, direction: str, error: Exception) -> None:
        status_code = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
        with self._metrics_lock:
            provider_errors = int(self.config.get("last_provider_error_count", 0)) + 1
            rate_limits = int(self.config.get("last_rate_limit_count", 0)) + (status_code == 429)
            values = {"last_provider_error_count": provider_errors, "last_rate_limit_count": rate_limits}
            self._pipeline(direction).metrics.update(values)
            self.config.update(values)
            self._save_state(set(values))

    def _record_metrics(self, direction: str, values: dict, keys: set[str]) -> None:
        with self._metrics_lock:
            self._pipeline(direction).metrics.update(values)
            updates = {key: values[key] for key in keys}
            if "last_max_queue_depth" in keys:
                updates["last_max_queue_depth"] = max(int(pipeline.metrics.get("last_max_queue_depth", 0)) for pipeline in self.pipelines.values())
            if "last_dropped_segments" in keys:
                updates["last_dropped_segments"] = sum(int(pipeline.metrics.get("last_dropped_segments", 0)) for pipeline in self.pipelines.values())
            self.config.update(updates)
            self._save_state(keys)

    def _save_state(self, keys: set[str]) -> None:
        if self.state_root is not None:
            save_config_state(self.state_root, self.config, keys)
