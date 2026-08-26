$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$UserPython = Join-Path $Root '.venv\Scripts\python.exe'
$UserPythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$EnvFile = Join-Path $Root '.env'
$ServiceName = 'AgentWindowsAI'

$ServiceRoot = Join-Path $env:ProgramData 'AgentWindowsAI'
$RuntimeRoot = Join-Path $ServiceRoot 'python-runtime'
$ServiceEnv = Join-Path $ServiceRoot '.env'
$ServiceData = Join-Path $ServiceRoot 'data'

if (-not (Test-Path $UserPython)) {
    throw "Virtual environment not found: $UserPython. Run scripts\setup.ps1 first."
}
if (-not (Test-Path $EnvFile)) {
    throw ".env not found: $EnvFile"
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this PowerShell script as Administrator.'
}

function Set-ServiceRootAcl {
    param([string]$Path)

    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $adminsSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')

    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)

    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $rights = [Security.AccessControl.FileSystemRights]::FullControl

    foreach ($sid in @($systemSid, $adminsSid, $currentSid)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid, $rights, $inherit, $propagation, $allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -Path $Path -AclObject $acl
}

function Stop-SessionCompanion {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*agent_windows.session_agent*' -or $_.CommandLine -like '*agent_windows.desktop_gui*' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

Push-Location $Root
try {
    Write-Host 'Preparing machine-wide service runtime under ProgramData...'

    New-Item -ItemType Directory -Path $ServiceRoot -Force | Out-Null
    Set-ServiceRootAcl -Path $ServiceRoot

    Stop-SessionCompanion

    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($existing.Status -ne 'Stopped') {
            Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            try { $existing.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15)) } catch {}
        }
        & sc.exe delete $ServiceName | Out-Null
        for ($i = 0; $i -lt 30; $i++) {
            if ($null -eq (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 500
        }
    }

    # The previous implementation hosted pythonservice.exe inside a venv.
    # It is no longer used for the Windows service. Remove it so a stale
    # host executable cannot be registered accidentally.
    $OldServiceVenv = Join-Path $ServiceRoot '.venv'
    if (Test-Path $OldServiceVenv) {
        Remove-Item -Path $OldServiceVenv -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path (Join-Path $RuntimeRoot 'python.exe'))) {
        $BasePython = (& $UserPython -c "import sys; print(sys.base_prefix)").Trim()
        if (-not (Test-Path (Join-Path $BasePython 'python.exe'))) {
            throw "Could not locate base Python installation: $BasePython"
        }

        New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
        Write-Host "Copying Python runtime from $BasePython ..."
        & robocopy.exe $BasePython $RuntimeRoot /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
        $robocopyExit = $LASTEXITCODE
        if ($robocopyExit -ge 8) {
            throw "Copying Python runtime failed (robocopy exit $robocopyExit)."
        }
    }

    $MachinePython = Join-Path $RuntimeRoot 'python.exe'
    if (-not (Test-Path $MachinePython)) {
        throw "Machine Python runtime is missing: $MachinePython"
    }

    # pywin32's pythonservice.exe is an embedded Python host, not the normal
    # venv launcher. Keep it next to python311.dll in the machine runtime.
    # This avoids the well-known virtualenv/pythonservice.exe startup failure
    # where SCM times out before the service can connect.
    $runtimePrefix = (& $MachinePython -c "import sys; print(sys.prefix)").Trim()
    if ($runtimePrefix -ne $RuntimeRoot) {
        throw "Copied Python runtime resolved unexpected prefix: '$runtimePrefix' (expected '$RuntimeRoot')."
    }

    Write-Host 'Installing service dependencies into machine runtime...'
    & $MachinePython -m pip install --disable-pip-version-check --upgrade "pywin32==312"
    if ($LASTEXITCODE -ne 0) { throw 'Installing pywin32 into machine runtime failed.' }

    Write-Host 'Installing current Agent Windows build into machine runtime...'
    & $MachinePython -m pip install --disable-pip-version-check --force-reinstall --no-deps $Root
    if ($LASTEXITCODE -ne 0) { throw 'Installing Agent Windows into machine runtime failed.' }

    Copy-Item -Path $EnvFile -Destination $ServiceEnv -Force

    New-Item -ItemType Directory -Path $ServiceData -Force | Out-Null

    $OldData = Join-Path $Root 'data'
    if (Test-Path $OldData) {
        & robocopy.exe $OldData $ServiceData /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
        $robocopyExit = $LASTEXITCODE
        if ($robocopyExit -ge 8) {
            throw "Copying Agent data failed (robocopy exit $robocopyExit)."
        }
    }
    Remove-Item -Path (Join-Path $ServiceData 'service.token') -Force -ErrorAction SilentlyContinue

    Set-ServiceRootAcl -Path $ServiceRoot

    Write-Host 'Installing Windows service from machine runtime...'
    & $MachinePython -m agent_windows.windows_service --startup auto install
    if ($LASTEXITCODE -ne 0) { throw 'Windows service installation failed.' }

    $pythonClassKey = "Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\$ServiceName\PythonClass"
    $expectedPythonClass = 'agent_windows.windows_service.AgentWindowsService'
    if (-not (Test-Path $pythonClassKey)) {
        throw "pywin32 PythonClass registry key is missing: $pythonClassKey"
    }
    $registeredPythonClass = (Get-Item $pythonClassKey).GetValue('')
    if ($registeredPythonClass -ne $expectedPythonClass) {
        throw "Invalid pywin32 PythonClass registration: '$registeredPythonClass' (expected '$expectedPythonClass')."
    }

    $serviceConfig = & sc.exe qc $ServiceName
    $expectedServiceExe = Join-Path $RuntimeRoot 'pythonservice.exe'
    if (($serviceConfig -join "`n") -notmatch [regex]::Escape($expectedServiceExe)) {
        throw "Service executable was not registered next to python311.dll: $expectedServiceExe"
    }

    Start-Service -Name $ServiceName -ErrorAction Stop
    $service = Get-Service -Name $ServiceName
    $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(25))
    $service.Refresh()
    if ($service.Status -ne 'Running') {
        throw "Windows service did not reach Running state (current: $($service.Status))."
    }

    $tokenPath = Join-Path $ServiceData 'service.token'
    for ($i = 0; $i -lt 40 -and -not (Test-Path $tokenPath); $i++) {
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path $tokenPath)) {
        throw "Service reached Running but did not create $tokenPath."
    }

    & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/""/0 | Out-Null

    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $runCommand = '"{0}" -m agent_windows.desktop_gui --env "{1}" --minimized' -f $UserPythonw, $EnvFile
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -Path $runKey -Name 'AgentWindowsSession' -Value $runCommand -PropertyType String -Force | Out-Null

    # Create a desktop shortcut for the graphical client.
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

    Start-Sleep -Seconds 2

    Write-Host ''
    Write-Host 'Agent Windows service installed and running.' -ForegroundColor Green
    Write-Host "Service: $ServiceName (Automatic / Running)"
    Write-Host "Service runtime: $ServiceRoot"
    Write-Host 'Graphical companion: starts automatically when you sign in.'
    Write-Host 'Use the desktop shortcut, the microphone button, or Ctrl+Alt+Space.'
    Write-Host "Log: $(Join-Path $OldData 'session-agent.log')"
}
finally {
    Pop-Location
}
