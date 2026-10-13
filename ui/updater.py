"""Startup update check: download the newer installer, verify it, run it."""
import hashlib
import json
import os
import tempfile
import urllib.request
from PySide6.QtCore import QThread, Signal

VERSION = '4.2'
UPDATE_URL = 'https://mizan.169-58-133-134.sslip.io/montagemaker/latest.json'


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


class UpdateDownload(QThread):
    progress = Signal(float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, data, parent=None):
        super().__init__(parent); self.data = data

    def run(self):
        try:
            path = os.path.join(tempfile.gettempdir(), f'MontageMaker-Setup-{self.data["version"]}.exe')
            digest = hashlib.sha256()
            with urllib.request.urlopen(self.data['url'], timeout=30) as response, open(path, 'wb') as handle:
                total = int(response.headers.get('Content-Length') or 0); received = 0
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk); digest.update(chunk); received += len(chunk)
                    if total: self.progress.emit(received / total)
            # The installer runs elevated, so only a file matching the published hash may run.
            if digest.hexdigest() != self.data['sha256'].lower():
                os.unlink(path); raise ValueError('ملف التحديث تالف؛ حاول مرة ثانية.')
            self.done.emit(path)
        except Exception as ex:
            self.failed.emit(str(ex))
