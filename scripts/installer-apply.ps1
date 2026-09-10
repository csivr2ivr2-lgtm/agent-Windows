param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [switch]$AllowServiceFailure
)

$ErrorActionPreference = 'Stop'
$ServiceName = 'AgentWindowsAI'
$ServiceRoot = Join-Path $env:ProgramData $ServiceName
$DataRoot = Join-Path $ServiceRoot 'data'
$RuntimeRoot = Join-Path $InstallRoot 'python-runtime'
$ToolsRoot = Join-Path $InstallRoot 'tools'
$LegacyRuntimeRoot = Join-Path $ServiceRoot 'python-runtime'
$LegacyToolsRoot = Join-Path $ServiceRoot 'tools'
$Python = Join-Path $RuntimeRoot 'python.exe'
$InstallLog = Join-Path $ServiceRoot 'install.log'
$ServiceErrorMarker = Join-Path $ServiceRoot 'service-install-error.txt'

function Write-InstallLog {
    param([Parameter(Mandatory=$true)][string]$Message)

    try {
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
        Add-Content -LiteralPath $InstallLog -Value "[$stamp] $Message" -Encoding UTF8
    } catch {
        Write-Verbose "Could not write installer log: $($_.Exception.Message)"
    }
}

function Stop-AgentService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $svc -and $svc.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        try {
            $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
        } catch {
            Write-InstallLog "Timed out waiting for service stop: $($_.Exception.Message)"
        }
    }
}

function Set-StateRootAcl {
    param([Parameter(Mandatory=$true)][string]$Path)

    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $adminsSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $localServiceSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-19')
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $full = [Security.AccessControl.FileSystemRights]::FullControl
    $modify = [Security.AccessControl.FileSystemRights]::Modify

    foreach ($sid in @($systemSid, $adminsSid)) {
        [void]$acl.AddAccessRule(
            [Security.AccessControl.FileSystemAccessRule]::new(
                $sid, $full, $inherit, $propagation, $allow
            )
        )
    }
    foreach ($sid in @($currentSid, $localServiceSid)) {
        [void]$acl.AddAccessRule(
            [Security.AccessControl.FileSystemAccessRule]::new(
                $sid, $modify, $inherit, $propagation, $allow
            )
        )
    }
    Set-Acl -Path $Path -AclObject $acl
}

if (-not (Test-Path $Python)) {
    throw "Bundled Python runtime is missing: $Python"
}
if (-not (Test-Path (Join-Path $RuntimeRoot 'pythonservice.exe'))) {
    throw "Bundled Windows service host is missing from $RuntimeRoot"
}
if (-not (Test-Path $ToolsRoot)) {
    throw "Bundled media tools are missing: $ToolsRoot"
}

New-Item -ItemType Directory -Path $ServiceRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
Write-InstallLog "=== AI Aharon installer configuration started ==="
Write-InstallLog "InstallRoot=$InstallRoot"
Write-InstallLog "RuntimeRoot=$RuntimeRoot"
Write-InstallLog "AllowServiceFailure=$AllowServiceFailure"

try {
    Set-StateRootAcl -Path $ServiceRoot
    Write-InstallLog 'ProgramData ACL configured.'

    Stop-AgentService

    $ServiceEnv = Join-Path $ServiceRoot '.env'
    $ExampleEnv = Join-Path $InstallRoot '.env.example'
    if (-not (Test-Path $ServiceEnv)) {
        if (-not (Test-Path $ExampleEnv)) {
            throw '.env.example is missing from installer payload.'
        }
        Copy-Item -Path $ExampleEnv -Destination $ServiceEnv
        Write-InstallLog 'Created .env from .env.example.'
    } else {
        Write-InstallLog 'Preserving existing .env.'
    }

    # Replace only the service registration. Program files stay under the ACL-protected
    # install directory; ProgramData contains only mutable settings, logs and memory.
    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Write-InstallLog 'Removing existing Windows service registration.'
        $removeOutput = & "$Python" -m agent_windows.windows_service remove 2>&1
        foreach ($line in @($removeOutput)) {
            Write-InstallLog "service-remove: $line"
        }
        if ($LASTEXITCODE -ne 0) {
            Write-InstallLog "pywin32 remove exited $LASTEXITCODE; falling back to sc.exe delete."
            $deleteOutput = & sc.exe delete $ServiceName 2>&1
            foreach ($line in @($deleteOutput)) {
                Write-InstallLog "sc-delete: $line"
            }
        }
        Start-Sleep -Milliseconds 800
    }

    # Remove binaries created by pre-0.2 installer attempts after the old service is stopped.
    Remove-Item -Path $LegacyRuntimeRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $LegacyToolsRoot -Recurse -Force -ErrorAction SilentlyContinue

    $env:AGENT_WINDOWS_HOME = $ServiceRoot
    $env:AGENT_WINDOWS_INSTALL_ROOT = $InstallRoot

    $serviceInstalled = $false
    try {
        Write-InstallLog 'Installing AgentWindowsAI service with pywin32.'
        $installOutput = & "$Python" -m agent_windows.windows_service --startup auto install 2>&1
        foreach ($line in @($installOutput)) {
            Write-InstallLog "service-install: $line"
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Windows service installation failed (exit code $LASTEXITCODE)."
        }

        # Never expose the authenticated local chat API through an administrative service
        # account. LocalService can access the explicit ProgramData state ACL but cannot
        # turn a normal desktop user into LocalSystem through agent tool execution.
        Write-InstallLog 'Configuring AgentWindowsAI to run as LocalService.'
        $configOutput = & sc.exe config $ServiceName obj= "NT AUTHORITY\LocalService" password= "" 2>&1
        foreach ($line in @($configOutput)) {
            Write-InstallLog "sc-config: $line"
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to configure AgentWindowsAI as LocalService (exit code $LASTEXITCODE)."
        }

        Write-InstallLog 'Starting AgentWindowsAI service.'
        Start-Service -Name $ServiceName
        $service = Get-Service -Name $ServiceName
        $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(25))
        $service.Refresh()
        if ($service.Status -ne 'Running') {
            throw "Agent Windows service did not reach Running state ($($service.Status))."
        }

        $tokenPath = Join-Path $DataRoot 'service.token'
        for ($i = 0; $i -lt 40 -and -not (Test-Path $tokenPath); $i++) {
            Start-Sleep -Milliseconds 250
        }
        if (-not (Test-Path $tokenPath)) {
            throw "Service started but did not create $tokenPath"
        }

        $failureOutput = & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/""/0 2>&1
        foreach ($line in @($failureOutput)) {
            Write-InstallLog "sc-failure: $line"
        }

        Remove-Item -LiteralPath $ServiceErrorMarker -Force -ErrorAction SilentlyContinue
        $serviceInstalled = $true
        Write-InstallLog 'AgentWindowsAI service installed and running.'
    } catch {
        $message = $_.Exception.Message
        $details = ($_ | Out-String).Trim()
        Write-InstallLog "SERVICE SETUP FAILED: $message"
        if ($details) {
            Write-InstallLog $details
        }

        @(
            'AI Aharon desktop application was installed, but the background service could not be configured.'
            "Reason: $message"
            "Log: $InstallLog"
        ) | Set-Content -LiteralPath $ServiceErrorMarker -Encoding UTF8

        Stop-AgentService

        if (-not $AllowServiceFailure) {
            throw
        }

        Write-Warning "AI Aharon background service setup failed; continuing with desktop fallback. See $InstallLog"
    }

    Set-StateRootAcl -Path $ServiceRoot
    if ($serviceInstalled) {
        Write-InstallLog 'Configuration completed successfully.'
    } else {
        Write-InstallLog 'Configuration completed with desktop fallback; service repair is required.'
    }
} catch {
    Write-InstallLog "FATAL INSTALLER CONFIGURATION ERROR: $($_.Exception.Message)"
    throw
}
