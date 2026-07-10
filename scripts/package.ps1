param(
  [string]$Version = "",
  [string]$OutputDir = "",
  [string]$DistDir = "",
  [string]$RuntimeSource = "",
  [string]$ModelsSource = "",
  [string]$ModelName = "",
  [switch]$SkipBuild,
  [switch]$SkipApp,
  [switch]$SkipRuntime,
  [switch]$SkipModels,
  [switch]$SplitRuntime,
  [switch]$AppendOutput
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
  $OutputDir = Join-Path $Root "dist-release"
}
if ([string]::IsNullOrWhiteSpace($DistDir)) {
  $DistDir = Join-Path $Root "dist\RealtimeAudioTranslator"
}

if (-not $SkipBuild) {
  .\scripts\build.ps1
}

$Out = $OutputDir
if ((-not $AppendOutput) -and (Test-Path -LiteralPath $Out)) {
  Remove-Item -LiteralPath $Out -Recurse -Force
}
if (-not (Test-Path -LiteralPath $Out)) {
  New-Item -ItemType Directory -Path $Out | Out-Null
}

function New-UnicodeString([int[]]$Codes) {
  -join ($Codes | ForEach-Object { [char]$_ })
}

$ExtractToLabel = New-UnicodeString @(0x89E3, 0x58D3, 0x7E2E, 0x5230, 0xFF1A)
$RuntimeFilesLabel = New-UnicodeString @(0x89E3, 0x58D3, 0x5F8C, 0x9019, 0x500B, 0x8CC7, 0x6599, 0x593E, 0x5167, 0x61C9, 0x76F4, 0x63A5, 0x5305, 0x542B, 0xFF1A)
$ModelFolderLabel = New-UnicodeString @(0x89E3, 0x58D3, 0x5F8C, 0x6A21, 0x578B, 0x8CC7, 0x6599, 0x593E, 0x61C9, 0x4F4D, 0x65BC, 0xFF1A)
$MissingAppBuildLabel = (New-UnicodeString @(0x627E, 0x4E0D, 0x5230)) + " app " + (New-UnicodeString @(0x5EFA, 0x7F6E, 0xFF1A))
$EmptyZipLabel = "zip " + (New-UnicodeString @(0x6C92, 0x6709, 0x53EF, 0x58D3, 0x7E2E, 0x5167, 0x5BB9, 0xFF1A))
$RuntimeMissingLabel = "runtime " + (New-UnicodeString @(0x8CC7, 0x6599, 0x593E, 0x7F3A, 0x5C11, 0xFF1A))
$ModelsMissingLabel = "ModelsSource " + (New-UnicodeString @(0x4E0D, 0x5B58, 0x5728, 0xFF1A))
$RuntimeSkippedWarning = (New-UnicodeString @(0x5DF2, 0x7565, 0x904E)) + " runtime zip" + (New-UnicodeString @(0xFF1B, 0x9700, 0x8981, 0x767C, 0x5E03)) + " runtime " + (New-UnicodeString @(0x8207)) + " CUDA DLL " + (New-UnicodeString @(0x6642, 0x8ACB, 0x52A0, 0x4E0A)) + " -RuntimeSource" + (New-UnicodeString @(0x3002))

$CreateApp = -not $SkipApp
$CreateRuntime = (-not $SkipRuntime) -and (-not [string]::IsNullOrWhiteSpace($RuntimeSource))
$CreateModels = (-not $SkipModels) -and (-not [string]::IsNullOrWhiteSpace($ModelsSource))

if (-not ($CreateApp -or $CreateRuntime -or $CreateModels)) {
  throw "Nothing selected to package."
}

function Compress-FolderContents($SourceDir, $DestinationZip) {
  $Items = Get-ChildItem -LiteralPath $SourceDir -Force
  if (-not $Items) {
    throw "$EmptyZipLabel$SourceDir"
  }
  Compress-Archive -LiteralPath $Items.FullName -DestinationPath $DestinationZip -CompressionLevel Optimal
}

if ($CreateApp) {
  $AppExe = Join-Path $DistDir "RealtimeAudioTranslator.exe"
  if (-not (Test-Path -LiteralPath $AppExe)) {
    throw "$MissingAppBuildLabel$AppExe"
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
}

if ($CreateRuntime) {
  $CudaDlls = @("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
  if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource "faster-whisper-xxl.exe"))) {
    throw "$RuntimeMissingLabel faster-whisper-xxl.exe ($RuntimeSource)"
  }
  if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource "ffmpeg.exe"))) {
    throw "$RuntimeMissingLabel ffmpeg.exe ($RuntimeSource)"
  }
  if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource "_xxl_data"))) {
    throw "$RuntimeMissingLabel _xxl_data ($RuntimeSource)"
  }
  foreach ($CudaDll in $CudaDlls) {
    if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource $CudaDll))) {
      throw "$RuntimeMissingLabel ${CudaDll} ($RuntimeSource)"
    }
  }
  if ($SplitRuntime) {
    $RuntimeCoreStage = Join-Path $Out "_stage_runtime_core"
    $RuntimeDllStage = Join-Path $Out "_stage_runtime_dlls"
    New-Item -ItemType Directory -Path $RuntimeCoreStage | Out-Null
    New-Item -ItemType Directory -Path $RuntimeDllStage | Out-Null
    Get-ChildItem -LiteralPath $RuntimeSource -Force |
      Where-Object { $_.Name -notin $CudaDlls } |
      Copy-Item -Destination $RuntimeCoreStage -Recurse -Force
    foreach ($CudaDll in $CudaDlls) {
      Copy-Item -LiteralPath (Join-Path $RuntimeSource $CudaDll) -Destination $RuntimeDllStage -Force
    }
    @(
      "Extract this archive and the CUDA DLL archive to:",
      "%USERPROFILE%\.realtime-audio\runtime\cuda12",
      "",
      "This archive contains faster-whisper-xxl.exe, ffmpeg.exe, and _xxl_data."
    ) | Set-Content -LiteralPath (Join-Path $RuntimeCoreStage "RUNTIME_README.txt") -Encoding UTF8
    @(
      "Extract this archive and the runtime core archive to:",
      "%USERPROFILE%\.realtime-audio\runtime\cuda12",
      "",
      "This archive contains the required CUDA12 DLL files."
    ) | Set-Content -LiteralPath (Join-Path $RuntimeDllStage "RUNTIME_README.txt") -Encoding UTF8
    @(
      "{",
      "  ""runtime"": ""faster-whisper-xxl"",",
      "  ""package"": ""core"",",
      "  ""platform"": ""windows-x64"",",
      "  ""cuda"": ""12"",",
      "  ""version"": ""$Version""",
      "}"
    ) | Set-Content -LiteralPath (Join-Path $RuntimeCoreStage "runtime_manifest.json") -Encoding UTF8
    @(
      "{",
      "  ""runtime"": ""faster-whisper-xxl"",",
      "  ""package"": ""cuda12-dlls"",",
      "  ""platform"": ""windows-x64"",",
      "  ""cuda"": ""12"",",
      "  ""version"": ""$Version""",
      "}"
    ) | Set-Content -LiteralPath (Join-Path $RuntimeDllStage "runtime_manifest.json") -Encoding UTF8
    Compress-FolderContents $RuntimeCoreStage (Join-Path $Out "RealtimeAudioTranslator-runtime-cuda12-core-$Version.zip")
    Compress-FolderContents $RuntimeDllStage (Join-Path $Out "RealtimeAudioTranslator-runtime-cuda12-dlls-$Version.zip")
  } else {
    $RuntimeStage = Join-Path $Out "_stage_runtime"
    New-Item -ItemType Directory -Path $RuntimeStage | Out-Null
    Copy-Item -Path (Join-Path $RuntimeSource "*") -Destination $RuntimeStage -Recurse -Force
    @(
      $ExtractToLabel,
      "%USERPROFILE%\.realtime-audio\runtime\cuda12",
      "",
      $RuntimeFilesLabel,
      "faster-whisper-xxl.exe, ffmpeg.exe, _xxl_data, CUDA12 DLL"
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
  }
} elseif ((-not $SkipRuntime) -and (-not $AppendOutput)) {
  Write-Warning $RuntimeSkippedWarning
}

if ($CreateModels) {
  if (-not (Test-Path -LiteralPath $ModelsSource)) {
    throw "$ModelsMissingLabel$ModelsSource"
  }
  if ([string]::IsNullOrWhiteSpace($ModelName)) {
    $ModelName = Split-Path -Leaf $ModelsSource
  }
  $ModelsStage = Join-Path $Out "_stage_models"
  New-Item -ItemType Directory -Path $ModelsStage | Out-Null
  Copy-Item -LiteralPath $ModelsSource -Destination (Join-Path $ModelsStage $ModelName) -Recurse -Force
  @(
    $ExtractToLabel,
    "%USERPROFILE%\.realtime-audio\models",
    "",
    $ModelFolderLabel,
    "%USERPROFILE%\.realtime-audio\models\$ModelName"
  ) | Set-Content -LiteralPath (Join-Path $ModelsStage "MODEL_README.txt") -Encoding UTF8
  $ModelsZip = Join-Path $Out "RealtimeAudioTranslator-models-$ModelName-$Version.zip"
  Compress-FolderContents $ModelsStage $ModelsZip
}

$Checksums = Join-Path $Out "SHA256SUMS.txt"
$Sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
  Get-ChildItem -LiteralPath $Out -Filter *.zip |
    ForEach-Object {
      $Path = $_.FullName
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

if (Test-Path -LiteralPath (Join-Path $Out "_stage_app")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_app") -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $Out "_stage_runtime")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_runtime") -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $Out "_stage_runtime_core")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_runtime_core") -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $Out "_stage_runtime_dlls")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_runtime_dlls") -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $Out "_stage_models")) {
  Remove-Item -LiteralPath (Join-Path $Out "_stage_models") -Recurse -Force
}
