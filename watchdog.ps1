<#
Single-pass watchdog for the Tradez trading bot. Checks whether run_paper.py
is already running; if not, starts it and posts a Discord alert. Designed to
be invoked repeatedly by a Scheduled Task (see docs below), not run as a
long-lived loop itself - a stuck infinite loop is a single point of failure,
a short-lived check that reruns every minute is not.

The task MUST be registered to run only in the interactive session
(session 1) the user is logged into, not "whether user is logged on or
not" - the MT5 terminal and this bot depend on IPC that only works within
the same interactive session (see CLAUDE.md).
#>

$ErrorActionPreference = 'Stop'

$repoPath = "C:\Users\Administrator\Tradez"
$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
$logFile = Join-Path $repoPath "logs\watchdog.log"

function Write-WatchdogLog([string]$msg) {
    $line = "$(Get-Date -Format o) $msg"
    Add-Content -Path $logFile -Value $line
}

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run_paper.py*" }

if ($running) {
    exit 0
}

Write-WatchdogLog "run_paper.py not found running - starting it"

$webhookUrl = $null
$envPath = Join-Path $repoPath ".env"
if (Test-Path $envPath) {
    $webhookLine = Get-Content $envPath | Where-Object { $_ -match '^NOTIFY_WEBHOOK_URL=' }
    if ($webhookLine) {
        $webhookUrl = ($webhookLine -replace '^NOTIFY_WEBHOOK_URL=', '').Trim()
    }
}

Start-Process -FilePath $pythonExe -ArgumentList "-u", "run_paper.py" -WorkingDirectory $repoPath -WindowStyle Normal
Write-WatchdogLog "Started run_paper.py"

if ($webhookUrl) {
    $body = @{ content = "[WATCHDOG] run_paper.py was niet actief en is automatisch herstart." } | ConvertTo-Json
    try {
        Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 5 | Out-Null
    } catch {
        Write-WatchdogLog "Discord alert failed: $_"
    }
}
