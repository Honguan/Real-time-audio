# Repository Guidelines

## Project Structure & Module Organization

This is a Windows-focused Python app for realtime audio translation.

- `realtime_audio_translator/` contains the application code. `__main__.py` starts the GUI, while modules such as `audio.py`, `engine.py`, `providers.py`, `runtime.py`, and `tts.py` keep the runtime concerns separated.
- `tests/` contains `unittest` test modules (`test_*.py`).
- `scripts/` contains PowerShell build and packaging automation.
- `assets/` stores bundled app assets such as the icon.
- `docs/` stores release and project documentation.
- `build/`, `dist/`, `installer-output/`, and `release-output/` are generated outputs.
- `_models/` and `_xxl_data/` are local model/data directories; avoid committing large generated or downloaded artifacts unless explicitly required.

## Build, Test, and Development Commands

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create and prepare a local development environment.

```powershell
python -m realtime_audio_translator
```

Run the app locally from source.

```powershell
python -m unittest discover -s tests
```

Run the test suite.

```powershell
.\scripts\build.ps1
.\scripts\package.ps1
```

Build the PyInstaller app and create release packages.

## Coding Style & Naming Conventions

Use Python with 4-space indentation. Follow the existing module style: `snake_case` for functions and variables, `PascalCase` for classes, and uppercase constants such as `DEFAULT_CONFIG`. Prefer standard-library utilities already used here, especially `pathlib`, `json`, and `unittest`. Keep files UTF-8 and LF-formatted when creating new text files.

## Testing Guidelines

Tests use the standard `unittest` framework. Put new tests in `tests/test_*.py`, name test methods `test_*`, and keep coverage close to the changed behavior. For runtime, packaging, or config changes, add or update the smallest relevant test before running discovery.

## Commit & Pull Request Guidelines

Recent history uses concise conventional commits, mainly `fix: ...` and `feat: ...`. Match that style, for example `fix: detect missing runtime files`.

Pull requests should include a short description, test results, linked issues when available, and screenshots or notes for GUI, overlay, or installer-visible changes.

## Configuration & Secrets

Do not commit API keys, service account JSON files, local logs, downloaded models, or user-specific files from `%USERPROFILE%\.realtime-audio`. Use environment variables such as `OPENAI_API_KEY` for local credentials.
