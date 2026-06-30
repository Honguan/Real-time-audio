import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audio import find_device, format_device_label, list_audio_devices
from .commands import refresh_commands
from .config import APP_DIR, clear_cache, clear_logs, load_config, save_config
from .engine import RealtimeEngine
from .models import download_model, list_models, recommend_model
from .paths import resource_root
from .providers import Translator, google_access_token
from .runtime import DEFAULT_RUNTIME_DIR, RUNTIME_RELEASE_URL, install_runtime_from, runtime_dir, runtime_status, whisper_exe


PROVIDER_CHOICES = ("local", "google", "openai")
TTS_PROVIDER_CHOICES = ("local", "google", "openai")
CLOUD_PROVIDERS = ("google", "openai")


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


def subtitle_updates_allowed(paused: bool) -> bool:
    return not paused


def swap_language_values(source_language: str, target_language: str) -> tuple[str, str]:
    return target_language, source_language


def mode_notice(provider: str, tts_provider: str) -> str:
    cloud = [name for name in dict.fromkeys((provider, tts_provider)) if name in CLOUD_PROVIDERS]
    if cloud:
        return f"Mode: local ASR + cloud API ({', '.join(cloud)}); API use may incur costs"
    return "Mode: local/offline; no cloud API selected"


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
        self.status = tk.StringVar(value="ready")
        self.runtime_text = tk.StringVar(value="")
        self.mode_text = tk.StringVar(value=mode_notice(self.config["provider"], self.config["tts_provider"]))
        self.overlay_generation = 0
        self.overlay = Overlay(
            self,
            self.config["overlay_topmost"],
            overlay_opacity_value(self.config.get("overlay_opacity", 0.86)),
            overlay_font_size_value(self.config.get("overlay_font_size", 18)),
        )
        self._build()
        self._set_overlay_visible(bool(self.config.get("overlay_visible", True)))
        self._refresh_lists()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self.vars = {key: tk.StringVar(value=str(value)) for key, value in self.config.items()}
        self.overlay_visible = tk.BooleanVar(value=bool(self.config["overlay_visible"]))
        self.overlay_topmost = tk.BooleanVar(value=bool(self.config["overlay_topmost"]))
        self.show_language_labels = tk.BooleanVar(value=bool(self.config["show_language_labels"]))
        self.show_original_text = tk.BooleanVar(value=bool(self.config["show_original_text"]))
        self.tts_enabled = tk.BooleanVar(value=bool(self.config.get("tts_enabled", True)))
        self.record_logs = tk.BooleanVar(value=bool(self.config["record_logs"]))
        self.comboboxes: dict[str, ttk.Combobox] = {}

        rows = [
            ("Source language", "source_language"),
            ("Target language", "target_language"),
            ("Provider", "provider"),
            ("TTS provider", "tts_provider"),
            ("Local translate URL", "local_translate_url"),
            ("Model", "model"),
            ("ASR device", "device"),
            ("Compute type", "compute_type"),
            ("Speaker device", "speaker_device"),
            ("Microphone device", "microphone_device"),
            ("TTS output", "tts_output_device"),
            ("Google project", "google_project_id"),
            ("Google JSON", "google_service_account_json"),
            ("Segment seconds", "segment_seconds"),
            ("Speech threshold", "speech_threshold"),
            ("Overlay opacity", "overlay_opacity"),
            ("Overlay font size", "overlay_font_size"),
            ("Overlay hold seconds", "overlay_hold_seconds"),
            ("Runtime dir", "runtime_dir"),
        ]
        for row, (label, key) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if key in ("provider", "tts_provider"):
                values = TTS_PROVIDER_CHOICES if key == "tts_provider" else PROVIDER_CHOICES
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=values, state="readonly")
                widget.bind("<<ComboboxSelected>>", lambda _event: self._save())
            elif key.endswith("device") or key == "model":
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=[])
                self.comboboxes[key] = widget
            else:
                widget = ttk.Entry(frame, textvariable=self.vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=4, padx=8)
            if key == "google_service_account_json":
                ttk.Button(frame, text="Select", command=self._pick_google_json).grid(row=row, column=2, sticky="ew")
            if key in ("overlay_opacity", "overlay_font_size", "overlay_hold_seconds"):
                ttk.Button(frame, text="Apply", command=self._apply_overlay).grid(row=row, column=2, sticky="ew")
            if key == "runtime_dir":
                ttk.Button(frame, text="Select", command=self._pick_runtime_dir).grid(row=row, column=2, sticky="ew")

        next_row = len(rows)
        ttk.Label(frame, textvariable=self.runtime_text, foreground="#a94442").grid(row=next_row, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(frame, textvariable=self.mode_text, foreground="#7a4b00").grid(row=next_row + 1, column=0, columnspan=3, sticky="ew", pady=4)

        runtime_buttons = ttk.Frame(frame)
        runtime_buttons.grid(row=next_row + 2, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Button(runtime_buttons, text="Open runtime folder", command=self._open_runtime_dir).pack(side="left", padx=3)
        ttk.Button(runtime_buttons, text="Import extracted runtime", command=self._import_runtime).pack(side="left", padx=3)
        ttk.Button(runtime_buttons, text="Download Faster-Whisper-XXL", command=lambda: webbrowser.open(RUNTIME_RELEASE_URL)).pack(side="left", padx=3)

        ttk.Checkbutton(frame, text="Show overlay", variable=self.overlay_visible, command=self._apply_overlay).grid(row=next_row + 3, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Overlay topmost", variable=self.overlay_topmost, command=self._apply_overlay).grid(row=next_row + 3, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Show language", variable=self.show_language_labels, command=self._save).grid(row=next_row + 3, column=2, sticky="w")
        ttk.Checkbutton(frame, text="Show original", variable=self.show_original_text, command=self._save).grid(row=next_row + 4, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Speak translations", variable=self.tts_enabled, command=self._save).grid(row=next_row + 4, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Record logs", variable=self.record_logs).grid(row=next_row + 4, column=2, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=next_row + 5, column=0, columnspan=3, sticky="ew", pady=12)
        def copy_overlay() -> None:
            text = overlay_clipboard_text(self.overlay.speaker.get(), self.overlay.mine.get())
            if not text:
                self.status.set("no subtitles to copy")
                return
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.set("subtitles copied")

        for text, command in (
            ("Refresh", self._refresh_lists),
            ("Swap languages", self._swap_languages),
            ("Recommend model", self._recommend),
            ("Download model", self._download_model),
            ("Update command config", self._refresh_commands),
            ("API test", self._test_api),
            ("Device tone", self._test_tone),
            ("Start", self._start),
            ("Stop", self._stop),
            ("Pause/resume", self._toggle_pause),
            ("Mute/unmute", self._toggle_mute),
            ("Copy subtitles", copy_overlay),
            ("Clear cache", self._clear_cache),
            ("Clear logs", self._clear_logs),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=3)

        ttk.Label(frame, textvariable=self.status).grid(row=next_row + 6, column=0, columnspan=3, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)

    def _refresh_lists(self) -> None:
        devices = [format_device_label(d) for d in list_audio_devices()]
        models = list_models(self.repo_root / "_models", APP_DIR / "models")
        for key, widget in self.comboboxes.items():
            widget.configure(values=models if key == "model" else devices)
        self._refresh_runtime_status()

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
        config["tts_enabled"] = self.tts_enabled.get()
        config["record_logs"] = self.record_logs.get()
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
        return config

    def _save(self) -> None:
        self.config = self._config_from_vars()
        self.mode_text.set(mode_notice(self.config["provider"], self.config["tts_provider"]))
        save_config(APP_DIR, self.config)

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

    def _pick_runtime_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=str(runtime_dir(self._config_from_vars())))
        if path:
            self.vars["runtime_dir"].set(path)
            self._save()
            self._refresh_runtime_status()

    def _open_runtime_dir(self) -> None:
        path = runtime_dir(self._config_from_vars())
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(path)])

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
        self.status.set("runtime imported")

    def _refresh_runtime_status(self) -> None:
        status = runtime_status(runtime_dir(self._config_from_vars()))
        if status["ready"]:
            note = "Runtime ready"
            if status["warnings"]:
                note += f"; recommended CUDA package: {status['cuda_package']}"
            self.runtime_text.set(note)
        else:
            self.runtime_text.set(f"Runtime missing: {', '.join(status['missing'])}. Download Faster-Whisper-XXL and cuBLAS.and.cuDNN_CUDA12_win_v3.7z.")

    def _recommend(self) -> None:
        exe = whisper_exe(runtime_dir(self._config_from_vars()))
        if not exe.exists():
            self.status.set("runtime missing")
            self.vars["model"].set("medium")
            return
        cuda = subprocess.run([str(exe), "--checkcuda"], capture_output=True, text=True, check=False)
        devices = 1 if "CUDA device" in (cuda.stdout + cuda.stderr) else 0
        self.vars["model"].set(recommend_model(devices, 4, False))

    def _download_model(self) -> None:
        self._save()
        exe = whisper_exe(runtime_dir(self.config))
        if not exe.exists():
            messagebox.showerror("Runtime missing", f"Put faster-whisper-xxl.exe in {exe.parent}")
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
            messagebox.showerror("Runtime missing", f"Put faster-whisper-xxl.exe in {exe.parent}")
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

    def _start(self) -> None:
        self._save()
        self.engine = RealtimeEngine(self.repo_root, self.config, self._overlay_update, self.status.set)
        threading.Thread(target=self.engine.start, daemon=True).start()

    def _stop(self) -> None:
        if self.engine:
            self.engine.stop()

    def _toggle_pause(self) -> None:
        if self.engine:
            self.engine.set_paused(not self.engine.paused)

    def _toggle_mute(self) -> None:
        if self.engine:
            self.engine.set_muted(not self.engine.muted)

    def _clear_cache(self) -> None:
        clear_cache(APP_DIR)
        self.status.set("cache cleared")

    def _clear_logs(self) -> None:
        clear_logs(APP_DIR)
        self.status.set("logs cleared")

    def _overlay_update(self, speaker: str, mine: str) -> None:
        if self.engine and not subtitle_updates_allowed(self.engine.paused):
            return
        self.overlay_generation += 1
        generation = self.overlay_generation
        speaker = format_overlay_line(speaker, self.config["source_language"], self.show_language_labels.get())
        mine = format_overlay_line(mine, self.config["target_language"], self.show_language_labels.get())
        self.after(0, self.overlay.update_lines, speaker, mine)
        hold_ms = int(overlay_hold_seconds_value(self.config.get("overlay_hold_seconds", 8.0)) * 1000)
        self.after(hold_ms, self._clear_overlay_if_current, generation)

    def _clear_overlay_if_current(self, generation: int) -> None:
        if generation == self.overlay_generation:
            self.overlay.clear_lines()


def main() -> None:
    TranslatorApp().mainloop()
