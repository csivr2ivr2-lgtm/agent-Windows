param(
    [Parameter(Mandatory=$true)][string]$InstallRoot
)

$ErrorActionPreference = 'SilentlyContinue'
$ServiceName = 'AgentWindowsAI'
$ServiceRoot = Join-Path $env:ProgramData $ServiceName
$RuntimePython = Join-Path (Join-Path $InstallRoot 'python-runtime') 'python.exe'
$LegacyRuntimeRoot = Join-Path $ServiceRoot 'python-runtime'
$LegacyToolsRoot = Join-Path $ServiceRoot 'tools'

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -ne $svc) {
    if ($svc.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        try { $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15)) } catch { Write-Verbose "Timed out waiting for service stop during uninstall: $($_.Exception.Message)" }
    }
    if (Test-Path $RuntimePython) {
        $env:AGENT_WINDOWS_HOME = $ServiceRoot
        $env:AGENT_WINDOWS_INSTALL_ROOT = $InstallRoot
        & "$RuntimePython" -m agent_windows.windows_service remove | Out-Null
    } else {
        & sc.exe delete $ServiceName | Out-Null
    }
}

# Pre-0.2 attempts placed replaceable binaries in ProgramData. Remove only those
# legacy directories. Keep .env and data/memory so reinstall preserves context.
Remove-Item -Path $LegacyRuntimeRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path $LegacyToolsRoot -Recurse -Force -ErrorAction SilentlyContinue
