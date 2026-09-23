param(
    [ValidateSet('compose', 'existing-local')]
    [string]$Mode = 'compose',
    [string]$ModelDirectory = '',
    [string]$Checkpoint = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root '.local/comfyui-source'
$models = Join-Path $root '.local/comfyui-models/checkpoints'
$commit = 'b5cc8830279eae909a59de030af1e50761c36751'
$checkpoint = 'v1-5-pruned-emaonly-fp16.safetensors'

if ($Mode -eq 'existing-local') {
    Write-Output 'Use COMFYUI_URL=http://host.docker.internal:8189 with a ComfyUI process bound to 127.0.0.1:8189.'
    exit 0
}

if (-not (Test-Path -LiteralPath $source)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $source) -Force | Out-Null
    git clone --depth 1 git@github.com:Comfy-Org/ComfyUI.git $source
    if ($LASTEXITCODE -ne 0) { throw 'ComfyUI source clone failed' }
}
$current = git -C $source rev-parse HEAD
if ($current -ne $commit) {
    git -C $source fetch --depth 1 origin $commit
    if ($LASTEXITCODE -ne 0) { throw 'Pinned ComfyUI commit fetch failed' }
    git -C $source checkout --detach $commit
    if ($LASTEXITCODE -ne 0) { throw 'Pinned ComfyUI checkout failed' }
}
@('.git', '.venv', '__pycache__', 'models', 'output', 'temp', 'user') | Set-Content -LiteralPath (Join-Path $source '.dockerignore')

if ($ModelDirectory) {
    if (-not (Test-Path -LiteralPath $ModelDirectory -PathType Container)) { throw 'ModelDirectory does not exist' }
    $models = (Resolve-Path -LiteralPath $ModelDirectory).Path
    if ($Checkpoint) {
        if ($Checkpoint -notmatch '^[A-Za-z0-9_.-]+\.(safetensors|ckpt)$') { throw 'Invalid checkpoint filename' }
        if (-not (Test-Path -LiteralPath (Join-Path $models $Checkpoint) -PathType Leaf)) { throw 'Checkpoint does not exist in ModelDirectory' }
        $checkpoint = $Checkpoint
    } else {
        $available = @(Get-ChildItem -LiteralPath $models -File -Filter '*.safetensors')
        if ($available.Count -ne 1) { throw 'Specify -Checkpoint when ModelDirectory does not contain exactly one safetensors file' }
        $checkpoint = $available[0].Name
    }
} else {
    New-Item -ItemType Directory -Path $models -Force | Out-Null
}
if (-not (Test-Path -LiteralPath (Join-Path $models $checkpoint))) {
    if (-not (Get-Command hf -ErrorAction SilentlyContinue)) { throw 'Hugging Face hf CLI is required to download the checkpoint' }
    hf download Comfy-Org/stable-diffusion-v1-5-archive $checkpoint --local-dir $models
    if ($LASTEXITCODE -ne 0) { throw 'Checkpoint download failed' }
}
$env:COMFYUI_MODELS_PATH = $models
docker compose --profile comfyui up -d --build comfyui
if ($LASTEXITCODE -ne 0) { throw 'ComfyUI Compose startup failed' }
Write-Output "ComfyUI private service started. Set COMFYUI_MODELS_PATH=$models, COMFYUI_URL=http://comfyui:8188 and COMFYUI_CHECKPOINT=$checkpoint."
