param(
  [string]$Version = "",
  [string]$OutputDir = "",
  [string]$DistDir = "",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$script:Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

& (Join-Path $PSScriptRoot "package.ps1") `
  -Version $Version `
  -OutputDir $OutputDir `
  -DistDir $DistDir `
  -SkipBuild:$SkipBuild `
  -SkipRuntime `
  -SkipModels
