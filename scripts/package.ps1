$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Out = Join-Path $Root "installer-output"

if (Test-Path $Out) {
  Get-ChildItem -LiteralPath $Out -Filter "*.bin" -File -ErrorAction SilentlyContinue | Remove-Item -Force
}

$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $Iscc) {
  throw "iscc.exe not found. Install Inno Setup or add iscc.exe to PATH."
}

.\scripts\build.ps1
& $Iscc.Source installer\RealtimeAudioTranslator.iss

$RuntimeDownloads = Join-Path $Out "RUNTIME_DOWNLOADS.txt"
$ReleaseZip = Join-Path $Out "RealtimeAudioTranslator-0.1.0-win-x64.zip"
$Checksums = Join-Path $Out "SHA256SUMS.txt"

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

@((Join-Path $Out "RealtimeAudioTranslatorSetup.exe"), $ReleaseZip, $RuntimeDownloads) |
  ForEach-Object {
    $Hash = Get-FileHash -LiteralPath $_ -Algorithm SHA256
    "$($Hash.Hash)  $(Split-Path -Leaf $Hash.Path)"
  } |
  Set-Content -LiteralPath $Checksums -Encoding UTF8
