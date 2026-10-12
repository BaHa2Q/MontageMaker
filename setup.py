"""Repeatable Windows setup: dependencies + FFmpeg, with checked failures."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import venv
import zipfile

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / 'venv' / 'Scripts' / 'python.exe'
FFMPEG_URL = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'


def command(args):
    subprocess.run([str(x) for x in args], check=True)


def check():
    if not PYTHON.exists():
        raise RuntimeError('The app environment is missing. Run setup.bat.')
    command([PYTHON, '-c', 'import cv2, numpy, scipy; from PySide6.QtWidgets import QApplication; '
             'from PySide6.QtMultimedia import QMediaPlayer, QVideoSink; '
             'from engine.pipeline import Config, run_montage; print("Libraries and editor: OK")'])
    for name in ('ffmpeg', 'ffprobe'):
        path = ROOT / 'ffmpeg' / 'bin' / (name + '.exe')
        subprocess.run([str(path), '-version'], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=0x08000000)
    print('FFmpeg and FFprobe: OK')


def install_ffmpeg():
    target = ROOT / 'ffmpeg' / 'bin'
    if all((target / name).exists() for name in ('ffmpeg.exe', 'ffprobe.exe')):
        print('[3/4] FFmpeg is already installed.')
        return
    print('[3/4] Downloading FFmpeg from its build project...')
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mm_setup_') as temp:
        archive = Path(temp) / 'ffmpeg.zip'
        request = urllib.request.Request(FFMPEG_URL, headers={'User-Agent': 'MontageMakerStudio/2'})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open('wb') as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        # Extract only known executable basenames; never trust archive paths.
        with zipfile.ZipFile(archive) as source:
            for name in ('ffmpeg.exe', 'ffprobe.exe'):
                member = next((item for item in source.namelist() if item.endswith('/bin/' + name)), None)
                if member is None:
                    raise RuntimeError(f'The downloaded FFmpeg archive is missing {name}.')
                staging = target / (name + '.download')
                with source.open(member) as src, staging.open('wb') as dest:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dest.write(chunk)
                os.replace(staging, target / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Check an existing installation without downloads.')
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.check:
        check()
        return
    if sys.version_info < (3, 10):
        raise RuntimeError('Python 3.10 or newer is required; Python 3.12 is recommended.')
    print('[1/4] Preparing the app environment...')
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / 'venv')
    print('[2/4] Installing/updating the desktop editor libraries...')
    command([PYTHON, '-m', 'pip', 'install', '--upgrade', 'pip'])
    command([PYTHON, '-m', 'pip', 'install', '--upgrade', '-r', ROOT / 'requirements.txt'])
    install_ffmpeg()
    print('[4/4] Checking the installation...')
    check()
    shortcut_script = ROOT / 'create_shortcut.ps1'
    if shortcut_script.exists():
        command(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', shortcut_script])
    print('\nSetup complete! Double-click run.bat to open Montage Maker Studio.')
    print('You can run setup.bat again to refresh this installation. No 3D models are required.')


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        print('\nSETUP FAILED:', ex)
        print('Fix this problem and run setup.bat again. Your source videos are untouched.')
        sys.exit(1)
