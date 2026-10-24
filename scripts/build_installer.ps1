param(
  [Parameter(Mandatory = $true)]
  [string]$Version,
  [Parameter(Mandatory = $true)]
  [string]$IsccPath,
  [string]$DistDir = "",
  [string]$ReleaseDir = "",
  [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if ($Version -notmatch '^v\d+\.\d+\.\d+$') {
  throw "Installer version must be a semantic tag such as v0.1.35."
}
if (-not (Test-Path -LiteralPath $IsccPath)) {
  throw "ISCC.exe not found: $IsccPath"
}
# Older or beta Inno Setup builds lack this file and ISCC then fails with only exit code 2.
$ChineseMessages = Join-Path (Split-Path -Parent $IsccPath) "Languages\ChineseTraditional.isl"
if (-not (Test-Path -LiteralPath $ChineseMessages)) {
  throw "Inno Setup is missing $ChineseMessages. Install the pinned version with scripts\install_inno_setup.ps1."
}
$DistDir = if ($DistDir) { [IO.Path]::GetFullPath($DistDir) } else { Join-Path $Root "dist\RealtimeAudioTranslator" }
$ReleaseDir = if ($ReleaseDir) { [IO.Path]::GetFullPath($ReleaseDir) } else { Join-Path $Root "dist-release" }
$OutputDir = if ($OutputDir) { [IO.Path]::GetFullPath($OutputDir) } else { $ReleaseDir }
if (-not (Test-Path -LiteralPath (Join-Path $DistDir "RealtimeAudioTranslator.exe"))) {
  throw "Packaged application not found: $DistDir"
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$Lock = Get-Content (Join-Path $Root "release-lock.json") -Raw | ConvertFrom-Json
$Core = $Lock.installer.external_runtime.core
$Cuda = $Lock.installer.external_runtime.cuda
$Arguments = @(
  "/DAppVersion=$($Version.Substring(1))",
  "/DReleaseTag=$Version",
  "/DRepoRoot=$Root",
  "/DSourceDir=$DistDir",
  "/DReleaseDir=$ReleaseDir",
  "/DOutputDir=$OutputDir",
  "/DRuntimeCoreUrl=$($Core.url)",
  "/DRuntimeCoreSize=$($Core.size)",
  "/DRuntimeCoreHash=$($Core.sha256)",
  "/DRuntimeCudaUrl=$($Cuda.url)",
  "/DRuntimeCudaSize=$($Cuda.size)",
  "/DRuntimeCudaHash=$($Cuda.sha256)",
  (Join-Path $Root "installer\RealtimeAudioTranslator.iss")
)
$CompilerOutput = & $IsccPath @Arguments 2>&1
$CompilerExitCode = $LASTEXITCODE
$CompilerOutput | ForEach-Object { [Console]::Error.WriteLine($_) }
if ($CompilerExitCode -ne 0) {
  throw "Inno Setup compilation failed: $CompilerExitCode"
}
$Installer = Join-Path $OutputDir "RealtimeAudioTranslator-$Version-setup.exe"
if (-not (Test-Path -LiteralPath $Installer)) {
  throw "Installer output not found: $Installer"
}
