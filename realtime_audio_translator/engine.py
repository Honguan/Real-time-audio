import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .asr import AudioTranscriber
from .audio import SegmentWorker, audio_segment_active, find_device
from .config import APP_DIR
from .logbook import ConversationLog
from .providers import TextToSpeech, Translator
from .tts import play_linear16


OverlayCallback = Callable[[str, str], None]
StatusCallback = Callable[[str], None]


def drain_queue(items) -> int:
    removed = 0
    while True:
        try:
            items.get_nowait()
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


class RealtimeEngine:
    def __init__(self, repo_root: Path, config: dict, overlay: OverlayCallback, status: StatusCallback):
        self.repo_root = repo_root
        self.config = config
        self.overlay = overlay
        self.status = status
        self.running = False
        self.paused = False
        self.muted = False
        self.threads: list[threading.Thread] = []
        self.workers: list[SegmentWorker] = []
        self.log = ConversationLog(Path(config.get("log_dir") or APP_DIR / "logs")) if config.get("record_logs") else None
        self.translator = Translator(config)
        self.tts = TextToSpeech(config)
        self.transcriber: AudioTranscriber | None = None

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        try:
            self.transcriber = AudioTranscriber(
                self.repo_root,
                self.config["model"],
                APP_DIR / "models",
                self.config["device"],
                self.config["compute_type"],
                self.config,
            )
        except Exception as exc:
            self.running = False
            self.status(str(exc))
            return
        started = []
        if self.config.get("speaker_enabled", True):
            started.append(self._start_direction("speaker", self.config.get("speaker_device", ""), True))
        if self.config.get("microphone_enabled", True):
            started.append(self._start_direction("me", self.config.get("microphone_device", ""), False))
        if any(started):
            self.status("running")
        else:
            self.running = False
            self.status("no audio devices")

    def stop(self) -> None:
        self.running = False
        for worker in self.workers:
            worker.stop()
        self.workers.clear()
        self.threads.clear()
        self.status("stopped")

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.status("paused" if paused else "running")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.status("muted" if muted else "running")

    def _start_direction(self, direction: str, device_hint: str, loopback: bool) -> bool:
        device = find_device(device_hint, want_output=loopback) if device_hint else None
        if device is None:
            device = find_device("Microphone" if not loopback else "Speakers", want_output=loopback)
        if device is None:
            self.status(f"{direction}: no device")
            return False
        worker = SegmentWorker(APP_DIR / "cache" / "audio", device, float(self.config["segment_seconds"]), loopback)
        self.workers.append(worker)
        capture_thread = threading.Thread(target=worker.run, args=(direction,), daemon=True)
        process_thread = threading.Thread(target=self._process_segments, args=(direction, worker), daemon=True)
        self.threads.extend([capture_thread, process_thread])
        capture_thread.start()
        process_thread.start()
        return True

    def _process_segments(self, direction: str, worker: SegmentWorker) -> None:
        assert self.transcriber is not None
        source = self.config["target_language"] if direction == "speaker" else self.config["source_language"]
        target = self.config["source_language"] if direction == "speaker" else self.config["target_language"]
        while self.running:
            if self.paused:
                drain_queue(worker.queue)
                time.sleep(0.1)
                continue
            try:
                wav = worker.queue.get(timeout=0.5)
            except Exception:
                continue
            if not self.config.get("speaker_enabled" if direction == "speaker" else "microphone_enabled", True):
                continue
            try:
                started = time.perf_counter()
                if not audio_segment_active(wav, self.config.get("speech_threshold", 0.01)):
                    continue
                text = self.transcriber.transcribe(wav, source)
                if not text:
                    continue
                detected_source = getattr(self.transcriber, "last_language", None) if source == "auto" else None
                source_for_output = detected_source or source
                translation_failed = False
                try:
                    translated = self.translator.translate(text, source_for_output, target)
                except Exception as exc:
                    translated = text
                    translation_failed = True
                    self.status(f"{direction}: translation failed: {exc}")
                if translation_failed:
                    overlay_text = f"{source_for_output}: {text}" if self.config.get("show_language_labels") else text
                else:
                    overlay_text = overlay_text_from_config(text, translated, source_for_output, target, self.config)
                if direction == "speaker":
                    self.overlay(overlay_text, "")
                else:
                    self.overlay("", overlay_text)
                    if self.config.get("tts_enabled", True) and not self.muted and translated and not translation_failed:
                        tts_device = self.config.get("tts_output_device", "CABLE Input")
                        if self.config.get("tts_provider") == "local":
                            self.tts.speak_local(translated, tts_device)
                        elif self.config.get("tts_provider") == "openai":
                            audio = self.tts.synthesize_openai_linear16(translated)
                            play_linear16(audio, tts_device)
                        else:
                            audio = self.tts.synthesize_google_linear16(translated, target)
                            play_linear16(audio, tts_device)
                latency = time.perf_counter() - started
                if self.log:
                    self.log.append(direction, source_for_output, target, text, translated, self.config["provider"], latency_seconds=latency)
                if not translation_failed:
                    self.status(f"{direction} latency {latency:.2f}s")
            except Exception as exc:
                self.status(f"{direction}: {exc}")
