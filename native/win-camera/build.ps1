<#
.SYNOPSIS
    Builds TesseraCamera.dll and tessera-camera.exe with MSVC, then self-tests them.
#>
[CmdletBinding()]
param([string]$Out = "build\native")

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = if (Test-Path $vswhere) {
    & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
}
if (-not $vs) { throw "The webcam needs Visual Studio Build Tools with the C++ workload." }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"

New-Item -ItemType Directory -Force $Out | Out-Null
$out = (Resolve-Path $Out).Path

# Static CRT: Frame Server loads the DLL, and may not have the runtime.
$flags = "/nologo /std:c++17 /EHsc /O2 /MT /W3 /DUNICODE /D_UNICODE"
$libs = "mfplat.lib mfuuid.lib mfsensorgroup.lib ole32.lib advapi32.lib"
$steps = @(
    "call `"$vcvars`" >nul",
    "cd /d `"$out`"",
    "cl $flags /LD `"$here\source.cpp`" /Fe:TesseraCamera.dll /link /DEF:`"$here\TesseraCamera.def`" $libs",
    "cl $flags `"$here\camera.cpp`" /Fe:tessera-camera.exe /link $libs"
)
cmd /c ($steps -join " && ")
if ($LASTEXITCODE -ne 0) { throw "The camera component did not build." }

& "$out\tessera-camera.exe" --self-test "$out\TesseraCamera.dll"
if ($LASTEXITCODE -ne 0) { throw "The camera component failed its self test." }
