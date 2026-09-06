param(
    [Parameter(Mandatory=$true)][string]$InstallRoot
)

$ErrorActionPreference = 'Stop'
$ServiceName = 'AgentWindowsAI'
$ServiceRoot = Join-Path $env:ProgramData $ServiceName
$DataRoot = Join-Path $ServiceRoot 'data'
$RuntimeRoot = Join-Path $ServiceRoot 'python-runtime'
$AppRoot = Join-Path $ServiceRoot 'app'

function Stop-AgentService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $svc -and $svc.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        try { $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20)) } catch {}
    }
}

New-Item -ItemType Directory -Path $ServiceRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
New-Item -ItemType Directory -Path $AppRoot -Force | Out-Null
Stop-AgentService

# Application files are replaceable; persistent data/config under ProgramData are not deleted.
& robocopy.exe $InstallRoot $AppRoot /MIR /XD 'user-data' /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) { throw "Copying application payload failed (robocopy exit $LASTEXITCODE)." }

$BundledPython = Join-Path $InstallRoot 'python-runtime\python.exe'
if (Test-Path $BundledPython) {
    if (Test-Path $RuntimeRoot) { Remove-Item -Path $RuntimeRoot -Recurse -Force }
    & robocopy.exe (Split-Path $BundledPython -Parent) $RuntimeRoot /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw "Installing bundled Python runtime failed (robocopy exit $LASTEXITCODE)." }
}

$Python = Join-Path $RuntimeRoot 'python.exe'
if (-not (Test-Path $Python)) {
    throw "Bundled Python runtime not found at $Python"
}

& $Python -m pip install --disable-pip-version-check --force-reinstall --no-deps $AppRoot
if ($LASTEXITCODE -ne 0) { throw 'Installing AI Aharon package failed.' }

# First install gets a local config template. Existing secrets/config are preserved on upgrades.
$ServiceEnv = Join-Path $ServiceRoot '.env'
$ExampleEnv = Join-Path $AppRoot '.env.example'
if (-not (Test-Path $ServiceEnv) -and (Test-Path $ExampleEnv)) {
    Copy-Item $ExampleEnv $ServiceEnv
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    & $Python -m agent_windows.windows_service remove | Out-Null
    Start-Sleep -Milliseconds 750
}

& $Python -m agent_windows.windows_service --startup auto install
if ($LASTEXITCODE -ne 0) { throw 'Windows service installation failed.' }

Start-Service -Name $ServiceName
(Get-Service -Name $ServiceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(25))
& sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/""/0 | Out-Null
