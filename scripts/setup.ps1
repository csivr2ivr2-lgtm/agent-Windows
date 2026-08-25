$ErrorActionPreference = "Stop"

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11 or newer is required. Install it from python.org, then rerun this script."
}

$Python = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
if ($Python -eq "py") { & py -3.11 -m venv .venv } else { & python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e .

if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host "Core installation complete. Edit .env, then run:"
Write-Host ".\.venv\Scripts\agent-windows.exe doctor"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "FFmpeg was not found. Voice mode stays disabled until you install FFmpeg and add it to PATH."
}
