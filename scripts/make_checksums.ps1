param(
  [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $Root "dist-release"
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
  throw "OutputDir not found: $OutputDir"
}

$Checksums = Join-Path $OutputDir "SHA256SUMS.txt"
$Sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
  Get-ChildItem -LiteralPath $OutputDir -Filter *.zip |
    ForEach-Object {
      $Stream = [System.IO.File]::OpenRead($_.FullName)
      try {
        $HashBytes = $Sha256.ComputeHash($Stream)
      } finally {
        $Stream.Dispose()
      }
      $Hash = ([BitConverter]::ToString($HashBytes)).Replace("-", "").ToUpperInvariant()
      "$Hash  $($_.Name)"
    } |
    Set-Content -LiteralPath $Checksums -Encoding UTF8
} finally {
  $Sha256.Dispose()
}
