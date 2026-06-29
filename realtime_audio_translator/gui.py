import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audio import find_device, format_device_label, list_audio_devices
from .commands import refresh_commands
from .config import APP_DIR, load_config, save_config
from .engine import RealtimeEngine
from .models import list_models, recommend_model
from .paths import resource_root
from .providers import Translator, google_access_token
from .runtime import RUNTIME_RELEASE_URL, runtime_dir, runtime_status, whisper_exe


class Overlay(tk.Toplevel):
    def __init__(self, master: tk.Tk, topmost: bool):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", topmost)
        self.attributes("-alpha", 0.86)
        self.configure(bg="#111111")
        self.geometry("900x96+240+820")
        self.speaker = tk.StringVar(value="")
        self.mine = tk.StringVar(value="")
        self._drag = (0, 0)
        for row, variable in enumerate((self.speaker, self.mine)):
            label = tk.Label(self, textvariable=variable, fg="#f5f5f5", bg="#111111", font=("Microsoft JhengHei UI", 18), anchor="w")
            label.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
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
        self.overlay = Overlay(self, self.config["overlay_topmost"])
        self._build()
        self._refresh_lists()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self.vars = {key: tk.StringVar(value=str(value)) for key, value in self.config.items()}
        self.overlay_topmost = tk.BooleanVar(value=bool(self.config["overlay_topmost"]))
        self.record_logs = tk.BooleanVar(value=bool(self.config["record_logs"]))
        self.comboboxes: dict[str, ttk.Combobox] = {}

        rows = [
            ("Source language", "source_language"),
            ("Target language", "target_language"),
            ("Provider", "provider"),
            ("Model", "model"),
            ("ASR device", "device"),
            ("Compute type", "compute_type"),
            ("Speaker device", "speaker_device"),
            ("Microphone device", "microphone_device"),
            ("TTS output", "tts_output_device"),
            ("Google project", "google_project_id"),
            ("Google JSON", "google_service_account_json"),
            ("Runtime dir", "runtime_dir"),
        ]
        for row, (label, key) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if key.endswith("device") or key == "model":
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=[])
                self.comboboxes[key] = widget
            else:
                widget = ttk.Entry(frame, textvariable=self.vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=4, padx=8)
            if key == "google_service_account_json":
                ttk.Button(frame, text="Select", command=self._pick_google_json).grid(row=row, column=2, sticky="ew")
            if key == "runtime_dir":
                ttk.Button(frame, text="Select", command=self._pick_runtime_dir).grid(row=row, column=2, sticky="ew")

        ttk.Label(frame, textvariable=self.runtime_text, foreground="#a94442").grid(row=12, column=0, columnspan=3, sticky="ew", pady=4)

        runtime_buttons = ttk.Frame(frame)
        runtime_buttons.grid(row=13, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Button(runtime_buttons, text="Open runtime folder", command=self._open_runtime_dir).pack(side="left", padx=3)
        ttk.Button(runtime_buttons, text="Download Faster-Whisper-XXL", command=lambda: webbrowser.open(RUNTIME_RELEASE_URL)).pack(side="left", padx=3)

        ttk.Checkbutton(frame, text="Overlay topmost", variable=self.overlay_topmost, command=self._apply_overlay).grid(row=14, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Record logs", variable=self.record_logs).grid(row=14, column=1, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=15, column=0, columnspan=3, sticky="ew", pady=12)
        for text, command in (
            ("Refresh", self._refresh_lists),
            ("Recommend model", self._recommend),
            ("Update command config", self._refresh_commands),
            ("API test", self._test_api),
            ("Device tone", self._test_tone),
            ("Start", self._start),
            ("Stop", self._stop),
            ("Pause/resume", self._toggle_pause),
            ("Mute/unmute", self._toggle_mute),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=3)

        ttk.Label(frame, textvariable=self.status).grid(row=16, column=0, columnspan=3, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)

    def _refresh_lists(self) -> None:
        devices = [format_device_label(d) for d in list_audio_devices()]
        models = list_models(self.repo_root / "_models", APP_DIR / "models")
        for key, widget in self.comboboxes.items():
            widget.configure(values=models if key == "model" else devices)
        self._refresh_runtime_status()

    def _config_from_vars(self) -> dict:
        config = self.config.copy()
        for key, variable in self.vars.items():
            config[key] = variable.get()
        config["overlay_topmost"] = self.overlay_topmost.get()
        config["record_logs"] = self.record_logs.get()
        try:
            config["segment_seconds"] = float(config["segment_seconds"])
        except Exception:
            config["segment_seconds"] = 2.0
        return config

    def _save(self) -> None:
        self.config = self._config_from_vars()
        save_config(APP_DIR, self.config)

    def _apply_overlay(self) -> None:
        self.overlay.attributes("-topmost", self.overlay_topmost.get())
        self._save()

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

    def _overlay_update(self, speaker: str, mine: str) -> None:
        self.after(0, self.overlay.update_lines, speaker, mine)


def main() -> None:
    TranslatorApp().mainloop()
