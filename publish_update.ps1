# Set the version, push the code, build the installer and publish it as the latest GitHub release.
# Usually run through publish.bat.
param([string]$Version = '', [string]$Notes = '', [string]$Repo = 'BaHa2Q/MontageMaker')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if ($Version) {
    if ($Version -notmatch '^\d+(\.\d+)+$') { throw "Version must look like 4.4" }
    $text = [IO.File]::ReadAllText("$PWD\ui\updater.py")
    [IO.File]::WriteAllText("$PWD\ui\updater.py", ($text -replace "VERSION = '[\d.]+'", "VERSION = '$Version'"), (New-Object Text.UTF8Encoding $false))
}
$version = (Select-String -Path ui\updater.py -Pattern "^VERSION = '(.+)'").Matches[0].Groups[1].Value
git add -A
git diff --cached --quiet
if ($LASTEXITCODE) { git commit -m "Release $version"; if ($LASTEXITCODE) { throw 'git commit failed.' } }
git push
if ($LASTEXITCODE) { throw 'git push failed.' }
$python = '.\venv\Scripts\python.exe'
& $python -m pip install -q PyInstaller==6.22.3
& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name MontageMaker --icon assets\montage.ico --add-data "assets;assets" --add-data "ffmpeg\bin;ffmpeg\bin" launcher.py
if ($LASTEXITCODE) { throw 'PyInstaller failed.' }
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Install Inno Setup 6 first.' }
& $iscc /Q "/DAppVer=$version" installer.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed.' }
$name = "MontageMaker-Setup-$version.exe"
Copy-Item dist\MontageMaker-Setup.exe "dist\$name" -Force

# Use the GitHub login Git already stores for this machine.
# PowerShell 5 mangles text piped into native programs, so feed Git from a plain LF file.
$query = Join-Path $env:TEMP 'mm-credential.txt'
[IO.File]::WriteAllText($query, "protocol=https`nhost=github.com`n`n")
$credential = cmd /c "git credential fill < `"$query`""
Remove-Item $query
if (-not $credential) { throw 'No saved GitHub login. Run: git push once and sign in.' }
$token = ($credential | Where-Object { $_ -like 'password=*' }) -replace '^password=', ''
$headers = @{ Authorization = "Bearer $token"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'MontageMaker' }
$body = @{ tag_name = "v$version"; target_commitish = (git rev-parse HEAD); name = "Montage Maker $version"; body = $Notes } | ConvertTo-Json
$release = Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$Repo/releases" -Headers $headers -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType 'application/json; charset=utf-8'
Invoke-RestMethod -Method Post -Uri "https://uploads.github.com/repos/$Repo/releases/$($release.id)/assets?name=$name" -Headers $headers -InFile "dist\$name" -ContentType 'application/octet-stream' | Out-Null
Write-Host "Published $version -> $($release.html_url)"
