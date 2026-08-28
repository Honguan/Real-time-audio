param([string]$Python = "python")

$ErrorActionPreference = "Stop"

& $Python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m compileall -q realtime_audio_translator tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
