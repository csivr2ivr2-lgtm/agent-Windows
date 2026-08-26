$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$EnvFile = Join-Path $Root '.env'

if (-not (Test-Path $Python)) {
    throw "Virtual environment not found: $Python. Run scripts\setup.ps1 first."
}
if (-not (Test-Path $EnvFile)) {
    throw ".env not found: $EnvFile"
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this PowerShell script as Administrator.'
}

Push-Location $Root
try {
    & $Python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed.' }

    $existing = Get-Service -Name 'AgentWindowsAI' -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        & $Python -m agent_windows.windows_service --startup auto install
    } else {
        & $Python -m agent_windows.windows_service --startup auto update
    }
    if ($LASTEXITCODE -ne 0) { throw 'Windows service installation/update failed.' }

    & $Python -m agent_windows.windows_service start
    if ($LASTEXITCODE -ne 0) { throw 'Windows service failed to start.' }

    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $runCommand = '"{0}" -m agent_windows.session_agent --env "{1}"' -f $Pythonw, $EnvFile
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -Path $runKey -Name 'AgentWindowsSession' -Value $runCommand -PropertyType String -Force | Out-Null

    $alreadyRunning = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*agent_windows.session_agent*' }
    if (-not $alreadyRunning) {
        Start-Process -FilePath $Pythonw -ArgumentList @('-m','agent_windows.session_agent','--env',$EnvFile) -WorkingDirectory $Root
    }

    Start-Sleep -Seconds 2
    Write-Host ''
    Write-Host 'Agent Windows service installed.' -ForegroundColor Green
    Write-Host 'Service: AgentWindowsAI (Automatic)'
    Write-Host 'Voice companion: starts automatically when you sign in.'
    Write-Host 'Press Ctrl+Alt+Space, then speak.'
    Write-Host "Log: $(Join-Path $Root 'data\session-agent.log')"
}
finally {
    Pop-Location
}
