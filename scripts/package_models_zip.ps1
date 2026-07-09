param(
  [string]$Version = "",
  [string]$OutputDir = "",
  [string]$ModelsSource = "",
  [string]$ModelName = ""
)

$ErrorActionPreference = "Stop"
$script:Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

& (Join-Path $PSScriptRoot "package.ps1") `
  -Version $Version `
  -OutputDir $OutputDir `
  -ModelsSource $ModelsSource `
  -ModelName $ModelName `
  -SkipBuild `
  -SkipApp `
  -SkipRuntime `
  -AppendOutput
