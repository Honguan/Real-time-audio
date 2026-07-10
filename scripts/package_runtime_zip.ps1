param(
  [string]$Version = "",
  [string]$OutputDir = "",
  [string]$RuntimeSource = "",
  [switch]$SplitRuntime
)

$ErrorActionPreference = "Stop"
$script:Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

& (Join-Path $PSScriptRoot "package.ps1") `
  -Version $Version `
  -OutputDir $OutputDir `
  -RuntimeSource $RuntimeSource `
  -SkipBuild `
  -SkipApp `
  -SkipModels `
  -SplitRuntime:$SplitRuntime `
  -AppendOutput
