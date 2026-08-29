param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$version = "0.2.0"
$pyInstallerVersion = "6.22.2"
$repoRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$buildRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot "build\release"))
$distRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot "dist"))
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $distRoot "Quick Processing Tool $version"))
$zipPath = [IO.Path]::GetFullPath((Join-Path $distRoot "Quick-Processing-Tool-$version-windows-x64.zip"))
$appIconPath = [IO.Path]::GetFullPath((Join-Path $repoRoot "assets\windows\quick-processing-tool.ico"))

function Assert-SafeChild([string]$Path) {
    $prefix = $repoRoot.TrimEnd('\') + '\'
    if (-not $Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the repository: $Path"
    }
}

foreach ($path in @($buildRoot, $distRoot, $releaseRoot, $zipPath)) {
    Assert-SafeChild $path
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python was not found: $Python"
}
if (-not (Test-Path -LiteralPath $appIconPath -PathType Leaf)) {
    throw "Formal app icon was not found: $appIconPath. Release builds must not use a placeholder icon."
}
& $Python (Join-Path $repoRoot "packaging\windows\verify_app_icon.py") $appIconPath
if ($LASTEXITCODE -ne 0) {
    throw "Formal app icon validation failed"
}

$pythonVersion = (& $Python -c "import platform; print(platform.python_version())").Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.12.13") {
    throw "Windows release builds require CPython 3.12.13; found $pythonVersion"
}
$pythonBits = (& $Python -c "import struct; print(struct.calcsize('P') * 8)").Trim()
if ($LASTEXITCODE -ne 0 -or $pythonBits -ne "64") {
    throw "Windows x64 release builds require a 64-bit Python; found $pythonBits-bit"
}
$packageVersion = (& $Python -c "import quick_processing_tool; print(quick_processing_tool.__version__)").Trim()
if ($LASTEXITCODE -ne 0 -or $packageVersion -ne $version) {
    throw "Package version mismatch: expected $version, found $packageVersion"
}
$installedPyInstaller = (& $Python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $installedPyInstaller -ne $pyInstallerVersion) {
    throw "PyInstaller $pyInstallerVersion is required; found $installedPyInstaller"
}
& $Python (Join-Path $repoRoot "packaging\windows\verify_build_environment.py") (Join-Path $repoRoot "constraints-release.txt")
if ($LASTEXITCODE -ne 0) {
    throw "The installed build environment does not match constraints-release.txt"
}

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
foreach ($path in @($buildRoot, $releaseRoot, $zipPath, "$zipPath.sha256")) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

$pyInstallerArguments = @(
    "--noconfirm", "--clean", "--onedir", "--windowed",
    "--name", "Quick Processing Tool",
    "--paths", (Join-Path $repoRoot "src"),
    "--distpath", (Join-Path $buildRoot "dist"),
    "--workpath", (Join-Path $buildRoot "work"),
    "--specpath", (Join-Path $buildRoot "spec"),
    "--version-file", (Join-Path $repoRoot "packaging\windows\file_version_info.txt"),
    "--manifest", (Join-Path $repoRoot "packaging\windows\app.manifest"),
    "--icon", $appIconPath,
    "--add-data", "$appIconPath;quick_processing_tool/assets",
    (Join-Path $repoRoot "packaging\windows\entry.py")
)
& $Python -m PyInstaller @pyInstallerArguments
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$frozenRoot = Join-Path $buildRoot "dist\Quick Processing Tool"
if (-not (Test-Path -LiteralPath (Join-Path $frozenRoot "Quick Processing Tool.exe") -PathType Leaf)) {
    throw "The expected windowed executable was not produced."
}
# Qt 6.11 uses the Windows system ICU. PyInstaller may accidentally collect an
# incompatible unversioned ICU from a build machine PATH (for example Poppler),
# which makes QtCore fail before application logging starts.
Get-ChildItem -LiteralPath (Join-Path $frozenRoot "_internal") -Filter "icu*.dll" -File |
    Remove-Item -Force
$qtRoot = Join-Path $frozenRoot "_internal\PySide6"
$allowedQtPlugins = @(
    "plugins\imageformats\qjpeg.dll",
    "plugins\imageformats\qwebp.dll",
    "plugins\platforms\qwindows.dll",
    "plugins\styles\qmodernwindowsstyle.dll"
)
Get-ChildItem -LiteralPath (Join-Path $qtRoot "plugins") -Recurse -File |
    Where-Object { $allowedQtPlugins -notcontains $_.FullName.Substring($qtRoot.Length + 1) } |
    Remove-Item -Force
$unusedQtBinaries = @(
    "opengl32sw.dll", "Qt6Network.dll", "QtNetwork.pyd", "Qt6OpenGL.dll",
    "Qt6Pdf.dll", "Qt6Qml.dll", "Qt6QmlMeta.dll", "Qt6QmlModels.dll",
    "Qt6QmlWorkerScript.dll", "Qt6Quick.dll", "Qt6Svg.dll",
    "Qt6VirtualKeyboard.dll"
)
foreach ($name in $unusedQtBinaries) {
    $path = Join-Path $qtRoot $name
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}
if (Get-ChildItem -LiteralPath $qtRoot -Recurse -File | Where-Object Name -Match "VirtualKeyboard|virtualkeyboard") {
    throw "GPL/commercial Qt Virtual Keyboard must not be included in the LGPL release."
}
& $Python (Join-Path $repoRoot "packaging\windows\verify_frozen_qt.py") $qtRoot
if ($LASTEXITCODE -ne 0) {
    throw "Frozen Qt allowlist or dependency verification failed"
}
Move-Item -LiteralPath $frozenRoot -Destination $releaseRoot

Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination $releaseRoot
New-Item -ItemType Directory -Path (Join-Path $releaseRoot "scripts") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\install_upscaler_runtime.ps1") -Destination (Join-Path $releaseRoot "scripts")
New-Item -ItemType Directory -Path (Join-Path $releaseRoot "licenses\optional-runtime") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot "licenses\THIRD-PARTY-NOTICES.txt") -Destination (Join-Path $releaseRoot "licenses")
Copy-Item -LiteralPath (Join-Path $repoRoot "licenses\LGPL-3.0.txt") -Destination (Join-Path $releaseRoot "licenses")
Copy-Item -LiteralPath (Join-Path $repoRoot "licenses\GPL-3.0.txt") -Destination (Join-Path $releaseRoot "licenses")
Copy-Item -LiteralPath (Join-Path $repoRoot "licenses\Qt-THIRD-PARTY-NOTICES.txt") -Destination (Join-Path $releaseRoot "licenses")
Copy-Item -LiteralPath (Join-Path $repoRoot "licenses\qt-6.11.2") -Destination (Join-Path $releaseRoot "licenses") -Recurse
Copy-Item -Path (Join-Path $repoRoot "docs\third_party\*") -Destination (Join-Path $releaseRoot "licenses\optional-runtime")
Copy-Item -LiteralPath (Join-Path $repoRoot "docs\upscaler_runtime.md") -Destination (Join-Path $releaseRoot "licenses\optional-runtime")
& $Python (Join-Path $repoRoot "packaging\windows\collect_licenses.py") (Join-Path $releaseRoot "licenses")
if ($LASTEXITCODE -ne 0) {
    throw "License collection failed with exit code $LASTEXITCODE"
}

$dependencyVersions = Get-Content -LiteralPath (Join-Path $repoRoot "constraints-release.txt") |
    Where-Object { $_.Trim() -and -not $_.Trim().StartsWith("#") }
$manifest = [ordered]@{
    product = "Quick Processing Tool"
    version = $version
    platform = "Windows x64"
    buildPython = $pythonVersion
    buildTool = "PyInstaller $installedPyInstaller onedir windowed"
    dependencies = @($dependencyVersions)
    optionalUpscalerRuntimeIncluded = $false
    executable = "Quick Processing Tool.exe"
    appIcon = "quick-processing-tool.ico"
    formalAppIconIncluded = $true
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $releaseRoot "RELEASE-MANIFEST.json") -Encoding utf8

# Production packages must contain only the product launcher. Development
# spikes and smoke-test apphosts are never valid release payloads.
$releaseExecutables = @(Get-ChildItem -LiteralPath $releaseRoot -Recurse -File -Filter "*.exe")
if ($releaseExecutables.Count -ne 1 -or $releaseExecutables[0].Name -cne "Quick Processing Tool.exe") {
    $names = ($releaseExecutables | ForEach-Object { $_.FullName.Substring($releaseRoot.Length + 1) }) -join ", "
    throw "Unexpected executable in the production package: $names"
}
$developmentPayload = @(Get-ChildItem -LiteralPath $releaseRoot -Recurse -File | Where-Object {
    $_.Name -match "(?i)VectorTrace|Spike|Smoke"
})
if ($developmentPayload.Count -gt 0) {
    $names = ($developmentPayload | ForEach-Object { $_.FullName.Substring($releaseRoot.Length + 1) }) -join ", "
    throw "Development spike/smoke payload found in the production package: $names"
}

Compress-Archive -Path (Join-Path $releaseRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
"$hash  $(Split-Path -Leaf $zipPath)" | Set-Content -LiteralPath "$zipPath.sha256" -Encoding ascii
Write-Host "Release folder: $releaseRoot"
Write-Host "ZIP: $zipPath"
Write-Host "SHA-256: $hash"
