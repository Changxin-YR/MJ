param(
    [Parameter(Mandatory = $true)]
    [string]$ComfyUIPath
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $ComfyUIPath).Path
$python = Join-Path $source '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'ComfyUI virtual environment is missing' }
$url = 'http://127.0.0.1:8189/system_stats'
try {
    Invoke-RestMethod -Uri $url -TimeoutSec 3 | Out-Null
    Write-Output 'ComfyUI is already running on 127.0.0.1:8189.'
    exit 0
} catch {
    # Start the loopback-only instance below.
}
$logs = Join-Path (Split-Path -Parent $PSScriptRoot) '.local/logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$process = Start-Process -FilePath $python -ArgumentList 'main.py','--listen','127.0.0.1','--port','8189','--lowvram' -WorkingDirectory $source -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs 'comfyui.log') -RedirectStandardError (Join-Path $logs 'comfyui.err.log')
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) { throw "ComfyUI exited with code $($process.ExitCode). See .local/logs/comfyui.err.log" }
    try {
        Invoke-RestMethod -Uri $url -TimeoutSec 3 | Out-Null
        Write-Output "ComfyUI ready at http://127.0.0.1:8189 (PID $($process.Id))."
        exit 0
    } catch {
        # Startup is still in progress.
    }
}
throw 'ComfyUI did not become ready within 120 seconds'
