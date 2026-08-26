$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$UserPythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$EnvFile = Join-Path $Root '.env'
$IconPath = Join-Path $Root 'assets\ai-aharon.ico'
$IconBase64Path = Join-Path $Root 'assets\ai-aharon.ico.b64'

function Initialize-AiAharonIcon {
    if (Test-Path $IconPath) { return }
    if (-not (Test-Path $IconBase64Path)) {
        throw "Application icon source not found: $IconBase64Path"
    }
    $encoded = (Get-Content -Path $IconBase64Path -Raw).Trim()
    try {
        $bytes = [Convert]::FromBase64String($encoded)
    }
    catch {
        throw "Application icon source is invalid Base64: $IconBase64Path"
    }
    [IO.File]::WriteAllBytes($IconPath, $bytes)
}

if (-not (Test-Path $UserPythonw)) {
    throw "Virtual environment not found: $UserPythonw. Run scripts\setup.ps1 first."
}
if (-not (Test-Path $EnvFile)) {
    throw ".env not found: $EnvFile"
}
Initialize-AiAharonIcon
if (-not (Test-Path $IconPath)) {
    throw "Application icon not found: $IconPath"
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

    # Stable user-level project path for Command Prompt: cd /d "%AI-AHARON%"
    [Environment]::SetEnvironmentVariable('AI-AHARON', $Root, 'User')

    # The Windows service starts automatically. The voice client opens manually,
    # so the microphone never activates just because the user signed in.
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    Remove-ItemProperty -Path $runKey -Name 'AgentWindowsSession' -ErrorAction SilentlyContinue

    $desktop = [Environment]::GetFolderPath('Desktop')
    $oldShortcutPath = Join-Path $desktop 'Agent Windows AI.lnk'
    Remove-Item -Path $oldShortcutPath -Force -ErrorAction SilentlyContinue
    $shortcutPath = Join-Path $desktop 'ai aharon.lnk'
    Remove-Item -Path $shortcutPath -Force -ErrorAction SilentlyContinue
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $UserPythonw
    $shortcut.Arguments = '-m agent_windows.desktop_gui --env "{0}"' -f $EnvFile
    $shortcut.WorkingDirectory = $Root
    $shortcut.IconLocation = "$IconPath,0"
    $shortcut.Description = 'ai aharon'
    $shortcut.Save()

    Start-Process -FilePath $UserPythonw `
        -ArgumentList @('-m','agent_windows.desktop_gui','--env',$EnvFile) `
        -WorkingDirectory $Root

    Write-Host ''
    Write-Host 'ai aharon installed.' -ForegroundColor Green
    Write-Host "Desktop shortcut: $shortcutPath"
    Write-Host "Project path variable: AI-AHARON=$Root"
    Write-Host 'Opening ai aharon starts a continuous voice call immediately.'
    Write-Host 'The Windows service still starts automatically with Windows.'
    Write-Host 'No push-to-talk button is used.'
}
finally {
    Pop-Location
}
