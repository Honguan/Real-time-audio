param(
  [Parameter(Mandatory = $true)]
  [string]$RuntimeRoot,
  [ValidateSet("cpu", "cuda")]
  [string]$Device = "cpu"
)

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath($RuntimeRoot)
New-Item -ItemType Directory -Path $Root -Force | Out-Null

$CoreExe = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter "faster-whisper-xxl.exe" | Select-Object -First 1
if (-not $CoreExe) {
  throw "Downloaded runtime does not contain faster-whisper-xxl.exe."
}
if ($CoreExe.Directory.FullName -ne $Root) {
  Get-ChildItem -LiteralPath $CoreExe.Directory.FullName -Force | Move-Item -Destination $Root -Force
}

foreach ($Name in "ffmpeg.exe", "_xxl_data") {
  if (-not (Test-Path -LiteralPath (Join-Path $Root $Name))) {
    throw "Downloaded runtime is missing $Name."
  }
}

if ($Device -eq "cuda") {
  foreach ($Name in "cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll") {
    $Destination = Join-Path $Root $Name
    if (-not (Test-Path -LiteralPath $Destination)) {
      $Source = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $Name | Select-Object -First 1
      if (-not $Source) {
        throw "Downloaded CUDA package is missing $Name."
      }
      Move-Item -LiteralPath $Source.FullName -Destination $Destination -Force
    }
  }
}

$Manifest = Join-Path $Root "install_manifest.json"
$Entries = @(
  Get-ChildItem -LiteralPath $Root -Recurse -File |
    Where-Object { $_.FullName -ne $Manifest } |
    Sort-Object FullName |
    ForEach-Object {
      [ordered]@{
        path = $_.FullName.Substring($Root.Length + 1).Replace("\", "/")
        size = $_.Length
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
      }
    }
)
$Temporary = Join-Path (Split-Path -Parent $Root) ".$((Split-Path -Leaf $Root))-install_manifest.json.tmp"
$Json = [ordered]@{ version = 1; files = $Entries } | ConvertTo-Json -Depth 4
try {
  [IO.File]::WriteAllText($Temporary, $Json, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $Temporary -Destination $Manifest -Force
} finally {
  Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
}
