[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

# Windowsのアプリケーション制御（Smart App Control等）は .venv の python.exe を
# ブロックすることがあるため、実際に起動して依存をimportできる最初の候補を使う。
function Get-PythonCandidates {
    if (Test-Path -LiteralPath $VenvPython) {
        $VenvPython
    }
    Get-Command python, python3 -CommandType Application -All -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Source }
    if (Get-Command py -CommandType Application -ErrorAction SilentlyContinue) {
        try {
            & py -3 -c "import sys; print(sys.executable)" 2>$null
        } catch {
        }
    }
}

function Test-PythonUsable([string]$Candidate) {
    $ErrorActionPreference = "Continue"
    try {
        & $Candidate -c "import fastapi, httpx, jsonschema, mcp, uvicorn, yaml" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        Write-Warning "Pythonを起動できません（$Candidate）: $($_.Exception.Message)"
        return $false
    }
}

$Python = Get-PythonCandidates |
    Where-Object { $_ } |
    Select-Object -Unique |
    Where-Object { Test-PythonUsable $_ } |
    Select-Object -First 1
if (-not $Python) {
    throw "使用できるPythonが見つかりません。.venvを作り直すか、マシンのPythonに依存をインストールしてください（python -m pip install -e .）。"
}
if ($Python -ne $VenvPython) {
    Write-Host "Using Python: $Python"
}

Set-Location -LiteralPath $ProjectRoot
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $SourceRoot
} else {
    $SourceRoot + [IO.Path]::PathSeparator + $env:PYTHONPATH
}
$env:USE_LLLM_WEB_PORT = [string]$Port
$arguments = @("-m", "use_lllm", "serve", "--host", "127.0.0.1", "--port", $Port)
if (-not $NoBrowser) {
    $arguments += "--open"
}
& $Python @arguments
