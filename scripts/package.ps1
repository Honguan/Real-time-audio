param(
  [string]$Version = "",
  [string]$OutputDir = "",
  [string]$DistDir = "",
  [string]$RuntimeSource = "",
  [string]$ModelsSource = "",
  [string]$ModelName = "",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($Version)) {
  $Version = (git describe --tags --always 2>$null)
  if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = "dev"
  }
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $Root "release-output"
}
if ([string]::IsNullOrWhiteSpace($DistDir)) {
  $DistDir = Join-Path $Root "dist\RealtimeAudioTranslator"
}

if (-not $SkipBuild) {
  .\scripts\build.ps1
}

$Out = $OutputDir
if (Test-Path -LiteralPath $Out) {
  Remove-Item -LiteralPath $Out -Recurse -Force
}
New-Item -ItemType Directory -Path $Out | Out-Null

$AppExe = Join-Path $DistDir "RealtimeAudioTranslator.exe"
if (-not (Test-Path -LiteralPath $AppExe)) {
  throw "Missing app build: $AppExe"
}

$Created = @()

function Compress-FolderContents($SourceDir, $DestinationZip) {
  $Items = Get-ChildItem -LiteralPath $SourceDir -Force
  if (-not $Items) {
    throw "Nothing to zip: $SourceDir"
  }
  Compress-Archive -LiteralPath $Items.FullName -DestinationPath $DestinationZip -CompressionLevel Optimal
}

$AppStage = Join-Path $Out "_stage_app"
New-Item -ItemType Directory -Path $AppStage | Out-Null
Copy-Item -Path (Join-Path $DistDir "*") -Destination $AppStage -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination (Join-Path $AppStage "README.md") -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\RELEASE_NOTES.md") -Destination (Join-Path $AppStage "RELEASE_NOTES.md") -Force
@(
  "Quick start:",
  "1. Run RealtimeAudioTranslator.exe.",
  "2. If runtime is missing, extract the runtime zip to %USERPROFILE%\.realtime-audio\runtime.",
  "3. If models are missing, download them in the app or extract a model zip to %USERPROFILE%\.realtime-audio\models."
) | Set-Content -LiteralPath (Join-Path $AppStage "START_HERE.txt") -Encoding UTF8

$AppZip = Join-Path $Out "RealtimeAudioTranslator-$Version-win-x64.zip"
Compress-FolderContents $AppStage $AppZip
$Created += $AppZip

if (-not [string]::IsNullOrWhiteSpace($RuntimeSource)) {
  if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource "faster-whisper-xxl.exe"))) {
    throw "RuntimeSource must contain faster-whisper-xxl.exe: $RuntimeSource"
  }
  $RuntimeStage = Join-Path $Out "_stage_runtime"
  New-Item -ItemType Directory -Path $RuntimeStage | Out-Null
  Copy-Item -Path (Join-Path $RuntimeSource "*") -Destination $RuntimeStage -Recurse -Force
  @(
    "Extract to:",
    "%USERPROFILE%\.realtime-audio\runtime",
    "",
    "The folder should directly contain faster-whisper-xxl.exe."
  ) | Set-Content -LiteralPath (Join-Path $RuntimeStage "RUNTIME_README.txt") -Encoding UTF8
  $RuntimeZip = Join-Path $Out "RealtimeAudioTranslator-runtime-cuda12-$Version.zip"
  Compress-FolderContents $RuntimeStage $RuntimeZip
  $Created += $RuntimeZip
} else {
  Write-Warning "Runtime zip skipped. Pass -RuntimeSource to publish RealtimeAudioTranslator-runtime-cuda12-$Version.zip."
}

if (-not [string]::IsNullOrWhiteSpace($ModelsSource)) {
  if (-not (Test-Path -LiteralPath $ModelsSource)) {
    throw "ModelsSource not found: $ModelsSource"
  }
  if ([string]::IsNullOrWhiteSpace($ModelName)) {
    $ModelName = Split-Path -Leaf $ModelsSource
  }
  $ModelsStage = Join-Path $Out "_stage_models"
  New-Item -ItemType Directory -Path $ModelsStage | Out-Null
  Copy-Item -Path (Join-Path $ModelsSource "*") -Destination $ModelsStage -Recurse -Force
  $ModelsZip = Join-Path $Out "RealtimeAudioTranslator-models-$ModelName-$Version.zip"
  Compress-FolderContents $ModelsStage $ModelsZip
  $Created += $ModelsZip
}

$Checksums = Join-Path $Out "SHA256SUMS.txt"
$Created |
  ForEach-Object {
    $Hash = Get-FileHash -LiteralPath $_ -Algorithm SHA256
    "$($Hash.Hash)  $(Split-Path -Leaf $Hash.Path)"
  } |
  Set-Content -LiteralPath $Checksums -Encoding UTF8

Remove-Item -LiteralPath $AppStage -Recurse -Force
if (Test-Path -LiteralPath (Join-Path $Out "_stage_runtime")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_runtime") -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $Out "_stage_models")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_models") -Recurse -Force
}
