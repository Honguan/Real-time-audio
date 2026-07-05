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
if (Test-Path -LiteralPath (Join-Path $Root "assets")) {
  Copy-Item -LiteralPath (Join-Path $Root "assets") -Destination (Join-Path $AppStage "assets") -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination (Join-Path $AppStage "README.md") -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\RELEASE_NOTES.md") -Destination (Join-Path $AppStage "RELEASE_NOTES.md") -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\README_QUICK_START_zh-TW.txt") -Destination (Join-Path $AppStage "README_QUICK_START_zh-TW.txt") -Force
Set-Content -LiteralPath (Join-Path $AppStage "release_version.txt") -Value "$Version" -Encoding UTF8

$AppZip = Join-Path $Out "RealtimeAudioTranslator-$Version-win-x64.zip"
Compress-FolderContents $AppStage $AppZip
$Created += $AppZip

if (-not [string]::IsNullOrWhiteSpace($RuntimeSource)) {
  $CudaDlls = @("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
  if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource "faster-whisper-xxl.exe"))) {
    throw "RuntimeSource must contain faster-whisper-xxl.exe: $RuntimeSource"
  }
  if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource "ffmpeg.exe"))) {
    throw "RuntimeSource must contain ffmpeg.exe: $RuntimeSource"
  }
  if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource "_xxl_data"))) {
    throw "RuntimeSource must contain _xxl_data: $RuntimeSource"
  }
  foreach ($CudaDll in $CudaDlls) {
    if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource $CudaDll))) {
      throw "RuntimeSource must contain ${CudaDll}: $RuntimeSource"
    }
  }
  $RuntimeStage = Join-Path $Out "_stage_runtime"
  New-Item -ItemType Directory -Path $RuntimeStage | Out-Null
  Copy-Item -Path (Join-Path $RuntimeSource "*") -Destination $RuntimeStage -Recurse -Force
  @(
    "Extract to:",
    "%USERPROFILE%\.realtime-audio\runtime\cuda12",
    "",
    "The folder should directly contain faster-whisper-xxl.exe, ffmpeg.exe, _xxl_data, and CUDA12 DLL files."
  ) | Set-Content -LiteralPath (Join-Path $RuntimeStage "RUNTIME_README.txt") -Encoding UTF8
  @(
    "{",
    "  ""runtime"": ""faster-whisper-xxl"",",
    "  ""platform"": ""windows-x64"",",
    "  ""cuda"": ""12"",",
    "  ""version"": ""$Version""",
    "}"
  ) | Set-Content -LiteralPath (Join-Path $RuntimeStage "runtime_manifest.json") -Encoding UTF8
  $RuntimeZip = Join-Path $Out "RealtimeAudioTranslator-runtime-cuda12-$Version.zip"
  Compress-FolderContents $RuntimeStage $RuntimeZip
  $Created += $RuntimeZip
} else {
  Write-Warning "Runtime zip skipped. Pass -RuntimeSource to publish the runtime and CUDA DLL zip."
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
  Copy-Item -LiteralPath $ModelsSource -Destination (Join-Path $ModelsStage $ModelName) -Recurse -Force
  @(
    "Extract this model folder to:",
    "%USERPROFILE%\.realtime-audio\models",
    "",
    "After extraction, the model should be available as:",
    "%USERPROFILE%\.realtime-audio\models\$ModelName"
  ) | Set-Content -LiteralPath (Join-Path $ModelsStage "MODEL_README.txt") -Encoding UTF8
  $ModelsZip = Join-Path $Out "RealtimeAudioTranslator-models-$ModelName-$Version.zip"
  Compress-FolderContents $ModelsStage $ModelsZip
  $Created += $ModelsZip
}

$Checksums = Join-Path $Out "SHA256SUMS.txt"
$Sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
  $Created |
    ForEach-Object {
      $Path = $_
      $Stream = [System.IO.File]::OpenRead((Resolve-Path -LiteralPath $Path))
      try {
        $HashBytes = $Sha256.ComputeHash($Stream)
      } finally {
        $Stream.Dispose()
      }
      $Hash = ([BitConverter]::ToString($HashBytes)).Replace("-", "").ToUpperInvariant()
      "$Hash  $(Split-Path -Leaf $Path)"
    } |
    Set-Content -LiteralPath $Checksums -Encoding UTF8
} finally {
  $Sha256.Dispose()
}

Remove-Item -LiteralPath $AppStage -Recurse -Force
if (Test-Path -LiteralPath (Join-Path $Out "_stage_runtime")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_runtime") -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $Out "_stage_models")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_models") -Recurse -Force
}
