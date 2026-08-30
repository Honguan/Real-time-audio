param(
  [Parameter(Mandatory = $true)]
  [string]$OutputPath,
  [string]$NvidiaSmiOutput = "",
  [string[]]$SoundDeviceNames = @(),
  [switch]$UseSystemDetection
)

$ErrorActionPreference = "Stop"

if ($UseSystemDetection) {
  $NvidiaSmi = Join-Path $env:SystemRoot "System32\nvidia-smi.exe"
  if (Test-Path -LiteralPath $NvidiaSmi) {
    $NvidiaSmiOutput = (& $NvidiaSmi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
      $NvidiaSmiOutput = ""
    }
  }
  try {
    $SoundDeviceNames = @(Get-CimInstance Win32_SoundDevice | ForEach-Object { [string]$_.Name })
  } catch {
    $SoundDeviceNames = @()
  }
}

$MemoryMb = @(
  $NvidiaSmiOutput -split "`r?`n" |
    ForEach-Object { if ($_ -match '(\d+)') { [int]$Matches[1] } }
)
$GpuCount = $MemoryMb.Count
$VramGb = if ($MemoryMb.Count) { [int][math]::Ceiling(($MemoryMb | Measure-Object -Maximum).Maximum / 1024) } else { 0 }
$Runtime = if ($GpuCount -gt 0) { "cuda" } else { "cpu" }
$VbCable = [bool]($SoundDeviceNames | Where-Object { $_ -match 'VB-Audio|CABLE (Input|Output)' } | Select-Object -First 1)

@(
  "runtime=$Runtime"
  "gpu_count=$GpuCount"
  "vram_gb=$VramGb"
  "vb_cable=$($VbCable.ToString().ToLowerInvariant())"
) | Set-Content -LiteralPath $OutputPath -Encoding UTF8
