param(
    [Parameter(Mandatory=$true)][string]$InstallRoot
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

function Stop-AgentService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $svc -and $svc.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        try { $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20)) } catch {}
    }
}

function Set-StateRootAcl {
    param([string]$Path)
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $adminsSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
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
    [void]$acl.AddAccessRule(
        [Security.AccessControl.FileSystemAccessRule]::new(
            $currentSid, $modify, $inherit, $propagation, $allow
        )
    )
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
Set-StateRootAcl -Path $ServiceRoot
Stop-AgentService

$ServiceEnv = Join-Path $ServiceRoot '.env'
$ExampleEnv = Join-Path $InstallRoot '.env.example'
if (-not (Test-Path $ServiceEnv)) {
    if (-not (Test-Path $ExampleEnv)) { throw '.env.example is missing from installer payload.' }
    Copy-Item -Path $ExampleEnv -Destination $ServiceEnv
}

# Replace only the service registration. Program files stay under the ACL-protected
# install directory; ProgramData contains only mutable settings, logs and memory.
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    & "$Python" -m agent_windows.windows_service remove | Out-Null
    if ($LASTEXITCODE -ne 0) {
        & sc.exe delete $ServiceName | Out-Null
    }
    Start-Sleep -Milliseconds 800
}

# Remove binaries created by pre-0.2 installer attempts after the old service is stopped.
Remove-Item -Path $LegacyRuntimeRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path $LegacyToolsRoot -Recurse -Force -ErrorAction SilentlyContinue

$env:AGENT_WINDOWS_HOME = $ServiceRoot
$env:AGENT_WINDOWS_INSTALL_ROOT = $InstallRoot
& "$Python" -m agent_windows.windows_service --startup auto install
if ($LASTEXITCODE -ne 0) { throw 'Windows service installation failed.' }

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

& sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/""/0 | Out-Null
Set-StateRootAcl -Path $ServiceRoot
