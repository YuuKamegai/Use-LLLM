[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = if (Test-Path -LiteralPath "C:\Python314\python.exe") {
    "C:\Python314\python.exe"
} else {
    (Get-Command python -ErrorAction Stop).Source
}

Set-Location -LiteralPath $ProjectRoot
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $SourceRoot
} else {
    $SourceRoot + [IO.Path]::PathSeparator + $env:PYTHONPATH
}
$arguments = @("-m", "use_lllm", "serve", "--host", "127.0.0.1", "--port", $Port)
if (-not $NoBrowser) {
    $arguments += "--open"
}
& $Python @arguments
