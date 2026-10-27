$ErrorActionPreference = 'Stop'
$Runtime = Join-Path $env:APPDATA 'CASE\python'
if (Test-Path $Runtime) { throw 'CASE Python runtime already exists; nothing changed.' }
# User-level dependencies only; do not change PATH, execution policy or profiles.
python -m venv $Runtime
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
& (Join-Path $Runtime 'Scripts\python.exe') -m pip install -r (Join-Path $PSScriptRoot 'requirements-windows.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; review the runtime before retrying.' }
Write-Output 'CASE Windows Python dependencies installed.'
