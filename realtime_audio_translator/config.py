import json
import os
import shutil
from pathlib import Path


APP_DIR = Path(os.environ.get("REALTIME_AUDIO_HOME", Path.home() / ".realtime-audio"))

DEFAULT_CONFIG = {
    "source_language": "zh",
    "target_language": "en",
    "provider": "local",
    "tts_provider": "local",
    "scenario": "discord_chat",
    "performance_mode": "balanced",
    "model": "large-v3-turbo",
    "compute_type": "auto",
    "device": "cuda",
    "speaker_device": "",
    "microphone_device": "",
    "speaker_enabled": True,
    "microphone_enabled": True,
    "tts_output_device": "CABLE Input",
    "tts_rate": 0,
    "tts_volume": 100,
    "tts_voice_name": "",
    "google_tts_voice": "",
    "overlay_visible": True,
    "overlay_topmost": True,
    "overlay_opacity": 0.86,
    "overlay_font_size": 18,
    "overlay_hold_seconds": 8.0,
    "show_language_labels": True,
    "show_original_text": True,
    "show_translated_text": True,
    "tts_enabled": True,
    "record_logs": False,
    "log_dir": str(APP_DIR / "logs"),
    "advanced_mode": False,
    "ai_auto_optimize": True,
    "ai_self_diagnosis": True,
    "google_project_id": "",
    "google_service_account_json": "",
    "glossary_path": str(APP_DIR / "glossary.json"),
    "translation_cache_path": str(APP_DIR / "cache" / "translation_cache.db"),
    "translation_cache_enabled": True,
    "local_translate_url": "",
    "openai_model": "gpt-4.1-mini",
    "openai_tts_model": "gpt-4o-mini-tts",
    "openai_tts_voice": "alloy",
    "segment_seconds": 2.0,
    "speech_threshold": 0.01,
    "runtime_dir": str(APP_DIR / "runtime" / "cuda12"),
}


def ensure_app_dirs(root: Path = APP_DIR) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative in ("config", "models", "logs", "cache/audio", "cache/temp_audio", "runtime/cuda12", "exports/subtitles"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    ensure_glossary_file(root / "glossary.json")
    devices = root / "config" / "audio_devices.json"
    if not devices.exists():
        devices.write_text("[]\n", encoding="utf-8")
    commands = root / "commands.json"
    if not commands.exists():
        commands.write_text("{}\n", encoding="utf-8")
    app_log = root / "logs" / "app.log"
    if not app_log.exists():
        app_log.write_text("", encoding="utf-8")


def ensure_glossary_file(glossary: Path) -> Path:
    glossary.parent.mkdir(parents=True, exist_ok=True)
    if not glossary.exists():
        glossary.write_text("{}\n", encoding="utf-8")
    return glossary


def load_config(root: Path = APP_DIR) -> dict:
    ensure_app_dirs(root)
    path = root / "config.json"
    if not path.exists():
        save_config(root, DEFAULT_CONFIG.copy())
        return DEFAULT_CONFIG.copy()
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    config = DEFAULT_CONFIG.copy()
    config.update(loaded)
    return config


def save_config(root: Path, config: dict) -> None:
    ensure_app_dirs(root)
    with (root / "config.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def save_audio_devices(root: Path, devices: list[dict]) -> Path:
    ensure_app_dirs(root)
    path = root / "config" / "audio_devices.json"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(devices, handle, ensure_ascii=False, indent=2)
    return path


def clear_logs(root: Path = APP_DIR, log_dir: Path | None = None) -> None:
    shutil.rmtree(log_dir or root / "logs", ignore_errors=True)
    ensure_app_dirs(root)


def clear_cache(root: Path = APP_DIR) -> None:
    shutil.rmtree(root / "cache" / "audio", ignore_errors=True)
    shutil.rmtree(root / "cache" / "temp_audio", ignore_errors=True)
    ensure_app_dirs(root)
