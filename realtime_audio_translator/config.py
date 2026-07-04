import json
import os
import shutil
from pathlib import Path

from .ai_memory import _ensure_cache


APP_DIR = Path(os.environ.get("REALTIME_AUDIO_HOME", Path.home() / ".realtime-audio"))

DEFAULT_CONFIG = {
    "app_language": "zh-TW",
    "ui_mode": "simple",
    "asr_engine": "faster-whisper-xxl",
    "asr_model": "small",
    "translation_engine": "local",
    "tts_engine": "system",
    "source_language": "zh",
    "target_language": "en",
    "provider": "local",
    "tts_provider": "local",
    "scenario": "discord_chat",
    "performance_mode": "balanced",
    "model": "small",
    "compute_type": "auto",
    "device": "cuda",
    "speaker_device": "",
    "microphone_device": "",
    "speaker_enabled": True,
    "microphone_enabled": True,
    "tts_output_device": "CABLE Input",
    "speaker_tts_output_device": "",
    "tts_rate": 0,
    "tts_volume": 100,
    "tts_voice_name": "",
    "google_tts_voice": "",
    "overlay_visible": True,
    "overlay_topmost": True,
    "overlay_opacity": 0.86,
    "overlay_font_size": 18,
    "overlay_hold_seconds": 8.0,
    "subtitle_always_on_top": True,
    "show_language_labels": True,
    "show_original_text": True,
    "show_translated_text": True,
    "tts_enabled": True,
    "speaker_tts_enabled": False,
    "record_logs": False,
    "save_conversation_history": False,
    "cloud_api_enabled": False,
    "virtual_mic_enabled": False,
    "log_dir": str(APP_DIR / "logs"),
    "advanced_mode": False,
    "ai_auto_optimize": True,
    "ai_self_diagnosis": True,
    "last_ffmpeg_failed": False,
    "last_cuda_devices": "",
    "last_vram_gb": "",
    "last_detected_language": "",
    "last_language_confidence": "",
    "last_source_text": "",
    "last_translated_text": "",
    "google_project_id": "",
    "google_service_account_json": "",
    "glossary_path": str(APP_DIR / "config" / "glossary.json"),
    "translation_cache_path": str(APP_DIR / "cache" / "translation_cache.db"),
    "translation_cache_enabled": True,
    "local_translate_url": "",
    "openai_model": "gpt-4.1-mini",
    "openai_tts_model": "gpt-4o-mini-tts",
    "openai_tts_voice": "alloy",
    "segment_seconds": 2.0,
    "speech_threshold": 0.01,
    "runtime_dir": str(APP_DIR / "runtime" / "cuda12"),
    "runtime_path": str(APP_DIR / "runtime" / "cuda12"),
    "models_path": str(APP_DIR / "models"),
}


def ensure_app_dirs(root: Path = APP_DIR) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative in (
        "config",
        "models",
        "models/whisper-small",
        "models/translation",
        "models/tts",
        "logs",
        "cache/audio",
        "cache/temp_audio",
        "runtime/cuda12",
        "exports/subtitles",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    legacy_glossary = root / "glossary.json"
    glossary = root / "config" / "glossary.json"
    if legacy_glossary.exists() and not glossary.exists():
        glossary.write_text(legacy_glossary.read_text(encoding="utf-8"), encoding="utf-8")
    ensure_glossary_file(glossary)
    devices = root / "config" / "audio_devices.json"
    if not devices.exists():
        devices.write_text("[]\n", encoding="utf-8")
    commands = root / "commands.json"
    if not commands.exists():
        commands.write_text("{}\n", encoding="utf-8")
    app_log = root / "logs" / "app.log"
    if not app_log.exists():
        app_log.write_text("", encoding="utf-8")
    _ensure_cache(root / "cache" / "translation_cache.db")


def ensure_glossary_file(glossary: Path) -> Path:
    glossary.parent.mkdir(parents=True, exist_ok=True)
    if not glossary.exists():
        glossary.write_text("{}\n", encoding="utf-8")
    return glossary


def load_config(root: Path = APP_DIR) -> dict:
    ensure_app_dirs(root)
    path = root / "config.json"
    settings_path = root / "config" / "settings.json"
    if not path.exists():
        if not settings_path.exists():
            save_config(root, DEFAULT_CONFIG.copy())
            return DEFAULT_CONFIG.copy()
        path = settings_path
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    config = DEFAULT_CONFIG.copy()
    config.update(loaded)
    if "model" not in loaded and "asr_model" in loaded:
        config["model"] = loaded["asr_model"]
    if "provider" not in loaded and loaded.get("translation_engine") in ("local", "google", "openai"):
        config["provider"] = loaded["translation_engine"]
    if "tts_provider" not in loaded and loaded.get("tts_engine") in ("system", "local", "google", "openai"):
        config["tts_provider"] = "local" if loaded["tts_engine"] == "system" else loaded["tts_engine"]
    if not config.get("cloud_api_enabled", False):
        if config.get("provider") in ("google", "openai"):
            config["provider"] = "local"
        if config.get("tts_provider") in ("google", "openai"):
            config["tts_provider"] = "local"
    if "advanced_mode" not in loaded and loaded.get("ui_mode") in ("advanced", "simple"):
        config["advanced_mode"] = loaded["ui_mode"] == "advanced"
    if "record_logs" not in loaded and "save_conversation_history" in loaded:
        config["record_logs"] = bool(loaded["save_conversation_history"])
    if "overlay_topmost" not in loaded and "subtitle_always_on_top" in loaded:
        config["overlay_topmost"] = bool(loaded["subtitle_always_on_top"])
    return config


def save_config(root: Path, config: dict) -> None:
    ensure_app_dirs(root)
    config = config.copy()
    config["ui_mode"] = "advanced" if config.get("advanced_mode") else "simple"
    config["asr_model"] = config.get("model", config.get("asr_model", "small"))
    config["translation_engine"] = config.get("provider", config.get("translation_engine", "local"))
    config["tts_engine"] = "system" if config.get("tts_provider", "local") == "local" else config.get("tts_provider")
    config["runtime_path"] = config.get("runtime_dir", config.get("runtime_path", str(APP_DIR / "runtime" / "cuda12")))
    config["save_conversation_history"] = bool(config.get("record_logs", False))
    config["subtitle_always_on_top"] = bool(config.get("overlay_topmost", True))
    with (root / "config.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
    with (root / "config" / "settings.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def save_audio_devices(root: Path, devices: list[dict]) -> Path:
    ensure_app_dirs(root)
    path = root / "config" / "audio_devices.json"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(devices, handle, ensure_ascii=False, indent=2)
    return path


def clear_logs(root: Path = APP_DIR, log_dir: Path | None = None) -> None:
    target = log_dir or root / "logs"
    shutil.rmtree(target, ignore_errors=True)
    if log_dir and target != root / "logs":
        shutil.rmtree(root / "logs", ignore_errors=True)
    ensure_app_dirs(root)
    if log_dir:
        target.mkdir(parents=True, exist_ok=True)
        (target / "app.log").write_text("", encoding="utf-8")


def clear_cache(root: Path = APP_DIR) -> None:
    shutil.rmtree(root / "cache" / "audio", ignore_errors=True)
    shutil.rmtree(root / "cache" / "temp_audio", ignore_errors=True)
    (root / "cache" / "translation_cache.db").unlink(missing_ok=True)
    ensure_app_dirs(root)
