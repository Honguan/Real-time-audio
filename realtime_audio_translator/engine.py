import threading
import time
from pathlib import Path
from typing import Callable

from .asr import AudioTranscriber
from .audio import SegmentWorker, find_device
from .config import APP_DIR
from .logbook import ConversationLog
from .providers import TextToSpeech, Translator
from .tts import play_linear16


OverlayCallback = Callable[[str, str], None]
StatusCallback = Callable[[str], None]


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
        self.log = ConversationLog(APP_DIR / "logs") if config.get("record_logs") else None
        self.translator = Translator(config)
        self.tts = TextToSpeech(config)
        self.transcriber: AudioTranscriber | None = None

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.transcriber = AudioTranscriber(
            self.repo_root,
            self.config["model"],
            APP_DIR / "models",
            self.config["device"],
            self.config["compute_type"],
            self.config,
        )
        self._start_direction("speaker", self.config.get("speaker_device", ""), True)
        self._start_direction("me", self.config.get("microphone_device", ""), False)
        self.status("running")

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

    def _start_direction(self, direction: str, device_hint: str, loopback: bool) -> None:
        device = find_device(device_hint, want_output=loopback) if device_hint else None
        if device is None:
            device = find_device("CABLE Output" if not loopback else "Speakers", want_output=loopback)
        if device is None:
            self.status(f"{direction}: no device")
            return
        worker = SegmentWorker(APP_DIR / "cache" / "audio", device, float(self.config["segment_seconds"]), loopback)
        self.workers.append(worker)
        capture_thread = threading.Thread(target=worker.run, args=(direction,), daemon=True)
        process_thread = threading.Thread(target=self._process_segments, args=(direction, worker), daemon=True)
        self.threads.extend([capture_thread, process_thread])
        capture_thread.start()
        process_thread.start()

    def _process_segments(self, direction: str, worker: SegmentWorker) -> None:
        assert self.transcriber is not None
        source = self.config["target_language"] if direction == "speaker" else self.config["source_language"]
        target = self.config["source_language"] if direction == "speaker" else self.config["target_language"]
        while self.running:
            if self.paused:
                time.sleep(0.1)
                continue
            try:
                wav = worker.queue.get(timeout=0.5)
            except Exception:
                continue
            try:
                started = time.perf_counter()
                text = self.transcriber.transcribe(wav, source)
                if not text:
                    continue
                translated = self.translator.translate(text, source, target)
                if direction == "speaker":
                    self.overlay(translated, "")
                else:
                    self.overlay("", translated)
                    if not self.muted and translated:
                        audio = self.tts.synthesize_google_linear16(translated, target)
                        play_linear16(audio, self.config.get("tts_output_device", "CABLE Input"))
                latency = time.perf_counter() - started
                if self.log:
                    self.log.append(direction, source, target, text, translated, self.config["provider"], latency_seconds=latency)
                self.status(f"{direction} latency {latency:.2f}s")
            except Exception as exc:
                self.status(f"{direction}: {exc}")
