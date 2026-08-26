$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$UserPythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$EnvFile = Join-Path $Root '.env'

if (-not (Test-Path $UserPythonw)) {
    throw "Virtual environment not found: $UserPythonw. Run scripts\setup.ps1 first."
}
if (-not (Test-Path $EnvFile)) {
    throw ".env not found: $EnvFile"
}

function Stop-AgentCompanions {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like '*agent_windows.session_agent*' -or
            $_.CommandLine -like '*agent_windows.desktop_gui*'
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

Push-Location $Root
try {
    Stop-AgentCompanions

    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $runCommand = '"{0}" -m agent_windows.desktop_gui --env "{1}" --minimized' -f $UserPythonw, $EnvFile
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -Path $runKey -Name 'AgentWindowsSession' -Value $runCommand -PropertyType String -Force | Out-Null

    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'Agent Windows AI.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $UserPythonw
    $shortcut.Arguments = '-m agent_windows.desktop_gui --env "{0}"' -f $EnvFile
    $shortcut.WorkingDirectory = $Root
    $shortcut.Save()

    Start-Process -FilePath $UserPythonw `
        -ArgumentList @('-m','agent_windows.desktop_gui','--env',$EnvFile) `
        -WorkingDirectory $Root

    Write-Host ''
    Write-Host 'Agent Windows graphical interface installed.' -ForegroundColor Green
    Write-Host "Desktop shortcut: $shortcutPath"
    Write-Host 'It will start minimized automatically when you sign in.'
    Write-Host 'Ctrl+Alt+Space still starts voice input.'
}
finally {
    Pop-Location
}
