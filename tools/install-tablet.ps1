param(
    [string]$Serial,
    [string]$Apk = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\android\ProKrita-Android-arm64-v8a.apk'),
    [string]$Adb = 'adb'
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Apk -PathType Leaf)) { throw "No built APK exists at $Apk. Source ZIPs cannot be installed." }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $Apk).Path)
try {
    if (-not $archive.GetEntry('AndroidManifest.xml')) { throw 'The supplied file is not an APK.' }
    if (-not @($archive.Entries | Where-Object FullName -Like 'lib/arm64-v8a/*.so').Count) { throw 'APK has no ARM64 native libraries.' }
} finally { $archive.Dispose() }
$device = if ($Serial) { @('-s', $Serial) } else { @() }
& $Adb @device get-state
if ($LASTEXITCODE -ne 0) { throw 'Tablet is unavailable or has not authorized USB debugging.' }
& $Adb @device install -r $Apk
if ($LASTEXITCODE -ne 0) { throw 'Android rejected the APK installation.' }
$installed = & $Adb @device shell pm path org.krita.prokrita
if ($LASTEXITCODE -ne 0 -or $installed -notmatch '^package:') { throw 'Custom package was not found after installation.' }
& $Adb @device shell am start -n org.krita.prokrita/org.krita.android.MainActivity
if ($LASTEXITCODE -ne 0) { throw 'Custom Krita could not be launched.' }
'Installed and launched ProKrita Android. Stock Krita remains installed; runtime validation is still required.'
