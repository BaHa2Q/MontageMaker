# Build the installer and upload it as the latest update.
# 1. Raise VERSION in ui\updater.py   2. Run: .\publish_update.ps1 -Notes "what changed"
param(
    [string]$Notes = '',
    [string]$Server = 'root@169.58.133.134',
    [string]$RemoteDir = '/var/www/montagemaker',
    [string]$BaseUrl = 'https://mizan.169-58-133-134.sslip.io/montagemaker'
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$version = (Select-String -Path ui\updater.py -Pattern "^VERSION = '(.+)'").Matches[0].Groups[1].Value
$python = '.\venv\Scripts\python.exe'
& $python -m pip install -q PyInstaller==6.22.3
& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name MontageMaker --icon assets\montage.ico --add-data "assets;assets" --add-data "ffmpeg\bin;ffmpeg\bin" launcher.py
if ($LASTEXITCODE) { throw 'PyInstaller failed.' }
$iscc = @("$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Install Inno Setup 6 first.' }
& $iscc "/DAppVer=$version" installer.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed.' }

$name = "MontageMaker-Setup-$version.exe"
Copy-Item dist\MontageMaker-Setup.exe "dist\$name" -Force
$hash = (Get-FileHash "dist\$name" -Algorithm SHA256).Hash.ToLower()
$manifest = [ordered]@{ version = $version; url = "$BaseUrl/$name"; sha256 = $hash; notes = $Notes } | ConvertTo-Json
[IO.File]::WriteAllText("$PWD\dist\latest.json", $manifest, (New-Object Text.UTF8Encoding $false))

# Installer first, manifest last: clients never see a version whose file is missing.
scp "dist\$name" "${Server}:$RemoteDir/"
scp dist\latest.json "${Server}:$RemoteDir/latest.json"
Write-Host "Published $version -> $BaseUrl/latest.json"
