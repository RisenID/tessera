# Build the companion APK and install it on an attached phone.
#   .\scripts\build-companion.ps1                  debug build
#   .\scripts\build-companion.ps1 release          signed release build
#   .\scripts\build-companion.ps1 release -NoInstall

param(
    [ValidateSet('debug', 'release')] [string] $Variant = 'debug',
    [switch] $NoInstall
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
. "$PSScriptRoot\android-env.ps1"
New-Item -ItemType Directory -Force $env:TESSERA_BUILD_DIR | Out-Null

$version = (Select-String -Path packaging\tessera.spec -Pattern '^Version:\s*(\S+)').Matches[0].Groups[1].Value
$task = 'assembleDebug'
if ($Variant -eq 'release') {
    $task = 'assembleRelease'
    $props = "$env:USERPROFILE\.gradle\gradle.properties"
    if (-not (Select-String -Path $props -Pattern '^\s*TESSERA_KEYSTORE\s*=' -Quiet -ErrorAction SilentlyContinue)) {
        throw "No TESSERA_KEYSTORE in $props; cannot sign a release."
    }
}
Write-Host "Building the companion app ($Variant) at version $version`n"

Push-Location android
try {
    & "$env:GRADLE_HOME\bin\gradle.bat" --console=plain `
        --project-cache-dir "$env:TESSERA_BUILD_DIR\project-cache" `
        "-Pkotlin.project.persistent.dir=$env:TESSERA_BUILD_DIR\kotlin" `
        $task
    if ($LASTEXITCODE -ne 0) { throw "Gradle failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

$apk = "$env:TESSERA_BUILD_DIR\modules\app\outputs\apk\$Variant\app-$Variant.apk"
if (-not (Test-Path $apk)) { throw "No APK at $apk -- check the Gradle output above." }
Write-Host ("`nBuilt {0} ({1:N1} MB), version {2}" -f $apk, ((Get-Item $apk).Length / 1MB), $version) -ForegroundColor Green

if ($Variant -eq 'release') {
    $apksigner = Get-ChildItem "$env:ANDROID_HOME\build-tools" -Directory |
        Sort-Object { [version]($_.Name -replace '[^\d.].*$', '') } |
        ForEach-Object { Join-Path $_.FullName 'apksigner.bat' } | Where-Object { Test-Path $_ } | Select-Object -Last 1
    if (-not $apksigner) {
        Write-Host 'apksigner not found; skipping signature check.' -ForegroundColor Yellow
    } else {
        $signature = & $apksigner verify --print-certs $apk 2>&1 | Out-String
        if ($signature -notmatch 'Signer.*certificate DN') { throw "The APK is not signed:`n$signature" }
        $signature -split "`n" | Select-String 'Signer.*certificate (DN|SHA-256)' | ForEach-Object Line
    }
}

if ($NoInstall) { return }

# Prefer USB when the phone is attached both ways.
$devices = adb devices | Select-Object -Skip 1 | Where-Object { $_ -match '\tdevice$' } | ForEach-Object { ($_ -split "`t")[0] }
$serial = ($devices | Where-Object { $_ -notmatch '\.' } | Select-Object -First 1)
if (-not $serial) { $serial = $devices | Select-Object -First 1 }
if (-not $serial) {
    Write-Host 'No phone attached over adb. Copy the APK across and install it manually.'
    return
}

Write-Host "Installing on $serial..."
$out = adb -s $serial install -r $apk 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Write-Host $out -ForegroundColor Red
    if ($out -match 'UPDATE_INCOMPATIBLE') {
        Write-Host "The installed app has a different signature. Uninstall it first (this clears`npairing and permissions): adb -s $serial uninstall dev.risenid.tessera" -ForegroundColor Yellow
    }
    exit 1
}
$installed = adb -s $serial shell dumpsys package dev.risenid.tessera | Select-String 'versionName=(\S+)' | Select-Object -First 1
Write-Host "Installed version $($installed.Matches[0].Groups[1].Value)" -ForegroundColor Green
