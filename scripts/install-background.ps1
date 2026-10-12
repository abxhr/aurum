# Keeps Wealth running in the background: starts at sign-in, restarts itself if it ever stops, survives sleep (the process simply resumes), and is hidden.
# Run:    powershell -ExecutionPolicy Bypass -File scripts\install-background.ps1
# Remove: powershell -ExecutionPolicy Bypass -File scripts\install-background.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$name = 'WealthTracker'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed the background task."
    return
}

$py = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $py)) { throw "Virtual environment not found at $py. Run run.bat once first." }
$serve = Join-Path $root 'backend\serve.py'
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

# 1. Lock the data folder, the Google credentials and any .env to this Windows account (and SYSTEM). Other accounts on the PC lose access.
foreach ($p in @((Join-Path $root 'data'), (Join-Path $root 'credentials.json'), (Join-Path $root 'token.json'), (Join-Path $root '.env'))) {
    if (Test-Path $p) {
        $grant = if ((Get-Item $p).PSIsContainer) { "(OI)(CI)F" } else { "F" }
        & icacls $p /inheritance:r /grant:r "${me}:$grant" "SYSTEM:$grant" | Out-Null
    }
}

# 2. The task: at logon, then a check every 5 minutes. serve.py exits at once when the app is already up, so a check costs nothing,
#    and if the app ever crashes the next check brings it back.
$action   = New-ScheduledTaskAction -Execute $py -Argument "`"$serve`"" -WorkingDirectory $root
$logon    = New-ScheduledTaskTrigger -AtLogOn -User $me
$repeat   = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logon, $repeat) -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Done. Wealth runs in the background (log: data\logs\wealth.log)."
Write-Host "  This PC:  http://localhost:5000"
