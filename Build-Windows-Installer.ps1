[CmdletBinding()]
param(
    [string]$Version = "0.1.0",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildRoot = Join-Path $ProjectRoot "build\windows-installer"
$PayloadRoot = Join-Path $BuildRoot "payload"
$RuntimeDist = Join-Path $BuildRoot "pyinstaller-dist"
$RuntimeWork = Join-Path $BuildRoot "pyinstaller-work"
$RuntimeSpec = Join-Path $BuildRoot "pyinstaller-spec"
$LauncherOut = Join-Path $BuildRoot "launcher"
$InstallerOut = Join-Path $BuildRoot "installer"
$PayloadZip = Join-Path $BuildRoot "payload.zip"
$DistRoot = Join-Path $ProjectRoot "dist"

function Assert-BuildPath([string]$Path) {
    $resolvedRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build path is outside the repository: $resolvedPath"
    }
}

Assert-BuildPath $BuildRoot
Assert-BuildPath $DistRoot
if (Test-Path -LiteralPath $BuildRoot) { Remove-Item -LiteralPath $BuildRoot -Recurse -Force }
New-Item -ItemType Directory -Path $PayloadRoot, $RuntimeDist, $RuntimeWork, $RuntimeSpec, $LauncherOut, $InstallerOut, $DistRoot -Force | Out-Null

Push-Location $ProjectRoot
try {
    if (-not $SkipTests) {
        uv run pytest -q
        if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
        uv run ruff check src tests
        if ($LASTEXITCODE -ne 0) { throw "ruff failed" }
        node --check src/use_lllm/general/static/app.js
        if ($LASTEXITCODE -ne 0) { throw "JavaScript syntax check failed" }
    }

    uv run --with pyinstaller pyinstaller `
        --noconfirm `
        --clean `
        --onedir `
        --console `
        --name Use-LLLM-Server `
        --paths (Join-Path $ProjectRoot "src") `
        --collect-all use_lllm `
        --hidden-import uvicorn.logging `
        --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols.http.auto `
        --hidden-import uvicorn.protocols.websockets.auto `
        --hidden-import uvicorn.lifespan.on `
        --distpath $RuntimeDist `
        --workpath $RuntimeWork `
        --specpath $RuntimeSpec `
        (Join-Path $ProjectRoot "src\use_lllm\__main__.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    Copy-Item -LiteralPath (Join-Path $RuntimeDist "Use-LLLM-Server") -Destination (Join-Path $PayloadRoot "runtime") -Recurse

    dotnet publish launcher/Use-LLLM-WebUI/Use-LLLM-WebUI.csproj `
        --configuration Release `
        --runtime win-x64 `
        --self-contained true `
        -p:PublishSingleFile=true `
        -p:IncludeNativeLibrariesForSelfExtract=true `
        --output $LauncherOut
    if ($LASTEXITCODE -ne 0) { throw "Launcher build failed" }
    Copy-Item -LiteralPath (Join-Path $LauncherOut "Use-LLLM-WebUI.exe") -Destination (Join-Path $PayloadRoot "Use-LLLM-WebUI.exe")
    Copy-Item -LiteralPath README.md -Destination (Join-Path $PayloadRoot "README.md")
    Set-Content -LiteralPath (Join-Path $PayloadRoot "version.txt") -Value $Version -Encoding utf8

    & tar.exe -a -c -f $PayloadZip -C $PayloadRoot .
    if ($LASTEXITCODE -ne 0) { throw "Payload archive failed" }
    dotnet publish installer/Use-LLLM-Installer/Use-LLLM-Installer.csproj `
        --configuration Release `
        --runtime win-x64 `
        --self-contained true `
        -p:PublishSingleFile=true `
        -p:IncludeNativeLibrariesForSelfExtract=true `
        -p:InstallerVersion=$Version `
        -p:PayloadZip=$PayloadZip `
        --output $InstallerOut
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed" }

    $Destination = Join-Path $DistRoot "Use-LLLM-Setup.exe"
    Copy-Item -LiteralPath (Join-Path $InstallerOut "Use-LLLM-Setup.exe") -Destination $Destination -Force
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Destination
    Write-Host "Installer: $Destination"
    Write-Host "SHA256: $($hash.Hash)"
}
finally {
    Pop-Location
}
