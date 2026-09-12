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
& $vpython -m pip install --upgrade "PySide6>=6.5" "pyinstaller>=6.6" --quiet

# -- the icon, drawn by the app's own code -----------------------------------
& $vpython scripts\make-icon.py packaging\windows\tessera.ico

# -- the executable ----------------------------------------------------------
Write-Host "Running PyInstaller" -ForegroundColor DarkGray
& $vpython -m PyInstaller packaging\windows\tessera.spec --noconfirm --distpath dist --workpath build\pyinstaller

$exe = "dist\Tessera\Tessera.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller did not produce $exe" }
$size = [math]::Round((Get-ChildItem -Recurse dist\Tessera | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "Built $exe ($size MB on disk)" -ForegroundColor Green

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
    $guess = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $guess) { $iscc = $guess }
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
