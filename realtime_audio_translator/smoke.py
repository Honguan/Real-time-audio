import importlib
import json
import os
import sys
from pathlib import Path


SMOKE_IMPORTS = (
    "realtime_audio_translator.gui",
    "realtime_audio_translator.engine",
    "realtime_audio_translator.providers",
    "numpy",
    "sounddevice",
    "pyaudiowpatch",
    "_portaudiowpatch",
    "cffi",
    "ctranslate2",
    "sentencepiece",
    "google.auth",
    "google.oauth2.service_account",
    "google.auth.transport.requests",
)


def run_smoke_test(config_root: Path | None = None, require_frozen: bool = True) -> dict:
    result = {
        "schema_version": 1,
        "status": "error",
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "working_directory": os.getcwd(),
        "checks": [],
    }
    try:
        if require_frozen and not result["frozen"]:
            raise RuntimeError("smoke test must run from the packaged executable")
        for name in SMOKE_IMPORTS:
            importlib.import_module(name)
        result["checks"].append("imports")

        import sounddevice
        if not getattr(sounddevice, "_lib", None):
            raise RuntimeError("PortAudio library did not load")
        result["checks"].append("portaudio")

        import tkinter
        window = tkinter.Tk()
        window.withdraw()
        window.update_idletasks()
        tcl_library = Path(window.tk.eval("info library"))
        tk_library = Path(window.tk.eval("set tk_library"))
        window.destroy()
        result["checks"].append("tk")
        if not (tcl_library / "init.tcl").is_file() or not (tk_library / "tk.tcl").is_file():
            raise RuntimeError("bundled Tcl/Tk resources are unavailable")
        result["checks"].append("resources")

        from .config import APP_DIR, ensure_app_dirs
        from .paths import resource_root
        root = config_root or APP_DIR
        ensure_app_dirs(root)
        if not (root / "config" / "settings.json").parent.is_dir():
            raise RuntimeError("configuration directory is unavailable")
        bundle = resource_root()
        if not Path(sys.executable).is_file() or not bundle.is_dir():
            raise RuntimeError("packaged executable or bundle directory is unavailable")
        if result["frozen"] and not (bundle / "base_library.zip").is_file():
            raise RuntimeError("bundled Python resources are unavailable")
        result.update({"bundle_root": str(bundle), "config_root": str(root)})
        result["checks"].extend(("config", "bundle"))
        result["status"] = "ok"
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    return result


def write_smoke_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
