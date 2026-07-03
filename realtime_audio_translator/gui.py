import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audio import audio_segment_active, capture_wav, find_device, format_device_label, list_audio_devices
from .ai_orchestrator import plan_session
from .app_log import append_app_log
from .commands import refresh_commands
from .config import APP_DIR, clear_cache, clear_logs, ensure_glossary_file, load_config, save_audio_devices, save_config
from .diagnostics import collect_diagnostics
from .engine import RealtimeEngine
from .models import cuda_hardware_from_check_output, download_model, list_models, model_available, model_install_message, recommend_model
from .paths import resource_root
from .providers import TextToSpeech, Translator, google_access_token
from .release_updater import RELEASES_URL, current_version, latest_release_tag, release_update_message
from .runtime import DEFAULT_RUNTIME_DIR, RUNTIME_RELEASE_URL, UPSTREAM_RUNTIME_RELEASE_URL, install_runtime_from, runtime_dir, runtime_install_message, runtime_status, whisper_exe
from .scenarios import SCENARIO_CHOICES, apply_scenario
from .subtitle_export import export_jsonl_to_srt
from .tts import list_windows_sapi_voices, play_linear16


LANGUAGE_CHOICES = ("auto", "zh", "en", "ja", "ko")
PROVIDER_CHOICES = ("local", "google", "openai")
TTS_PROVIDER_CHOICES = ("local", "google", "openai")
PERFORMANCE_CHOICES = ("low_latency", "balanced", "quality")
CLOUD_PROVIDERS = ("google", "openai")
SETTING_ROWS = (
    ("Source language", "source_language"),
    ("Target language", "target_language"),
    ("Provider", "provider"),
    ("TTS provider", "tts_provider"),
    ("Scenario", "scenario"),
    ("Performance mode", "performance_mode"),
    ("Local translate URL", "local_translate_url"),
    ("OpenAI model", "openai_model"),
    ("Model", "model"),
    ("ASR device", "device"),
    ("Compute type", "compute_type"),
    ("Speaker device", "speaker_device"),
    ("Microphone device", "microphone_device"),
    ("TTS output", "tts_output_device"),
    ("TTS rate", "tts_rate"),
    ("TTS volume", "tts_volume"),
    ("TTS voice", "tts_voice_name"),
    ("Google TTS voice", "google_tts_voice"),
    ("OpenAI TTS model", "openai_tts_model"),
    ("OpenAI TTS voice", "openai_tts_voice"),
    ("Google project", "google_project_id"),
    ("Google JSON", "google_service_account_json"),
    ("Glossary JSON", "glossary_path"),
    ("Segment seconds", "segment_seconds"),
    ("Speech threshold", "speech_threshold"),
    ("Overlay opacity", "overlay_opacity"),
    ("Overlay font size", "overlay_font_size"),
    ("Overlay hold seconds", "overlay_hold_seconds"),
    ("Log dir", "log_dir"),
    ("Runtime dir", "runtime_dir"),
)
BASIC_SETTING_KEYS = {
    "source_language",
    "target_language",
    "provider",
    "tts_provider",
    "scenario",
    "performance_mode",
    "local_translate_url",
    "model",
    "speaker_device",
    "microphone_device",
    "tts_output_device",
    "runtime_dir",
}
ADVANCED_SETTING_KEYS = {key for _label, key in SETTING_ROWS} - BASIC_SETTING_KEYS


def visible_setting_keys(advanced: bool) -> list[str]:
    return [key for _label, key in SETTING_ROWS if advanced or key in BASIC_SETTING_KEYS]


def performance_segment_seconds(mode: str) -> float:
    return {"low_latency": 1.5, "balanced": 2.0, "quality": 3.0}.get(mode, 2.0)


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
    return target_language, source_language


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
        "open_runtime": "Open runtime folder / Download runtime files",
        "download_model": "Download model",
        "audio_settings": "Speaker test / Mic test / Virtual mic test",
        "api_settings": "API test",
        "local_translation": "Fix local translation",
        "optimize_settings": "Optimize settings",
        "language_settings": "Source language",
    }.get(action, action)


def mode_notice(provider: str, tts_provider: str, record_logs: bool = False, local_translate_url: str = "") -> str:
    cloud = [name for name in dict.fromkeys((provider, tts_provider)) if name in CLOUD_PROVIDERS]
    logs = "對話紀錄：開啟" if record_logs else "對話紀錄：關閉"
    setup = "；本機翻譯 URL 未設定" if provider == "local" and not local_translate_url.strip() else ""
    if cloud:
        return f"目前模式：雲端 API 模式（{', '.join(cloud)}）；語音或文字可能傳送到第三方服務；可能依 API 供應商產生費用；{logs}{setup}"
    return f"目前模式：本機免費模式；語音是否上傳：否；是否可能產生 API 費用：否；{logs}{setup}"


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
        self.engine: RealtimeEngine | None = None
        self.title("Realtime Audio Translator")
        self.geometry("900x680")
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.status = tk.StringVar(value="ready")
        self.runtime_text = tk.StringVar(value="")
        self.mode_text = tk.StringVar(value=mode_notice(self.config["provider"], self.config["tts_provider"], bool(self.config["record_logs"]), self.config.get("local_translate_url", "")))
        self.overlay_generation = 0
        self._push_to_talk_previous_muted = None
        self.overlay = Overlay(
            self,
            self.config["overlay_topmost"],
            overlay_opacity_value(self.config.get("overlay_opacity", 0.86)),
            overlay_font_size_value(self.config.get("overlay_font_size", 18)),
        )
        self._build()
        self._set_overlay_visible(bool(self.config.get("overlay_visible", True)))
        self._refresh_lists()
        if self.config.get("ai_self_diagnosis", True):
            self.after(250, self._show_first_run_wizard)

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self.vars = {key: tk.StringVar(value=str(value)) for key, value in self.config.items()}
        self.overlay_visible = tk.BooleanVar(value=bool(self.config["overlay_visible"]))
        self.overlay_topmost = tk.BooleanVar(value=bool(self.config["overlay_topmost"]))
        self.show_language_labels = tk.BooleanVar(value=bool(self.config["show_language_labels"]))
        self.show_original_text = tk.BooleanVar(value=bool(self.config["show_original_text"]))
        self.show_translated_text = tk.BooleanVar(value=bool(self.config.get("show_translated_text", True)))
        self.tts_enabled = tk.BooleanVar(value=bool(self.config.get("tts_enabled", True)))
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
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=LANGUAGE_CHOICES)
                widget.bind("<<ComboboxSelected>>", lambda _event: self._save())
            elif key in ("provider", "tts_provider", "performance_mode", "scenario"):
                values = SCENARIO_CHOICES if key == "scenario" else PERFORMANCE_CHOICES if key == "performance_mode" else TTS_PROVIDER_CHOICES if key == "tts_provider" else PROVIDER_CHOICES
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=values, state="readonly")
                widget.bind("<<ComboboxSelected>>", lambda _event, name=key: self._apply_performance_mode() if name == "performance_mode" else self._save())
            elif key.endswith("device") or key in ("model", "tts_voice_name"):
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=[])
                widget.bind("<<ComboboxSelected>>", lambda _event: self._save())
                self.comboboxes[key] = widget
            else:
                widget = ttk.Entry(frame, textvariable=self.vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=4, padx=8)
            row_widgets.append(widget)
            if key == "google_service_account_json":
                button = ttk.Button(frame, text="Select", command=self._pick_google_json)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "glossary_path":
                button = ttk.Button(frame, text="Select", command=self._pick_glossary_json)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key in ("overlay_opacity", "overlay_font_size", "overlay_hold_seconds"):
                button = ttk.Button(frame, text="Apply", command=self._apply_overlay)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "runtime_dir":
                button = ttk.Button(frame, text="Select", command=self._pick_runtime_dir)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "log_dir":
                button = ttk.Button(frame, text="Select", command=self._pick_log_dir)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            if key == "tts_voice_name":
                button = ttk.Button(frame, text="List", command=self._list_tts_voices)
                button.grid(row=row, column=2, sticky="ew")
                row_widgets.append(button)
            self.setting_widgets[key] = row_widgets

        next_row = len(SETTING_ROWS)
        ttk.Label(frame, textvariable=self.runtime_text, foreground="#a94442").grid(row=next_row, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(frame, textvariable=self.mode_text, foreground="#7a4b00").grid(row=next_row + 1, column=0, columnspan=3, sticky="ew", pady=4)

        runtime_buttons = ttk.Frame(frame)
        runtime_buttons.grid(row=next_row + 2, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Button(runtime_buttons, text="Open runtime folder", command=self._open_runtime_dir).pack(side="left", padx=3)
        ttk.Button(runtime_buttons, text="Import extracted runtime", command=self._import_runtime).pack(side="left", padx=3)
        ttk.Button(runtime_buttons, text="Download runtime files", command=lambda: webbrowser.open(RUNTIME_RELEASE_URL)).pack(side="left", padx=3)
        ttk.Button(runtime_buttons, text="Fallback runtime source", command=lambda: webbrowser.open(UPSTREAM_RUNTIME_RELEASE_URL)).pack(side="left", padx=3)

        ttk.Checkbutton(frame, text="Show overlay", variable=self.overlay_visible, command=self._apply_overlay).grid(row=next_row + 3, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Overlay topmost", variable=self.overlay_topmost, command=self._apply_overlay).grid(row=next_row + 3, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Show language", variable=self.show_language_labels, command=self._save).grid(row=next_row + 3, column=2, sticky="w")
        ttk.Checkbutton(frame, text="Show original", variable=self.show_original_text, command=self._save).grid(row=next_row + 4, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Show translation", variable=self.show_translated_text, command=self._save).grid(row=next_row + 4, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Speak translations", variable=self.tts_enabled, command=self._save).grid(row=next_row + 4, column=2, sticky="w")
        ttk.Checkbutton(frame, text="Speaker capture", variable=self.speaker_enabled, command=self._save).grid(row=next_row + 5, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Mic capture", variable=self.microphone_enabled, command=self._save).grid(row=next_row + 5, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Advanced settings", variable=self.advanced_mode, command=self._apply_mode).grid(row=next_row + 5, column=2, sticky="w")
        ttk.Checkbutton(frame, text="Record logs", variable=self.record_logs, command=self._save).grid(row=next_row + 6, column=0, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=next_row + 7, column=0, columnspan=3, sticky="ew", pady=12)
        def copy_overlay() -> None:
            text = overlay_clipboard_text(self.overlay.speaker.get(), self.overlay.mine.get())
            if not text:
                self.status.set("no subtitles to copy")
                return
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.set("subtitles copied")

        for text, command in (
            ("Setup guide", self._show_setup_guide),
            ("Refresh", self._refresh_lists),
            ("Swap languages", self._swap_languages),
            ("Apply scenario", self._apply_scenario),
            ("Optimize settings", self._optimize_settings),
            ("Recommend model", self._recommend),
            ("Download model", self._download_model),
            ("Run diagnostics", self._run_diagnostics),
            ("Check updates", self._check_updates),
            ("Update command config", self._refresh_commands),
            ("Open app folder", self._open_app_dir),
            ("Open glossary", self._open_glossary),
            ("API test", self._test_api),
            ("Device tone", self._test_tone),
            ("TTS test", self._test_tts),
            ("Virtual mic test", self._test_virtual_mic),
            ("Speaker test", self._test_speaker),
            ("Mic test", self._test_mic),
            ("Subtitle test", self._test_subtitles),
            ("Start", self._start),
            ("Stop", self._stop),
            ("Quit", self._quit),
            ("Pause/resume", self._toggle_pause),
            ("Mute/unmute", self._toggle_mute),
            ("Toggle subtitles", self._toggle_subtitles),
            ("Toggle speech", self._toggle_speech),
            ("Toggle speaker", self._toggle_speaker),
            ("Toggle mic", self._toggle_microphone),
            ("Copy subtitles", copy_overlay),
            ("Fix speaker audio", lambda: self._troubleshoot("speaker_audio")),
            ("Fix mic output", lambda: self._troubleshoot("mic_output")),
            ("Fix subtitles", lambda: self._troubleshoot("subtitles")),
            ("Fix local translation", lambda: self._troubleshoot("local_translation")),
            ("Clear cache", self._clear_cache),
            ("Open logs", self._open_logs),
            ("Export subtitles", self._export_subtitles),
            ("Clear logs", self._clear_logs),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=3)
        ptt_button = ttk.Button(buttons, text="Push to talk")
        ptt_button.bind("<ButtonPress-1>", lambda _event: self._push_to_talk(True))
        ptt_button.bind("<ButtonRelease-1>", lambda _event: self._push_to_talk(False))
        ptt_button.pack(side="left", padx=3)

        ttk.Label(frame, textvariable=self.status).grid(row=next_row + 8, column=0, columnspan=3, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        self._apply_mode(save=False)

    def _refresh_lists(self) -> None:
        raw_devices = list_audio_devices()
        save_audio_devices(APP_DIR, raw_devices)
        devices = [format_device_label(d) for d in raw_devices]
        models = list_models(self.repo_root / "_models", APP_DIR / "models")
        for key, widget in self.comboboxes.items():
            if key == "model":
                widget.configure(values=models)
            elif key != "tts_voice_name":
                widget.configure(values=devices)
        self._refresh_runtime_status()

    def _list_tts_voices(self) -> None:
        try:
            voices = list_windows_sapi_voices()
        except Exception as exc:
            self.status.set(f"could not list TTS voices: {exc}")
            return
        self.comboboxes["tts_voice_name"].configure(values=voices)
        self.status.set("; ".join(voices) if voices else "no Windows TTS voices found")

    def _swap_languages(self) -> None:
        source, target = swap_language_values(self.vars["source_language"].get(), self.vars["target_language"].get())
        self.vars["source_language"].set(source)
        self.vars["target_language"].set(target)
        self._save()

    def _config_from_vars(self) -> dict:
        config = self.config.copy()
        for key, variable in self.vars.items():
            config[key] = variable.get()
        config["overlay_visible"] = self.overlay_visible.get()
        config["overlay_topmost"] = self.overlay_topmost.get()
        config["show_language_labels"] = self.show_language_labels.get()
        config["show_original_text"] = self.show_original_text.get()
        config["show_translated_text"] = self.show_translated_text.get()
        config["tts_enabled"] = self.tts_enabled.get()
        config["speaker_enabled"] = self.speaker_enabled.get()
        config["microphone_enabled"] = self.microphone_enabled.get()
        config["record_logs"] = self.record_logs.get()
        config["advanced_mode"] = self.advanced_mode.get()
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
        except Exception:
            config["tts_volume"] = 100
        return config

    def _save(self) -> None:
        self.config = self._config_from_vars()
        self.mode_text.set(mode_notice(self.config["provider"], self.config["tts_provider"], bool(self.config["record_logs"]), self.config.get("local_translate_url", "")))
        save_config(APP_DIR, self.config)
        if self.engine:
            self.engine.config = self.config

    def _apply_mode(self, save: bool = True) -> None:
        for key in ADVANCED_SETTING_KEYS:
            for widget in self.setting_widgets.get(key, []):
                if self.advanced_mode.get():
                    widget.grid()
                else:
                    widget.grid_remove()
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
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.vars["google_service_account_json"].set(path)
            self._save()

    def _pick_glossary_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
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

    def _import_runtime(self) -> None:
        source = filedialog.askdirectory(title="Select extracted Faster-Whisper-XXL folder")
        if not source:
            return
        try:
            target = install_runtime_from(Path(source), DEFAULT_RUNTIME_DIR)
        except Exception as exc:
            messagebox.showerror("Runtime import failed", str(exc))
            return
        self.vars["runtime_dir"].set(str(target))
        self._save()
        self._refresh_runtime_status()
        try:
            refresh_commands(whisper_exe(target), APP_DIR / "commands.json")
        except Exception as exc:
            self.status.set(f"runtime imported; commands update failed: {exc}")
            return
        self.status.set("runtime imported; commands.json updated")

    def _refresh_runtime_status(self) -> None:
        config = self._config_from_vars()
        status = runtime_status(runtime_dir(config))
        if status["ready"]:
            note = "Runtime ready"
            if status["warnings"]:
                note += f"; recommended CUDA package: {status['cuda_package']}"
            if not model_available(config["model"], self.repo_root / "_models", APP_DIR / "models"):
                note += f"; model missing: {config['model']}"
            self.runtime_text.set(note)
        else:
            self.runtime_text.set(runtime_install_message(runtime_dir(config)))

    def _diagnostic_message(self) -> str:
        config = self._config_from_vars()
        issues = collect_diagnostics(config, self.repo_root)
        if not issues:
            return "目前沒有發現需要處理的設定問題。"
        log_path = Path(config.get("log_dir") or APP_DIR / "logs") / "app.log"
        lines = []
        for issue in issues:
            lines.append(f"[{issue.severity}] {issue.title}\n{issue.detail}\n修復：{issue.fix}\n可用按鈕：{diagnostic_action_label(issue.action)}\n進階日誌：{log_path}")
        return "\n\n".join(lines)

    def _show_first_run_wizard(self) -> None:
        issues = collect_diagnostics(self._config_from_vars(), self.repo_root)
        if not any(issue.code in ("runtime_missing", "model_missing") for issue in issues):
            return
        messagebox.showinfo("First run setup", self._diagnostic_message())

    def _run_diagnostics(self) -> None:
        messagebox.showinfo("Diagnostics", self._diagnostic_message())

    def _check_updates(self) -> None:
        self.status.set("checking updates")

        def run() -> None:
            try:
                latest = latest_release_tag()
                message = release_update_message(current_version(self.repo_root), latest)
            except Exception as exc:
                message = f"update check failed: {exc}; {RELEASES_URL}"
            self.after(0, self.status.set, message)

        threading.Thread(target=run, daemon=True).start()

    def _show_setup_guide(self) -> None:
        messagebox.showinfo(
            "Setup guide",
            "1. Import runtime or open runtime folder.\n"
            "2. Download model or put model zip under the models folder.\n"
            "3. Select speaker, microphone, and TTS output.\n"
            "4. For Discord, select CABLE Output as microphone and CABLE Input as TTS output.\n"
            "5. Run Subtitle test, Speaker test, Mic test, and Virtual mic test before Start.",
        )

    def _recommend(self) -> None:
        exe = whisper_exe(runtime_dir(self._config_from_vars()))
        if not exe.exists():
            self.status.set("runtime missing")
            self.vars["model"].set("medium")
            return
        cuda = subprocess.run([str(exe), "--checkcuda"], capture_output=True, text=True, check=False)
        devices, vram_gb = cuda_hardware_from_check_output(cuda.stdout + cuda.stderr)
        prefer_quality = self.vars["performance_mode"].get() == "quality"
        self.vars["model"].set(recommend_model(devices, vram_gb, prefer_quality))
        self._apply_performance_mode()

    def _apply_scenario(self) -> None:
        updated = apply_scenario(self._config_from_vars(), self.vars["scenario"].get())
        self._load_config_into_widgets(updated)
        self._save()
        self.status.set(f"scenario applied: {updated['scenario']}")

    def _load_config_into_widgets(self, updated: dict) -> None:
        for key, variable in self.vars.items():
            if key in updated:
                variable.set(str(updated[key]))
        self.overlay_visible.set(bool(updated.get("overlay_visible", self.overlay_visible.get())))
        self.overlay_topmost.set(bool(updated.get("overlay_topmost", self.overlay_topmost.get())))
        self.show_language_labels.set(bool(updated.get("show_language_labels", self.show_language_labels.get())))
        self.show_original_text.set(bool(updated.get("show_original_text", self.show_original_text.get())))
        self.show_translated_text.set(bool(updated.get("show_translated_text", self.show_translated_text.get())))
        self.tts_enabled.set(bool(updated.get("tts_enabled", self.tts_enabled.get())))
        self.speaker_enabled.set(bool(updated.get("speaker_enabled", self.speaker_enabled.get())))
        self.microphone_enabled.set(bool(updated.get("microphone_enabled", self.microphone_enabled.get())))
        self.record_logs.set(bool(updated.get("record_logs", self.record_logs.get())))

    def _optimize_settings(self) -> None:
        config = self._config_from_vars()
        exe = whisper_exe(runtime_dir(config))
        devices = 0
        vram_gb = 0
        if exe.exists():
            cuda = subprocess.run([str(exe), "--checkcuda"], capture_output=True, text=True, check=False)
            devices, vram_gb = cuda_hardware_from_check_output(cuda.stdout + cuda.stderr)
        decision = plan_session(config, self.repo_root, devices, vram_gb)
        if not decision.recommendations:
            self.status.set("settings already optimized")
            return
        self._load_config_into_widgets(decision.config)
        self._save()
        self.status.set(decision.summary)

    def _download_model(self) -> None:
        self._save()
        exe = whisper_exe(runtime_dir(self.config))
        if not exe.exists():
            messagebox.showerror("Runtime missing", runtime_install_message(exe.parent))
            return
        model = self.config["model"]
        self.status.set(f"downloading model {model}")

        def run() -> None:
            code = download_model(exe, model, APP_DIR / "models")
            self.after(0, self.status.set, "model downloaded" if code == 0 else f"model download failed: {code}")
            self.after(0, self._refresh_lists)

        threading.Thread(target=run, daemon=True).start()

    def _refresh_commands(self) -> None:
        exe = whisper_exe(runtime_dir(self._config_from_vars()))
        if not exe.exists():
            messagebox.showerror("Runtime missing", runtime_install_message(exe.parent))
            return
        refresh_commands(exe, APP_DIR / "commands.json")
        self.status.set("commands.json updated")

    def _test_api(self) -> None:
        self._save()
        try:
            if self.config["provider"] == "google":
                google_access_token(self.config["google_service_account_json"])
                self.status.set("google auth ok")
            else:
                translated = Translator(self.config).translate("hello", "en", "zh")
                self.status.set(translated[:80])
        except Exception as exc:
            messagebox.showerror("API test failed", str(exc))

    def _test_tone(self) -> None:
        import math
        import numpy as np
        import sounddevice as sd

        device = find_device(self.vars["tts_output_device"].get(), want_output=True)
        samplerate = 24000
        data = np.array([math.sin(2 * math.pi * 440 * i / samplerate) * 0.2 for i in range(samplerate // 4)], dtype="float32")
        sd.play(data, samplerate=samplerate, device=device, blocking=True)

    def _test_tts(self) -> None:
        try:
            config = self._config_from_vars()
            provider = config.get("tts_provider", "local")
            device = self.vars["tts_output_device"].get()
            tts = TextToSpeech(config)
            if provider == "local":
                tts.speak_local("Translation output test", device)
            elif provider == "openai":
                audio = tts.synthesize_openai_linear16("Translation output test")
                play_linear16(audio, device)
            else:
                audio = tts.synthesize_google_linear16("Translation output test", config["target_language"])
                play_linear16(audio, device)
            self.status.set("tts output tested")
        except Exception as exc:
            messagebox.showerror("TTS test failed", str(exc))

    def _test_virtual_mic(self) -> None:
        self._test_tts()

    def _test_speaker(self) -> None:
        try:
            device = find_device(self.vars["speaker_device"].get(), want_output=True)
            path = APP_DIR / "cache" / "audio" / "speaker-test.wav"
            capture_wav(path, device, 0.5, loopback=True)
            active = audio_segment_active(path, float(self.vars["speech_threshold"].get()))
            self.status.set("speaker audio detected" if active else "speaker audio quiet")
        except Exception as exc:
            messagebox.showerror("Speaker test failed", str(exc))

    def _test_mic(self) -> None:
        import numpy as np
        import sounddevice as sd

        try:
            device = find_device(self.vars["microphone_device"].get(), want_output=False)
            data = sd.rec(int(0.5 * 16000), samplerate=16000, channels=1, dtype="float32", device=device)
            sd.wait()
            level = float(np.sqrt(np.mean(np.square(data))))
            self.status.set(f"mic level {level:.4f}")
        except Exception as exc:
            messagebox.showerror("Mic test failed", str(exc))

    def _test_subtitles(self) -> None:
        self.overlay_visible.set(True)
        self._apply_overlay()
        self.overlay.update_lines("Subtitle test", "字幕測試")
        self.status.set("subtitle overlay tested")

    def _troubleshoot(self, issue: str) -> None:
        action, target = troubleshooting_action(issue)
        if action == "overlay":
            self.overlay_visible.set(True)
            self._apply_overlay()
            self.status.set("subtitles shown")
            return
        if target.startswith("ms-settings:"):
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        else:
            webbrowser.open(target)
        self.status.set("repair help opened")

    def _start(self) -> None:
        self._save()
        if not model_available(self.config["model"], self.repo_root / "_models", APP_DIR / "models"):
            append_app_log(APP_DIR, "model_missing", model=self.config["model"])
            messagebox.showerror("Model missing", model_install_message(self.config["model"], APP_DIR / "models"))
            self.status.set(f"model missing: {self.config['model']}")
            return
        append_app_log(APP_DIR, "start", model=self.config["model"], provider=self.config["provider"])
        self.engine = RealtimeEngine(self.repo_root, self.config, self._overlay_update, self.status.set)
        threading.Thread(target=self.engine.start, daemon=True).start()

    def _stop(self) -> None:
        if self.engine:
            self.engine.stop()
            append_app_log(APP_DIR, "stop")

    def _quit(self) -> None:
        self._stop()
        self.destroy()

    def _toggle_pause(self) -> None:
        if self.engine:
            self.engine.set_paused(not self.engine.paused)

    def _toggle_mute(self) -> None:
        if self.engine:
            self.engine.set_muted(not self.engine.muted)

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
                self._push_to_talk_previous_muted = self.engine.muted
                self.engine.set_muted(False)
            else:
                self.engine.set_muted(bool(getattr(self, "_push_to_talk_previous_muted", True)))
                self._push_to_talk_previous_muted = None

    def _clear_cache(self) -> None:
        clear_cache(APP_DIR)
        self.status.set("cache cleared")

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
            self.status.set("no logs to export")
            append_app_log(APP_DIR, "subtitle_export_empty")
            return
        srt = export_jsonl_to_srt(logs[0], APP_DIR / "exports" / "subtitles")
        append_app_log(APP_DIR, "subtitle_export", source=logs[0], output=srt)
        self.status.set(f"subtitles exported: {srt}")

    def _clear_logs(self) -> None:
        self._save()
        clear_logs(APP_DIR, Path(self.config.get("log_dir") or APP_DIR / "logs"))
        self.status.set("logs cleared")

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
