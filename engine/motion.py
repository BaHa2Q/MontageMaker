"""2D freeze animation with complete-frame rotation and optional visual effects."""
import math
import cv2
import numpy as np
from .background import apply_background


class Motion:
    def __init__(self, frame, cfg, count, protected_mask=None):
        self.original = frame
        self.cfg, self.count = cfg, count
        self.protected_mask = protected_mask
        self.h, self.w = frame.shape[:2]
        small = cv2.resize(frame, (max(2, self.w // 8), max(2, self.h // 8)))
        self.background = cv2.resize(cv2.GaussianBlur(small, (0, 0), 10), (self.w, self.h))
        self.background = (self.background.astype(np.float32) * 0.4).astype(np.uint8)

    def frame(self, k):
        # Reach the requested angle, then return smoothly to the source orientation.
        p = k / max(1, self.count - 1)
        if p < 0.85:
            q = np.clip(p / 0.85, 0, 1)
            amount = (1 - math.cos(q * math.pi)) / 2
        else:
            q = np.clip((p - 0.85) / 0.15, 0, 1)
            amount = (1 + math.cos(q * math.pi)) / 2
        style, strength = self.cfg.motion_style, self.cfg.motion_strength
        angle = self.cfg.orbit_angle * self.cfg.rotation_direction * amount if style == 'spin' else 0
        if style == 'tilt':
            angle = math.sin(p * math.pi * 2) * 9 * strength
        theta = math.radians(angle)
        # Fit the entire rotated image. No source edges are discarded during rotation.
        bw = abs(self.w * math.cos(theta)) + abs(self.h * math.sin(theta))
        bh = abs(self.h * math.cos(theta)) + abs(self.w * math.sin(theta))
        scale = min(self.w / max(bw, 1), self.h / max(bh, 1))
        if style == 'pulse':
            scale *= 1 - strength * .13 * (1 - math.cos(p * math.pi * 6)) / 2
        elif style == 'focus':
            scale *= 1 - strength * .15 * math.sin(p * math.pi)
        matrix = cv2.getRotationMatrix2D((self.w / 2, self.h / 2), angle, scale)
        original = apply_background(self.original, self.protected_mask, self.cfg, k / self.cfg.output_fps)
        frame = cv2.warpAffine(original, matrix, (self.w, self.h), flags=cv2.INTER_LINEAR)
        alpha = cv2.warpAffine(np.full((self.h, self.w), 255, np.uint8), matrix,
                               (self.w, self.h), flags=cv2.INTER_LINEAR).astype(np.float32)[..., None] / 255
        out = frame.astype(np.float32) + self.background * (1 - alpha)
        if style == 'glitch' and .03 < p < .94:
            shift = int(math.sin(k * 1.7) * self.w * .008 * strength)
            out[..., 2] = np.roll(out[..., 2], shift, axis=1)
            out[..., 0] = np.roll(out[..., 0], -shift, axis=1)
            for band in range(4):
                y = int(((band*.23 + p*.6) % 1)*self.h)
                height = max(1, self.h//45)
                out[y:y+height] = np.roll(out[y:y+height], shift*3, axis=1)
        if (self.cfg.scan or style == 'scan') and p < 0.65:
            y = int(self.h * min(p / 0.65, 1))
            line = np.zeros_like(out)
            cv2.line(line, (0, y), (self.w, y), (255, 220, 100), max(1, self.h // 180))
            out += (cv2.GaussianBlur(line, (0, 0), max(1, self.h / 100)) + line) * strength
        if self.cfg.flash:
            amp = max(0, 1 - p / 0.035) * 0.65 + max(0, 1 - abs(p - 0.965) / 0.025) * 0.25
            out += (255 - out) * amp * strength
        if self.cfg.shake and 0.88 < p < 0.98:
            dx = math.sin(p * 250) * self.w * 0.004 * math.sin((p - 0.88) / 0.1 * math.pi) * strength
            out = cv2.warpAffine(out, np.float32([[1, 0, dx], [0, 1, 0]]),
                                 (self.w, self.h), borderMode=cv2.BORDER_REFLECT)
        if k == self.count - 1:
            out = original.astype(np.float32)
        return np.clip(out, 0, 255).astype(np.uint8)


def finish(frame, cfg):
    out = frame.astype(np.float32)
    if cfg.clarity:
        blur = cv2.GaussianBlur(out, (0, 0), 1.1)
        out = (out + 0.35 * (out - blur) - 127.5) * 1.05 + 127.5
        gray = out.mean(-1, keepdims=True)
        out = gray + (out - gray) * 1.08
    if cfg.glow:
        light = np.clip(out - 155, 0, 100)
        out += cv2.GaussianBlur(light, (0, 0), max(1, frame.shape[0] / 120)) * 0.3
    out = np.clip(out, 0, 255).astype(np.uint8)
    if cfg.bars:
        size = round(out.shape[0] * 0.09)
        out[:size] = 0
        out[-size:] = 0
    return out
