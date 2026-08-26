$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$AgentExe = Join-Path $Root '.venv\Scripts\agent-windows.exe'
$EnvFile = Join-Path $Root '.env'
$Desktop = [Environment]::GetFolderPath('Desktop')
$ReportPath = Join-Path $Desktop 'ai-aharon-final-report.json'
$LogPath = Join-Path $Desktop 'ai-aharon-finalize.log'
$TempLog = Join-Path $env:TEMP 'ai-aharon-finalize.log'
$IntegrationRoot = Join-Path $env:LOCALAPPDATA 'ai-aharon\integrations'
$UfoRoot = Join-Path $IntegrationRoot 'UFO'

function Write-Step([string]$Text) {
    Write-Host "`n==> $Text" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE" }
}

function Set-EnvValue([string]$Name, [string]$Value) {
    if (-not (Test-Path $EnvFile)) { return }
    $lines = @(Get-Content -Path $EnvFile -ErrorAction Stop)
    $escapedName = [Regex]::Escape($Name)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$escapedName=") {
            $lines[$i] = "$Name=$Value"
            $found = $true
            break
        }
    }
    if (-not $found) { $lines += "$Name=$Value" }
    Set-Content -Path $EnvFile -Value $lines -Encoding UTF8
}

function Get-EnvValue([string]$Name) {
    if (-not (Test-Path $EnvFile)) { return '' }
    $escapedName = [Regex]::Escape($Name)
    $line = Get-Content -Path $EnvFile | Where-Object { $_ -match "^$escapedName=" } | Select-Object -First 1
    if (-not $line) { return '' }
    return ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
}

function Install-UfoIsolated {
    Write-Step 'Preparing Microsoft UFO² in an isolated virtual environment'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Warning 'git is unavailable; UFO² installation skipped. The final report will mark it for external validation.'
        return
    }
    New-Item -ItemType Directory -Path $IntegrationRoot -Force | Out-Null
    if (-not (Test-Path (Join-Path $UfoRoot '.git'))) {
        & git clone --depth 1 https://github.com/microsoft/UFO.git $UfoRoot
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'UFO² clone failed; continuing with Windows-Use fallback.'
            return
        }
    }
    else {
        & git -C $UfoRoot pull --ff-only
        if ($LASTEXITCODE -ne 0) { Write-Warning 'UFO² update failed; using the existing checkout.' }
    }

    $UfoPython = Join-Path $UfoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $UfoPython)) {
        & $Python -m venv (Join-Path $UfoRoot '.venv')
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Could not create the isolated UFO² venv.'
            return
        }
    }
    $Requirements = Join-Path $UfoRoot 'requirements.txt'
    if (Test-Path $Requirements) {
        & $UfoPython -m pip install -r $Requirements
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'UFO² dependencies failed to install; Windows-Use remains available as fallback.'
            return
        }
    }
    Set-EnvValue 'UFO_WORKDIR' $UfoRoot
}

New-Item -ItemType File -Path $TempLog -Force | Out-Null
Start-Transcript -Path $TempLog -Force | Out-Null
$finalExit = 0
try {
    Set-Location $Root

    Write-Step 'Updating repository'
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Invoke-Checked git -C $Root pull --ff-only
    }
    else { Write-Warning 'git not found; continuing with the current checkout.' }

    Write-Step 'Installing/updating the Python core'
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if (-not (Test-Path $Python)) { throw "Python virtual environment missing after setup: $Python" }

    Write-Step 'Installing realtime and Needle integrations'
    Invoke-Checked $Python -m pip install -e "$Root[realtime,needle]"

    Write-Step 'Installing Windows-Use fallback'
    & $Python -m pip install 'windows-use==0.8.1'
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Windows-Use installation failed; final-check will report the missing integration.' }

    Install-UfoIsolated

    if (-not (Get-EnvValue 'WINDOWS_USE_MODEL')) {
        $localModel = Get-EnvValue 'LOCAL_LLM_MODEL'
        if ($localModel) { Set-EnvValue 'WINDOWS_USE_MODEL' $localModel }
    }

    Write-Step 'Installing/updating Windows service'
    & (Join-Path $PSScriptRoot 'install-service.ps1')

    Write-Step 'Installing/updating ai aharon phone-call GUI'
    & (Join-Path $PSScriptRoot 'install-gui.ps1')

    Write-Step 'Running provider diagnostics'
    & $AgentExe --env $EnvFile providers-check
    if ($LASTEXITCODE -ne 0) { Write-Warning 'One or more configured providers failed live validation.' }

    Write-Step 'Running realtime diagnostics'
    & $AgentExe --env $EnvFile realtime-check
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Realtime diagnostics reported a failure.' }

    Write-Step 'Running 15-project integration diagnostics'
    & $AgentExe --env $EnvFile integrations-check
    if ($LASTEXITCODE -ne 0) { Write-Warning 'At least one integration is still pending.' }

    Write-Step 'Running final live report'
    & $AgentExe --env $EnvFile final-check --live --output $ReportPath
    $finalExit = $LASTEXITCODE

    if (Test-Path $ReportPath) {
        try { Get-Content -Path $ReportPath -Raw | Set-Clipboard } catch { Write-Warning 'Could not copy the report to clipboard.' }
    }

    Write-Step 'Windows service status'
    & sc.exe query AgentWindowsAI
    & sc.exe qc AgentWindowsAI
}
catch {
    $finalExit = 2
    Write-Error $_
}
finally {
    try { Stop-Transcript | Out-Null } catch {}
    Copy-Item -Path $TempLog -Destination $LogPath -Force -ErrorAction SilentlyContinue
}

Write-Host "`nFinal report: $ReportPath" -ForegroundColor Green
Write-Host "Log: $LogPath"
if ($finalExit -eq 0) {
    Write-Host 'Finalizer completed. Review the report for PASS vs external-validation requirements.' -ForegroundColor Green
}
else {
    Write-Warning "Finalizer completed with validation exit code $finalExit. The report/log contain the blockers."
}
exit $finalExit
