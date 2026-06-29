$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $Iscc) {
  throw "iscc.exe not found. Install Inno Setup or add iscc.exe to PATH."
}

.\scripts\build.ps1
& $Iscc.Source installer\RealtimeAudioTranslator.iss
