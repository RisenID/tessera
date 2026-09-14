<#
.SYNOPSIS
    Builds Tessera for Windows: a folder, a zip, and an installer if Inno Setup
    is present.

.DESCRIPTION
    Run from the repository root in a normal (unelevated) PowerShell:

        .\scripts\build-windows.ps1

    It creates a virtual environment under build\venv, installs PySide6 and
    PyInstaller into it, draws the application icon from the app's own glyph
    code, and produces:

        dist\Tessera\Tessera.exe          the app
        dist\Tessera-<version>-win64.zip  the same thing, portable
        dist\Tessera-<version>-setup.exe  an installer, if iscc is on PATH

    Nothing is installed outside the repository, and nothing needs admin.

.PARAMETER SkipZip
    Leave the folder build alone and produce no archive.

.PARAMETER Clean
    Delete build\ and dist\ first.
#>
[CmdletBinding()]
param(
    [switch]$SkipZip,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (Select-String -Path "packaging\tessera.spec" -Pattern '^Version:\s*(.+)$').Matches[0].Groups[1].Value.Trim()
Write-Host "Building Tessera $version for Windows" -ForegroundColor Cyan

if ($Clean) {
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

# -- the interpreter ---------------------------------------------------------
$python = (Get-Command py -ErrorAction SilentlyContinue)
$pythonExe = if ($python) { "py" } else { "python" }

$venv = "build\venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "Creating $venv" -ForegroundColor DarkGray
    & $pythonExe -3 -m venv $venv
}
$vpython = Resolve-Path "$venv\Scripts\python.exe"

Write-Host "Installing build dependencies" -ForegroundColor DarkGray
& $vpython -m pip install --upgrade pip --quiet
# The WinRT projections are for Bluetooth audio from the phone, which the app
# imports only when it is used.
& $vpython -m pip install --upgrade "PySide6>=6.5" "pyinstaller>=6.6" `
    "winrt-runtime>=3.0" "winrt-Windows.Foundation>=3.0" `
    "winrt-Windows.Foundation.Collections>=3.0" `
    "winrt-Windows.Devices.Enumeration>=3.0" "winrt-Windows.Media.Audio>=3.0" `
    "winrt-Windows.ApplicationModel.Calls>=3.0" `
    "winrt-Windows.Storage>=3.0" "winrt-Windows.Storage.Provider>=3.0" `
    "winrt-Windows.Storage.Streams>=3.0" "winrt-Windows.Security.Cryptography>=3.0" `
    "paramiko>=3.2" --quiet

# -- the virtual camera ------------------------------------------------------
& "$root\native\win-camera\build.ps1" -Out build\native

# -- the icon, drawn by the app's own code -----------------------------------
& $vpython scripts\make-icon.py packaging\windows\tessera.ico

# -- the executable ----------------------------------------------------------
Write-Host "Running PyInstaller" -ForegroundColor DarkGray
& $vpython -m PyInstaller packaging\windows\tessera.spec --noconfirm --distpath dist --workpath build\pyinstaller

$exe = "dist\Tessera\Tessera.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller did not produce $exe" }
$size = [math]::Round((Get-ChildItem -Recurse dist\Tessera | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "Built $exe ($size MB on disk)" -ForegroundColor Green

# -- does it start? ----------------------------------------------------------
# A windowed build that fails on import shows a dialog and keeps running, so a
# live process proves nothing. The self test imports everything and says so in
# its exit code, without connecting to any phone.
$report = Join-Path $env:TEMP "tessera-self-test.txt"
Remove-Item $report -ErrorAction SilentlyContinue
$test = Start-Process $exe -ArgumentList "--self-test", "`"$report`"" -PassThru
if (-not $test.WaitForExit(120000)) {
    Stop-Process -Id $test.Id -Force
    throw "$exe did not finish its self test; it probably showed an error dialog."
}
if ($test.ExitCode -ne 0) {
    if (Test-Path $report) { Get-Content $report | Write-Host -ForegroundColor Red }
    throw "$exe does not start: something it imports is missing from the build."
}
Write-Host "The build imports everything it needs ($(Get-Content $report))" -ForegroundColor Green

# -- the portable zip --------------------------------------------------------
if (-not $SkipZip) {
    $zip = "dist\Tessera-$version-win64.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    Compress-Archive -Path dist\Tessera\* -DestinationPath $zip
    Write-Host "Wrote $zip" -ForegroundColor Green
}

# -- the installer -----------------------------------------------------------
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    # winget installs it per user unless asked otherwise.
    foreach ($guess in "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                       "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                       "$env:ProgramFiles\Inno Setup 6\ISCC.exe") {
        if (Test-Path $guess) { $iscc = $guess; break }
    }
}
if ($iscc) {
    & $iscc "packaging\windows\tessera.iss" "/DVersion=$version" | Out-Null
    Write-Host "Wrote dist\Tessera-$version-setup.exe" -ForegroundColor Green
} else {
    Write-Host "Inno Setup (iscc) not found; skipped the installer." -ForegroundColor Yellow
    Write-Host "  winget install JRSoftware.InnoSetup" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Tessera $version is in dist\Tessera. Run it with:" -ForegroundColor Cyan
Write-Host "  .\dist\Tessera\Tessera.exe"
