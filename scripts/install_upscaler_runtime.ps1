param(
    [ValidateSet("User", "Portable")]
    [string]$InstallScope = "User"
)

$ErrorActionPreference = "Stop"

$runtimeVersion = "Real-ESRGAN portable v0.2.5.0 (2022-04-24)"
$runtimeUrl = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip"
$expectedSha256 = "ABC02804E17982A3BE33675E4D471E91EA374E65B70167ABC09E31ACB412802D"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runtimeBase = if ($InstallScope -eq "Portable") {
    $repositoryRoot
} else {
    Join-Path $env:LOCALAPPDATA "QuickProcessingTool"
}
$runtimeRoot = Join-Path $runtimeBase "runtime\upscaler\realesrgan-ncnn-vulkan"
$temporaryZip = Join-Path ([IO.Path]::GetTempPath()) "quick-processing-tool-realesrgan-v0.2.5.0.zip"

Write-Host "Downloading $runtimeVersion..."
Invoke-WebRequest -Uri $runtimeUrl -OutFile $temporaryZip
$actualSha256 = (Get-FileHash -LiteralPath $temporaryZip -Algorithm SHA256).Hash
if ($actualSha256 -ne $expectedSha256) {
    Remove-Item -LiteralPath $temporaryZip -Force
    throw "Runtime checksum mismatch. Expected $expectedSha256, received $actualSha256."
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
Expand-Archive -LiteralPath $temporaryZip -DestinationPath $runtimeRoot -Force
Remove-Item -LiteralPath $temporaryZip -Force

$executable = Join-Path $runtimeRoot "realesrgan-ncnn-vulkan.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Runtime executable was not installed: $executable"
}
Write-Host "Installed and verified: $runtimeRoot"
