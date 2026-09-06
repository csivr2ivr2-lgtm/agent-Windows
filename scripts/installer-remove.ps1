param(
    [Parameter(Mandatory=$true)][string]$InstallRoot
)

$ErrorActionPreference = 'SilentlyContinue'
$ServiceName = 'AgentWindowsAI'
$ServiceRoot = Join-Path $env:ProgramData $ServiceName
$RuntimeRoot = Join-Path $ServiceRoot 'python-runtime'
$ToolsRoot = Join-Path $ServiceRoot 'tools'
$RuntimePython = Join-Path $RuntimeRoot 'python.exe'

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

# Remove replaceable binaries, but intentionally keep .env and data/memory so a
# reinstall can restore the user's configuration and long-term context.
Remove-Item -Path $RuntimeRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path $ToolsRoot -Recurse -Force -ErrorAction SilentlyContinue
