"""Startup update check; a separate updater window downloads and installs after the editor closes."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout

VERSION = '4.3'
UPDATE_URL = 'https://api.github.com/repos/BaHa2Q/MontageMaker/releases/latest'


def newer(remote, local=VERSION):
    parse = lambda value: tuple(int(part) for part in str(value).split('.'))
    return parse(remote) > parse(local)


class UpdateCheck(QThread):
    found = Signal(dict)

    def run(self):
        try:
            request = urllib.request.Request(UPDATE_URL, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'MontageMaker'})
            with urllib.request.urlopen(request, timeout=8) as response:
                release = json.load(response)
            version = release['tag_name'].lstrip('v')
            asset = next(a for a in release['assets'] if a['name'].lower().endswith('.exe'))
            # GitHub publishes each asset's SHA-256 as "sha256:<hex>".
            sha = asset['digest'].split(':', 1)[1]
            if newer(version) and len(sha) == 64:
                self.found.emit(dict(version=version, url=asset['browser_download_url'], sha256=sha, notes=release.get('body') or ''))
        except Exception:
            pass  # Offline or server down: the editor must still open normally.


def start_update(data):
    """Reopen this program in updater mode; the caller closes the editor right after."""
    args = [data['url'], data['sha256'], str(data['version'])]
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--update', *args]
    else:
        command = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'app.py'), '--update', *args]
    subprocess.Popen(command)


class UpdateDownload(QThread):
    progress = Signal(float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, url, sha, path, parent=None):
        super().__init__(parent); self.url, self.sha, self.path = url, sha.lower(), path

    def run(self):
        try:
            digest = hashlib.sha256()
            with urllib.request.urlopen(self.url, timeout=30) as response, open(self.path, 'wb') as handle:
                total = int(response.headers.get('Content-Length') or 0); received = 0
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk); digest.update(chunk); received += len(chunk)
                    if total: self.progress.emit(received / total)
            # The installer runs elevated, so only a file matching the published hash may run.
            if digest.hexdigest() != self.sha:
                os.unlink(self.path); raise ValueError('ملف التحديث وصل ناقص أو تالف. جرّب مرة ثانية.')
            self.done.emit(self.path)
        except Exception as ex:
            self.failed.emit(str(ex))


class UpdateWindow(QDialog):
    def __init__(self, url, sha, version):
        super().__init__()
        self.setWindowTitle('تحديث Montage Maker'); self.setLayoutDirection(Qt.RightToLeft); self.setFixedWidth(430)
        box = QVBoxLayout(self); box.setContentsMargins(22, 20, 22, 18); box.setSpacing(12)
        title = QLabel(f'جاري تنزيل الإصدار {version}'); title.setObjectName('section'); box.addWidget(title)
        self.text = QLabel('البرنامج رح يفتح لحاله بعد التحديث.'); self.text.setObjectName('muted'); self.text.setWordWrap(True); box.addWidget(self.text)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.setTextVisible(False); box.addWidget(self.bar)
        self.button = QPushButton('إلغاء'); self.button.clicked.connect(self.reject); box.addWidget(self.button, 0, Qt.AlignLeft)
        path = os.path.join(tempfile.gettempdir(), f'MontageMaker-Setup-{version}.exe')
        self.job = UpdateDownload(url, sha, path, self)
        self.job.progress.connect(lambda value: (self.bar.setValue(round(value * 1000)), self.text.setText(f'تم تنزيل {value * 100:.0f}%')))
        self.job.done.connect(self.install); self.job.failed.connect(self.failed); self.job.start()

    def install(self, path):
        self.bar.setValue(1000); self.text.setText('جاري التنصيب… وافق على رسالة ويندوز إذا طلعت.'); self.button.setEnabled(False)
        try:
            # ShellExecute shows the UAC prompt the admin installer needs; the installer reopens the editor.
            os.startfile(path, arguments='/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS')
        except OSError as ex:
            self.failed('ما انبدأ التنصيب: ' + str(ex)); return
        self.accept()

    def failed(self, message):
        self.text.setText(message); self.bar.setValue(0)
        self.button.setText('إغلاق'); self.button.setEnabled(True)

    def reject(self):
        if self.job.isRunning():
            self.job.terminate(); self.job.wait()
        super().reject()


def run_update_window(url, sha, version):
    return 0 if UpdateWindow(url, sha, version).exec() == QDialog.Accepted else 1
