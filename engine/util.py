"""Shared helpers: ffmpeg location, process running, easing, push-pull fill."""
import os, sys, math, shutil, subprocess
import numpy as np, cv2

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(os.environ.get('LOCALAPPDATA', __import__('tempfile').gettempdir()),
                         'MontageMakerStudio', 'Cache')
H, WD, FPS = 1080, 1920, 60


def _find(exe):
    configured = os.environ.get('MONTAGEMAKER_FFMPEG_DIR')
    if configured:
        candidate = os.path.join(configured, exe + ('.exe' if os.name == 'nt' else ''))
        if os.path.isfile(candidate):
            return candidate
    local = os.path.join(APP_DIR, 'ffmpeg', 'bin', exe + ('.exe' if os.name == 'nt' else ''))
    if os.path.exists(local):
        return local
    return shutil.which(exe) or exe


FFMPEG = _find('ffmpeg')
FFPROBE = _find('ffprobe')
NOWIN = 0x08000000 if os.name == 'nt' else 0   # no console windows on Windows


class Cancelled(RuntimeError):
    pass


def run(cmd, log=None, cancel=None):
    if log:
        log('> ' + os.path.basename(cmd[0]) + ' ' + ' '.join(str(c) for c in cmd[1:6]) + ' ...')
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while True:
            if cancel and cancel.is_set():
                raise Cancelled('Cancelled by user.')
            try:
                stdout, stderr = p.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        p.kill()
        p.communicate()
        raise
    if p.returncode != 0:
        raise RuntimeError(stderr[-2000:])
    return stdout


def popen(cmd, **kw):
    return subprocess.Popen([str(c) for c in cmd], creationflags=NOWIN, **kw)


def probe_frames(path):
    out = run([FFPROBE, '-v', 'error', '-count_packets', '-select_streams', 'v', '-show_entries',
               'stream=nb_read_packets', '-of', 'csv=p=0', path])
    return int(out.strip().split()[0])


def has_audio(path):
    out = run([FFPROBE, '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', path])
    return bool(out.strip())


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * x)


def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_io3(x):
    x = min(max(x, 0.0), 1.0)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def smooth(a, b, x):
    return ease((x - a) / (b - a))


def smooth_fill(field, known):
    """Push-pull interpolation of a float field (H,W,C) from known pixels."""
    f = field * known[..., None]
    w = known.astype(np.float32)
    pyr = [(f, w)]
    while min(pyr[-1][1].shape) > 8:
        f2 = cv2.pyrDown(pyr[-1][0]); w2 = cv2.pyrDown(pyr[-1][1])
        if f2.ndim == 2:
            f2 = f2[..., None]
        pyr.append((f2, w2))
    fo, wo = pyr[-1]
    gmean = fo.reshape(-1, fo.shape[-1]).sum(0) / max(wo.sum(), 1e-12)
    est = np.where(wo[..., None] > 1e-8, fo / np.maximum(wo[..., None], 1e-12), gmean[None, None, :])
    for f_l, w_l in reversed(pyr[:-1]):
        up = cv2.resize(est, (w_l.shape[1], w_l.shape[0]), interpolation=cv2.INTER_LINEAR)
        if up.ndim == 2:
            up = up[..., None]
        wn = np.clip(w_l * 4, 0, 1)[..., None]
        cur = f_l / np.maximum(w_l[..., None], 1e-12)
        est = wn * cur + (1 - wn) * up
    return est
