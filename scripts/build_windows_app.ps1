param(
    [string]$CertificateThumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$appName = -join ([char[]](0x5FAE, 0x6CE2, 0x667A, 0x9009))
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found: $python"
}

$requiredFiles = @(
    "assets\microwave-smart-sort-icon.png",
    "assets\microwave-smart-sort.ico",
    "assets\windows_version_info.txt",
    "artifacts\resnet50_baseline_fold0\model_retrained.onnx",
    "artifacts\resnet50_baseline_fold0\model_retrained.onnx.data",
    "artifacts\resnet50_baseline_fold0\model_retrained.metadata.json"
)
foreach ($relativePath in $requiredFiles) {
    $fullPath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath)) {
        throw "Required app resource not found: $fullPath"
    }
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name $appName `
        --icon "assets\microwave-smart-sort.ico" `
        --version-file "assets\windows_version_info.txt" `
        --add-data "assets\microwave-smart-sort-icon.png;assets" `
        --add-data "artifacts\resnet50_baseline_fold0\model_retrained.onnx;artifacts\resnet50_baseline_fold0" `
        --add-data "artifacts\resnet50_baseline_fold0\model_retrained.onnx.data;artifacts\resnet50_baseline_fold0" `
        --add-data "artifacts\resnet50_baseline_fold0\model_retrained.metadata.json;artifacts\resnet50_baseline_fold0" `
        --exclude-module torch `
        --exclude-module torchvision `
        --exclude-module onnx `
        --exclude-module onnxscript `
        "run_demo.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code: $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$exePath = Join-Path $projectRoot ("dist\{0}\{0}.exe" -f $appName)
if ($CertificateThumbprint) {
    $thumbprint = $CertificateThumbprint.Replace(" ", "")
    $certificate = Get-Item -LiteralPath ("Cert:\CurrentUser\My\{0}" -f $thumbprint) -ErrorAction SilentlyContinue
    if (-not $certificate) {
        throw "Code-signing certificate not found in CurrentUser\My: $thumbprint"
    }
    $signTool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin" -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if (-not $signTool) {
        throw "SignTool was not found. Install the Windows SDK Signing Tools component."
    }
    & $signTool.FullName sign /sha1 $thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 /d $appName $exePath
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool failed with exit code: $LASTEXITCODE"
    }
    & $signTool.FullName verify /pa /v $exePath
    if ($LASTEXITCODE -ne 0) {
        throw "Signature verification failed with exit code: $LASTEXITCODE"
    }
    Write-Host "Signed publisher: $($certificate.Subject)"
}
else {
    Write-Warning "No code-signing certificate was supplied. Version metadata is embedded, but Windows will still show Unknown publisher."
}
Write-Host "Build complete: $exePath"
