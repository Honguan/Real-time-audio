$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $Iscc) {
  throw "iscc.exe not found. Install Inno Setup or add iscc.exe to PATH."
}

.\scripts\build.ps1
& $Iscc.Source installer\RealtimeAudioTranslator.iss

$Out = Join-Path $Root "installer-output"
$RuntimeDownloads = Join-Path $Out "RUNTIME_DOWNLOADS.txt"
$ReleaseZip = Join-Path $Out "RealtimeAudioTranslator-0.1.0-win-x64.zip"

@(
  "Realtime Audio Translator runtime downloads",
  "",
  "Installer:",
  "  RealtimeAudioTranslatorSetup.exe",
  "",
  "Whisper runtime:",
  "  https://github.com/Purfview/whisper-standalone-win/releases",
  "",
  "CUDA dependency for Windows CUDA12:",
  "  cuBLAS.and.cuDNN_CUDA12_win_v3.7z",
  "",
  "In the app, click Import extracted runtime and select the extracted folder or its parent folder.",
  "The app finds faster-whisper-xxl.exe and copies that runtime into:",
  "  %USERPROFILE%\.realtime-audio\runtime",
  "",
  "Models stay in:",
  "  %USERPROFILE%\.realtime-audio\models"
) | Set-Content -LiteralPath $RuntimeDownloads -Encoding UTF8

if (Test-Path $ReleaseZip) {
  Remove-Item -LiteralPath $ReleaseZip -Force
}

Compress-Archive `
  -LiteralPath (Join-Path $Out "RealtimeAudioTranslatorSetup.exe"), (Join-Path $Root "README.md"), $RuntimeDownloads `
  -DestinationPath $ReleaseZip `
  -CompressionLevel Optimal
