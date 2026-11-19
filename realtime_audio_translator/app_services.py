import math
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .audio import DeviceResolutionError, audio_frame_stream, audio_segment_active, capture_audio, capture_wav, find_device
from .commands import refresh_commands
from .diagnostics import DiagnosticIssue, collect_diagnostics
from .localization import translate
from .models import download_model, model_status
from .offline_translation import download_translation_models
from .providers import TextToSpeech, Translator, google_access_token
from .release_updater import current_version, latest_release_tag, release_update_message
from .runtime import install_runtime_from, runtime_status, whisper_exe
from .tts import play_linear16


@dataclass(frozen=True)
class RuntimeImportResult:
    target: Path
    status: dict


@dataclass(frozen=True)
class RuntimeCheckResult:
    status: dict
    ffmpeg_failed: bool


class RuntimeService:
    def __init__(self, commands_path: Path):
        self.commands_path = commands_path

    def import_runtime(self, source: Path, target: Path, device: str, compute_type: str) -> RuntimeImportResult:
        installed = install_runtime_from(source, target)
        status = runtime_status(installed, device, compute_type)
        if status["ready"]:
            refresh_commands(whisper_exe(installed), self.commands_path)
        return RuntimeImportResult(installed, status)

    def check(self, target: Path, device: str, compute_type: str, *, verify_hashes: bool = False) -> RuntimeCheckResult:
        status = runtime_status(target, device, compute_type, verify_hashes=verify_hashes)
        failed = False
        if status["ready"]:
            try:
                result = subprocess.run([str(target / "ffmpeg.exe"), "-version"], capture_output=True, text=True, timeout=2, check=False)
                failed = result.returncode != 0
            except Exception:
                failed = True
        return RuntimeCheckResult(status, failed)

    def refresh_commands(self, target: Path, language: str = "zh-TW") -> None:
        status = runtime_status(target, "cpu", verify_hashes=True)
        if not status["ready"]:
            raise RuntimeError(translate(language, "找不到可用的 runtime：{missing}", missing=", ".join(status["missing"])))
        refresh_commands(whisper_exe(target), self.commands_path)


class ModelService:
    def status(self, model: str, local_models: Path, app_models: Path) -> str:
        return model_status(model, local_models, app_models)

    def download(self, model: str, target: Path, progress, cancel: threading.Event, language: str = "zh-TW") -> Path:
        return download_model(model, target, progress, cancel, language=language)

    def download_translation(self, config: dict, source: str, target: str, registry=None) -> list:
        return download_translation_models(config, source, target, registry)


class AudioDiagnosticsService:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def collect(self, config: dict, repo_root: Path) -> list[DiagnosticIssue]:
        return collect_diagnostics(config, repo_root)

    def test_api(self, config: dict) -> str:
        if config["provider"] == "google":
            google_access_token(config["google_service_account_json"])
            return translate(config.get("app_language"), "Google 驗證成功")
        return Translator({**config, "translation_cache_enabled": False}).translate("hello", "en", "zh").text[:80]

    def test_tone(self, config: dict) -> None:
        import numpy as np

        samplerate = 24000
        data = np.array([math.sin(2 * math.pi * 440 * i / samplerate) * 0.2 * 32767 for i in range(samplerate // 4)], dtype="<i2")
        play_linear16(data.tobytes(), config["tts_output_device"], samplerate=samplerate)

    def test_tts(self, config: dict) -> None:
        provider = config.get("tts_provider", "local")
        device = config["tts_output_device"]
        tts = TextToSpeech(config)
        if provider == "local":
            tts.speak_local("翻譯語音輸出測試", device)
        elif provider == "openai":
            play_linear16(tts.synthesize_openai_linear16("翻譯語音輸出測試"), device)
        else:
            play_linear16(tts.synthesize_google_linear16("翻譯語音輸出測試", config["target_language"]), device)

    def test_virtual_microphone(self, config: dict) -> bool:
        if not config["virtual_mic_input_device"]:
            raise DeviceResolutionError("請先選擇虛擬麥克風檢查輸入")
        device = find_device(config["virtual_mic_input_device"], want_output=False)
        threshold = float(config["speech_threshold"])
        cancel = threading.Event()
        with closing(audio_frame_stream(device, False, cancel, 0.05)) as frames, ThreadPoolExecutor(max_workers=1) as executor:
            next(frames)  # Confirm capture has started before generating speech.

            def detect():
                active = False
                for frame in frames:
                    active = audio_segment_active(frame, threshold) or active
                return active

            capture = executor.submit(detect)
            try:
                self.test_tts(config)
                time.sleep(0.15)  # Allow the virtual cable's buffered audio to arrive.
            finally:
                cancel.set()
            return capture.result()

    def test_speaker(self, config: dict) -> bool:
        device = find_device(config["speaker_device"], want_output=True)
        path = self.cache_dir / "speaker-test.wav"
        capture_wav(path, device, 0.5, loopback=True)
        return audio_segment_active(path, float(config["speech_threshold"]))

    def test_microphone(self, config: dict) -> float:
        import numpy as np

        device = find_device(config["microphone_device"], want_output=False)
        segment = capture_audio(device, 0.5)
        data = np.frombuffer(segment.pcm, dtype="<i2").astype("float32") / 32768.0
        return float(np.sqrt(np.mean(np.square(data))))


class UpdateService:
    def check(self, app_root: Path, language: str = "zh-TW") -> str:
        return release_update_message(current_version(app_root), latest_release_tag(), language)
