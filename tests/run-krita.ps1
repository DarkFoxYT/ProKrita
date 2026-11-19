param([string]$Krita = "$env:ProgramFiles\Krita (x64)\bin\krita.com", [string]$TestScript = 'krita_gestures.py')

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$scratchName = 'prokrita-qa-' + [guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$scratch = Join-Path $tempRoot $scratchName
$local = Join-Path $scratch 'AppData\Local'
$roaming = Join-Path $scratch 'AppData\Roaming'
$plugins = Join-Path $roaming 'krita\pykrita'
New-Item -ItemType Directory -Path $local,$plugins -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $project 'prokrita') -Destination $plugins -Recurse
Copy-Item -LiteralPath (Join-Path $project 'prokrita.desktop') -Destination $plugins
Copy-Item -LiteralPath (Join-Path $PSScriptRoot $TestScript) -Destination (Join-Path $scratch 'krita_gestures.py')
# Only the scratch copy runs the in-app checks. Exercise a full undo stack as well.
[IO.File]::WriteAllLines((Join-Path $local 'kritarc'), @('undoStackLimit=4', '[python]', 'enable_prokrita=true', '[ProKrita]', 'penSnap=true'))
Add-Content -Encoding ascii -LiteralPath (Join-Path $plugins 'prokrita\__init__.py') -Value @'

import os, runpy
runpy.run_path(os.path.join(os.environ["PROKRITA_QA"], "krita_gestures.py"))
'@
$start = [Diagnostics.ProcessStartInfo]::new($Krita, '--nosplash')
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
$start.EnvironmentVariables['PROKRITA_QA'] = $scratch
$start.EnvironmentVariables['USERPROFILE'] = $scratch
$start.EnvironmentVariables['LOCALAPPDATA'] = $local
$start.EnvironmentVariables['APPDATA'] = $roaming
$start.EnvironmentVariables['TMP'] = $scratch
$start.EnvironmentVariables['TEMP'] = $scratch
$process = [Diagnostics.Process]::Start($start)
if (-not $process.WaitForExit(240000)) { throw "The scratch Krita instance did not finish. See $scratch" }
$log = Join-Path $scratch 'gestures.log'
if (-not (Test-Path -LiteralPath $log)) { throw "No Krita test report. See $scratch" }
Get-Content -Encoding utf8 -LiteralPath $log
if ($process.ExitCode -ne 0 -or -not (Select-String -Quiet -Pattern '^PASS$' -LiteralPath $log) -or
    (Select-String -Quiet -Pattern '^(FAILED|UNHANDLED)' -LiteralPath $log)) {
    throw "Krita gesture checks failed (process exit code $($process.ExitCode)). See $log"
}
$resolved = (Resolve-Path -LiteralPath $scratch).Path
if ((Split-Path -Parent $resolved) -eq $tempRoot -and (Split-Path -Leaf $resolved) -eq $scratchName) {
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
