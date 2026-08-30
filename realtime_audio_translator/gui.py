import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Literal

from .app_controller import AppController, TaskResult
from .app_services import AudioDiagnosticsService, ModelService, RuntimeCheckResult, RuntimeImportResult, RuntimeService, UpdateService
from .audio import DeviceResolutionError, device_descriptor, device_identity, find_device, format_device_label, list_audio_devices
from .ai_auto_tuner import format_tuning_preview, recommend_tuning
from .ai_memory import add_glossary_term
from .ai_orchestrator import plan_session
from .app_log import append_app_log
from .commands import command_choices
from .config import APP_DIR, clear_cache, clear_logs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config, save_config_state, validate_language_pair
from .engine import RealtimeEngine
from .logbook import conversation_log_usage
from .models import MODEL_INVALID, MODEL_READY, cuda_hardware_from_check_output, list_models, model_available, model_install_message, models_dir, recommend_model
from .paths import resource_root
from .runtime import DEFAULT_RUNTIME_DIR, UPSTREAM_RUNTIME_RELEASE_URL, runtime_dir, runtime_install_message, runtime_status
from .scenarios import SCENARIO_CHOICES, apply_scenario, scenario_key, scenario_label
from .subtitle_export import export_jsonl_to_srt, export_jsonl_to_txt
from .tts import list_windows_sapi_voices


LANGUAGE_CHOICES = ("auto", "zh", "en", "ja", "ko")
TARGET_LANGUAGE_CHOICES = ("zh", "en", "ja", "ko")
PROVIDER_CHOICES = ("local", "google", "openai")
TTS_PROVIDER_CHOICES = ("local", "google", "openai")
PERFORMANCE_CHOICES = ("low_latency", "balanced", "quality", "offline_light")
CLOUD_PROVIDERS = ("google", "openai")
AUDIO_DEVICE_KEYS = ("speaker_device", "microphone_device", "tts_output_device", "virtual_mic_input_device", "speaker_tts_output_device")
SEVERITY_LABELS = {"error": "錯誤", "warning": "警告", "info": "提示"}


@dataclass(frozen=True)
class UiEvent:
    kind: Literal["status", "overlay", "callback"]
    args: tuple
    engine: RealtimeEngine | None = None


SETTING_ROWS = (
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
    "source_language",
    "target_language",
    "scenario",
    "speaker_device",
    "microphone_device",
    "tts_output_device",
}
ADVANCED_SETTING_KEYS = {key for _label, key in SETTING_ROWS} - BASIC_SETTING_KEYS
BASIC_BUTTON_TEXTS = {
    "設定精靈",
    "一鍵診斷",
    "開始",
    "停止",
    "測試麥克風",
    "測試虛擬麥克風",
}
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
    return any(marker in message for marker in ("找不到", "失敗", "沒有可用音訊裝置"))


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


def toggle_overlay_visibility(visible: bool) -> bool:
    return not visible


def toggle_speech_enabled(enabled: bool) -> bool:
    return not enabled


def toggle_source_enabled(enabled: bool) -> bool:
    return not enabled


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


def diagnostic_action_label(action: str) -> str:
    return {
        "open_runtime": "開啟 runtime 資料夾 / 下載 runtime",
        "download_model": "下載模型",
        "download_translation_models": "下載離線翻譯模型",
        "audio_settings": "測試喇叭 / 測試麥克風 / 測試虛擬麥克風",
        "api_settings": "測試 API",
        "local_translation": "修復本機翻譯",
        "optimize_settings": "自動優化",
        "language_settings": "來源語言",
        "open_logs": "開啟紀錄",
    }.get(action, action)


def setup_guide_message() -> str:
    return (
        "1. 按「一鍵診斷」處理 runtime，進階模式也可手動匯入 runtime。\n"
        "2. 按「一鍵診斷」下載模型，或把模型 zip 解壓到 models 資料夾。\n"
        "3. 選擇「喇叭來源」、「麥克風來源」與「TTS 輸出」。\n"
        "4. 本工具「TTS 輸出」選虛擬音訊線輸出，通話軟體麥克風選對應的輸入。\n"
        "5. 選場景會自動套用；進階模式可調模型、runtime 路徑與自動優化。\n"
        "6. 開始前先跑「測試麥克風」與「測試虛擬麥克風」；進階模式可再測字幕、喇叭與 TTS。"
    )


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
    def __init__(self, master: tk.Tk, topmost: bool, opacity: float, font_size: int):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", topmost)
        self.attributes("-alpha", opacity)
        self.configure(bg="#111111")
        self.geometry("900x96+240+820")
        self.speaker = tk.StringVar(value="")
        self.mine = tk.StringVar(value="")
        self._drag = (0, 0)
        self.labels = []
        for row, variable in enumerate((self.speaker, self.mine)):
            label = tk.Label(self, textvariable=variable, fg="#f5f5f5", bg="#111111", font=("Microsoft JhengHei UI", font_size), anchor="w")
            label.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
            self.labels.append(label)
        self.grid_columnconfigure(0, weight=1)
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._drag_to)

    def _start_drag(self, event):
        self._drag = (event.x, event.y)

    def _drag_to(self, event):
        self.geometry(f"+{self.winfo_x() + event.x - self._drag[0]}+{self.winfo_y() + event.y - self._drag[1]}")

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
            label.configure(font=("Microsoft JhengHei UI", font_size))


class TranslatorApp(tk.Tk):
    def __init__(self, repo_root: Path | None = None):
        super().__init__()
        self.repo_root = repo_root or resource_root()
        self.config = load_config(APP_DIR)
        self._log_consent_granted = False
        self._model_download_cancel: threading.Event | None = None
        self._device_id_by_label: dict[str, str] = {"系統預設": ""}
        self._device_label_by_id: dict[str, str] = {"": "系統預設"}
        self._engine_events = queue.Queue()
        self.controller = AppController(self._post_ui)
        self.runtime_service = RuntimeService(APP_DIR / "commands.json")
        self.model_service = ModelService()
        self.audio_diagnostics = AudioDiagnosticsService(APP_DIR / "cache" / "audio")
        self.update_service = UpdateService()
        self._list_generation = 0
        self._runtime_check_generation = 0
        self._closing = False
        self.title("Realtime Audio Translator")
        self.geometry("900x680")
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.status = tk.StringVar(value="就緒")
        self.runtime_text = tk.StringVar(value="")
        self.mode_text = tk.StringVar(value=self._mode_text())
        self.overlay_generation = 0
        self._push_to_talk_previous_virtual_mic_muted = None
        self.overlay = Overlay(
            self,
            self.config["overlay_topmost"],
            overlay_opacity_value(self.config.get("overlay_opacity", 0.86)),
            overlay_font_size_value(self.config.get("overlay_font_size", 18)),
        )
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

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self.vars = {key: tk.StringVar(value=str(value)) for key, value in self.config.items()}
        self.vars["scenario"].set(scenario_label(self.config["scenario"]))
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
        self.comboboxes: dict[str, ttk.Combobox] = {}
        self.setting_widgets: dict[str, list[tk.Widget]] = {}

        for row, (label, key) in enumerate(SETTING_ROWS):
            row_widgets: list[tk.Widget] = []
            label_widget = ttk.Label(frame, text=label)
            label_widget.grid(row=row, column=0, sticky="w", pady=4)
            row_widgets.append(label_widget)
            if key in ("source_language", "target_language"):
                values = LANGUAGE_CHOICES if key == "source_language" else TARGET_LANGUAGE_CHOICES
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=values)
                widget.bind("<<ComboboxSelected>>", lambda _event: self._save())
            elif key in ("provider", "tts_provider", "performance_mode", "scenario", "conversation_log_content"):
                values = tuple(scenario_label(key) for key in SCENARIO_CHOICES) if key == "scenario" else ("both", "original", "translation", "none") if key == "conversation_log_content" else PERFORMANCE_CHOICES if key == "performance_mode" else TTS_PROVIDER_CHOICES if key == "tts_provider" else PROVIDER_CHOICES
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=values, state="readonly")
                widget.bind("<<ComboboxSelected>>", lambda _event, name=key: self._apply_performance_mode() if name == "performance_mode" else self._apply_scenario() if name == "scenario" else self._save())
            elif key in AUDIO_DEVICE_KEYS or key in ("model", "device", "compute_type", "tts_voice_name"):
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=[], state="readonly" if key in AUDIO_DEVICE_KEYS else "normal")
                widget.bind("<<ComboboxSelected>>", lambda _event: self._save())
                self.comboboxes[key] = widget
            else:
                widget = ttk.Entry(frame, textvariable=self.vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=4, padx=8)
            row_widgets.append(widget)
            if key == "google_service_account_json":
                button = ttk.Button(frame, text="選擇", command=self._pick_google_json)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "glossary_path":
                button = ttk.Button(frame, text="選擇", command=self._pick_glossary_json)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key in ("overlay_opacity", "overlay_font_size", "overlay_hold_seconds"):
                button = ttk.Button(frame, text="套用", command=self._apply_overlay)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "runtime_dir":
                button = ttk.Button(frame, text="選擇", command=self._pick_runtime_dir)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "log_dir":
                button = ttk.Button(frame, text="選擇", command=self._pick_log_dir)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "tts_voice_name":
                button = ttk.Button(frame, text="列出", command=self._list_tts_voices)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            self.setting_widgets[key] = row_widgets

        next_row = len(SETTING_ROWS)
        ttk.Label(frame, textvariable=self.runtime_text, foreground="#a94442").grid(row=next_row, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(frame, textvariable=self.mode_text, foreground="#7a4b00").grid(row=next_row + 1, column=0, columnspan=3, sticky="ew", pady=4)

        runtime_buttons_widget = ttk.Frame(frame)
        runtime_buttons_widget.grid(row=next_row + 2, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Button(runtime_buttons_widget, text="開啟 runtime 資料夾", command=self._open_runtime_dir).pack(side="left", padx=3)
        ttk.Button(runtime_buttons_widget, text="匯入已解壓 runtime", command=self._import_runtime).pack(side="left", padx=3)
        ttk.Button(runtime_buttons_widget, text="下載上游 runtime", command=lambda: webbrowser.open(UPSTREAM_RUNTIME_RELEASE_URL)).pack(side="left", padx=3)

        ttk.Checkbutton(frame, text="顯示字幕", variable=self.overlay_visible, command=self._apply_overlay).grid(row=next_row + 3, column=0, sticky="w")
        overlay_topmost_widget = ttk.Checkbutton(frame, text="字幕最上層", variable=self.overlay_topmost, command=self._apply_overlay)
        overlay_topmost_widget.grid(row=next_row + 3, column=1, sticky="w")
        language_labels_widget = ttk.Checkbutton(frame, text="顯示語言", variable=self.show_language_labels, command=self._save)
        language_labels_widget.grid(row=next_row + 3, column=2, sticky="w")
        original_text_widget = ttk.Checkbutton(frame, text="顯示原文", variable=self.show_original_text, command=self._save)
        original_text_widget.grid(row=next_row + 4, column=0, sticky="w")
        translated_text_widget = ttk.Checkbutton(frame, text="顯示譯文", variable=self.show_translated_text, command=self._save)
        translated_text_widget.grid(row=next_row + 4, column=1, sticky="w")
        ttk.Checkbutton(frame, text="播放翻譯語音", variable=self.tts_enabled, command=self._save).grid(row=next_row + 4, column=2, sticky="w")
        speaker_capture_widget = ttk.Checkbutton(frame, text="擷取喇叭", variable=self.speaker_enabled, command=self._save)
        speaker_capture_widget.grid(row=next_row + 5, column=0, sticky="w")
        microphone_capture_widget = ttk.Checkbutton(frame, text="擷取麥克風", variable=self.microphone_enabled, command=self._save)
        microphone_capture_widget.grid(row=next_row + 5, column=1, sticky="w")
        ttk.Checkbutton(frame, text="進階設定", variable=self.advanced_mode, command=self._apply_mode).grid(row=next_row + 5, column=2, sticky="w")
        record_logs_widget = ttk.Checkbutton(frame, text="儲存對話紀錄", variable=self.record_logs, command=self._save)
        record_logs_widget.grid(row=next_row + 6, column=0, sticky="w")
        speaker_tts_widget = ttk.Checkbutton(frame, text="播放對方翻譯", variable=self.speaker_tts_enabled, command=self._save)
        speaker_tts_widget.grid(row=next_row + 6, column=2, sticky="w")
        start_virtual_mic_muted_widget = ttk.Checkbutton(frame, text="虛擬麥克風啟動時靜音", variable=self.start_virtual_mic_muted, command=self._save)
        start_virtual_mic_muted_widget.grid(row=next_row + 7, column=0, sticky="w")
        self.advanced_mode_widgets = [runtime_buttons_widget, overlay_topmost_widget, language_labels_widget, original_text_widget, translated_text_widget, speaker_capture_widget, microphone_capture_widget, record_logs_widget, speaker_tts_widget, start_virtual_mic_muted_widget]
        ttk.Checkbutton(frame, text="輸出到虛擬麥克風", variable=self.virtual_mic_enabled, command=self._save).grid(row=next_row + 6, column=1, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=next_row + 8, column=0, columnspan=3, sticky="ew", pady=12)
        self.button_widgets: list[tuple[str, ttk.Button]] = []
        def copy_overlay() -> None:
            text = overlay_clipboard_text(self.overlay.speaker.get(), self.overlay.mine.get())
            if not text:
                self.status.set("沒有字幕可複製")
                return
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.set("字幕已複製")

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
            ("測試字幕", self._test_subtitles),
            ("開始", self._start),
            ("停止", self._stop),
            ("離開", self._quit),
            ("暫停/繼續", self._toggle_pause),
            ("虛擬麥克風靜音/取消", self._toggle_virtual_mic_mute),
            ("本機翻譯靜音/取消", self._toggle_speaker_translation_mute),
            ("切換字幕", self._toggle_subtitles),
            ("切換語音", self._toggle_speech),
            ("切換喇叭", self._toggle_speaker),
            ("切換麥克風", self._toggle_microphone),
            ("複製字幕", copy_overlay),
            ("修復喇叭音訊", lambda: self._troubleshoot("speaker_audio")),
            ("修復麥克風輸出", lambda: self._troubleshoot("mic_output")),
            ("修復字幕", lambda: self._troubleshoot("subtitles")),
            ("修復本機翻譯", lambda: self._troubleshoot("local_translation")),
            ("清除快取", self._clear_cache),
            ("開啟紀錄", self._open_logs),
            ("匯出字幕", self._export_subtitles),
            ("清除紀錄", self._clear_logs),
            ("清除本機資料", self._clear_local_data),
        ):
            button = ttk.Button(buttons, text=text, command=command)
            self.button_widgets.append((text, button))
        ptt_button = ttk.Button(buttons, text="按住說話")
        ptt_button.bind("<ButtonPress-1>", lambda _event: self._push_to_talk(True))
        ptt_button.bind("<ButtonRelease-1>", lambda _event: self._push_to_talk(False))
        self.button_widgets.append(("按住說話", ptt_button))

        ttk.Label(frame, textvariable=self.status).grid(row=next_row + 9, column=0, columnspan=3, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        self._apply_mode(save=False)

    def _refresh_lists(self) -> None:
        config = self._config_from_vars()
        self._list_generation += 1
        generation = self._list_generation

        def work():
            raw_devices = list_audio_devices()
            save_audio_devices(APP_DIR, raw_devices)
            commands = APP_DIR / "commands.json"
            models = sorted(set(list_models(self.repo_root / "_models", models_dir(config))) | set(command_choices(commands, "model")))
            asr_devices = command_choices(commands, "device") or ["cuda", "cpu"]
            compute_types = command_choices(commands, "compute_type") or ["auto", "int8", "float16", "float32"]
            return raw_devices, models, asr_devices, compute_types

        self.controller.submit(work, lambda result: self._lists_ready(generation, result))

    def _lists_ready(self, generation: int, result: TaskResult) -> None:
        if generation != self._list_generation:
            return
        if result.error is not None:
            self.status.set(f"無法重新整理裝置與模型：{result.error}")
            return
        raw_devices, models, asr_devices, compute_types = result.value
        config_changed = False
        self._device_id_by_label = {"系統預設": ""}
        self._device_label_by_id = {"": "系統預設"}
        for device in raw_devices:
            label = format_device_label(device)
            identity = device_identity(device)
            self._device_id_by_label[label] = identity
            self._device_label_by_id[identity] = label
        for key in AUDIO_DEVICE_KEYS:
            identity = str(self.config.get(key) or "")
            if not identity:
                self.vars[key].set("系統預設")
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
        devices = list(self._device_id_by_label)
        for key, widget in self.comboboxes.items():
            if key == "model":
                widget.configure(values=models)
            elif key == "device":
                widget.configure(values=asr_devices)
            elif key == "compute_type":
                widget.configure(values=compute_types)
            elif key != "tts_voice_name":
                widget.configure(values=devices)
        self._refresh_runtime_status()

    def _list_tts_voices(self) -> None:
        self._submit("正在列出 Windows TTS 聲音", list_windows_sapi_voices, self._tts_voices_ready, "無法列出 Windows TTS 聲音")

    def _tts_voices_ready(self, voices: list[str]) -> None:
        self.comboboxes["tts_voice_name"].configure(values=voices)
        self.status.set("; ".join(voices) if voices else "找不到 Windows TTS 聲音")

    def _swap_languages(self) -> None:
        if self.vars["source_language"].get() == "auto":
            self.status.set("自動偵測來源語言時無法交換；請先選擇固定來源語言")
            return
        source, target = swap_language_values(self.vars["source_language"].get(), self.vars["target_language"].get())
        self.vars["source_language"].set(source)
        self.vars["target_language"].set(target)
        self._save()

    def _config_from_vars(self) -> dict:
        config = self.config.copy()
        for key, variable in self.vars.items():
            if key.startswith("last_"):
                continue
            config[key] = self._device_id_by_label.get(variable.get(), "") if key in AUDIO_DEVICE_KEYS else variable.get()
        config["scenario"] = scenario_key(config["scenario"])
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
        config["overlay_opacity"] = overlay_opacity_value(config["overlay_opacity"])
        config["overlay_font_size"] = overlay_font_size_value(config["overlay_font_size"])
        config["overlay_hold_seconds"] = overlay_hold_seconds_value(config["overlay_hold_seconds"])
        try:
            config["segment_seconds"] = float(config["segment_seconds"])
        except Exception:
            config["segment_seconds"] = 2.0
        try:
            config["speech_threshold"] = min(1.0, max(0.0, float(config["speech_threshold"])))
        except Exception:
            config["speech_threshold"] = 0.01
        try:
            config["tts_rate"] = max(-10, min(10, int(config["tts_rate"])))
        except Exception:
            config["tts_rate"] = 0
        try:
            config["tts_volume"] = max(0, min(100, int(config["tts_volume"])))
            config["speaker_tts_volume"] = max(0, min(100, int(config["speaker_tts_volume"])))
        except Exception:
            config["tts_volume"] = 100
            config["speaker_tts_volume"] = 100
        try:
            config["conversation_log_retention_days"] = max(0, min(3650, int(config["conversation_log_retention_days"])))
            config["conversation_log_max_mb"] = max(0, min(10240, int(config["conversation_log_max_mb"])))
        except Exception:
            config["conversation_log_retention_days"] = 7
            config["conversation_log_max_mb"] = 100
        return config

    def _save(self) -> bool:
        config = self._config_from_vars()
        try:
            validate_language_pair(config)
        except ValueError as exc:
            self.status.set(str(exc))
            return False
        cloud_enabled = bool({config["provider"], config["tts_provider"]} & set(CLOUD_PROVIDERS))
        if cloud_activation_requires_confirmation(self.config.get("provider", "local"), self.config.get("tts_provider", "local"), config["provider"], config["tts_provider"]):
            if not messagebox.askyesno("啟用雲端 API？", mode_notice(config["provider"], config["tts_provider"], bool(config["record_logs"]), config.get("local_translate_url", ""))):
                self._load_config_into_widgets(self.config)
                self.status.set("雲端 API 未啟用")
                return False
        if config["record_logs"] and not getattr(self, "_log_consent_granted", False):
            if not messagebox.askyesno("啟用本次對話紀錄？", conversation_log_notice(config) + "\n\n本次執行是否允許儲存上述內容？"):
                self.record_logs.set(False)
                config["record_logs"] = False
            else:
                self._log_consent_granted = True
        elif not config["record_logs"]:
            self._log_consent_granted = False
        config["cloud_api_enabled"] = cloud_enabled
        if self.engine:
            try:
                self.engine.update_config(config)
            except Exception as exc:
                self.status.set(f"設定套用失敗：{exc}")
                return False
        self.config = config
        self.mode_text.set(self._mode_text())
        save_config(APP_DIR, self.config)
        return True

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

    def _drain_ui_events(self) -> None:
        assert threading.current_thread() is threading.main_thread(), "Tk event pump must run on the main thread"
        while True:
            try:
                event = self._engine_events.get_nowait()
            except queue.Empty:
                break
            if self._closing or (event.engine is not None and event.engine is not self.engine):
                continue
            if event.kind == "status":
                self._engine_status(*event.args)
            elif event.kind == "overlay":
                self._overlay_update(*event.args)
            else:
                event.args[0](*event.args[1:])
        if not self._closing:
            self.after(50, self._drain_ui_events)

    def _submit(self, status: str, work, done, error_title: str) -> None:
        self.status.set(status)

        def finish(result: TaskResult) -> None:
            if result.error is not None:
                messagebox.showerror(error_title, str(result.error))
            else:
                done(result.value)

        self.controller.submit(work, finish)

    def _mode_text(self) -> str:
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
        for _text, button in self.button_widgets:
            button.pack_forget()
        visible_buttons = visible_button_texts([text for text, _button in self.button_widgets], self.advanced_mode.get())
        for text, button in self.button_widgets:
            if text in visible_buttons:
                button.pack(side="left", padx=3)
        if save:
            self._save()

    def _apply_performance_mode(self) -> None:
        self.vars["segment_seconds"].set(str(performance_segment_seconds(self.vars["performance_mode"].get())))
        self._save()

    def _apply_overlay(self) -> None:
        self._set_overlay_visible(self.overlay_visible.get())
        self.overlay.attributes("-topmost", self.overlay_topmost.get())
        self.overlay.attributes("-alpha", overlay_opacity_value(self.vars["overlay_opacity"].get()))
        self.overlay.set_font_size(overlay_font_size_value(self.vars["overlay_font_size"].get()))
        self._save()

    def _set_overlay_visible(self, visible: bool) -> None:
        if overlay_visibility_action(visible) == "show":
            self.overlay.deiconify()
        else:
            self.overlay.withdraw()

    def _pick_google_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("所有檔案", "*.*")])
        if path:
            self.vars["google_service_account_json"].set(path)
            self._save()

    def _pick_glossary_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("所有檔案", "*.*")])
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
        source = simpledialog.askstring("新增術語", "原文")
        if not source:
            return
        target = simpledialog.askstring("新增術語", "譯文")
        if not target:
            return
        path = ensure_glossary_file(Path(self.config.get("glossary_path") or APP_DIR / "glossary.json"))
        add_glossary_term(path, source, target)
        self.status.set("詞彙已加入")

    def _fix_last_translation(self) -> None:
        self._save()
        source = str(self.config.get("last_source_text") or "").strip()
        if not source:
            self.status.set("沒有可修正的近期翻譯")
            return
        target = simpledialog.askstring("修正上次翻譯", f"請輸入修正翻譯：\n{source}", initialvalue=str(self.config.get("last_translated_text") or ""))
        if not target:
            return
        if not messagebox.askyesno("加入術語表？", "是否將這個修正加入術語表？"):
            self.status.set("翻譯修正未加入詞彙表")
            return
        path = ensure_glossary_file(Path(self.config.get("glossary_path") or APP_DIR / "glossary.json"))
        add_glossary_term(path, source, target)
        self.status.set("翻譯修正已加入詞彙表")

    def _import_runtime(self) -> None:
        source = filedialog.askdirectory(title="選擇已解壓的 Faster-Whisper-XXL 資料夾")
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
            messagebox.showerror("runtime 不完整", "缺少：" + ", ".join(result.status["missing"]))
            return
        self._refresh_lists()
        self.status.set("runtime 已匯入；commands.json 已更新")

    def _refresh_runtime_status(self) -> None:
        config = self._config_from_vars()
        target = runtime_dir(config)
        self._runtime_check_generation += 1
        generation = self._runtime_check_generation
        self.controller.submit(
            lambda: self.runtime_service.check(target, config.get("device", "auto"), config.get("compute_type", "auto")),
            lambda result: self._runtime_status_ready(config, generation, result),
        )

    def _runtime_status_ready(self, config: dict, generation: int, result: TaskResult[RuntimeCheckResult]) -> None:
        if generation != self._runtime_check_generation:
            return
        if result.error is not None:
            self.runtime_text.set(f"runtime 檢查失敗：{result.error}")
            return
        check = result.value
        assert check is not None
        status = check.status
        if status["ready"]:
            self.config["last_ffmpeg_failed"] = check.ffmpeg_failed
            self.vars["last_ffmpeg_failed"].set(str(check.ffmpeg_failed))
            save_config_state(APP_DIR, self.config, {"last_ffmpeg_failed"})
            note = f"runtime 已就緒；CPU：{'可用' if status['cpu_ready'] else '不可用'}；CUDA：{'可用' if status['cuda_ready'] else '不可用'}"
            if not model_available(config["model"], self.repo_root / "_models", models_dir(config)):
                note += f"；找不到模型：{config['model']}"
            self.runtime_text.set(note)
        else:
            self.runtime_text.set(runtime_install_message(runtime_dir(config), config.get("device", "auto")))

    def _diagnostic_message(self, issues) -> str:
        config = self._config_from_vars()
        if not issues:
            return "目前沒有發現需要處理的設定問題。"
        log_path = Path(config.get("log_dir") or APP_DIR / "logs") / "app.log"
        lines = []
        for issue in issues:
            lines.append(
                f"[{SEVERITY_LABELS.get(issue.severity, issue.severity)}]\n"
                f"問題名稱：{issue.title}\n"
                f"可能原因：{issue.detail}\n"
                f"自動檢查結果：{issue.code}\n"
                f"建議修復步驟：{issue.fix}\n"
                f"一鍵修復按鈕：{diagnostic_action_label(issue.action)}\n"
                f"進階日誌：{log_path}"
            )
        return "\n\n".join(lines)

    def _show_first_run_wizard(self) -> None:
        config = self._config_from_vars()
        self.controller.submit(
            lambda: self.audio_diagnostics.collect(config, self.repo_root),
            self._first_run_diagnostics_ready,
        )

    def _first_run_diagnostics_ready(self, result: TaskResult) -> None:
        if result.error is not None:
            self.status.set(f"首次診斷失敗：{result.error}")
            return
        issues = result.value
        action = first_run_setup_action(issues, bool(self.config.get("setup_guide_shown", False)))
        if action == "diagnostics":
            self._show_diagnostics("首次設定", issues)
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
            "正在執行診斷",
            lambda: self.audio_diagnostics.collect(config, self.repo_root),
            lambda issues: self._show_diagnostics("診斷結果", issues),
            "診斷失敗",
        )

    def _show_diagnostics(self, title: str, issues) -> None:
        message = self._diagnostic_message(issues)
        actions = [(diagnostic_action_label(action), lambda name=action: self._run_diagnostic_action(name)) for action in diagnostic_actions(issues)]
        if not any(label == "設定指南" for label, _callback in actions):
            actions.append(("設定指南", self._show_setup_guide))
        self._show_text_dialog(title, message, actions)

    def _show_text_dialog(self, title: str, message: str, actions: list[tuple[str, object]]) -> None:
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry("760x520")
        window.transient(self)
        window.grab_set()

        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title).pack(anchor="w")

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, pady=(8, 0))
        scrollbar = ttk.Scrollbar(body)
        scrollbar.pack(side="right", fill="y")
        text = tk.Text(body, wrap="word", yscrollcommand=scrollbar.set, height=20)
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
        ttk.Button(buttons, text="關閉", command=window.destroy).grid(row=close_index // 3, column=close_index % 3, sticky="ew", padx=3, pady=3)

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
            self.status.set("沒有偵測到可鎖定的語言")
            return
        self.vars["source_language"].set(locked)
        self._save()
        self.status.set(f"來源語言已鎖定：{locked}")

    def _check_updates(self) -> None:
        self._submit(
            "正在檢查更新",
            lambda: self.update_service.check(self.repo_root),
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
        actions = [(label, action_map[label]) for label in setup_guide_actions()]
        self._show_text_dialog("設定指南", setup_guide_message(), actions)

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
            self.status.set("找不到 runtime：" + ", ".join(status["missing"]))
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
            self.status.set(str(exc))
            return
        self._load_config_into_widgets(updated)
        self._save()
        self.status.set(f"已套用場景：{scenario_label(updated['scenario'])}")

    def _load_config_into_widgets(self, updated: dict) -> None:
        for key, variable in self.vars.items():
            if key in updated:
                value = self._device_label_by_id.get(str(updated[key]), "系統預設") if key in AUDIO_DEVICE_KEYS else scenario_label(str(updated[key])) if key == "scenario" else str(updated[key])
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
            self.status.set("設定已是建議值")
            return
        if not messagebox.askyesno("預覽自動優化", format_tuning_preview(before, decision.config, decision.recommendations)):
            self.status.set("已保留原設定")
            return
        self._load_config_into_widgets(decision.config)
        self._save()
        self.status.set(decision.summary)

    def _download_model(self) -> None:
        if self.__dict__.get("_model_download_cancel") is not None:
            self.status.set("模型下載已在進行；可按「取消模型下載」")
            return
        self._save()
        model = self.config["model"]
        app_models = models_dir(self.config)
        cancel = threading.Event()
        self._model_download_cancel = cancel
        self.status.set(f"正在下載模型 {model}")

        self.controller.submit(
            lambda: self.model_service.download(model, app_models, lambda message: self._post_ui("status", message), cancel),
            self._model_downloaded,
        )

    def _model_downloaded(self, result: TaskResult[Path]) -> None:
        self._model_download_cancel = None
        if result.error is not None:
            self.status.set(str(result.error))
        else:
            self.status.set(f"模型下載完成：{result.value.name}；版本與 SHA 完整性已驗證")
        self._refresh_lists()

    def _cancel_model_download(self) -> None:
        cancel = self.__dict__.get("_model_download_cancel")
        if cancel is None:
            self.status.set("目前沒有模型下載工作")
            return
        cancel.set()
        self.status.set("正在取消模型下載；已下載部分可供稍後續傳")

    def _download_translation_models(self) -> None:
        self._save()
        source_language = self.config["source_language"]
        target_language = self.config["target_language"]
        if source_language == "auto":
            messagebox.showinfo("請先選擇來源語言", "下載離線翻譯模型前，請把「來源語言」改成固定語言。")
            return
        if not messagebox.askyesno(
            "下載離線翻譯模型",
            f"將下載 {source_language} 與 {target_language} 的雙向 Argos Translate 模型。\n"
            "模型會儲存在程式資料夾的 models\\translation。",
        ):
            return
        self.status.set("正在下載離線翻譯模型")

        config = self.config.copy()
        registry = self.engine.offline_translation_registry if self.engine else None
        self._submit(
            "正在下載離線翻譯模型",
            lambda: self.model_service.download_translation(config, source_language, target_language, registry),
            lambda downloaded: self.status.set(f"離線翻譯模型下載完成：{len(downloaded)} 個"),
            "離線翻譯模型下載失敗",
        )

    def _refresh_commands(self) -> None:
        runtime = runtime_dir(self._config_from_vars())
        self._submit(
            "正在更新 commands.json",
            lambda: self.runtime_service.refresh_commands(runtime),
            lambda _value: (self._refresh_lists(), self.status.set("commands.json 已更新")),
            "commands.json 更新失敗",
        )

    def _test_api(self) -> None:
        self._save()
        config = self.config.copy()
        self._submit("正在測試 API", lambda: self.audio_diagnostics.test_api(config), self.status.set, "API 測試失敗")

    def _test_tone(self) -> None:
        config = self._config_from_vars()
        self._submit("正在測試裝置音", lambda: self.audio_diagnostics.test_tone(config), lambda _value: self.status.set("裝置音測試完成"), "裝置音測試失敗")

    def _test_tts(self) -> None:
        config = self._config_from_vars()
        self.controller.submit(lambda: self.audio_diagnostics.test_tts(config), lambda result: self._tts_tested(config, result))

    def _tts_tested(self, config: dict, result: TaskResult) -> None:
        self.config["last_tts_failed"] = result.error is not None
        save_config_state(APP_DIR, self.config, {"last_tts_failed"})
        if result.error is not None:
            messagebox.showerror("TTS 測試失敗", str(result.error))
        else:
            self.status.set("TTS 輸出測試完成")

    def _test_virtual_mic(self) -> None:
        config = self._config_from_vars()
        self.controller.submit(lambda: self.audio_diagnostics.test_virtual_microphone(config), lambda result: self._virtual_mic_tested(config, result))

    def _virtual_mic_tested(self, config: dict, result: TaskResult[bool]) -> None:
        active = bool(result.value) if result.error is None else False
        self.config["last_virtual_mic_failed"] = not active
        save_config_state(APP_DIR, self.config, {"last_virtual_mic_failed"})
        if result.error is not None:
            messagebox.showerror("虛擬麥克風測試失敗", str(result.error))
        else:
            self.status.set("虛擬麥克風已偵測到聲音" if active else "虛擬麥克風沒有偵測到聲音")

    def _test_speaker(self) -> None:
        config = self._config_from_vars()
        self.controller.submit(lambda: self.audio_diagnostics.test_speaker(config), lambda result: self._speaker_tested(config, result))

    def _speaker_tested(self, config: dict, result: TaskResult[bool]) -> None:
        if result.error is not None:
            messagebox.showerror("喇叭測試失敗", str(result.error))
            return
        active = bool(result.value)
        self.config["last_speaker_quiet"] = not active
        save_config_state(APP_DIR, self.config, {"last_speaker_quiet"})
        self.status.set("喇叭已偵測到聲音" if active else "喇叭目前沒有偵測到聲音")

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
        self.status.set(f"麥克風音量 {level:.4f}")

    def _test_subtitles(self) -> None:
        self.overlay_visible.set(True)
        self._apply_overlay()
        self.overlay.update_lines("字幕測試", "字幕測試")
        self.status.set("字幕測試完成")

    def _troubleshoot(self, issue: str) -> None:
        action, target = troubleshooting_action(issue)
        if action == "overlay":
            self.overlay_visible.set(True)
            self._apply_overlay()
            self.status.set("字幕已顯示")
            return
        if target.startswith("ms-settings:"):
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        else:
            webbrowser.open(target)
        self.status.set("已開啟修復說明")

    def _start(self) -> None:
        if not self._save():
            return
        config = self.config.copy()
        target = runtime_dir(config)
        app_models = models_dir(config)
        self._submit(
            "正在檢查啟動需求",
            lambda: (
                self.runtime_service.check(target, config.get("device", "auto"), config.get("compute_type", "auto"), verify_hashes=True),
                self.model_service.status(config["model"], self.repo_root / "_models", app_models),
            ),
            lambda result: self._start_checked(config, app_models, result),
            "啟動檢查失敗",
        )

    def _start_checked(self, config: dict, app_models: Path, result: tuple[RuntimeCheckResult, str]) -> None:
        if self._config_from_vars() != config:
            self.status.set("設定已在啟動檢查期間變更；請重新按開始")
            return
        runtime_check, current_model_status = result
        status = runtime_check.status
        if not status["ready"]:
            append_app_log(APP_DIR, "runtime_missing", missing=status["missing"])
            error = "找不到 runtime：" + ", ".join(status["missing"])
            if "CUDA --checkcuda probe" in status["missing"]:
                error += "；CUDA probe：" + status["cuda_probe_output"]
            messagebox.showerror("runtime 不可用", error + "\n\n" + runtime_install_message(runtime_dir(self.config), self.config.get("device", "auto")))
            self._set_last_error(error)
            self.status.set(error)
            return
        if current_model_status != MODEL_READY:
            corrupt = current_model_status == MODEL_INVALID
            title = "模型不完整或損毀" if corrupt else "找不到模型"
            append_app_log(APP_DIR, "model_corrupt" if corrupt else "model_missing", model=config["model"])
            messagebox.showerror(title, model_install_message(config["model"], app_models))
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
            self.status.set(f"自動優化有 {len(recommendations)} 項建議；請按「自動優化」預覽並確認")
        self._set_last_error("")
        append_app_log(APP_DIR, "start", model=config["model"], provider=config["provider"])
        engine = RealtimeEngine(
            self.repo_root,
            config,
            lambda speaker, mine: self._post_ui("overlay", speaker, mine, engine=engine),
            lambda message: self._post_ui("status", message, engine=engine),
            APP_DIR,
        )
        self.controller.start_engine(engine)

    def _stop(self) -> None:
        self.controller.stop_engine(self._engine_stopped)

    def _engine_stopped(self, result: TaskResult[str]) -> None:
        if result.error is not None:
            self._engine_status(f"停止失敗：{result.error}")
        elif not self._closing and result.value:
            self._engine_status(result.value)
        append_app_log(APP_DIR, "stop")

    def _quit(self) -> None:
        self._closing = True
        cancel = self.__dict__.get("_model_download_cancel")
        if cancel is not None:
            cancel.set()
        self._stop()
        self.after(50, self._destroy_when_stopped)

    def _destroy_when_stopped(self) -> None:
        if self.engine is None:
            self.destroy()
        else:
            self.after(50, self._destroy_when_stopped)

    def _toggle_pause(self) -> None:
        if self.engine:
            self.engine.set_paused(not self.engine.paused)

    def _toggle_virtual_mic_mute(self) -> None:
        if self.engine:
            self.engine.set_virtual_mic_muted(not self.engine.virtual_mic_muted)

    def _toggle_speaker_translation_mute(self) -> None:
        if self.engine:
            self.engine.set_speaker_translation_muted(not self.engine.speaker_translation_muted)

    def _toggle_subtitles(self) -> None:
        self.overlay_visible.set(toggle_overlay_visibility(self.overlay_visible.get()))
        self._apply_overlay()

    def _toggle_speech(self) -> None:
        self.tts_enabled.set(toggle_speech_enabled(self.tts_enabled.get()))
        self._save()

    def _toggle_speaker(self) -> None:
        self.speaker_enabled.set(toggle_source_enabled(self.speaker_enabled.get()))
        self._save()

    def _toggle_microphone(self) -> None:
        self.microphone_enabled.set(toggle_source_enabled(self.microphone_enabled.get()))
        self._save()

    def _push_to_talk(self, active: bool) -> None:
        if self.engine:
            if active:
                self._push_to_talk_previous_virtual_mic_muted = self.engine.virtual_mic_muted
                self.engine.set_virtual_mic_muted(False)
            else:
                self.engine.set_virtual_mic_muted(bool(getattr(self, "_push_to_talk_previous_virtual_mic_muted", True)))
                self._push_to_talk_previous_virtual_mic_muted = None

    def _clear_cache(self) -> None:
        self._save()
        clear_cache(APP_DIR, Path(self.config.get("translation_cache_path") or APP_DIR / "cache" / "translation_cache.db"))
        self.status.set("快取已清除")

    def _open_logs(self) -> None:
        self._save()
        path = Path(self.config.get("log_dir") or APP_DIR / "logs")
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(path)])

    def _export_subtitles(self) -> None:
        self._save()
        log_dir = Path(self.config.get("log_dir") or APP_DIR / "logs")
        logs = sorted(log_dir.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not logs:
            self.status.set("沒有可匯出的紀錄")
            append_app_log(APP_DIR, "subtitle_export_empty")
            return
        output_dir = APP_DIR / "exports" / "subtitles"
        srt = export_jsonl_to_srt(logs[0], output_dir)
        txt = export_jsonl_to_txt(logs[0], output_dir)
        append_app_log(APP_DIR, "subtitle_export", source=logs[0], output=srt, text_output=txt)
        self.status.set(f"字幕已匯出：{srt}")

    def _clear_logs(self) -> None:
        target = self._confirm_log_cleanup()
        if target is None:
            return
        clear_logs(APP_DIR, target)
        self.status.set("紀錄已清除")

    def _clear_local_data(self) -> None:
        target = self._confirm_log_cleanup()
        if target is None:
            return
        clear_cache(APP_DIR, Path(self.config.get("translation_cache_path") or APP_DIR / "cache" / "translation_cache.db"))
        clear_logs(APP_DIR, target)
        self.status.set("本機快取與紀錄已清除")

    def _confirm_log_cleanup(self) -> Path | None:
        self._save()
        target = Path(self.config.get("log_dir") or APP_DIR / "logs")
        try:
            files = log_files_to_clear(APP_DIR, target)
        except ValueError as exc:
            messagebox.showerror("無法清除紀錄", str(exc))
            return None
        if not target.resolve().is_relative_to(APP_DIR.resolve()) and not messagebox.askyesno(
            "確認清除外部紀錄",
            f"將從以下資料夾清除 {sum(path.parent == target.resolve() for path in files)} 個本程式紀錄檔：\n{target.resolve()}\n\n其他檔案與子資料夾不會刪除。是否繼續？",
        ):
            return None
        return target

    def _overlay_update(self, speaker: str, mine: str) -> None:
        if self.engine and not subtitle_updates_allowed(self.engine.paused):
            return
        self.overlay_generation += 1
        generation = self.overlay_generation
        self.after(0, self.overlay.update_lines, speaker, mine)
        hold_ms = int(overlay_hold_seconds_value(self.config.get("overlay_hold_seconds", 8.0)) * 1000)
        self.after(hold_ms, self._clear_overlay_if_current, generation)

    def _clear_overlay_if_current(self, generation: int) -> None:
        if generation == self.overlay_generation:
            self.overlay.clear_lines()


def main() -> None:
    TranslatorApp().mainloop()
