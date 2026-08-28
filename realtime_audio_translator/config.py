import json
import os
import shutil
import stat
import tempfile
import threading
from pathlib import Path

from .ai_memory import _ensure_cache


APP_DIR = Path(os.environ.get("REALTIME_AUDIO_HOME", Path.home() / ".realtime-audio"))

DEFAULT_CONFIG = {
    "app_language": "zh-TW",
    "ui_mode": "simple",
    "asr_engine": "faster-whisper-xxl",
    "asr_model": "small",
    "translation_engine": "local",
    "translation_style": "plain",
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
    "start_muted": False,
    "record_logs": False,
    "save_conversation_history": False,
    "cloud_api_enabled": False,
    "virtual_mic_enabled": False,
    "log_dir": str(APP_DIR / "logs"),
    "advanced_mode": False,
    "ai_auto_optimize": True,
    "ai_self_diagnosis": True,
    "setup_guide_shown": False,
    "last_ffmpeg_failed": False,
    "last_asr_failed": False,
    "last_translation_empty": False,
    "last_tts_failed": False,
    "last_virtual_mic_failed": False,
    "last_mic_quiet": False,
    "last_speaker_quiet": False,
    "last_cuda_devices": "",
    "last_vram_gb": "",
    "last_detected_language": "",
    "last_language_confidence": "",
    "last_asr_confidence": "",
    "last_translation_confidence": "",
    "last_error": "",
    "last_tts_latency_seconds": "",
    "last_latency_seconds": "",
    "last_speech_units_per_second": "",
    "last_queue_depth": 0,
    "last_dropped_segments": 0,
    "last_processing_lag_seconds": "",
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

SOURCE_LANGUAGE_CHOICES = ("auto", "zh", "en", "ja", "ko")
TARGET_LANGUAGE_CHOICES = ("zh", "en", "ja", "ko")
CONFIG_SCHEMA_VERSION = 1
DIAGNOSTIC_STATE_KEYS = {
    "last_error",
    "last_ffmpeg_failed",
    "last_asr_failed",
    "last_translation_empty",
    "last_tts_failed",
    "last_virtual_mic_failed",
    "last_mic_quiet",
    "last_speaker_quiet",
}
SESSION_STATE_KEYS = {key for key in DEFAULT_CONFIG if key.startswith("last_")} - DIAGNOSTIC_STATE_KEYS
STATE_KEYS = DIAGNOSTIC_STATE_KEYS | SESSION_STATE_KEYS
SETTINGS_KEYS = set(DEFAULT_CONFIG) - STATE_KEYS
PATH_SETTING_KEYS = {"log_dir", "glossary_path", "translation_cache_path", "runtime_dir", "runtime_path", "models_path"}
SETTING_CHOICES = {
    "ui_mode": {"simple", "advanced"},
    "translation_engine": {"local", "google", "openai"},
    "translation_style": {"plain", "formal"},
    "tts_engine": {"system", "local", "google", "openai"},
    "source_language": set(SOURCE_LANGUAGE_CHOICES),
    "target_language": set(TARGET_LANGUAGE_CHOICES),
    "provider": {"local", "google", "openai"},
    "tts_provider": {"local", "google", "openai"},
    "scenario": {"game_voice", "discord_chat", "meeting", "customer_service", "subtitle_only", "speak_translate", "two_way"},
    "performance_mode": {"low_latency", "balanced", "quality", "offline_light"},
    "compute_type": {"auto", "float16", "int8", "int8_float16"},
    "device": {"cuda", "cpu", "auto"},
}
NUMERIC_STATE_KEYS = {
    "last_cuda_devices",
    "last_vram_gb",
    "last_language_confidence",
    "last_asr_confidence",
    "last_translation_confidence",
    "last_tts_latency_seconds",
    "last_latency_seconds",
    "last_speech_units_per_second",
    "last_queue_depth",
    "last_dropped_segments",
    "last_processing_lag_seconds",
}
SETTING_RANGES = {
    "overlay_opacity": (0.2, 1.0),
    "overlay_font_size": (10, 48),
    "overlay_hold_seconds": (1.0, 60.0),
    "tts_rate": (-10, 10),
    "tts_volume": (0, 100),
    "segment_seconds": (0.1, 30.0),
    "speech_threshold": (0.0, 1.0),
}
_CONFIG_LOCKS: dict[Path, threading.RLock] = {}
_CONFIG_LOCKS_GUARD = threading.Lock()


def validate_language_pair(config: dict) -> None:
    source = config.get("source_language")
    target = config.get("target_language")
    if source not in SOURCE_LANGUAGE_CHOICES or target not in TARGET_LANGUAGE_CHOICES:
        raise ValueError("不支援的來源或目標語言")
    if source != "auto" and source == target:
        raise ValueError("來源與目標語言不可相同")


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


def _config_lock(root: Path) -> threading.RLock:
    key = root.resolve()
    with _CONFIG_LOCKS_GUARD:
        return _CONFIG_LOCKS.setdefault(key, threading.RLock())


def _atomic_write_json(path: Path, document: dict, backup_current: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    backup_temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if backup_current and path.exists():
            with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.bak.", suffix=".tmp", delete=False) as backup_handle:
                backup_temporary_path = Path(backup_handle.name)
                with path.open("rb") as current_handle:
                    shutil.copyfileobj(current_handle, backup_handle)
                backup_handle.flush()
                os.fsync(backup_handle.fileno())
            os.replace(backup_temporary_path, path.with_suffix(path.suffix + ".bak"))
            backup_temporary_path = None
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if backup_temporary_path is not None:
            backup_temporary_path.unlink(missing_ok=True)


def _read_json_with_backup(path: Path, validator=lambda document: True) -> tuple[dict, bool]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict) or not validator(document):
            raise ValueError(f"設定檔必須是 JSON object：{path}")
        return document, False
    except (json.JSONDecodeError, OSError, ValueError) as error:
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            with backup.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
            if not isinstance(document, dict) or not validator(document):
                raise ValueError
        except (json.JSONDecodeError, OSError, ValueError):
            raise ValueError(f"設定檔損毀或 schema_version/格式無效，且無法從備份復原：{path}") from error
        _atomic_write_json(path, document, backup_current=False)
        return document, True


def _valid_setting_value(key: str, value):
    default = DEFAULT_CONFIG[key]
    if key in PATH_SETTING_KEYS and (not isinstance(value, str) or not value.strip() or "\0" in value or not Path(value).is_absolute()):
        return default
    if isinstance(default, bool):
        valid = isinstance(value, bool)
    elif isinstance(default, int):
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, float):
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        valid = isinstance(value, str)
    if not valid:
        return default
    if key in SETTING_CHOICES and value not in SETTING_CHOICES[key]:
        return default
    if key in SETTING_RANGES:
        minimum, maximum = SETTING_RANGES[key]
        if not minimum <= value <= maximum:
            return default
    return float(value) if isinstance(default, float) else value


def _valid_state_value(key: str, value):
    if isinstance(DEFAULT_CONFIG[key], bool):
        return value if isinstance(value, bool) else DEFAULT_CONFIG[key]
    if key in NUMERIC_STATE_KEYS:
        return value if value == "" or isinstance(value, (int, float)) and not isinstance(value, bool) else DEFAULT_CONFIG[key]
    return value if isinstance(value, str) else DEFAULT_CONFIG[key]


def _normalized_config(loaded: dict, repair_pair: bool = True) -> dict:
    config = DEFAULT_CONFIG.copy()
    for key, value in loaded.items():
        if key in DEFAULT_CONFIG:
            config[key] = _valid_setting_value(key, value) if key in SETTINGS_KEYS else _valid_state_value(key, value)
    if "runtime_dir" not in loaded and loaded.get("runtime_path"):
        config["runtime_dir"] = config["runtime_path"]
    if "model" not in loaded and loaded.get("asr_model"):
        config["model"] = config["asr_model"]
    if "provider" not in loaded and loaded.get("translation_engine") in ("local", "google", "openai"):
        config["provider"] = config["translation_engine"]
    if "tts_provider" not in loaded and loaded.get("tts_engine") in ("system", "local", "google", "openai"):
        config["tts_provider"] = "local" if loaded["tts_engine"] == "system" else loaded["tts_engine"]
    if "advanced_mode" not in loaded and loaded.get("ui_mode") in ("advanced", "simple"):
        config["advanced_mode"] = loaded["ui_mode"] == "advanced"
    if "record_logs" not in loaded and "save_conversation_history" in loaded:
        config["record_logs"] = bool(loaded["save_conversation_history"])
    if "overlay_topmost" not in loaded and "subtitle_always_on_top" in loaded:
        config["overlay_topmost"] = bool(loaded["subtitle_always_on_top"])
    if not config.get("cloud_api_enabled", False):
        if config.get("provider") in ("google", "openai"):
            config["provider"] = "local"
        if config.get("tts_provider") in ("google", "openai"):
            config["tts_provider"] = "local"
    if config.get("source_language") not in SOURCE_LANGUAGE_CHOICES:
        config["source_language"] = DEFAULT_CONFIG["source_language"]
    if config.get("target_language") not in TARGET_LANGUAGE_CHOICES:
        config["target_language"] = DEFAULT_CONFIG["target_language"]
    if repair_pair:
        try:
            validate_language_pair(config)
        except ValueError:
            config["source_language"] = DEFAULT_CONFIG["source_language"]
            config["target_language"] = DEFAULT_CONFIG["target_language"]
    else:
        validate_language_pair(config)
    return config


class ConfigStore:
    def __init__(self, root: Path = APP_DIR):
        self.root = root
        self.settings_path = root / "config" / "settings.json"
        self.state_path = root / "config" / "state.json"
        self.legacy_path = root / "config.json"
        self.lock = _config_lock(root)

    def load(self) -> dict:
        with self.lock:
            ensure_app_dirs(self.root)
            source = self.settings_path if self.settings_path.exists() else self.legacy_path
            if not source.exists():
                config = DEFAULT_CONFIG.copy()
                if self.state_path.exists():
                    state_document, _recovered = _read_json_with_backup(self.state_path, self._valid_state_document)
                    config.update(state_document["session_metrics"])
                    config.update(state_document["diagnostics"])
                self._save_all(config)
                return config
            try:
                document, recovered = _read_json_with_backup(source, self._valid_settings_document)
            except ValueError as settings_error:
                if source != self.settings_path or not self.legacy_path.exists():
                    raise
                try:
                    _read_json_with_backup(source)
                except ValueError:
                    pass
                else:
                    raise settings_error
                source = self.legacy_path
                document, recovered = _read_json_with_backup(source)
            version = document.get("schema_version", 0)
            if source == self.settings_path and version == 0 and self.legacy_path.exists():
                try:
                    document, legacy_recovered = _read_json_with_backup(self.legacy_path, self._valid_settings_document)
                    source = self.legacy_path
                    recovered = recovered or legacy_recovered
                    version = document.get("schema_version", 0)
                except ValueError:
                    pass
            if version == 0:
                loaded = document
                migrated = True
            elif version == CONFIG_SCHEMA_VERSION and isinstance(document.get("settings"), dict):
                loaded = document["settings"]
                migrated = False
            else:
                raise ValueError(f"不支援的設定 schema_version：{version}")
            state_missing = not self.state_path.exists()
            if not state_missing:
                state_document, state_recovered = _read_json_with_backup(self.state_path, self._valid_state_document)
                loaded = {
                    **loaded,
                    **state_document.get("session_metrics", {}),
                    **state_document.get("diagnostics", {}),
                }
                recovered = recovered or state_recovered
            config = _normalized_config(loaded)
            expected_settings = {key: config[key] for key in SETTINGS_KEYS}
            if migrated or recovered or state_missing or source == self.legacy_path or document.get("settings") != expected_settings:
                self._save_all(config)
            if self.legacy_path.exists() and self.settings_path.exists():
                self.legacy_path.unlink()
            return config

    @staticmethod
    def _valid_settings_document(document: dict) -> bool:
        version = document.get("schema_version", 0)
        return version == 0 or version == CONFIG_SCHEMA_VERSION and isinstance(document.get("settings"), dict)

    @staticmethod
    def _valid_state_document(document: dict) -> bool:
        return (
            document.get("schema_version") == CONFIG_SCHEMA_VERSION
            and isinstance(document.get("session_metrics"), dict)
            and isinstance(document.get("diagnostics"), dict)
        )

    def save(self, config: dict) -> None:
        with self.lock:
            ensure_app_dirs(self.root)
            prepared = self._prepare(config)
            self._write_settings(prepared)
            if not self.state_path.exists():
                self._write_state(DEFAULT_CONFIG)

    def update_state(self, config: dict, keys: set[str]) -> None:
        invalid = keys - STATE_KEYS
        if invalid:
            raise ValueError(f"不是執行狀態欄位：{', '.join(sorted(invalid))}")
        with self.lock:
            ensure_app_dirs(self.root)
            current = DEFAULT_CONFIG.copy()
            if self.state_path.exists():
                document, _recovered = _read_json_with_backup(self.state_path, self._valid_state_document)
                current.update(document["session_metrics"])
                current.update(document["diagnostics"])
            for key in keys:
                current[key] = _valid_state_value(key, config.get(key, DEFAULT_CONFIG[key]))
            self._write_state(current)

    def _prepare(self, config: dict) -> dict:
        prepared = _normalized_config(config.copy(), repair_pair=False)
        prepared["ui_mode"] = "advanced" if prepared.get("advanced_mode") else "simple"
        prepared["asr_model"] = prepared.get("model", prepared.get("asr_model", "small"))
        prepared["translation_engine"] = prepared.get("provider", prepared.get("translation_engine", "local"))
        prepared["tts_engine"] = "system" if prepared.get("tts_provider", "local") == "local" else prepared.get("tts_provider")
        prepared["runtime_path"] = prepared.get("runtime_dir", prepared.get("runtime_path"))
        prepared["save_conversation_history"] = bool(prepared.get("record_logs", False))
        prepared["subtitle_always_on_top"] = bool(prepared.get("overlay_topmost", True))
        validate_language_pair(prepared)
        return prepared

    def _save_all(self, config: dict) -> None:
        prepared = self._prepare(config)
        self._write_settings(prepared)
        self._write_state(prepared)

    def _write_settings(self, config: dict) -> None:
        settings = {key: config[key] for key in SETTINGS_KEYS}
        _atomic_write_json(self.settings_path, {"schema_version": CONFIG_SCHEMA_VERSION, "settings": settings})

    def _write_state(self, config: dict) -> None:
        state = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "session_metrics": {key: config[key] for key in SESSION_STATE_KEYS},
            "diagnostics": {key: config[key] for key in DIAGNOSTIC_STATE_KEYS},
        }
        _atomic_write_json(self.state_path, state)


def load_config(root: Path = APP_DIR) -> dict:
    return ConfigStore(root).load()


def save_config(root: Path, config: dict) -> None:
    ConfigStore(root).save(config)


def save_config_state(root: Path, config: dict, keys: set[str]) -> None:
    ConfigStore(root).update_state(config, keys)


def save_audio_devices(root: Path, devices: list[dict]) -> Path:
    ensure_app_dirs(root)
    path = root / "config" / "audio_devices.json"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(devices, handle, ensure_ascii=False, indent=2)
    return path


def _has_reparse_point(path: Path) -> bool:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except OSError:
            continue
        if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return True
    return False


def _safe_log_dir(root: Path, log_dir: Path) -> Path:
    if not log_dir.is_absolute():
        raise ValueError("紀錄資料夾必須使用絕對路徑")
    if _has_reparse_point(log_dir):
        raise ValueError("紀錄資料夾不可是符號連結或 junction")
    target = log_dir.resolve()
    app_root = root.resolve()
    home = Path.home().resolve()
    unsafe = {Path(target.anchor), app_root, home, home / "Desktop", home / "Documents", home / "Downloads"}
    if target in unsafe or app_root.is_relative_to(target):
        raise ValueError(f"拒絕清除高風險紀錄路徑：{target}")
    return target


def log_files_to_clear(root: Path = APP_DIR, log_dir: Path | None = None) -> list[Path]:
    default_logs = _safe_log_dir(root, root / "logs")
    targets = {_safe_log_dir(root, log_dir or root / "logs"), default_logs}
    files = []
    for target in targets:
        if not target.is_dir():
            continue
        for path in target.iterdir():
            if _is_managed_log_file(path, default_logs):
                files.append(path)
    return sorted(files)


def _is_managed_log_file(path: Path, default_logs: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path == default_logs / "app.log":
        return True
    try:
        if path.suffix == ".md":
            with path.open("r", encoding="utf-8") as handle:
                return handle.readline().rstrip() == f"# Conversation {path.stem}"
        if path.suffix == ".jsonl":
            with path.open("r", encoding="utf-8") as handle:
                first = next((line for line in handle if line.strip()), "")
            row = json.loads(first)
            required = {"timestamp", "direction", "source_language", "target_language", "text", "translated_text", "provider"}
            return isinstance(row, dict) and required <= row.keys() and row.get("session_id", path.stem) == path.stem
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return False


def clear_logs(root: Path = APP_DIR, log_dir: Path | None = None) -> None:
    _safe_log_dir(root, log_dir or root / "logs")
    for path in log_files_to_clear(root, log_dir):
        path.unlink(missing_ok=True)
    shutil.rmtree(root / "exports" / "subtitles", ignore_errors=True)
    ensure_app_dirs(root)


def clear_cache(root: Path = APP_DIR, cache_path: Path | None = None) -> None:
    shutil.rmtree(root / "cache" / "audio", ignore_errors=True)
    shutil.rmtree(root / "cache" / "temp_audio", ignore_errors=True)
    (root / "cache" / "translation_cache.db").unlink(missing_ok=True)
    if cache_path:
        cache_path.unlink(missing_ok=True)
    ensure_app_dirs(root)
