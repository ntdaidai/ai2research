param(
    [switch]$WorkbenchOnly,
    [switch]$SkipArxiv
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONUTF8 = '1'
$env:UV_CACHE_DIR = Join-Path $ProjectRoot '.local\uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot '.local\python'
$UvDirectory = Join-Path $ProjectRoot '.local\tools\uv'
$UvExecutable = Join-Path $UvDirectory 'uv.exe'
try {
    if (-not (Test-Path -LiteralPath $UvExecutable)) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $DownloadDirectory = Join-Path $ProjectRoot '.local\downloads'
        New-Item -ItemType Directory -Force -Path $DownloadDirectory | Out-Null
        $UvVersion = '0.11.25'
        $Architecture = $env:PROCESSOR_ARCHITECTURE
        if ($env:PROCESSOR_ARCHITEW6432) { $Architecture = $env:PROCESSOR_ARCHITEW6432 }
        if ($Architecture -eq 'ARM64') { $WheelTag = 'win_arm64' }
        elseif ($Architecture -eq 'AMD64') { $WheelTag = 'win_amd64' }
        else { throw 'A 64-bit Windows installation is required.' }
        Write-Host 'Downloading the official uv package from PyPI...'
        $Metadata = Invoke-RestMethod -Uri "https://pypi.org/pypi/uv/$UvVersion/json" -TimeoutSec 120
        $Wheel = $Metadata.urls | Where-Object { $_.filename -eq "uv-$UvVersion-py3-none-$WheelTag.whl" } | Select-Object -First 1
        if (-not $Wheel) { throw 'No official uv package matches this Windows architecture.' }
        $Archive = Join-Path $DownloadDirectory 'uv-wheel.zip'
        $Extracted = Join-Path $DownloadDirectory 'uv-extracted'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Wheel.url -OutFile $Archive -TimeoutSec 300
            $Digest = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($Digest -ne $Wheel.digests.sha256) { throw 'uv package checksum mismatch.' }
            if (Test-Path -LiteralPath $Extracted) { Remove-Item -LiteralPath $Extracted -Recurse -Force }
            Expand-Archive -LiteralPath $Archive -DestinationPath $Extracted
            $Executable = Get-ChildItem -LiteralPath $Extracted -Filter 'uv.exe' -Recurse | Select-Object -First 1
            if (-not $Executable) { throw 'The uv executable was missing from the package.' }
            New-Item -ItemType Directory -Force -Path $UvDirectory | Out-Null
            Copy-Item -LiteralPath $Executable.FullName -Destination $UvExecutable
            $LicenseSource = Join-Path $Extracted "uv-$UvVersion.dist-info\licenses"
            if (Test-Path -LiteralPath $LicenseSource) { Copy-Item -LiteralPath $LicenseSource -Destination (Join-Path $UvDirectory 'licenses') -Recurse -Force }
        } finally {
            if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
            if (Test-Path -LiteralPath $Extracted) { Remove-Item -LiteralPath $Extracted -Recurse -Force }
        }
    }
    & $UvExecutable python install --no-bin --no-registry 3.14.4
    if ($LASTEXITCODE -ne 0) { throw 'Python download failed; check your network and retry.' }
    $PythonExecutable = (& $UvExecutable python find 3.14.4 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonExecutable)) { throw 'Python runtime was not found.' }
    $BootstrapArguments = @((Join-Path $ProjectRoot 'tools\bootstrap_workspace.py'), '--uv', $UvExecutable)
    if ($WorkbenchOnly) { $BootstrapArguments += '--workbench-only' }
    if ($SkipArxiv) { $BootstrapArguments += '--skip-arxiv' }
    & $PythonExecutable @BootstrapArguments
    if ($LASTEXITCODE -ne 0) { throw 'Workspace installation failed; see the message above.' }
    Write-Host 'Ready. Double-click 2_start.cmd to open ai2research.'
} catch {
    Write-Error $_
    exit 1
}
