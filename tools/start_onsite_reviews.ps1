param(
    [Parameter(Mandatory=$true)][string]$CorpusRoot,
    [Parameter(Mandatory=$true)][string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$caseRoot = (Resolve-Path -LiteralPath $CorpusRoot).Path
$runtimePython = (Resolve-Path -LiteralPath $PythonPath).Path
$selectionFile = Join-Path $caseRoot 'delivery-selection.json'
$selectedCases = Get-Content -LiteralPath $selectionFile -Raw | ConvertFrom-Json
$expectedIds = @(1..9 | ForEach-Object { 'drawing-{0:D2}' -f $_ })
$actualIds = @($selectedCases | ForEach-Object { $_.id } | Sort-Object)
if ($actualIds.Count -ne 9 -or (Compare-Object $expectedIds $actualIds)) {
    throw 'Selection must contain each drawing-01 through drawing-09 exactly once'
}
$reviewRoot = Join-Path $caseRoot 'web-review'
New-Item -ItemType Directory -Path $reviewRoot -Force | Out-Null
$env:PYTHONPATH = $null
$env:CAD2GIS_BACKEND_PATH = $null
$env:PYTHONUTF8 = '1'
$servers = @{}
$startedProcesses = @()
try {
foreach ($case in $selectedCases) {
    $number = [int]($case.id -replace '^drawing-', '')
    if ($number -lt 1 -or $number -gt 9) { throw 'Unexpected drawing identifier' }
    $caseRun = (Resolve-Path -LiteralPath $case.run_dir).Path
    if (-not $caseRun.StartsWith($caseRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Review run must stay within the selected corpus directory'
    }
    $port = 8780 + $number
    $url = "http://127.0.0.1:$port"
    $workspace = Join-Path $reviewRoot $case.id
    $outLog = Join-Path $reviewRoot ($case.id + '.stdout.log')
    $errLog = Join-Path $reviewRoot ($case.id + '.stderr.log')
    $occupied = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($occupied.Count -gt 0) { throw "Port $port is occupied; existing server runtime is not verified" }
        # Paths come from the fixed local selection manifest, never from a web request.
        if ($caseRun.Contains('"') -or $workspace.Contains('"')) { throw 'Invalid quoted path' }
        $arguments = @('-I', '-m', 'cad2gis', 'review', ('"' + $caseRun + '"'),
            '--workspace', ('"' + $workspace + '"'), '--host', '127.0.0.1', '--port', "$port")
        $process = Start-Process -FilePath $runtimePython -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $startedProcesses += $process
        $serverPid = $process.Id
        $serverPython = $runtimePython
    $ready = $false
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        try {
            $state = Invoke-RestMethod -Uri "$url/api/run" -TimeoutSec 2
            $layers = Invoke-RestMethod -Uri "$url/api/layers" -TimeoutSec 2
            if ($state.run_dir -eq $caseRun -and $layers.layers.Count -gt 0) { $ready = $true; break }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    $servers[$case.id] = @{
        url = "$url/#console-app"; status = $(if ($ready) { 'ready' } else { 'failed' })
        run_dir = $caseRun; pid = $serverPid; python = $serverPython
    }
    $servers | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $reviewRoot 'servers.json') -Encoding utf8
    Write-Output "$($case.id): $($servers[$case.id].status) $url"
    if (-not $ready) { throw "Server $($case.id) did not become ready" }
}
} catch {
    # Only stop process objects created by this invocation; never touch other listeners.
    foreach ($ownedProcess in $startedProcesses) {
        if (-not $ownedProcess.HasExited) {
            # Windows venv launchers may own a child that holds the listening socket.
            & taskkill.exe /PID $ownedProcess.Id /T /F | Out-Null
        }
    }
    foreach ($entry in $servers.Values) { $entry.status = 'stopped_after_launch_failure' }
    $servers | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $reviewRoot 'servers.json') -Encoding utf8
    throw
}
