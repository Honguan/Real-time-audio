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
  --hidden-import numpy `
  --hidden-import sounddevice `
  --hidden-import cffi `
  --hidden-import google.auth `
  --hidden-import google.oauth2.service_account `
  --hidden-import google.auth.transport.requests `
  realtime_audio_translator\__main__.py
