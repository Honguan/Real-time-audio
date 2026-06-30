import json
import os
import shutil
from pathlib import Path


APP_DIR = Path(os.environ.get("REALTIME_AUDIO_HOME", Path.home() / ".realtime-audio"))

DEFAULT_CONFIG = {
    "source_language": "zh",
    "target_language": "en",
    "provider": "google",
    "tts_provider": "google",
    "model": "large-v3-turbo",
    "compute_type": "auto",
    "device": "cuda",
    "speaker_device": "",
    "microphone_device": "",
    "tts_output_device": "CABLE Input",
    "overlay_topmost": True,
    "show_language_labels": True,
    "record_logs": False,
    "google_project_id": "",
    "google_service_account_json": "",
    "openai_model": "gpt-4.1-mini",
    "openai_tts_model": "gpt-4o-mini-tts",
    "openai_tts_voice": "alloy",
    "segment_seconds": 2.0,
    "runtime_dir": str(APP_DIR / "runtime"),
}


def ensure_app_dirs(root: Path = APP_DIR) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative in ("models", "logs", "cache/audio"):
        (root / relative).mkdir(parents=True, exist_ok=True)


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


def clear_logs(root: Path = APP_DIR) -> None:
    shutil.rmtree(root / "logs", ignore_errors=True)
    ensure_app_dirs(root)


def clear_cache(root: Path = APP_DIR) -> None:
    shutil.rmtree(root / "cache" / "audio", ignore_errors=True)
    ensure_app_dirs(root)
