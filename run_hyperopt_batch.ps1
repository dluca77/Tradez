<#
Runs the remaining hyperopt combinations sequentially at Idle CPU priority,
so it never competes with the live trading bot for the VPS's single core.
Appends each result to hyperopt_results.txt in the repo root.
#>

$repoPath = "C:\Users\Administrator\Tradez"
Set-Location $repoPath

$strategies = @("pullback", "breakout_retest", "vwap_reversion", "support_resistance")
$instruments = @("XAUUSD", "NAS100", "SPX500", "UK100", "JPN225")

# XAUUSD/pullback already ran manually (40 trials) and was applied to config.yaml.
$done = @("XAUUSD:pullback")

$resultsFile = Join-Path $repoPath "hyperopt_results.txt"

foreach ($instrument in $instruments) {
    foreach ($strategy in $strategies) {
        $key = "${instrument}:${strategy}"
        if ($done -contains $key) { continue }

        Add-Content -Path $resultsFile -Value "`n=== $key (started $(Get-Date -Format o)) ==="
        $p = Start-Process -FilePath "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" `
            -ArgumentList "run_hyperopt.py", $instrument, $strategy, "--trials", "20" `
            -WorkingDirectory $repoPath -NoNewWindow -PassThru `
            -RedirectStandardOutput "$repoPath\hyperopt_last_run.txt" `
            -RedirectStandardError "$repoPath\hyperopt_last_run_err.txt"
        $p.PriorityClass = 'Idle'
        Wait-Process -Id $p.Id
        Get-Content "$repoPath\hyperopt_last_run.txt" | Add-Content -Path $resultsFile
        $errContent = Get-Content "$repoPath\hyperopt_last_run_err.txt" -ErrorAction SilentlyContinue
        if ($errContent) { Add-Content -Path $resultsFile -Value "STDERR: $errContent" }
    }
}

Add-Content -Path $resultsFile -Value "`n=== BATCH COMPLETE $(Get-Date -Format o) ==="
