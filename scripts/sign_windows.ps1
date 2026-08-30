param(
  [Parameter(Mandatory = $true)]
  [string[]]$Files,
  [Parameter(Mandatory = $true)]
  [string]$CertificatePath,
  [Parameter(Mandatory = $true)]
  [string]$CertificatePassword,
  [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $CertificatePath)) {
  throw "Authenticode certificate not found."
}
$SignTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe |
  Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
  Sort-Object FullName -Descending |
  Select-Object -First 1
if (-not $SignTool) {
  throw "signtool.exe not found."
}
foreach ($File in $Files) {
  $Resolved = (Resolve-Path -LiteralPath $File).Path
  & $SignTool.FullName sign /fd SHA256 /td SHA256 /tr $TimestampUrl /f $CertificatePath /p $CertificatePassword $Resolved
  if ($LASTEXITCODE -ne 0) {
    throw "Authenticode signing failed: $Resolved"
  }
  & $SignTool.FullName verify /pa /all $Resolved
  if ($LASTEXITCODE -ne 0) {
    throw "Authenticode verification failed: $Resolved"
  }
}
