param(
  [Parameter(Mandatory = $true)]
  [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Tool = (Get-Content (Join-Path $Root "release-lock.json") -Raw | ConvertFrom-Json).installer.builder
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$Iscc = Join-Path $InstallDir "ISCC.exe"
if (Test-Path -LiteralPath $Iscc) {
  $Iscc
  exit 0
}
$Download = Join-Path $env:RUNNER_TEMP "innosetup-$($Tool.version)-x64.exe"
Invoke-WebRequest -Uri $Tool.url -OutFile $Download
$File = Get-Item -LiteralPath $Download
$Hash = (Get-FileHash -LiteralPath $Download -Algorithm SHA256).Hash.ToLowerInvariant()
if ($File.Length -ne $Tool.size -or $Hash -ne $Tool.sha256) {
  throw "Inno Setup download verification failed."
}
$Process = Start-Process -FilePath $Download `
  -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$InstallDir" `
  -WindowStyle Hidden `
  -Wait `
  -PassThru
if ($Process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Iscc)) {
  throw "Inno Setup installation failed: $($Process.ExitCode)"
}
$Iscc
