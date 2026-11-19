param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root 'android\build-host-install.log'
$state = Join-Path $root 'android\build-host-install-status.txt'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this setup as administrator; Windows requires it for WSL.'
}
Start-Transcript -LiteralPath $log -Append
try {
    'Installing WSL' | Set-Content -LiteralPath $state
    & winget.exe install --id Microsoft.WSL --exact --source winget --accept-package-agreements --accept-source-agreements --silent --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "WSL package installer failed: $LASTEXITCODE" }
    'Enabling Virtual Machine Platform' | Set-Content -LiteralPath $state
    $feature = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
    if ($feature.RestartNeeded) {
        'Restart required. Save your work and restart Windows, then continue the ProKrita build setup.' | Set-Content -LiteralPath $state
        exit 3010
    }
    'Installing Ubuntu 24.04' | Set-Content -LiteralPath $state
    & wsl.exe --install -d Ubuntu-24.04 --no-launch --web-download
    if ($LASTEXITCODE -ne 0) { throw "Ubuntu setup failed: $LASTEXITCODE" }
    'WSL and Ubuntu installation commands completed. Verify wsl --list --verbose before building.' | Set-Content -LiteralPath $state
} catch {
    ('FAILED: ' + $_.Exception.Message) | Set-Content -LiteralPath $state
    throw
} finally {
    Stop-Transcript
}
