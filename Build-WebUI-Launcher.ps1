[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherProject = Join-Path $ProjectRoot "launcher\Use-LLLM-WebUI\Use-LLLM-WebUI.csproj"
$PublishDirectory = Join-Path $ProjectRoot "launcher\Use-LLLM-WebUI\publish"
$Destination = Join-Path $ProjectRoot "Use-LLLM-WebUI.exe"

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw ".NET SDK was not found."
}

dotnet publish $LauncherProject `
    --configuration $Configuration `
    --runtime win-x64 `
    --self-contained false `
    --output $PublishDirectory

Copy-Item -LiteralPath (Join-Path $PublishDirectory "Use-LLLM-WebUI.exe") -Destination $Destination -Force
Write-Host "WebUI launcher: $Destination"
