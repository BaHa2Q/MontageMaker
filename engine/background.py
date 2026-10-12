"""Background effects with user-drawn foreground masks and optical-flow tracking."""
import math
import cv2
import numpy as np
from .templates import overlay


def polygon_mask(shape, polygons):
    h, w = shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for polygon in polygons:
        points = np.round(np.array(polygon) * [w - 1, h - 1]).astype(np.int32)
        cv2.fillPoly(mask, [points], 255)
    return mask


def enabled(cfg):
    return bool(cfg.background_blur or cfg.background_dim or cfg.background_desaturate
                or cfg.background_glow or cfg.background_particles or cfg.background_template != 'none')


class ProtectionTracker:
    """Propagate a user selection between nearby frames, including before its anchor."""
    def __init__(self, video, polygons, anchor_time, shape, cancel=lambda: None, tracking_width=640):
        self.h, self.w = shape[:2]
        scale = min(1, tracking_width / self.w, 480 / self.h)
        self.sw, self.sh = max(32, round(self.w * scale)), max(32, round(self.h * scale))
        self.cap = cv2.VideoCapture(video)
        self.mask = polygon_mask((self.sh, self.sw), polygons)
        self.anchor_mask = self.mask.copy()
        self.time = max(0, anchor_time)
        self.anchor_time = self.time
        self.cancel = cancel
        self.gray = self._at(self.time)
        self.anchor_gray = self.gray.copy()
        self.gx, self.gy = np.meshgrid(np.arange(self.sw, dtype=np.float32), np.arange(self.sh, dtype=np.float32))

    def _at(self, t):
        self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0, t) * 1000)
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError('Could not read the selected object frame.')
        return self._gray(frame)

    def _gray(self, frame):
        return cv2.cvtColor(cv2.resize(frame, (self.sw, self.sh)), cv2.COLOR_BGR2GRAY)

    def _step(self, gray):
        self.cancel()
        flow = cv2.calcOpticalFlowFarneback(gray, self.gray, None, .5, 3, 25, 3, 5, 1.2, 0)
        self.mask = cv2.remap(self.mask, self.gx + flow[..., 0], self.gy + flow[..., 1],
                              cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        self.gray = gray

    def at(self, t, frame=None):
        if abs(t - self.anchor_time) < 0.001:
            self.mask = self.anchor_mask.copy()
            self.gray = self.anchor_gray.copy()
            self.time = t
        else:
            delta = t - self.time
            steps = max(1, int(math.ceil(abs(delta) / .1)))
            start = self.time
            for i in range(1, steps + 1):
                self.cancel()
                target = start + delta * i / steps
                gray = self._gray(frame) if i == steps and frame is not None else self._at(target)
                self._step(gray)
            self.time = t
        return cv2.resize(self.mask, (self.w, self.h), interpolation=cv2.INTER_LINEAR)

    def close(self):
        self.cap.release()


def apply_background(frame, mask, cfg, time=0):
    if not enabled(cfg):
        return frame
    h, w = frame.shape[:2]
    if mask is None:
        mask = np.zeros((h,w),np.uint8)
    sigma = cfg.background_blur * h / 1080
    background = cv2.GaussianBlur(frame, (0, 0), max(.2, sigma)).astype(np.float32) if sigma else frame.astype(np.float32)
    if cfg.background_desaturate:
        gray = cv2.cvtColor(np.clip(background, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY)
        background = np.repeat(gray[..., None], 3, axis=2).astype(np.float32)
    background *= 1 - cfg.background_dim
    if cfg.background_glow:
        bright = np.clip(background - 100, 0, 155)
        background += cv2.GaussianBlur(bright, (0, 0), max(1, h / 65)) * .65
    if cfg.background_particles:
        particles = np.zeros_like(background)
        rng = np.random.default_rng(18)
        for x, y, speed, radius in rng.uniform(0, 1, (55, 4)):
            px = int((x + .018 * math.sin(time * .8 + y * 7)) * w)
            py = int(((y - time * (.025 + speed * .04)) % 1) * h)
            cv2.circle(particles, (px, py), max(1, round((1 + radius * 3) * h / 720)),
                       (120, 220, 190), -1, cv2.LINE_AA)
        background += particles + cv2.GaussianBlur(particles, (0, 0), max(1, h / 160)) * .9
    # Only soften the border. Foreground interiors keep the source image exactly.
    background = overlay(background, cfg, time)
    alpha = mask.astype(np.float32) / 255
    if cfg.mask_feather:
        alpha = cv2.GaussianBlur(alpha, (0, 0), max(.2, cfg.mask_feather * h / 1080))
    alpha = alpha[..., None]
    alpha[mask[..., None] >= 254] = 1
    return np.clip(frame * alpha + background * (1 - alpha), 0, 255).astype(np.uint8)
