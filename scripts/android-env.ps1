# Toolchain for building the companion app on Windows. Dot-source it:
#   . .\scripts\android-env.ps1

function Find-Jdk {
    if ($env:JAVA_HOME_ANDROID) { return $env:JAVA_HOME_ANDROID }
    if ($env:JAVA_HOME -and (Test-Path "$env:JAVA_HOME\bin\java.exe")) { return $env:JAVA_HOME.TrimEnd('\') }
    $found = Get-ChildItem "$env:ProgramFiles\Eclipse Adoptium" -Directory -Filter 'jdk-21*' -ErrorAction SilentlyContinue |
        Sort-Object Name | Select-Object -Last 1
    if ($found) { return $found.FullName }
    throw "No JDK 21. Install it: winget install EclipseAdoptium.Temurin.21.JDK"
}

function Find-Gradle {
    $found = Get-ChildItem "$env:LOCALAPPDATA\Programs" -Directory -Filter 'gradle-*' -ErrorAction SilentlyContinue |
        Sort-Object { [version]($_.Name -replace '^gradle-', '') } | Select-Object -Last 1
    if ($found) { return $found.FullName }
    throw "No Gradle under $env:LOCALAPPDATA\Programs. Unpack gradle-9.6.0-bin.zip there."
}

$env:ANDROID_SDK_ROOT = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { "$env:LOCALAPPDATA\Android\Sdk" }
$env:ANDROID_HOME = $env:ANDROID_SDK_ROOT
$env:JAVA_HOME = Find-Jdk
$env:GRADLE_HOME = Find-Gradle

# Build output outside the checkout, as on Linux (settings.gradle.kts).
$env:TESSERA_BUILD_DIR = if ($env:TESSERA_BUILD_DIR) { $env:TESSERA_BUILD_DIR } else { "$env:USERPROFILE\android\build\tessera" }

$env:PATH = @(
    "$env:JAVA_HOME\bin", "$env:GRADLE_HOME\bin",
    "$env:ANDROID_HOME\platform-tools", "$env:ANDROID_HOME\cmdline-tools\latest\bin", $env:PATH
) -join ';'

if (-not (Test-Path "$env:ANDROID_HOME\platforms")) { throw "No Android SDK at $env:ANDROID_HOME" }
Write-Host ("Android toolchain ready: JDK {0}, Gradle {1}, SDK {2}" -f
    (Split-Path $env:JAVA_HOME -Leaf), (Split-Path $env:GRADLE_HOME -Leaf), $env:ANDROID_HOME) -ForegroundColor DarkGray
