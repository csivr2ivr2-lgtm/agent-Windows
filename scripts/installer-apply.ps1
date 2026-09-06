param(
    [Parameter(Mandatory=$true)][string]$InstallRoot
)

$ErrorActionPreference = 'Stop'
$ServiceName = 'AgentWindowsAI'
$ServiceRoot = Join-Path $env:ProgramData $ServiceName
$DataRoot = Join-Path $ServiceRoot 'data'
$RuntimeRoot = Join-Path $ServiceRoot 'python-runtime'
$ToolsRoot = Join-Path $ServiceRoot 'tools'

function Stop-AgentService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $svc -and $svc.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        try { $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20)) } catch {}
    }
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
        [void]$acl.AddAccessRule(
            [Security.AccessControl.FileSystemAccessRule]::new(
                $sid, $rights, $inherit, $propagation, $allow
            )
        )
    }
    Set-Acl -Path $Path -AclObject $acl
}

New-Item -ItemType Directory -Path $ServiceRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
Set-ServiceRootAcl -Path $ServiceRoot
Stop-AgentService

$BundledRuntime = Join-Path $InstallRoot 'python-runtime'
if (-not (Test-Path (Join-Path $BundledRuntime 'python.exe'))) {
    throw "Bundled Python runtime is missing: $BundledRuntime"
}

if (Test-Path $RuntimeRoot) {
    Remove-Item -Path $RuntimeRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
& robocopy.exe "$BundledRuntime" "$RuntimeRoot" /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) {
    throw "Installing bundled Python runtime failed (robocopy exit $LASTEXITCODE)."
}

$BundledTools = Join-Path $InstallRoot 'tools'
if (Test-Path $BundledTools) {
    if (Test-Path $ToolsRoot) { Remove-Item -Path $ToolsRoot -Recurse -Force }
    New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null
    & robocopy.exe "$BundledTools" "$ToolsRoot" /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) {
        throw "Installing bundled media tools failed (robocopy exit $LASTEXITCODE)."
    }
}

$Python = Join-Path $RuntimeRoot 'python.exe'
& "$Python" -m pip install --disable-pip-version-check --no-index --no-build-isolation --force-reinstall --no-deps "$InstallRoot"
if ($LASTEXITCODE -ne 0) { throw 'Installing AI Aharon package failed.' }

$ServiceEnv = Join-Path $ServiceRoot '.env'
$ExampleEnv = Join-Path $InstallRoot '.env.example'
if (-not (Test-Path $ServiceEnv)) {
    if (-not (Test-Path $ExampleEnv)) { throw '.env.example is missing from installer payload.' }
    Copy-Item $ExampleEnv $ServiceEnv
}

# Preserve memory and settings across upgrades. Only stale service registration is replaced.
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    & "$Python" -m agent_windows.windows_service remove | Out-Null
    Start-Sleep -Milliseconds 800
}

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
Set-ServiceRootAcl -Path $ServiceRoot
