$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

py -3.10 -m pip install -r requirements.txt
py -3.10 -m realtime_audio_translator.tools.generate_assets
py -3.10 -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --windowed `
  --name RealtimeAudioTranslator `
  --icon assets\icon.ico `
  --add-data "_xxl_data;_xxl_data" `
  --add-data "_models;_models" `
  --add-binary "faster-whisper-xxl.exe;." `
  --add-binary "ffmpeg.exe;." `
  realtime_audio_translator\__main__.py
