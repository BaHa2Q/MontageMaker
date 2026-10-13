"""Startup update check; the download and install run after the editor closes."""
import json
import os
import subprocess
import tempfile
import urllib.request
from PySide6.QtCore import QThread, Signal

VERSION = '4.3'
UPDATE_URL = 'https://mizan.169-58-133-134.sslip.io/montagemaker/latest.json'

# Runs in its own window after the editor exits, so the installer can replace every file.
_SCRIPT = r'''
param($Url, $Sha, $Out)
$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'Montage Maker Update'
try {
    Write-Host 'Downloading Montage Maker update...'
    Start-BitsTransfer -Source $Url -Destination $Out -DisplayName 'Montage Maker update'
    # The installer runs elevated, so only a file matching the published hash may run.
    if ((Get-FileHash $Out -Algorithm SHA256).Hash.ToLower() -ne $Sha.ToLower()) { Remove-Item $Out; throw 'The downloaded file is damaged. Try again later.' }
    Write-Host 'Installing...'
    Start-Process $Out -Verb RunAs -ArgumentList '/SILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS'
} catch {
    Write-Host "Update failed: $_" -ForegroundColor Red
    Read-Host 'Press Enter to close'
}
'''


def newer(remote, local=VERSION):
    parse = lambda value: tuple(int(part) for part in str(value).split('.'))
    return parse(remote) > parse(local)


class UpdateCheck(QThread):
    found = Signal(dict)

    def run(self):
        try:
            with urllib.request.urlopen(UPDATE_URL, timeout=8) as response:
                data = json.load(response)
            if newer(data['version']) and str(data['url']).startswith('https://') and len(data['sha256']) == 64:
                self.found.emit(data)
        except Exception:
            pass  # Offline or server down: the editor must still open normally.


def start_update(data):
    """Launch the detached downloader/installer; the caller quits right after."""
    folder = tempfile.gettempdir()
    script = os.path.join(folder, 'montagemaker_update.ps1')
    with open(script, 'w', encoding='utf-8-sig') as handle:
        handle.write(_SCRIPT)
    out = os.path.join(folder, f'MontageMaker-Setup-{data["version"]}.exe')
    subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script,
                      '-Url', data['url'], '-Sha', data['sha256'], '-Out', out],
                     creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP)
