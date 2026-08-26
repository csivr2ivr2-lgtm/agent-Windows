$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$EnvFile = Join-Path $Root '.env'
$ServiceName = 'AgentWindowsAI'

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
    # Installs pywin32 on Windows because it is a platform-specific project dependency.
    & $Python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed.' }

    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        & $Python -m agent_windows.windows_service --startup auto install
    } else {
        if ($existing.Status -ne 'Stopped') {
            Stop-Service -Name $ServiceName -Force -ErrorAction Stop
            $existing.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15))
        }
        & $Python -m agent_windows.windows_service --startup auto update
    }
    if ($LASTEXITCODE -ne 0) { throw 'Windows service installation/update failed.' }

    # Start through the Service Control Manager and verify the actual state.
    Start-Service -Name $ServiceName -ErrorAction Stop
    $service = Get-Service -Name $ServiceName
    $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(20))
    $service.Refresh()
    if ($service.Status -ne 'Running') {
        throw "Windows service did not reach Running state (current: $($service.Status))."
    }

    # Ask Windows to restart the service after unexpected crashes.
    & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/""/0 | Out-Null

    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $runCommand = '"{0}" -m agent_windows.session_agent --env "{1}"' -f $Pythonw, $EnvFile
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -Path $runKey -Name 'AgentWindowsSession' -Value $runCommand -PropertyType String -Force | Out-Null

    # Start the per-user audio companion now. Windows services run in Session 0 and
    # cannot safely own the logged-in user's microphone/speakers.
    $alreadyRunning = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*agent_windows.session_agent*' }
    if (-not $alreadyRunning) {
        Start-Process -FilePath $Pythonw -ArgumentList @('-m','agent_windows.session_agent','--env',$EnvFile) -WorkingDirectory $Root
    }

    Start-Sleep -Seconds 2
    Write-Host ''
    Write-Host 'Agent Windows service installed and running.' -ForegroundColor Green
    Write-Host "Service: $ServiceName (Automatic / Running)"
    Write-Host 'Voice companion: starts automatically when you sign in.'
    Write-Host 'Press Ctrl+Alt+Space, then speak.'
    Write-Host "Log: $(Join-Path $Root 'data\session-agent.log')"
}
finally {
    Pop-Location
}
