import queue
import math
import sys
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Literal

from .app_controller import AppController, TaskResult, TaskState
from .app_services import AudioDiagnosticsService, ModelService, RuntimeCheckResult, RuntimeImportResult, RuntimeService, UpdateService
from .audio import DeviceResolutionError, device_descriptor, device_identity, find_device, format_device_label, list_audio_devices
from .ai_auto_tuner import format_tuning_preview, recommend_tuning
from .ai_memory import add_glossary_term
from .ai_orchestrator import plan_session
from .app_log import append_app_log
from .commands import command_choices
from .config import APP_DIR, DEFAULT_CONFIG, SETTING_CHOICES, SETTING_RANGES, clear_cache, clear_logs, ensure_glossary_file, load_config, log_files_to_clear, normalize_config, save_audio_devices, save_config, save_config_state, validate_language_pair
from .engine import RealtimeEngine
from .logbook import conversation_log_usage
from .localization import SUPPORTED_LANGUAGES, setup_guide_text, translate, translate_known
from .models import MODEL_INVALID, MODEL_READY, cuda_hardware_from_check_output, list_models, model_available, model_install_message, models_dir, recommend_model
from .paths import resource_root
from .runtime import DEFAULT_RUNTIME_DIR, UPSTREAM_RUNTIME_RELEASE_URL, runtime_dir, runtime_install_message, runtime_status
from .scenarios import SCENARIO_CHOICES, apply_scenario, scenario_key, scenario_label
from .subtitle_export import export_subtitles
from .tts import list_windows_sapi_voices


LANGUAGE_CHOICES = ("auto", "zh", "en", "ja", "ko")
TARGET_LANGUAGE_CHOICES = ("zh", "en", "ja", "ko")
PROVIDER_CHOICES = ("local", "google", "openai")
TTS_PROVIDER_CHOICES = ("local", "google", "openai")
PERFORMANCE_CHOICES = ("low_latency", "balanced", "quality", "offline_light")
CLOUD_PROVIDERS = ("google", "openai")
AUDIO_DEVICE_KEYS = ("speaker_device", "microphone_device", "tts_output_device", "virtual_mic_input_device", "speaker_tts_output_device")
RESTART_SETTING_KEYS = {"model", "models_path", "device", "compute_type", "runtime_dir", "speaker_device", "microphone_device", "speech_threshold", "segment_seconds", "performance_mode"}
SEVERITY_LABELS = {"error": "錯誤", "warning": "警告", "info": "提示"}
UI_FONT = ("Microsoft JhengHei UI", 10)
THEME = {
    "bg": "#141516",
    "bar": "#0f1011",
    "control": "#23252a",
    "hover": "#2c2e34",
    "border": "#34343a",
    "text": "#f7f8f8",
    "muted": "#b6bdc9",
    "disabled": "#8a8f98",
    "accent": "#5e6ad2",
    "accent_hover": "#6f7be0",
    "accent_pressed": "#505bb8",
    "warning": "#f5c26b",
    "overlay": "#111111",
    "overlay_mine": "#c9cefa",
}


@dataclass(frozen=True)
class UiEvent:
    kind: Literal["status", "overlay", "callback"]
    args: tuple
    engine: RealtimeEngine | None = None


SETTING_ROWS = (
    ("介面語言", "app_language"),
    ("來源語言", "source_language"),
    ("目標語言", "target_language"),
    ("翻譯服務", "provider"),
    ("翻譯風格", "translation_style"),
    ("TTS 服務", "tts_provider"),
    ("場景", "scenario"),
    ("效能模式", "performance_mode"),
    ("本機翻譯 URL", "local_translate_url"),
    ("翻譯後端版本", "translation_backend_revision"),
    ("OpenAI 模型", "openai_model"),
    ("模型", "model"),
    ("ASR 裝置", "device"),
    ("運算精度", "compute_type"),
    ("喇叭來源", "speaker_device"),
    ("麥克風來源", "microphone_device"),
    ("TTS 輸出", "tts_output_device"),
    ("虛擬麥克風檢查輸入", "virtual_mic_input_device"),
    ("對方翻譯播放輸出", "speaker_tts_output_device"),
    ("TTS 速度", "tts_rate"),
    ("虛擬麥克風語音音量", "tts_volume"),
    ("本機翻譯音量", "speaker_tts_volume"),
    ("TTS 聲音", "tts_voice_name"),
    ("Google TTS 聲音", "google_tts_voice"),
    ("OpenAI TTS 模型", "openai_tts_model"),
    ("OpenAI TTS 聲音", "openai_tts_voice"),
    ("Google 專案", "google_project_id"),
    ("Google JSON", "google_service_account_json"),
    ("術語表 JSON", "glossary_path"),
    ("最大語句秒數", "segment_seconds"),
    ("語音閾值", "speech_threshold"),
    ("字幕透明度", "overlay_opacity"),
    ("字幕字級", "overlay_font_size"),
    ("字幕停留秒數", "overlay_hold_seconds"),
    ("紀錄內容", "conversation_log_content"),
    ("紀錄保留天數", "conversation_log_retention_days"),
    ("紀錄容量上限 MB", "conversation_log_max_mb"),
    ("紀錄資料夾", "log_dir"),
    ("runtime 資料夾", "runtime_dir"),
)
BASIC_SETTING_KEYS = {
    "app_language",
    "source_language",
    "target_language",
    "scenario",
    "speaker_device",
    "microphone_device",
    "tts_output_device",
}
ADVANCED_SETTING_KEYS = {key for _label, key in SETTING_ROWS} - BASIC_SETTING_KEYS
BASIC_BUTTON_TEXTS = {
    "重新整理", "交換語言", "複製字幕", "匯出字幕", "開啟紀錄", "開啟匯出資料夾",
    "設定精靈",
    "一鍵診斷",
    "開始",
    "停止",
    "測試麥克風",
    "測試虛擬麥克風",
    "取消背景工作",
}
SESSION_BUTTON_TEXTS = {"開始", "停止", "暫停/繼續", "按住說話"}
RUNNING_ONLY_BUTTON_TEXTS = {"暫停/繼續", "按住說話", "虛擬麥克風靜音/取消", "本機翻譯靜音/取消"}
ALWAYS_ENABLED_BUTTON_TEXTS = {"離開", "取消背景工作", "取消模型下載"}
# Downloads can take minutes; the rest of the UI stays usable while they run.
NON_BLOCKING_TASKS = frozenset({"model_download", "translation_model_download"})
TOOL_GROUPS = (
    ("設定與診斷", ("設定精靈", "一鍵診斷", "重新整理", "套用場景", "自動優化", "推薦模型", "測試 API", "取消背景工作")),
    ("音訊測試", ("測試喇叭", "測試麥克風", "測試虛擬麥克風", "測試 TTS", "測試裝置音", "測試字幕")),
    ("翻譯與字幕", ("交換語言", "鎖定語言", "複製字幕", "匯出字幕", "開啟匯出資料夾", "新增術語", "開啟術語表", "修正上次翻譯")),
    ("執行中控制", ("虛擬麥克風靜音/取消", "本機翻譯靜音/取消")),
    ("模型與 runtime", ("下載模型", "取消模型下載", "下載離線翻譯模型", "開啟 runtime 資料夾", "匯入已解壓 runtime", "下載上游 runtime", "更新指令設定", "檢查更新")),
    ("問題修復", ("修復喇叭音訊", "修復麥克風輸出", "修復字幕", "修復本機翻譯")),
    ("資料與紀錄", ("開啟紀錄", "開啟程式資料夾", "清除快取", "清除紀錄", "清除本機資料", "離開")),
)
TOOL_GROUP_BY_TEXT = {text: title for title, texts in TOOL_GROUPS for text in texts}
SETTING_GROUPS = (
    ("翻譯設定", {"app_language", "source_language", "target_language", "provider", "translation_style", "scenario", "local_translate_url", "translation_backend_revision", "openai_model", "google_project_id", "google_service_account_json", "glossary_path"}),
    ("音訊與語音", set(AUDIO_DEVICE_KEYS) | {"tts_provider", "tts_rate", "tts_volume", "speaker_tts_volume", "tts_voice_name", "google_tts_voice", "openai_tts_model", "openai_tts_voice"}),
    ("模型與效能", {"model", "device", "compute_type", "performance_mode", "segment_seconds", "speech_threshold", "runtime_dir"}),
    ("字幕與紀錄", {"overlay_opacity", "overlay_font_size", "overlay_hold_seconds", "conversation_log_content", "conversation_log_retention_days", "conversation_log_max_mb", "log_dir"}),
)
FIRST_RUN_ISSUE_CODES = {
    "runtime_missing",
    "model_missing",
    "model_corrupt",
    "offline_translation_model_missing",
    "speaker_device_missing",
    "microphone_device_missing",
    "virtual_mic_route",
    "virtual_mic_device_missing",
    "virtual_mic_input_missing",
    "virtual_mic_no_output",
}


def visible_setting_keys(advanced: bool) -> list[str]:
    return [key for _label, key in SETTING_ROWS if advanced or key in BASIC_SETTING_KEYS]


def restart_required_settings(started: dict, current: dict) -> set[str]:
    changed = {key for key in RESTART_SETTING_KEYS if started.get(key) != current.get(key)}
    return changed | {key for key in ("speaker_enabled", "microphone_enabled") if not started.get(key, True) and current.get(key, True)}


def visible_button_texts(buttons: list[str], advanced: bool) -> list[str]:
    return [text for text in buttons if advanced or text in BASIC_BUTTON_TEXTS]


def first_run_wizard_needed(issues) -> bool:
    return any(issue.code in FIRST_RUN_ISSUE_CODES for issue in issues)


def first_run_setup_action(issues, setup_guide_shown: bool) -> str:
    if first_run_wizard_needed(issues):
        return "diagnostics"
    return "" if setup_guide_shown else "guide"


def first_diagnostic_action(issues) -> str:
    actions = diagnostic_actions(issues)
    return actions[0] if actions else ""


def diagnostic_actions(issues) -> list[str]:
    actions = ("open_runtime", "download_model", "download_translation_models", "audio_settings", "optimize_settings", "language_settings", "local_translation", "api_settings", "open_logs")
    ordered: list[str] = []
    seen: set[str] = set()
    for action in actions:
        if any(issue.action == action for issue in issues) and action not in seen:
            ordered.append(action)
            seen.add(action)
    for issue in issues:
        action = str(getattr(issue, "action", "") or "")
        if action and action not in seen:
            ordered.append(action)
            seen.add(action)
    return ordered


def performance_segment_seconds(mode: str) -> float:
    return {"low_latency": 1.5, "balanced": 2.0, "quality": 3.0, "offline_light": 2.5}.get(mode, 2.0)


def latency_seconds_value(value) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def status_message_is_error(message: str) -> bool:
    return any(marker in message.casefold() for marker in ("找不到", "失敗", "沒有可用音訊裝置", "not found", "failed", "no audio device"))


def format_overlay_line(text: str, language: str, show_language: bool) -> str:
    return f"{language}: {text}" if show_language and text else text


def overlay_clipboard_text(speaker: str, mine: str) -> str:
    return "\n".join(line for line in (speaker, mine) if line)


def overlay_opacity_value(value) -> float:
    try:
        opacity = float(value)
    except Exception:
        return 0.86
    return min(1.0, max(0.2, opacity))


def overlay_font_size_value(value) -> int:
    try:
        size = int(value)
    except Exception:
        return 18
    return min(48, max(12, size))


def overlay_hold_seconds_value(value) -> float:
    try:
        seconds = float(value)
    except Exception:
        return 8.0
    return min(60.0, max(1.0, seconds))


def overlay_visibility_action(visible: bool) -> str:
    return "show" if visible else "hide"


def clamp_window_position(x: int, y: int, width: int, height: int, work_area: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = work_area
    return min(max(x, left), max(left, right - width)), min(max(y, top), max(top, bottom - height))


def responsive_button_columns(width: int) -> int:
    return max(1, min(5, width // 180))


def _geometry_offset(x: int, y: int) -> str:
    return f"+{x}+{y}"


def _work_area(window: tk.Misc, x: int | None = None, y: int | None = None) -> tuple[int, int, int, int]:
    try:
        import ctypes
        from ctypes import wintypes

        if x is not None and y is not None:
            class MonitorInfo(ctypes.Structure):
                _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
            user32.MonitorFromPoint.restype = wintypes.HMONITOR
            monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 0)
            if monitor:
                info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
                if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    return info.work.left, info.work.top, info.work.right, info.work.bottom
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError, TypeError):
        pass
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()


def session_button_label(text: str, engine) -> str:
    """Label running-session toggles with the action a click will take."""
    running = engine is not None and engine.running
    if text == "暫停/繼續":
        return "繼續" if running and engine.paused else "暫停"
    if text == "虛擬麥克風靜音/取消":
        return "取消虛擬麥克風靜音" if running and engine.virtual_mic_muted else "虛擬麥克風靜音"
    if text == "本機翻譯靜音/取消":
        return "取消本機翻譯靜音" if running and engine.speaker_translation_muted else "本機翻譯靜音"
    return text


def subtitle_updates_allowed(paused: bool) -> bool:
    return not paused


def swap_language_values(source_language: str, target_language: str) -> tuple[str, str]:
    if source_language == "auto":
        return source_language, target_language
    return target_language, source_language


def language_lock_value(source_language: str, detected_language: str, target_language: str = "") -> str:
    detected = str(detected_language or "").strip()
    return detected if source_language == "auto" and detected in LANGUAGE_CHOICES and detected not in {"auto", target_language} else source_language


def troubleshooting_action(issue: str) -> tuple[str, str]:
    actions = {
        "speaker_audio": ("open", "ms-settings:sound"),
        "mic_output": ("open", "https://vb-audio.com/Cable/"),
        "subtitles": ("overlay", "show"),
        "local_translation": ("open", "https://github.com/LibreTranslate/LibreTranslate"),
    }
    return actions[issue]


def diagnostic_action_label(action: str, language: str = "zh-TW") -> str:
    label = {
        "open_runtime": "開啟 runtime 資料夾 / 下載 runtime",
        "download_model": "下載模型",
        "download_translation_models": "下載離線翻譯模型",
        "audio_settings": "測試喇叭 / 測試麥克風 / 測試虛擬麥克風",
        "api_settings": "測試 API",
        "local_translation": "修復本機翻譯",
        "optimize_settings": "自動優化",
        "language_settings": "來源語言",
        "open_logs": "開啟紀錄",
    }.get(action)
    return translate(language, label) if label else action


def setup_guide_message(language: str = "zh-TW") -> str:
    return setup_guide_text(language)


def setup_guide_actions() -> tuple[str, ...]:
    return ("一鍵診斷", "套用場景", "自動優化", "測試喇叭", "測試麥克風", "測試虛擬麥克風", "測試 TTS")


def mode_notice(provider: str, tts_provider: str, record_logs: bool = False, local_translate_url: str = "") -> str:
    cloud = [name for name in dict.fromkeys((provider, tts_provider)) if name in CLOUD_PROVIDERS]
    logs = "對話紀錄：開啟" if record_logs else "對話紀錄：關閉"
    setup = "；本機翻譯 URL 未設定" if provider == "local" and not local_translate_url.strip() else ""
    if cloud:
        labels = {"google": "Google", "openai": "OpenAI"}
        return f"目前模式：雲端 API 模式；目前供應商：{', '.join(labels.get(name, name) for name in cloud)}；語音或文字可能傳送到第三方服務；可能依 API 供應商產生費用；{logs}{setup}"
    return f"目前模式：本機免費模式；語音是否上傳：否；是否可能產生 API 費用：否；{logs}{setup}"


def conversation_log_notice(config: dict) -> str:
    path = Path(config.get("log_dir") or APP_DIR / "logs" / "conversations")
    try:
        usage_mb = conversation_log_usage(path) / (1024 * 1024)
    except OSError:
        usage_mb = 0.0
    content = {"both": "原文與譯文", "original": "僅原文", "translation": "僅譯文", "none": "不含對話文字"}.get(str(config.get("conversation_log_content", "both")), "未知")
    days = int(config.get("conversation_log_retention_days", 7))
    max_mb = int(config.get("conversation_log_max_mb", 100))
    retention = f"{days} 天" if days else "不限天數"
    capacity = f"{max_mb} MB" if max_mb else "不限容量"
    return f"對話紀錄內容：{content}；位置：{path}；目前用量：{usage_mb:.1f} MB；保留：{retention}；容量上限：{capacity}"


def main_status_summary(config: dict) -> str:
    speaker = str(config.get("speaker_device") or "未選擇")
    microphone = str(config.get("microphone_device") or "未選擇")
    latency = latency_seconds_value(config.get("last_latency_seconds"))
    latency_text = f"{latency:.2f}s" if latency is not None else "尚未測試"
    error_text = str(config.get("last_error") or "無")
    return (
        f"目前場景：{scenario_label(str(config.get('scenario', '')))}；"
        f"輸入音源：{speaker} / {microphone}；"
        f"輸出音源：{config.get('tts_output_device') or '未選擇'}；"
        f"對方翻譯播放：{'開啟' if config.get('speaker_tts_enabled', False) else '關閉'}；"
        f"來源語言：{config.get('source_language', '')}；"
        f"目標語言：{config.get('target_language', '')}；"
        f"字幕：{'開啟' if config.get('overlay_visible', True) else '關閉'}；"
        f"TTS：{'開啟' if config.get('tts_enabled', True) else '關閉'}；"
        f"虛擬麥克風：{'開啟' if config.get('virtual_mic_enabled', False) else '關閉'}；"
        f"延遲：{latency_text}；"
        f"錯誤提示：{error_text}"
    )


def cloud_activation_requires_confirmation(old_provider: str, old_tts_provider: str, new_provider: str, new_tts_provider: str) -> bool:
    old_cloud = {name for name in (old_provider, old_tts_provider) if name in CLOUD_PROVIDERS}
    new_cloud = {name for name in (new_provider, new_tts_provider) if name in CLOUD_PROVIDERS}
    return bool(new_cloud - old_cloud)


class Overlay(tk.Toplevel):
    def __init__(self, master: tk.Tk, topmost: bool, opacity: float, font_size: int, x: int = -1, y: int = -1, moved=None):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", topmost)
        self.attributes("-alpha", opacity)
        self.configure(bg=THEME["overlay"])
        self._moved = moved
        scale = self.winfo_fpixels("1i") / 96
        work_area = _work_area(self, x, y) if (x, y) != (-1, -1) else _work_area(self)
        width = min(round(900 * scale), work_area[2] - work_area[0])
        self._width = width
        self._min_height = height = round(96 * scale)
        if x < work_area[0]:
            x = work_area[0] + max(0, (work_area[2] - work_area[0] - width) // 2)
        if y < work_area[1]:
            y = work_area[3] - height - round(20 * scale)
        x, y = clamp_window_position(x, y, width, height, work_area)
        self.geometry(f"{width}x{height}{_geometry_offset(x, y)}")
        self.speaker = tk.StringVar(value="")
        self.mine = tk.StringVar(value="")
        self._drag = (0, 0)
        self.labels = []
        for row, (variable, color) in enumerate(((self.speaker, "#f5f5f5"), (self.mine, THEME["overlay_mine"]))):
            label = tk.Label(self, textvariable=variable, fg=color, bg=THEME["overlay"], font=(UI_FONT[0], font_size), anchor="w")
            label.grid(row=row, column=0, sticky="ew", padx=round(18 * scale), pady=round(6 * scale))
            self.labels.append(label)
        self.grid_columnconfigure(0, weight=1)
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._drag_to)
        self.bind("<ButtonRelease-1>", self._finish_drag)
        self.bind("<Map>", lambda event: self._fit_height() if event.widget is self else None)

    def _start_drag(self, event):
        self._drag = (event.x, event.y)

    def _drag_to(self, event):
        self.geometry(_geometry_offset(self.winfo_x() + event.x - self._drag[0], self.winfo_y() + event.y - self._drag[1]))

    def _finish_drag(self, _event=None) -> None:
        self.ensure_visible()
        if self._moved is not None:
            self._moved(self.winfo_x(), self.winfo_y())

    def ensure_visible(self) -> None:
        if not self.winfo_ismapped():
            return
        x, y = clamp_window_position(self.winfo_x(), self.winfo_y(), self.winfo_width(), self.winfo_height(), _work_area(self, self.winfo_x(), self.winfo_y()))
        if (x, y) != (self.winfo_x(), self.winfo_y()):
            self.geometry(_geometry_offset(x, y))

    def _fit_height(self) -> None:
        self.update_idletasks()
        height = max(self._min_height, self.winfo_reqheight())
        self.geometry(f"{self._width}x{height}")
        self.update_idletasks()
        self.ensure_visible()

    def update_lines(self, speaker: str = "", mine: str = "") -> None:
        if speaker:
            self.speaker.set(speaker)
        if mine:
            self.mine.set(mine)

    def clear_lines(self) -> None:
        self.speaker.set("")
        self.mine.set("")

    def set_font_size(self, font_size: int) -> None:
        for label in self.labels:
            label.configure(font=(UI_FONT[0], font_size))
        self._fit_height()


class TranslatorApp(tk.Tk):
    def __init__(self, repo_root: Path | None = None):
        super().__init__()
        self._ui_scale = self.winfo_fpixels("1i") / 96
        self.repo_root = repo_root or resource_root()
        self.config = load_config(APP_DIR)
        self._log_consent_granted = False
        self._model_download_cancel: threading.Event | None = None
        self._device_id_by_label: dict[str, str] = {"系統預設": ""}
        self._device_label_by_id: dict[str, str] = {"": "系統預設"}
        self._engine_events = queue.Queue()
        self.controller = AppController(self._post_ui, self._task_state_changed)
        self.runtime_service = RuntimeService(APP_DIR / "commands.json")
        self.model_service = ModelService()
        self.audio_diagnostics = AudioDiagnosticsService(APP_DIR / "cache" / "audio")
        self.update_service = UpdateService()
        self._list_generation = 0
        self._runtime_check_generation = 0
        self._closing = False
        self.title("Realtime Audio Translator")
        left, top, right, bottom = _work_area(self)
        work_width, work_height = right - left, bottom - top
        width, height = min(self._px(1000), work_width), min(self._px(760), work_height)
        self.geometry(f"{width}x{height}{_geometry_offset(left, top)}")
        self.minsize(min(self._px(720), work_width), min(self._px(520), work_height))
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.status = tk.StringVar(value=self._t("就緒"))
        self.restart_notice = tk.StringVar()
        self._session_config = None
        self.runtime_text = tk.StringVar(value="")
        self.mode_text = tk.StringVar(value=self._mode_text())
        self.overlay_generation = 0
        self._latest_subtitle = ""
        self._push_to_talk_previous_virtual_mic_muted = None
        self.overlay = Overlay(
            self,
            self.config["overlay_topmost"],
            overlay_opacity_value(self.config.get("overlay_opacity", 0.86)),
            overlay_font_size_value(self.config.get("overlay_font_size", 18)),
            int(self.config.get("overlay_x", -1)),
            int(self.config.get("overlay_y", -1)),
            self._overlay_moved,
        )
        self._overlay_check_pending = False
        self.bind("<Configure>", lambda event: self._schedule_overlay_check() if event.widget is self else None, add="+")
        self._build()
        self._set_overlay_visible(bool(self.config.get("overlay_visible", True)))
        self._refresh_lists()
        self.after(50, self._drain_ui_events)
        if self.config.get("ai_self_diagnosis", True):
            self.after(250, self._show_first_run_wizard)

    @property
    def engine(self) -> RealtimeEngine | None:
        controller = self.__dict__.get("controller")
        return controller.engine if controller is not None else self.__dict__.get("_engine")

    @engine.setter
    def engine(self, value: RealtimeEngine | None) -> None:
        controller = self.__dict__.get("controller")
        if controller is not None:
            controller.engine = value
        else:
            self.__dict__["_engine"] = value

    def _t(self, message: str, **values) -> str:
        return translate(self.config.get("app_language"), message, **values)

    def _build(self) -> None:
        self._build_styles()
        self._build_variables()
        content = self._build_shell()
        self._build_settings(content)
        self._build_options(content)
        self._build_summary(content)
        self._build_tools(content)
        content.grid_columnconfigure(1, weight=1)
        self._apply_mode(save=False)
        self._task_state_changed(TaskState("ui", "completed"))

    def _build_styles(self) -> None:
        c = THEME
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=UI_FONT, background=c["bg"], foreground=c["text"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], troughcolor=c["bg"], focuscolor=c["accent"], selectbackground=c["accent"], selectforeground="#ffffff", insertcolor=c["text"], arrowcolor=c["muted"])
        style.configure("TButton", padding=(self._px(12), self._px(7)), background=c["control"], borderwidth=1)
        style.map("TButton", background=[("pressed", c["border"]), ("active", c["hover"])], foreground=[("disabled", c["disabled"])], bordercolor=[("focus", c["accent"])], lightcolor=[("active", c["hover"])], darkcolor=[("active", c["hover"])])
        style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff", bordercolor=c["accent"], lightcolor=c["accent"], darkcolor=c["accent"])
        style.map("Accent.TButton", background=[("disabled", c["control"]), ("pressed", c["accent_pressed"]), ("active", c["accent_hover"])], lightcolor=[("active", c["accent_hover"])], darkcolor=[("active", c["accent_hover"])], bordercolor=[("disabled", c["border"])])
        style.configure("TEntry", fieldbackground=c["control"], padding=self._px(5))
        style.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])])
        style.configure("TCombobox", fieldbackground=c["control"], background=c["control"], padding=self._px(5), arrowsize=self._px(14))
        style.map("TCombobox", fieldbackground=[("readonly", c["control"])], foreground=[("readonly", c["text"])], background=[("active", c["hover"])], bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])], arrowcolor=[("active", c["text"])])
        style.configure("TCheckbutton", indicatorsize=self._px(12), indicatorbackground=c["control"], indicatorforeground="#ffffff", upperbordercolor=c["border"], lowerbordercolor=c["border"])
        style.map("TCheckbutton", indicatorbackground=[("selected", c["accent"]), ("active", c["hover"])], background=[("active", c["bg"])])
        style.configure("Vertical.TScrollbar", background=c["control"], bordercolor=c["bg"], lightcolor=c["control"], darkcolor=c["control"], arrowsize=self._px(12))
        style.map("Vertical.TScrollbar", background=[("active", c["border"])])
        style.configure("TNotebook", background=c["bg"], bordercolor=c["border"], tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", padding=(self._px(16), self._px(8)), background=c["bg"], foreground=c["muted"], bordercolor=c["border"], lightcolor=c["bg"])
        style.map("TNotebook.Tab", background=[("selected", c["control"]), ("active", c["hover"])], foreground=[("selected", c["text"])], lightcolor=[("selected", c["accent"])], expand=[("selected", (0, 0, 0, 0))], padding=[("selected", (self._px(16), self._px(8)))])
        style.configure("TLabelframe", background=c["bg"], bordercolor=c["border"])
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["muted"], font=(UI_FONT[0], 10, "bold"))
        style.configure("Muted.TLabel", foreground=c["muted"])
        style.configure("Bar.TFrame", background=c["bar"])
        style.configure("Bar.TLabel", background=c["bar"])
        style.configure("BarMuted.TLabel", background=c["bar"], foreground=c["muted"])
        style.configure("BarWarning.TLabel", background=c["bar"], foreground=c["warning"])
        style.configure("Title.TLabel", background=c["bar"], font=("Segoe UI", 17, "bold"))
        style.configure("Bar.TCheckbutton", background=c["bar"])
        style.map("Bar.TCheckbutton", background=[("active", c["bar"])])
        self.option_add("*TCombobox*Listbox.background", c["control"])
        self.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.configure(background=c["bg"])

    def _build_variables(self) -> None:
        self.vars = {key: tk.StringVar(value=str(value)) for key, value in self.config.items()}
        self.vars["scenario"].set(self._t(scenario_label(self.config["scenario"])))
        self.overlay_visible = tk.BooleanVar(value=bool(self.config["overlay_visible"]))
        self.overlay_topmost = tk.BooleanVar(value=bool(self.config["overlay_topmost"]))
        self.show_language_labels = tk.BooleanVar(value=bool(self.config["show_language_labels"]))
        self.show_original_text = tk.BooleanVar(value=bool(self.config["show_original_text"]))
        self.show_translated_text = tk.BooleanVar(value=bool(self.config.get("show_translated_text", True)))
        self.tts_enabled = tk.BooleanVar(value=bool(self.config.get("tts_enabled", True)))
        self.speaker_tts_enabled = tk.BooleanVar(value=bool(self.config.get("speaker_tts_enabled", False)))
        self.start_virtual_mic_muted = tk.BooleanVar(value=bool(self.config.get("start_virtual_mic_muted", False)))
        self.virtual_mic_enabled = tk.BooleanVar(value=bool(self.config.get("virtual_mic_enabled", False)))
        self.speaker_enabled = tk.BooleanVar(value=bool(self.config.get("speaker_enabled", True)))
        self.microphone_enabled = tk.BooleanVar(value=bool(self.config.get("microphone_enabled", True)))
        self.record_logs = tk.BooleanVar(value=bool(self.config["record_logs"]))
        self.advanced_mode = tk.BooleanVar(value=bool(self.config.get("advanced_mode", False)))

    def _build_shell(self) -> ttk.Frame:
        header = ttk.Frame(self, padding=(self._px(20), self._px(14), self._px(20), self._px(12)), style="Bar.TFrame")
        header.pack(fill="x")
        tk.Frame(self, height=1, background=THEME["border"]).pack(fill="x")
        heading = ttk.Frame(header, style="Bar.TFrame")
        heading.pack(fill="x")
        ttk.Label(heading, text="Realtime Audio Translator", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=self._t("選擇場景與音訊來源，開始即時翻譯。"), style="BarMuted.TLabel").pack(anchor="w", pady=(2, 10))
        self._session_frame = ttk.Frame(header, style="Bar.TFrame")
        self._session_frame.pack(fill="x")
        footer = ttk.Frame(self, padding=(self._px(20), self._px(8)), style="Bar.TFrame")
        footer.pack(side="bottom", fill="x")
        tk.Frame(self, height=1, background=THEME["border"]).pack(side="bottom", fill="x")
        status_label = ttk.Label(footer, textvariable=self.status, wraplength=self._px(650), style="Bar.TLabel")
        status_label.pack(fill="x")
        restart_label = ttk.Label(footer, textvariable=self.restart_notice, wraplength=self._px(650), style="BarWarning.TLabel")
        restart_label.pack(fill="x")
        footer.bind("<Configure>", lambda event: [label.configure(wraplength=max(200, event.width - 40)) for label in (status_label, restart_label)])
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0, background=THEME["bg"])
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        frame = ttk.Frame(canvas, padding=self._px(12))
        self._content_canvas = canvas
        self._content_window = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", self._resize_content)
        self.bind("<MouseWheel>", self._scroll_content, add="+")
        ttk.Checkbutton(heading, text=self._t("進階設定"), variable=self.advanced_mode, command=self._apply_mode, style="Bar.TCheckbutton").pack(side="right")
        return frame

    def _build_settings(self, content: ttk.Frame) -> None:
        self.comboboxes: dict[str, ttk.Combobox] = {}
        self.setting_widgets: dict[str, list[tk.Widget]] = {}
        notebook = ttk.Notebook(content)
        notebook.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        notebook.enable_traversal()
        self._settings_notebook = notebook
        self._setting_panels = {}
        for title, keys in SETTING_GROUPS:
            panel = ttk.Frame(notebook, padding=self._px(12))
            panel.grid_columnconfigure(1, weight=1)
            notebook.add(panel, text=self._t(title))
            for key in keys:
                self._setting_panels[key] = panel
        for row, (label, key) in enumerate(SETTING_ROWS):
            frame = self._setting_panels[key]
            row_widgets: list[tk.Widget] = []
            label_widget = ttk.Label(frame, text=self._t(label))
            label_widget.grid(row=row, column=0, sticky="w", pady=4)
            row_widgets.append(label_widget)
            if key in ("source_language", "target_language"):
                values = LANGUAGE_CHOICES if key == "source_language" else TARGET_LANGUAGE_CHOICES
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=values)
                widget.bind("<<ComboboxSelected>>", lambda _event: self._save())
            elif key in ("app_language", "provider", "translation_style", "tts_provider", "performance_mode", "scenario", "conversation_log_content"):
                values = SUPPORTED_LANGUAGES if key == "app_language" else sorted(SETTING_CHOICES[key]) if key == "translation_style" else tuple(self._t(scenario_label(key)) for key in SCENARIO_CHOICES) if key == "scenario" else ("both", "original", "translation", "none") if key == "conversation_log_content" else PERFORMANCE_CHOICES if key == "performance_mode" else TTS_PROVIDER_CHOICES if key == "tts_provider" else PROVIDER_CHOICES
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=values, state="readonly")
                widget.bind("<<ComboboxSelected>>", lambda _event, name=key: self._apply_performance_mode() if name == "performance_mode" else self._apply_scenario() if name == "scenario" else self._save())
            elif key in AUDIO_DEVICE_KEYS or key in ("model", "device", "compute_type", "tts_voice_name"):
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=[], state="readonly" if key in AUDIO_DEVICE_KEYS or key in {"device", "compute_type"} else "normal")
                widget.bind("<<ComboboxSelected>>", lambda _event: self._save())
                self.comboboxes[key] = widget
            else:
                widget = ttk.Entry(frame, textvariable=self.vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=4, padx=8)
            row_widgets.append(widget)
            if key == "google_service_account_json":
                button = ttk.Button(frame, text=self._t("選擇"), command=self._pick_google_json)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "glossary_path":
                button = ttk.Button(frame, text=self._t("選擇"), command=self._pick_glossary_json)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key in ("overlay_opacity", "overlay_font_size", "overlay_hold_seconds"):
                button = ttk.Button(frame, text=self._t("套用"), command=self._apply_overlay)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "runtime_dir":
                button = ttk.Button(frame, text=self._t("選擇"), command=self._pick_runtime_dir)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "log_dir":
                button = ttk.Button(frame, text=self._t("選擇"), command=self._pick_log_dir)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "tts_voice_name":
                button = ttk.Button(frame, text=self._t("列出"), command=self._list_tts_voices)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            self.setting_widgets[key] = row_widgets

    def _build_options(self, content: ttk.Frame) -> None:
        next_row = 2
        options = ttk.Frame(content)
        options.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        options.grid_columnconfigure((0, 1), weight=1)
        frame = options
        ttk.Checkbutton(frame, text=self._t("顯示字幕"), variable=self.overlay_visible, command=self._apply_overlay).grid(row=next_row + 3, column=0, sticky="w")
        overlay_topmost_widget = ttk.Checkbutton(frame, text=self._t("字幕最上層"), variable=self.overlay_topmost, command=self._apply_overlay)
        overlay_topmost_widget.grid(row=next_row + 3, column=1, sticky="w")
        language_labels_widget = ttk.Checkbutton(frame, text=self._t("顯示語言"), variable=self.show_language_labels, command=self._save)
        language_labels_widget.grid(row=next_row + 3, column=2, sticky="w")
        original_text_widget = ttk.Checkbutton(frame, text=self._t("顯示原文"), variable=self.show_original_text, command=self._save)
        original_text_widget.grid(row=next_row + 4, column=0, sticky="w")
        translated_text_widget = ttk.Checkbutton(frame, text=self._t("顯示譯文"), variable=self.show_translated_text, command=self._save)
        translated_text_widget.grid(row=next_row + 4, column=1, sticky="w")
        ttk.Checkbutton(frame, text=self._t("播放翻譯語音"), variable=self.tts_enabled, command=self._save).grid(row=next_row + 4, column=2, sticky="w")
        speaker_capture_widget = ttk.Checkbutton(frame, text=self._t("擷取喇叭"), variable=self.speaker_enabled, command=self._save)
        speaker_capture_widget.grid(row=next_row + 5, column=0, sticky="w")
        microphone_capture_widget = ttk.Checkbutton(frame, text=self._t("擷取麥克風"), variable=self.microphone_enabled, command=self._save)
        microphone_capture_widget.grid(row=next_row + 5, column=1, sticky="w")
        record_logs_widget = ttk.Checkbutton(frame, text=self._t("儲存對話紀錄"), variable=self.record_logs, command=self._save)
        record_logs_widget.grid(row=next_row + 6, column=0, sticky="w")
        speaker_tts_widget = ttk.Checkbutton(frame, text=self._t("播放對方翻譯"), variable=self.speaker_tts_enabled, command=self._save)
        speaker_tts_widget.grid(row=next_row + 6, column=2, sticky="w")
        start_virtual_mic_muted_widget = ttk.Checkbutton(frame, text=self._t("虛擬麥克風啟動時靜音"), variable=self.start_virtual_mic_muted, command=self._save)
        start_virtual_mic_muted_widget.grid(row=next_row + 7, column=0, sticky="w")
        self.advanced_mode_widgets = [overlay_topmost_widget, language_labels_widget, original_text_widget, translated_text_widget, speaker_capture_widget, microphone_capture_widget, record_logs_widget, speaker_tts_widget, start_virtual_mic_muted_widget]
        ttk.Checkbutton(frame, text=self._t("輸出到虛擬麥克風"), variable=self.virtual_mic_enabled, command=self._save).grid(row=next_row + 6, column=1, sticky="w")
        self._option_widgets = options.winfo_children()

    def _build_summary(self, frame: ttk.Frame) -> None:
        runtime_label = ttk.Label(frame, textvariable=self.runtime_text, style="Muted.TLabel", wraplength=self._px(650))
        runtime_label.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
        mode_label = ttk.Label(frame, textvariable=self.mode_text, style="Muted.TLabel", wraplength=self._px(650))
        mode_label.grid(row=4, column=0, columnspan=3, sticky="ew", pady=4)
        self._detail_labels = [runtime_label, mode_label]
        preview = ttk.LabelFrame(frame, text=self._t("即時字幕預覽"), padding=self._px(12))
        preview.grid(row=2, column=0, columnspan=3, sticky="ew", pady=8)
        self._preview_text = tk.StringVar(value=self._t("尚無字幕；開始翻譯或使用「測試字幕」。"))
        preview_label = ttk.Label(preview, textvariable=self._preview_text, wraplength=self._px(650))
        preview_label.pack(fill="x")
        self._detail_labels.append(preview_label)

    def _build_tools(self, frame: ttk.Frame) -> None:
        tools = ttk.Frame(frame)
        tools.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        tools.grid_columnconfigure(0, weight=1)
        self._tools_frame = tools
        self._tool_search = tk.StringVar()
        search = ttk.Frame(tools)
        search.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(search, text=self._t("搜尋工具")).pack(side="left", padx=(0, 8))
        ttk.Entry(search, textvariable=self._tool_search).pack(side="left", fill="x", expand=True)
        self._tool_search.trace_add("write", lambda *_args: self._layout_buttons())
        self._tool_groups = {title: ttk.LabelFrame(tools, text=self._t(title), padding=self._px(8)) for title, _texts in TOOL_GROUPS}
        self.button_widgets: list[tuple[str, ttk.Button]] = []
        def copy_overlay() -> None:
            text = self._latest_subtitle or overlay_clipboard_text(self.overlay.speaker.get(), self.overlay.mine.get())
            if not text:
                self.status.set(self._t("沒有字幕可複製"))
                return
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.set(self._t("字幕已複製"))

        for text, command in (
            ("設定精靈", self._show_setup_guide),
            ("重新整理", self._refresh_lists),
            ("交換語言", self._swap_languages),
            ("套用場景", self._apply_scenario),
            ("自動優化", self._optimize_settings),
            ("推薦模型", self._recommend),
            ("下載模型", self._download_model),
            ("取消模型下載", self._cancel_model_download),
            ("下載離線翻譯模型", self._download_translation_models),
            ("一鍵診斷", self._run_diagnostics),
            ("鎖定語言", self._lock_language),
            ("檢查更新", self._check_updates),
            ("更新指令設定", self._refresh_commands),
            ("開啟程式資料夾", self._open_app_dir),
            ("開啟術語表", self._open_glossary),
            ("新增術語", self._add_glossary_term),
            ("修正上次翻譯", self._fix_last_translation),
            ("測試 API", self._test_api),
            ("測試裝置音", self._test_tone),
            ("測試 TTS", self._test_tts),
            ("測試虛擬麥克風", self._test_virtual_mic),
            ("測試喇叭", self._test_speaker),
            ("測試麥克風", self._test_mic),
            ("取消背景工作", self._cancel_background_tasks),
            ("測試字幕", self._test_subtitles),
            ("開始", self._start),
            ("停止", self._stop),
            ("離開", self._quit),
            ("暫停/繼續", self._toggle_pause),
            ("虛擬麥克風靜音/取消", self._toggle_virtual_mic_mute),
            ("本機翻譯靜音/取消", self._toggle_speaker_translation_mute),
            ("複製字幕", copy_overlay),
            ("開啟 runtime 資料夾", self._open_runtime_dir),
            ("匯入已解壓 runtime", self._import_runtime),
            ("下載上游 runtime", lambda: webbrowser.open(UPSTREAM_RUNTIME_RELEASE_URL)),
            ("修復喇叭音訊", lambda: self._troubleshoot("speaker_audio")),
            ("修復麥克風輸出", lambda: self._troubleshoot("mic_output")),
            ("修復字幕", lambda: self._troubleshoot("subtitles")),
            ("修復本機翻譯", lambda: self._troubleshoot("local_translation")),
            ("清除快取", self._clear_cache),
            ("開啟紀錄", self._open_logs),
            ("匯出字幕", self._export_subtitles),
            ("開啟匯出資料夾", self._open_exports),
            ("清除紀錄", self._clear_logs),
            ("清除本機資料", self._clear_local_data),
        ):
            parent = self._session_frame if text in SESSION_BUTTON_TEXTS else self._tool_groups[TOOL_GROUP_BY_TEXT[text]]
            button = ttk.Button(parent, text=self._t(text), command=command, style="Accent.TButton" if text == "開始" else "TButton")
            self.button_widgets.append((text, button))
        ptt_button = ttk.Button(self._session_frame, text=self._t("按住說話"))
        ptt_button.bind("<ButtonPress-1>", lambda _event: self._push_to_talk(True))
        ptt_button.bind("<ButtonRelease-1>", lambda _event: self._push_to_talk(False))
        ptt_button.bind("<KeyPress-space>", lambda _event: self._push_to_talk(True))
        ptt_button.bind("<KeyRelease-space>", lambda _event: self._push_to_talk(False))
        ptt_button.bind("<FocusOut>", lambda _event: self._push_to_talk(False))
        self.button_widgets.append(("按住說話", ptt_button))
        ttk.Button(self._session_frame, text=self._t("儲存設定"), command=self._save_settings).grid(row=0, column=4, padx=3, sticky="ew")
        for column in range(5):
            self._session_frame.grid_columnconfigure(column, weight=1)
        self.bind("<Control-s>", lambda _event: self._save_settings())

    def _save_settings(self) -> None:
        if self._save():
            self.status.set(self._t("設定已儲存；介面語言變更於重新啟動後完整套用。"))

    def _resize_content(self, event) -> None:
        self._content_canvas.itemconfigure(self._content_window, width=event.width)
        for label in self._detail_labels:
            label.configure(wraplength=max(200, event.width - 64))
        self._layout_buttons(event.width)

    def _scroll_content(self, event):
        widget = self._content_canvas.winfo_containing(event.x_root, event.y_root)
        if isinstance(widget, (ttk.Combobox, tk.Text)) or not event.delta:
            return
        while widget is not None and widget is not self._content_canvas:
            widget = widget.master
        if widget is self._content_canvas and self._content_canvas.yview() != (0.0, 1.0):
            self._content_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

    def _px(self, value: float) -> int:
        return round(value * self.__dict__.get("_ui_scale", 1.0))

    def _layout_buttons(self, width: int | None = None) -> None:
        for _text, button in self.button_widgets:
            button.grid_forget()
        visible = set(visible_button_texts([text for text, _button in self.button_widgets], self.advanced_mode.get()))
        columns = responsive_button_columns(round((width or self._tools_frame.winfo_width()) / self.__dict__.get("_ui_scale", 1.0)))
        counts = dict.fromkeys(self._tool_groups, 0)
        session_index = 0
        query = self._tool_search.get().strip().casefold()
        for text, button in self.button_widgets:
            if text in SESSION_BUTTON_TEXTS:
                button.grid(row=0, column=session_index, sticky="ew", padx=3)
                session_index += 1
            elif text in visible and query in self._t(text).casefold():
                group = TOOL_GROUP_BY_TEXT[text]
                button.grid(row=counts[group] // columns, column=counts[group] % columns, sticky="ew", padx=3, pady=3)
                counts[group] += 1
        for row, (title, group) in enumerate(self._tool_groups.items(), start=1):
            if counts[title]:
                group.grid(row=row, column=0, sticky="ew", pady=4)
                for column in range(5):
                    group.grid_columnconfigure(column, weight=1 if column < columns else 0)
            else:
                group.grid_remove()

    def _refresh_lists(self) -> None:
        self.status.set(self._t("正在重新整理裝置與模型"))
        config = self._config_from_vars()
        generation = self._list_generation + 1

        def work():
            raw_devices = list_audio_devices()
            save_audio_devices(APP_DIR, raw_devices)
            commands = APP_DIR / "commands.json"
            models = sorted(set(list_models(self.repo_root / "_models", models_dir(config))) | set(command_choices(commands, "model")))
            asr_devices = command_choices(commands, "device") or sorted(SETTING_CHOICES["device"])
            compute_types = command_choices(commands, "compute_type") or sorted(SETTING_CHOICES["compute_type"])
            return raw_devices, models, asr_devices, compute_types

        if self.controller.submit(work, lambda result: self._lists_ready(generation, result), name="refresh_lists"):
            self._list_generation = generation
        else:
            self.status.set(self._t("裝置與模型重新整理已在進行"))

    def _lists_ready(self, generation: int, result: TaskResult) -> None:
        if generation != self._list_generation:
            return
        if result.error is not None:
            self.status.set(self._t("無法重新整理裝置與模型：{error}", error=result.error))
            return
        raw_devices, models, asr_devices, compute_types = result.value
        config_changed = False
        default_device = self._t("系統預設")
        self._device_id_by_label = {default_device: ""}
        self._device_label_by_id = {"": default_device}
        for device in raw_devices:
            label = format_device_label(device)
            identity = device_identity(device)
            self._device_id_by_label[label] = identity
            self._device_label_by_id[identity] = label
        for key in AUDIO_DEVICE_KEYS:
            identity = str(self.config.get(key) or "")
            if not identity:
                self.vars[key].set(default_device)
                continue
            try:
                index = find_device(identity, want_output=key in {"speaker_device", "tts_output_device", "speaker_tts_output_device"}, devices=raw_devices)
                current = next(device for device in raw_devices if device["index"] == index)
                current_identity = device_identity(current)
                config_changed = config_changed or current_identity != identity
                self.config[key] = current_identity
                self.vars[key].set(self._device_label_by_id[current_identity])
            except (DeviceResolutionError, StopIteration):
                try:
                    label = f"{format_device_label(device_descriptor(identity))}（不可用，請重新選擇）"
                except DeviceResolutionError:
                    label = "未知裝置（不可用，請重新選擇）"
                self._device_id_by_label[label] = identity
                self._device_label_by_id[identity] = label
                self.vars[key].set(label)
        if config_changed:
            save_config(APP_DIR, self.config)
        for key, widget in self.comboboxes.items():
            if key == "model":
                widget.configure(values=models)
            elif key == "device":
                widget.configure(values=[value for value in asr_devices if value in SETTING_CHOICES[key]] or sorted(SETTING_CHOICES[key]))
            elif key == "compute_type":
                widget.configure(values=[value for value in compute_types if value in SETTING_CHOICES[key]] or sorted(SETTING_CHOICES[key]))
            elif key != "tts_voice_name":
                channel = "output_channels" if key in {"speaker_device", "tts_output_device", "speaker_tts_output_device"} else "input_channels"
                devices = [default_device] + [format_device_label(device) for device in raw_devices if device[channel] > 0]
                current_label = self.vars[key].get()
                if current_label not in devices:
                    devices.append(current_label)
                widget.configure(values=devices)
        self.status.set(self._t("裝置與模型已更新"))
        self._refresh_runtime_status()

    def _list_tts_voices(self) -> None:
        self._submit("正在列出 Windows TTS 聲音", list_windows_sapi_voices, self._tts_voices_ready, "無法列出 Windows TTS 聲音")

    def _tts_voices_ready(self, voices: list[str]) -> None:
        self.comboboxes["tts_voice_name"].configure(values=voices)
        self.status.set("; ".join(voices) if voices else self._t("找不到 Windows TTS 聲音"))

    def _swap_languages(self) -> None:
        if self.vars["source_language"].get() == "auto":
            self.status.set(self._t("自動偵測來源語言時無法交換；請先選擇固定來源語言"))
            return
        source, target = swap_language_values(self.vars["source_language"].get(), self.vars["target_language"].get())
        self.vars["source_language"].set(source)
        self.vars["target_language"].set(target)
        self._save()

    def _config_from_vars(self) -> dict:
        config = self.config.copy()
        editable_keys = {key for _label, key in SETTING_ROWS}
        for key, variable in self.vars.items():
            if key not in editable_keys:
                continue
            config[key] = self._device_id_by_label.get(variable.get(), self.config.get(key, "")) if key in AUDIO_DEVICE_KEYS else variable.get()
        config["scenario"] = next((key for key in SCENARIO_CHOICES if config["scenario"] == self._t(scenario_label(key))), scenario_key(config["scenario"]))
        config["overlay_visible"] = self.overlay_visible.get()
        config["overlay_topmost"] = self.overlay_topmost.get()
        config["show_language_labels"] = self.show_language_labels.get()
        config["show_original_text"] = self.show_original_text.get()
        config["show_translated_text"] = self.show_translated_text.get()
        config["tts_enabled"] = self.tts_enabled.get()
        config["speaker_tts_enabled"] = self.speaker_tts_enabled.get()
        config["start_virtual_mic_muted"] = self.start_virtual_mic_muted.get()
        config["virtual_mic_enabled"] = self.virtual_mic_enabled.get()
        config["speaker_enabled"] = self.speaker_enabled.get()
        config["microphone_enabled"] = self.microphone_enabled.get()
        config["record_logs"] = self.record_logs.get()
        config["advanced_mode"] = self.advanced_mode.get()
        config["setup_guide_shown"] = str(config.get("setup_guide_shown", False)).lower() == "true"
        if config.get("performance_mode") not in PERFORMANCE_CHOICES:
            config["performance_mode"] = "balanced"
        for key, (minimum, maximum) in SETTING_RANGES.items():
            try:
                value = type(DEFAULT_CONFIG[key])(config[key])
                config[key] = max(minimum, min(maximum, value)) if math.isfinite(value) else DEFAULT_CONFIG[key]
            except (TypeError, ValueError, OverflowError):
                config[key] = DEFAULT_CONFIG[key]
        return config

    def _save(self) -> bool:
        config = self._config_from_vars()
        language_changed = config.get("app_language") != self.config.get("app_language")
        try:
            validate_language_pair(config)
        except ValueError as exc:
            self.status.set(translate_known(self.config.get("app_language"), str(exc)))
            return False
        cloud_enabled = bool({config["provider"], config["tts_provider"]} & set(CLOUD_PROVIDERS))
        if cloud_activation_requires_confirmation(self.config.get("provider", "local"), self.config.get("tts_provider", "local"), config["provider"], config["tts_provider"]):
            cloud_message = self._t("雲端 API 可能傳送語音或文字並產生費用。是否繼續？") if config.get("app_language") == "en" else mode_notice(config["provider"], config["tts_provider"], bool(config["record_logs"]), config.get("local_translate_url", ""))
            if not messagebox.askyesno(self._t("啟用雲端 API？"), cloud_message):
                self._load_config_into_widgets(self.config)
                self.status.set(self._t("雲端 API 未啟用"))
                return False
        if config["record_logs"] and not getattr(self, "_log_consent_granted", False):
            log_message = self._t("本次執行是否允許儲存設定中指定的對話內容？") if config.get("app_language") == "en" else conversation_log_notice(config) + "\n\n本次執行是否允許儲存上述內容？"
            if not messagebox.askyesno(self._t("啟用本次對話紀錄？"), log_message):
                self.record_logs.set(False)
                config["record_logs"] = False
            else:
                self._log_consent_granted = True
        elif not config["record_logs"]:
            self._log_consent_granted = False
        config["cloud_api_enabled"] = cloud_enabled
        config = normalize_config(config, repair_pair=False)
        try:
            save_config(APP_DIR, config)
        except (OSError, ValueError) as exc:
            self.status.set(self._t("設定儲存失敗：{error}", error=exc))
            return False
        if self.engine:
            try:
                self.engine.update_config(config)
            except Exception as exc:
                try:
                    save_config(APP_DIR, self.config)
                except (OSError, ValueError) as rollback_error:
                    self.status.set(self._t("設定套用失敗：{error}；設定檔還原失敗：{rollback_error}", error=exc, rollback_error=rollback_error))
                else:
                    self.status.set(self._t("設定套用失敗：{error}", error=exc))
                return False
        self.config = config
        for key, variable in self.__dict__.get("vars", {}).items():
            if key in {name for _label, name in SETTING_ROWS} - set(AUDIO_DEVICE_KEYS) - {"scenario"}:
                variable.set(str(config[key]))
        self.mode_text.set(self._mode_text())
        self._update_restart_notice()
        if language_changed:
            self.status.set(self._t("介面語言已變更；請重新啟動程式以完整套用"))
        return True

    def _update_restart_notice(self) -> None:
        notice = self.__dict__.get("restart_notice")
        if notice is None:
            return
        started = self.__dict__.get("_session_config")
        changed = restart_required_settings(started, self.config) if started and self.engine is not None and self.engine.running else set()
        labels = {key: label for label, key in SETTING_ROWS} | {"models_path": "模型資料夾", "speaker_enabled": "擷取喇叭", "microphone_enabled": "擷取麥克風"}
        notice.set(self._t("需停止後重新開始以套用：{settings}", settings=", ".join(self._t(labels[key]) for key in sorted(changed))) if changed else "")

    def _set_last_error(self, message: str) -> None:
        self.config["last_error"] = message
        if "last_error" in self.vars:
            self.vars["last_error"].set(message)
        self.mode_text.set(self._mode_text())
        save_config_state(APP_DIR, self.config, {"last_error"})

    def _engine_status(self, message: str) -> None:
        self.status.set(message)
        if status_message_is_error(message):
            self._set_last_error(message)

    def _post_ui(self, kind: Literal["status", "overlay", "callback"], *args, engine: RealtimeEngine | None = None) -> None:
        self._engine_events.put(UiEvent(kind, args, engine))

    def _schedule_overlay_check(self) -> None:
        # Dragging the main window fires many Configure events; check the overlay once per idle pass.
        if self._overlay_check_pending:
            return
        self._overlay_check_pending = True

        def check() -> None:
            self._overlay_check_pending = False
            self.overlay.ensure_visible()

        self.after_idle(check)

    def _drain_ui_events(self) -> None:
        assert threading.current_thread() is threading.main_thread(), "Tk event pump must run on the main thread"
        deadline = time.perf_counter() + 0.008
        for _ in range(100):
            if time.perf_counter() >= deadline:
                break
            try:
                event = self._engine_events.get_nowait()
            except queue.Empty:
                break
            if (self._closing and event.kind != "callback") or (event.engine is not None and event.engine is not self.engine):
                continue
            try:
                if event.kind == "status":
                    self._engine_status(*event.args)
                elif event.kind == "overlay":
                    self._overlay_update(*event.args)
                else:
                    event.args[0](*event.args[1:])
            except Exception:
                self.report_callback_exception(*sys.exc_info())
        if not self._closing or self.engine is not None or self.controller.busy:
            self.after(1 if not self._engine_events.empty() else 50, self._drain_ui_events)

    def _submit(self, status: str, work, done, error_title: str, task_name: str | None = None) -> None:
        status = translate_known(self.config.get("app_language"), status)
        error_title = translate_known(self.config.get("app_language"), error_title)
        self.status.set(status)

        def finish(result: TaskResult) -> None:
            if result.error is not None:
                self.status.set(f"{error_title}: {result.error}" if self.config.get("app_language") == "en" else f"{error_title}：{result.error}")
                messagebox.showerror(error_title, str(result.error))
            else:
                done(result.value)

        if not self.controller.submit(work, finish, name=task_name or error_title):
            self.status.set(self._t("{status}；相同工作已在進行", status=status))

    def _task_state_changed(self, _state: TaskState) -> None:
        buttons = self.__dict__.get("button_widgets", [])
        if not buttons:
            return
        disabled = self.controller.busy_except(NON_BLOCKING_TASKS)
        engine = self.engine
        running = engine is not None and engine.running
        for text, button in buttons:
            enabled = not disabled or text in ALWAYS_ENABLED_BUTTON_TEXTS
            if text == "開始":
                enabled = enabled and engine is None
            elif text == "停止":
                enabled = engine is not None or self.controller.has_task("start_check")
            elif text in RUNNING_ONLY_BUTTON_TEXTS:
                enabled = running
            label = session_button_label(text, engine)
            if label != text:
                button.configure(text=self._t(label))
            button.configure(state="normal" if enabled else "disabled")

    def _cancel_background_tasks(self) -> None:
        model_cancel = self.__dict__.get("_model_download_cancel")
        if model_cancel is not None:
            model_cancel.set()
            self._model_download_cancel = None
        count = self.controller.cancel()
        self.status.set(self._t("正在取消 {count} 個背景工作", count=count) if count else self._t("目前沒有可取消的背景工作"))

    def _mode_text(self) -> str:
        if self.config.get("app_language") == "en" or not self.config.get("advanced_mode", False):
            return "\n".join((
                self._t("模式：翻譯 {provider}；TTS {tts_provider}", provider=self.config["provider"], tts_provider=self.config["tts_provider"]),
                self._t("對話紀錄：{state}", state=self._t("開啟" if self.config["record_logs"] else "關閉")),
                self._t("場景：{scenario}；來源 {source}；目標 {target}", scenario=self._t(scenario_label(str(self.config.get("scenario", "")))), source=self.config["source_language"], target=self.config["target_language"]),
            ))
        return f"{mode_notice(self.config['provider'], self.config['tts_provider'], bool(self.config['record_logs']), self.config.get('local_translate_url', ''))}\n{conversation_log_notice(self.config)}\n{main_status_summary(self.config)}"

    def _apply_mode(self, save: bool = True) -> None:
        for key in ADVANCED_SETTING_KEYS:
            for widget in self.setting_widgets.get(key, []):
                if self.advanced_mode.get():
                    widget.grid()
                else:
                    widget.grid_remove()
        for widget in self.advanced_mode_widgets:
            if self.advanced_mode.get():
                widget.grid()
            else:
                widget.grid_remove()
        option_index = 0
        for widget in self.__dict__.get("_option_widgets", []):
            if self.advanced_mode.get() or widget not in self.advanced_mode_widgets:
                widget.grid(row=option_index // 2, column=option_index % 2, sticky="w", pady=3)
                option_index += 1
        self._layout_buttons()
        notebook = self.__dict__.get("_settings_notebook")
        if notebook is not None:
            for title, keys in SETTING_GROUPS:
                panel = self._setting_panels[next(iter(keys))]
                if self.advanced_mode.get() or keys & BASIC_SETTING_KEYS:
                    notebook.add(panel, text=self._t(title))
                else:
                    notebook.hide(panel)
        if save:
            self._save()
            self._refresh_runtime_status()

    def _apply_performance_mode(self) -> None:
        self.vars["segment_seconds"].set(str(performance_segment_seconds(self.vars["performance_mode"].get())))
        self._save()

    def _apply_overlay(self) -> None:
        self._set_overlay_visible(self.overlay_visible.get())
        self.overlay.attributes("-topmost", self.overlay_topmost.get())
        self.overlay.attributes("-alpha", overlay_opacity_value(self.vars["overlay_opacity"].get()))
        self.overlay.set_font_size(overlay_font_size_value(self.vars["overlay_font_size"].get()))
        self.overlay.ensure_visible()
        self._save()

    def _overlay_moved(self, x: int, y: int) -> None:
        self.config.update({"overlay_x": x, "overlay_y": y})
        if "overlay_x" in self.vars:
            self.vars["overlay_x"].set(str(x))
            self.vars["overlay_y"].set(str(y))
        save_config(APP_DIR, self.config)

    def _set_overlay_visible(self, visible: bool) -> None:
        if overlay_visibility_action(visible) == "show":
            self.overlay.deiconify()
        else:
            self.overlay.withdraw()

    def _pick_google_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), (self._t("所有檔案"), "*.*")])
        if path:
            self.vars["google_service_account_json"].set(path)
            self._save()

    def _pick_glossary_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), (self._t("所有檔案"), "*.*")])
        if path:
            self.vars["glossary_path"].set(path)
            self._save()

    def _pick_runtime_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=str(runtime_dir(self._config_from_vars())))
        if path:
            self.vars["runtime_dir"].set(path)
            self._save()
            self._refresh_runtime_status()

    def _pick_log_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.vars["log_dir"].get())
        if path:
            self.vars["log_dir"].set(path)
            self._save()

    def _open_runtime_dir(self) -> None:
        path = runtime_dir(self._config_from_vars())
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(path)])

    def _open_app_dir(self) -> None:
        path = APP_DIR
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(path)])

    def _open_glossary(self) -> None:
        self._save()
        path = ensure_glossary_file(Path(self.config.get("glossary_path") or APP_DIR / "glossary.json"))
        subprocess.Popen(["notepad", str(path)])

    def _add_glossary_term(self) -> None:
        self._save()
        source = simpledialog.askstring(self._t("新增術語"), self._t("原文"))
        if not source:
            return
        target = simpledialog.askstring(self._t("新增術語"), self._t("譯文"))
        if not target:
            return
        path = ensure_glossary_file(Path(self.config.get("glossary_path") or APP_DIR / "glossary.json"))
        add_glossary_term(path, source, target)
        self.status.set(self._t("詞彙已加入"))

    def _fix_last_translation(self) -> None:
        self._save()
        source = str(self.config.get("last_source_text") or "").strip()
        if not source:
            self.status.set(self._t("沒有可修正的近期翻譯"))
            return
        target = simpledialog.askstring(self._t("修正上次翻譯"), self._t("請輸入修正翻譯：\n{source}", source=source), initialvalue=str(self.config.get("last_translated_text") or ""))
        if not target:
            return
        if not messagebox.askyesno(self._t("加入術語表？"), self._t("是否將這個修正加入術語表？")):
            self.status.set(self._t("翻譯修正未加入詞彙表"))
            return
        path = ensure_glossary_file(Path(self.config.get("glossary_path") or APP_DIR / "glossary.json"))
        add_glossary_term(path, source, target)
        self.status.set(self._t("翻譯修正已加入詞彙表"))

    def _import_runtime(self) -> None:
        source = filedialog.askdirectory(title=self._t("選擇已解壓的 Faster-Whisper-XXL 資料夾"))
        if not source:
            return
        config = self._config_from_vars()
        self._submit(
            "正在匯入 runtime",
            lambda: self.runtime_service.import_runtime(Path(source), DEFAULT_RUNTIME_DIR, config.get("device", "auto"), config.get("compute_type", "auto")),
            self._runtime_imported,
            "runtime 匯入失敗",
        )

    def _runtime_imported(self, result: RuntimeImportResult) -> None:
        self.vars["runtime_dir"].set(str(result.target))
        self._save()
        if not result.status["ready"]:
            messagebox.showerror(self._t("runtime 不完整"), self._t("缺少：{missing}", missing=", ".join(result.status["missing"])))
            return
        self._refresh_lists()
        self.status.set(self._t("runtime 已匯入；commands.json 已更新"))

    def _refresh_runtime_status(self) -> None:
        config = self._config_from_vars()
        target = runtime_dir(config)
        self.runtime_text.set(self._t("正在檢查 runtime"))
        generation = self._runtime_check_generation + 1
        if self.controller.submit(
            lambda: self.runtime_service.check(target, config.get("device", "auto"), config.get("compute_type", "auto")),
            lambda result: self._runtime_status_ready(config, generation, result),
            name="runtime_status",
        ):
            self._runtime_check_generation = generation

    def _runtime_status_ready(self, config: dict, generation: int, result: TaskResult[RuntimeCheckResult]) -> None:
        if generation != self._runtime_check_generation:
            return
        if result.error is not None:
            self.runtime_text.set(self._t("runtime 檢查失敗：{error}", error=result.error))
            return
        check = result.value
        assert check is not None
        status = check.status
        if status["ready"]:
            self.config["last_ffmpeg_failed"] = check.ffmpeg_failed
            self.vars["last_ffmpeg_failed"].set(str(check.ffmpeg_failed))
            save_config_state(APP_DIR, self.config, {"last_ffmpeg_failed"})
            note = self._t("runtime 已就緒；CPU：{cpu}；CUDA：{cuda}", cpu=self._t("可用" if status["cpu_ready"] else "不可用"), cuda=self._t("可用" if status["cuda_ready"] else "不可用"))
            if not model_available(config["model"], self.repo_root / "_models", models_dir(config)):
                note += "; " + self._t("找不到模型：{model}", model=config["model"])
            self.runtime_text.set(note)
        elif not self.config.get("advanced_mode", False):
            self.runtime_text.set(self._t("runtime 尚未就緒；請按「一鍵診斷」查看安裝步驟"))
        else:
            self.runtime_text.set(self._t("runtime 尚未就緒；請開啟 runtime 資料夾或匯入 runtime") if self.config.get("app_language") == "en" else runtime_install_message(runtime_dir(config), config.get("device", "auto")))

    def _diagnostic_message(self, issues) -> str:
        config = self._config_from_vars()
        if not issues:
            return self._t("目前沒有發現需要處理的設定問題。")
        log_path = Path(config.get("log_dir") or APP_DIR / "logs") / "app.log"
        lines = []
        for issue in issues:
            lines.append(
                f"[{self._t(SEVERITY_LABELS.get(issue.severity, issue.severity))}]\n"
                f"{self._t('問題名稱：{title}', title=issue.title)}\n"
                f"{self._t('可能原因：{detail}', detail=issue.detail)}\n"
                f"{self._t('自動檢查結果：{code}', code=issue.code)}\n"
                f"{self._t('建議修復步驟：{fix}', fix=issue.fix)}\n"
                f"{self._t('一鍵修復按鈕：{action}', action=diagnostic_action_label(issue.action, self.config.get('app_language', 'zh-TW')))}\n"
                f"{self._t('進階日誌：{path}', path=log_path)}"
            )
        return "\n\n".join(lines)

    def _show_first_run_wizard(self) -> None:
        config = self._config_from_vars()
        self.controller.submit(
            lambda: self.audio_diagnostics.collect(config, self.repo_root),
            self._first_run_diagnostics_ready,
            name="first_run_diagnostics",
        )

    def _first_run_diagnostics_ready(self, result: TaskResult) -> None:
        if result.error is not None:
            self.status.set(self._t("首次診斷失敗：{error}", error=result.error))
            return
        issues = result.value
        action = first_run_setup_action(issues, bool(self.config.get("setup_guide_shown", False)))
        if action == "diagnostics":
            self._show_diagnostics(self._t("首次設定"), issues)
        elif action == "guide":
            self._optimize_settings()
            self._show_setup_guide()
            if "setup_guide_shown" in self.vars:
                self.vars["setup_guide_shown"].set("True")
            self.config["setup_guide_shown"] = True
            save_config(APP_DIR, self.config)

    def _run_diagnostics(self) -> None:
        config = self._config_from_vars()
        self._submit(
            self._t("正在執行診斷"),
            lambda: self.audio_diagnostics.collect(config, self.repo_root),
            lambda issues: self._show_diagnostics(self._t("診斷結果"), issues),
            self._t("診斷失敗"),
        )

    def _show_diagnostics(self, title: str, issues) -> None:
        message = self._diagnostic_message(issues)
        language = self.config.get("app_language", "zh-TW")
        actions = [(diagnostic_action_label(action, language), lambda name=action: self._run_diagnostic_action(name)) for action in diagnostic_actions(issues)]
        guide_label = self._t("設定指南")
        if not any(label == guide_label for label, _callback in actions):
            actions.append((guide_label, self._show_setup_guide))
        self._show_text_dialog(title, message, actions)

    def _show_text_dialog(self, title: str, message: str, actions: list[tuple[str, object]]) -> None:
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry(f"{self._px(760)}x{self._px(520)}")
        window.transient(self)
        window.grab_set()

        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title).pack(anchor="w")

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, pady=(8, 0))
        scrollbar = ttk.Scrollbar(body)
        scrollbar.pack(side="right", fill="y")
        text = tk.Text(body, wrap="word", yscrollcommand=scrollbar.set, height=20, background=THEME["control"], foreground=THEME["text"], insertbackground=THEME["text"], selectbackground=THEME["accent"], font=UI_FONT, relief="flat", padx=self._px(10), pady=self._px(8))
        text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=text.yview)
        text.insert("1.0", message)
        text.configure(state="disabled")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        for column in range(3):
            buttons.grid_columnconfigure(column, weight=1)
        for index, (label, callback) in enumerate(actions):
            ttk.Button(buttons, text=label, command=lambda cb=callback, dialog=window: self._run_dialog_action(dialog, cb)).grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=3)
        close_index = len(actions)
        ttk.Button(buttons, text=self._t("關閉視窗"), command=window.destroy).grid(row=close_index // 3, column=close_index % 3, sticky="ew", padx=3, pady=3)

    def _run_dialog_action(self, window: tk.Toplevel, callback) -> None:
        window.destroy()
        callback()

    def _run_diagnostic_action(self, action: str) -> None:
        if action == "open_runtime":
            webbrowser.open(UPSTREAM_RUNTIME_RELEASE_URL)
        elif action == "download_model":
            self._download_model()
        elif action == "download_translation_models":
            self._download_translation_models()
        elif action == "audio_settings":
            self._show_setup_guide()
        elif action == "optimize_settings":
            self._optimize_settings()
        elif action == "language_settings":
            self._lock_language()
        elif action == "local_translation":
            self._troubleshoot("local_translation")
        elif action == "api_settings":
            self._test_api()
        elif action == "open_logs":
            self._open_logs()

    def _lock_language(self) -> None:
        locked = language_lock_value(
            self.vars["source_language"].get(),
            self.config.get("last_detected_language", ""),
            self.vars["target_language"].get(),
        )
        if locked == self.vars["source_language"].get():
            self.status.set(self._t("沒有偵測到可鎖定的語言"))
            return
        self.vars["source_language"].set(locked)
        self._save()
        self.status.set(self._t("來源語言已鎖定：{language}", language=locked))

    def _check_updates(self) -> None:
        self._submit(
            "正在檢查更新",
            lambda: self.update_service.check(self.repo_root, self.config.get("app_language", "zh-TW")),
            self.status.set,
            "更新檢查失敗",
        )

    def _show_setup_guide(self) -> None:
        action_map = {
            "一鍵診斷": self._run_diagnostics,
            "套用場景": self._apply_scenario,
            "自動優化": self._optimize_settings,
            "測試喇叭": self._test_speaker,
            "測試麥克風": self._test_mic,
            "測試虛擬麥克風": self._test_virtual_mic,
            "測試 TTS": self._test_tts,
        }
        actions = [(self._t(label), action_map[label]) for label in setup_guide_actions()]
        self._show_text_dialog(self._t("設定指南"), setup_guide_message(self.config.get("app_language", "zh-TW")), actions)

    def _recommend(self) -> None:
        config = self._config_from_vars()
        target = runtime_dir(config)
        self._submit(
            "正在偵測硬體",
            lambda: self.runtime_service.check(target, "auto", config.get("compute_type", "auto"), verify_hashes=True),
            lambda result: self._recommend_ready(config, result),
            "硬體偵測失敗",
        )

    def _recommend_ready(self, config: dict, result: RuntimeCheckResult) -> None:
        status = result.status
        if not status["ready"]:
            self.status.set(self._t("找不到 runtime：{missing}", missing=", ".join(status["missing"])))
            self.vars["model"].set("medium")
            return
        devices, vram_gb = cuda_hardware_from_check_output(status["cuda_probe_output"]) if status["cuda_ready"] else (0, 0)
        config["last_cuda_devices"] = devices
        config["last_vram_gb"] = vram_gb
        self.config.update({"last_cuda_devices": devices, "last_vram_gb": vram_gb})
        save_config_state(APP_DIR, config, {"last_cuda_devices", "last_vram_gb"})
        self.vars["last_cuda_devices"].set(str(devices))
        self.vars["last_vram_gb"].set(str(vram_gb))
        prefer_quality = self.vars["performance_mode"].get() == "quality"
        self.vars["model"].set(recommend_model(devices, vram_gb, prefer_quality))
        self._apply_performance_mode()

    def _apply_scenario(self) -> None:
        config = self._config_from_vars()
        try:
            updated = apply_scenario(config, config["scenario"])
        except ValueError as exc:
            self.status.set(translate_known(self.config.get("app_language"), str(exc)))
            return
        self._load_config_into_widgets(updated)
        self._save()
        self.status.set(self._t("已套用場景：{scenario}", scenario=self._t(scenario_label(updated["scenario"]))))

    def _load_config_into_widgets(self, updated: dict) -> None:
        for key, variable in self.vars.items():
            if key in updated:
                value = self._device_label_by_id.get(str(updated[key]), self._t("系統預設")) if key in AUDIO_DEVICE_KEYS else self._t(scenario_label(str(updated[key]))) if key == "scenario" else str(updated[key])
                variable.set(value)
        self.overlay_visible.set(bool(updated.get("overlay_visible", self.overlay_visible.get())))
        self.overlay_topmost.set(bool(updated.get("overlay_topmost", self.overlay_topmost.get())))
        self.show_language_labels.set(bool(updated.get("show_language_labels", self.show_language_labels.get())))
        self.show_original_text.set(bool(updated.get("show_original_text", self.show_original_text.get())))
        self.show_translated_text.set(bool(updated.get("show_translated_text", self.show_translated_text.get())))
        self.tts_enabled.set(bool(updated.get("tts_enabled", self.tts_enabled.get())))
        self.speaker_tts_enabled.set(bool(updated.get("speaker_tts_enabled", self.speaker_tts_enabled.get())))
        self.start_virtual_mic_muted.set(bool(updated.get("start_virtual_mic_muted", self.start_virtual_mic_muted.get())))
        self.virtual_mic_enabled.set(bool(updated.get("virtual_mic_enabled", self.virtual_mic_enabled.get())))
        self.speaker_enabled.set(bool(updated.get("speaker_enabled", self.speaker_enabled.get())))
        self.microphone_enabled.set(bool(updated.get("microphone_enabled", self.microphone_enabled.get())))
        self.record_logs.set(bool(updated.get("record_logs", self.record_logs.get())))

    def _optimize_settings(self) -> None:
        before = self._config_from_vars()
        target = runtime_dir(before)

        def work():
            check = self.runtime_service.check(target, "cuda", before.get("compute_type", "auto"), verify_hashes=True)
            devices, vram_gb = cuda_hardware_from_check_output(check.status["cuda_probe_output"]) if check.status["cuda_ready"] else (0, 0)
            return plan_session(before, self.repo_root, devices, vram_gb), devices, vram_gb

        self._submit("正在分析設定", work, lambda result: self._optimization_ready(before, result), "自動優化失敗")

    def _optimization_ready(self, before: dict, result) -> None:
        decision, devices, vram_gb = result
        before.update({"last_cuda_devices": devices, "last_vram_gb": vram_gb})
        self.config.update({"last_cuda_devices": devices, "last_vram_gb": vram_gb})
        save_config_state(APP_DIR, before, {"last_cuda_devices", "last_vram_gb"})
        if not decision.recommendations:
            self.status.set(self._t("設定已是建議值"))
            return
        preview = self._t("套用 {count} 項自動優化建議？", count=len(decision.recommendations)) if self.config.get("app_language") == "en" else format_tuning_preview(before, decision.config, decision.recommendations)
        if not messagebox.askyesno(self._t("預覽自動優化"), preview):
            self.status.set(self._t("已保留原設定"))
            return
        self._load_config_into_widgets(decision.config)
        self._save()
        self.status.set(self._t("已套用 {count} 項自動優化建議", count=len(decision.recommendations)) if self.config.get("app_language") == "en" else decision.summary)

    def _download_model(self) -> None:
        if self.__dict__.get("_model_download_cancel") is not None:
            self.status.set(self._t("模型下載已在進行；可按「取消模型下載」"))
            return
        self._save()
        model = self.config["model"]
        app_models = models_dir(self.config)
        cancel = threading.Event()
        self._model_download_cancel = cancel
        self.status.set(self._t("正在下載模型 {model}", model=model))

        if not self.controller.submit(
            lambda: self.model_service.download(model, app_models, lambda message: self._post_ui("status", message), cancel, self.config.get("app_language", "zh-TW")),
            self._model_downloaded,
            name="model_download",
        ):
            self._model_download_cancel = None
            self.status.set(self._t("前一個模型下載仍在結束中"))

    def _model_downloaded(self, result: TaskResult[Path]) -> None:
        self._model_download_cancel = None
        if result.error is not None:
            self.status.set(str(result.error))
        else:
            self.status.set(self._t("模型下載完成：{model}；版本與 SHA 完整性已驗證", model=result.value.name))
        self._refresh_lists()

    def _cancel_model_download(self) -> None:
        cancel = self.__dict__.get("_model_download_cancel")
        if cancel is None:
            self.status.set(self._t("目前沒有模型下載工作"))
            return
        cancel.set()
        self.status.set(self._t("正在取消模型下載；已下載部分可供稍後續傳"))

    def _download_translation_models(self) -> None:
        self._save()
        source_language = self.config["source_language"]
        target_language = self.config["target_language"]
        if source_language == "auto":
            messagebox.showinfo(self._t("請先選擇來源語言"), self._t("下載離線翻譯模型前，請把「來源語言」改成固定語言。"))
            return
        if not messagebox.askyesno(
            self._t("下載離線翻譯模型"),
            self._t("將下載 {source} 與 {target} 的雙向 Argos Translate 模型。\n模型會儲存在程式資料夾的 models\\translation。", source=source_language, target=target_language),
        ):
            return
        self.status.set(self._t("正在下載離線翻譯模型"))

        config = self.config.copy()
        registry = self.engine.offline_translation_registry if self.engine else None
        self._submit(
            "正在下載離線翻譯模型",
            lambda: self.model_service.download_translation(config, source_language, target_language, registry),
            lambda downloaded: self.status.set(self._t("離線翻譯模型下載完成：{count} 個", count=len(downloaded))),
            "離線翻譯模型下載失敗",
            task_name="translation_model_download",
        )

    def _refresh_commands(self) -> None:
        runtime = runtime_dir(self._config_from_vars())
        self._submit(
            "正在更新 commands.json",
            lambda: self.runtime_service.refresh_commands(runtime),
            lambda _value: (self._refresh_lists(), self.status.set(self._t("commands.json 已更新"))),
            "commands.json 更新失敗",
        )

    def _test_api(self) -> None:
        self._save()
        config = self.config.copy()
        self._submit("正在測試 API", lambda: self.audio_diagnostics.test_api(config), self.status.set, "API 測試失敗")

    def _test_tone(self) -> None:
        config = self._config_from_vars()
        self._submit("正在測試裝置音", lambda: self.audio_diagnostics.test_tone(config), lambda _value: self.status.set(self._t("裝置音測試完成")), "裝置音測試失敗")

    def _test_tts(self) -> None:
        config = self._config_from_vars()
        self.status.set(self._t("正在測試 TTS"))
        if not self.controller.submit(lambda: self.audio_diagnostics.test_tts(config), lambda result: self._tts_tested(config, result), name="tts_test"):
            self.status.set(self._t("TTS 測試已在進行"))

    def _tts_tested(self, config: dict, result: TaskResult) -> None:
        self.config["last_tts_failed"] = result.error is not None
        save_config_state(APP_DIR, self.config, {"last_tts_failed"})
        if result.error is not None:
            messagebox.showerror(self._t("TTS 測試失敗"), str(result.error))
        else:
            self.status.set(self._t("TTS 輸出測試完成"))

    def _test_virtual_mic(self) -> None:
        config = self._config_from_vars()
        self.status.set(self._t("正在測試虛擬麥克風"))
        if not self.controller.submit(lambda: self.audio_diagnostics.test_virtual_microphone(config), lambda result: self._virtual_mic_tested(config, result), name="virtual_mic_test"):
            self.status.set(self._t("虛擬麥克風測試已在進行"))

    def _virtual_mic_tested(self, config: dict, result: TaskResult[bool]) -> None:
        active = bool(result.value) if result.error is None else False
        self.config["last_virtual_mic_failed"] = not active
        save_config_state(APP_DIR, self.config, {"last_virtual_mic_failed"})
        if result.error is not None:
            messagebox.showerror(self._t("虛擬麥克風測試失敗"), str(result.error))
        else:
            self.status.set(self._t("虛擬麥克風已偵測到聲音" if active else "虛擬麥克風沒有偵測到聲音"))

    def _test_speaker(self) -> None:
        config = self._config_from_vars()
        self.status.set(self._t("正在測試喇叭"))
        if not self.controller.submit(lambda: self.audio_diagnostics.test_speaker(config), lambda result: self._speaker_tested(config, result), name="speaker_test"):
            self.status.set(self._t("喇叭測試已在進行"))

    def _speaker_tested(self, config: dict, result: TaskResult[bool]) -> None:
        if result.error is not None:
            messagebox.showerror(self._t("喇叭測試失敗"), str(result.error))
            return
        active = bool(result.value)
        self.config["last_speaker_quiet"] = not active
        save_config_state(APP_DIR, self.config, {"last_speaker_quiet"})
        self.status.set(self._t("喇叭已偵測到聲音" if active else "喇叭目前沒有偵測到聲音"))

    def _test_mic(self) -> None:
        config = self._config_from_vars()
        self._submit(
            "正在測試麥克風",
            lambda: self.audio_diagnostics.test_microphone(config),
            lambda level: self._microphone_tested(config, level),
            "麥克風測試失敗",
        )

    def _microphone_tested(self, config: dict, level: float) -> None:
        self.config["last_mic_quiet"] = level < float(config["speech_threshold"])
        save_config_state(APP_DIR, self.config, {"last_mic_quiet"})
        self.status.set(self._t("麥克風音量 {level:.4f}", level=level))

    def _test_subtitles(self) -> None:
        self.overlay_visible.set(True)
        self._apply_overlay()
        self.overlay.update_lines("字幕測試", "字幕測試")
        self.status.set(self._t("字幕測試完成"))

    def _troubleshoot(self, issue: str) -> None:
        action, target = troubleshooting_action(issue)
        if action == "overlay":
            self.overlay_visible.set(True)
            self._apply_overlay()
            self.status.set(self._t("字幕已顯示"))
            return
        if target.startswith("ms-settings:"):
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        else:
            webbrowser.open(target)
        self.status.set(self._t("已開啟修復說明"))

    def _start(self) -> None:
        if not self._save():
            return
        config = self.config.copy()
        target = runtime_dir(config)
        app_models = models_dir(config)
        self._submit(
            "正在驗證 runtime 與模型完整性（約 10–30 秒，可按停止取消）",
            lambda: (
                self.runtime_service.check(target, config.get("device", "auto"), config.get("compute_type", "auto"), verify_hashes=True),
                self.model_service.status(config["model"], self.repo_root / "_models", app_models),
            ),
            lambda result: self._start_checked(config, app_models, result),
            "啟動檢查失敗",
            task_name="start_check",
        )

    def _start_checked(self, config: dict, app_models: Path, result: tuple[RuntimeCheckResult, str]) -> None:
        if self._config_from_vars() != config:
            self.status.set(self._t("設定已在啟動檢查期間變更；請重新按開始"))
            return
        runtime_check, current_model_status = result
        status = runtime_check.status
        if not status["ready"]:
            append_app_log(APP_DIR, "runtime_missing", missing=status["missing"])
            error = self._t("找不到 runtime：{missing}", missing=", ".join(status["missing"]))
            if "CUDA --checkcuda probe" in status["missing"]:
                error += "；CUDA probe：" + status["cuda_probe_output"]
            install_message = self._t("runtime 尚未就緒；請開啟 runtime 資料夾或匯入 runtime") if self.config.get("app_language") == "en" else runtime_install_message(runtime_dir(self.config), self.config.get("device", "auto"))
            self._show_text_dialog(self._t("runtime 不可用"), error + "\n\n" + install_message, [
                (self._t("匯入已解壓 runtime"), self._import_runtime),
                (self._t("下載上游 runtime"), lambda: webbrowser.open(UPSTREAM_RUNTIME_RELEASE_URL)),
                (self._t("一鍵診斷"), self._run_diagnostics),
            ])
            self._set_last_error(error)
            self.status.set(error)
            return
        if current_model_status != MODEL_READY:
            corrupt = current_model_status == MODEL_INVALID
            title = self._t("模型不完整或損毀" if corrupt else "找不到模型")
            append_app_log(APP_DIR, "model_corrupt" if corrupt else "model_missing", model=config["model"])
            message = self._t("請下載或重新安裝模型：{model}", model=config["model"]) if self.config.get("app_language") == "en" else model_install_message(config["model"], app_models)
            self._show_text_dialog(title, message, [(self._t("下載模型"), self._download_model), (self._t("一鍵診斷"), self._run_diagnostics)])
            error = f"{title}：{config['model']}"
            self._set_last_error(error)
            self.status.set(error)
            return
        devices, vram_gb = cuda_hardware_from_check_output(status["cuda_probe_output"]) if status["cuda_ready"] else (0, 0)
        config.update({"last_cuda_devices": devices, "last_vram_gb": vram_gb})
        self.config.update({"last_cuda_devices": devices, "last_vram_gb": vram_gb})
        save_config_state(APP_DIR, config, {"last_cuda_devices", "last_vram_gb"})
        recommendations = recommend_tuning(config, devices, vram_gb) if config.get("ai_auto_optimize", True) else []
        if recommendations:
            self.status.set(self._t("自動優化有 {count} 項建議；請按「自動優化」預覽並確認", count=len(recommendations)))
        self._set_last_error("")
        append_app_log(APP_DIR, "start", model=config["model"], provider=config["provider"])
        engine = RealtimeEngine(
            self.repo_root,
            config,
            lambda speaker, mine: self._post_ui("overlay", speaker, mine, engine=engine),
            lambda message: self._post_ui("status", message, engine=engine),
            APP_DIR,
        )
        if not self.controller.start_engine(engine, self._engine_started):
            self.status.set(self._t("啟動工作已在進行"))
        else:
            self._session_config = config.copy()

    def _engine_started(self, result: TaskResult) -> None:
        self._update_restart_notice()
        if result.error is not None:
            self._engine_status(self._t("啟動失敗：{error}", error=result.error))

    def _stop(self) -> None:
        cancelled = self.controller.cancel("start_check")
        if cancelled and self.engine is None:
            self.status.set(self._t("已停止"))
        self._push_to_talk(False)
        if self.controller.stop_engine(self._engine_stopped) is False:
            self.status.set(self._t("停止工作已在進行"))

    def _engine_stopped(self, result: TaskResult[str]) -> None:
        self._update_restart_notice()
        cancelled_close = self._closing and (result.error is not None or self.engine is not None)
        if cancelled_close:
            self._closing = False
        try:
            if result.error is not None:
                self._engine_status(self._t("停止失敗：{error}", error=result.error))
            elif result.value:
                self._engine_status(result.value)
        finally:
            if cancelled_close:
                self.status.set(self._t("背景工作尚未完成，已取消關閉；請稍後按停止重試。{reason}", reason=result.error or result.value or ""))
        append_app_log(APP_DIR, "stop")

    def _quit(self) -> None:
        self._closing = True
        self.controller.cancel()
        cancel = self.__dict__.get("_model_download_cancel")
        if cancel is not None:
            cancel.set()
        self._stop()
        self.after(50, self._destroy_when_stopped)

    def _destroy_when_stopped(self) -> None:
        if not self._closing:
            return
        if self.engine is None and not self.controller.busy:
            self.destroy()
        else:
            self.after(50, self._destroy_when_stopped)

    def _toggle_pause(self) -> None:
        if self.engine:
            self.engine.set_paused(not self.engine.paused)
            self._task_state_changed(TaskState("pause", "completed"))

    def _toggle_virtual_mic_mute(self) -> None:
        if self.engine:
            self.engine.set_virtual_mic_muted(not self.engine.virtual_mic_muted)
            self._task_state_changed(TaskState("virtual_mic_mute", "completed"))

    def _toggle_speaker_translation_mute(self) -> None:
        if self.engine:
            self.engine.set_speaker_translation_muted(not self.engine.speaker_translation_muted)
            self._task_state_changed(TaskState("speaker_translation_mute", "completed"))

    def _push_to_talk(self, active: bool) -> None:
        if self.engine:
            if active:
                if self.__dict__.get("_push_to_talk_previous_virtual_mic_muted") is not None:
                    return
                self._push_to_talk_previous_virtual_mic_muted = self.engine.virtual_mic_muted
                self.engine.set_virtual_mic_muted(False)
            elif self.__dict__.get("_push_to_talk_previous_virtual_mic_muted") is not None:
                self.engine.set_virtual_mic_muted(self._push_to_talk_previous_virtual_mic_muted)
                self._push_to_talk_previous_virtual_mic_muted = None

    def _clear_cache(self) -> None:
        self._save()
        clear_cache(APP_DIR, Path(self.config.get("translation_cache_path") or APP_DIR / "cache" / "translation_cache.db"))
        self.status.set(self._t("快取已清除"))

    def _open_logs(self) -> None:
        self._save()
        path = Path(self.config.get("log_dir") or APP_DIR / "logs")
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(path)])

    def _export_subtitles(self) -> None:
        if not self._save():
            return
        log_dir = Path(self.config.get("log_dir") or APP_DIR / "logs")
        selected = filedialog.askopenfilename(title=self._t("選擇要匯出的對話紀錄"), initialdir=log_dir, filetypes=((self._t("對話紀錄"), "*.jsonl"),))
        if not selected:
            return
        source = Path(selected)
        output_dir = APP_DIR / "exports" / "subtitles"
        self._submit("正在匯出字幕", lambda: export_subtitles(source, output_dir), lambda paths: self._subtitles_exported(source, paths), "字幕匯出失敗", task_name="subtitle_export")

    def _subtitles_exported(self, source: Path, paths: tuple[Path, Path]) -> None:
        srt, txt = paths
        self.status.set(self._t("字幕已匯出：{path}", path=srt))
        append_app_log(APP_DIR, "subtitle_export", source=source, output=srt, text_output=txt)

    def _open_exports(self) -> None:
        path = APP_DIR / "exports" / "subtitles"
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(path)])

    def _clear_logs(self) -> None:
        target = self._confirm_log_cleanup()
        if target is None:
            return
        clear_logs(APP_DIR, target)
        self.status.set(self._t("紀錄已清除"))

    def _clear_local_data(self) -> None:
        target = self._confirm_log_cleanup()
        if target is None:
            return
        clear_cache(APP_DIR, Path(self.config.get("translation_cache_path") or APP_DIR / "cache" / "translation_cache.db"))
        clear_logs(APP_DIR, target)
        self.status.set(self._t("本機快取與紀錄已清除"))

    def _confirm_log_cleanup(self) -> Path | None:
        self._save()
        target = Path(self.config.get("log_dir") or APP_DIR / "logs")
        try:
            files = log_files_to_clear(APP_DIR, target)
        except ValueError as exc:
            messagebox.showerror(self._t("無法清除紀錄"), str(exc))
            return None
        if not target.resolve().is_relative_to(APP_DIR.resolve()) and not messagebox.askyesno(
            self._t("確認清除外部紀錄"),
            self._t("將從以下資料夾清除 {count} 個本程式紀錄檔：\n{path}\n\n其他檔案與子資料夾不會刪除。是否繼續？", count=sum(path.parent == target.resolve() for path in files), path=target.resolve()),
        ):
            return None
        return target

    def _overlay_update(self, speaker: str, mine: str) -> None:
        if self.engine and not subtitle_updates_allowed(self.engine.paused):
            return
        self.overlay_generation += 1
        self._latest_subtitle = overlay_clipboard_text(speaker, mine)
        preview = self.__dict__.get("_preview_text")
        if preview is not None:
            preview.set(self._latest_subtitle)
        generation = self.overlay_generation
        self.after(0, self.overlay.update_lines, speaker, mine)
        hold_ms = int(overlay_hold_seconds_value(self.config.get("overlay_hold_seconds", 8.0)) * 1000)
        self.after(hold_ms, self._clear_overlay_if_current, generation)

    def _clear_overlay_if_current(self, generation: int) -> None:
        if generation == self.overlay_generation:
            self.overlay.clear_lines()


def main() -> None:
    try:
        import ctypes

        # System DPI awareness gives crisp text instead of Windows bitmap scaling; sizes are scaled via _px.
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    TranslatorApp().mainloop()
