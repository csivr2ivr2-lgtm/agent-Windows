param(
    [Parameter(Mandatory=$true)][string]$InstallRoot
)

$ErrorActionPreference = 'SilentlyContinue'
$ServiceName = 'AgentWindowsAI'
$ServiceRoot = Join-Path $env:ProgramData $ServiceName
$RuntimePython = Join-Path $ServiceRoot 'python-runtime\python.exe'

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -ne $svc) {
    if ($svc.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        try { $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15)) } catch {}
    }
    if (Test-Path $RuntimePython) {
        & $RuntimePython -m agent_windows.windows_service remove | Out-Null
    } else {
        & sc.exe delete $ServiceName | Out-Null
    }
}

# Intentionally preserve %ProgramData%\AgentWindowsAI\data and .env so a reinstall
# or upgrade does not destroy memory, settings, API keys, or user state.
