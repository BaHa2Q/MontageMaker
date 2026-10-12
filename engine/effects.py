"""Timed video overlays shared by immediate viewing and exported frames."""
from dataclasses import replace
import math
import cv2
import numpy as np
from .background import apply_background


def active_effects(cfg, time, kind=None):
    return [clip for clip in cfg.effect_clips or []
            if not clip.get('muted', False)
            and clip.get('kind') not in ('freeze', 'slowmo')
            and (kind is None or clip.get('kind') == kind)
            and clip.get('time', 0) <= time < clip.get('time', 0) + clip.get('duration', 1)]


def clean_config(cfg):
    """Legacy global switches must not leak into timeline-based projects."""
    return replace(cfg, clarity=False, flash=False, shake=False, glow=False,
                   bars=False, scan=False, background_blur=0, background_dim=0,
                   background_desaturate=False, background_glow=False,
                   background_particles=False, background_template='none')


def motion_config(cfg, clip):
    params = clip.get('params', {})
    base = clean_config(cfg)
    return replace(base, **{key: params.get(key, getattr(cfg, key))
                           for key in ('motion_style', 'orbit_angle', 'rotation_direction', 'motion_strength')})


def backgrounds_at(cfg, time):
    base = clean_config(cfg)
    result = []
    keys = ('background_blur', 'background_dim', 'background_desaturate',
            'background_glow', 'background_particles', 'background_template',
            'background_strength', 'background_speed', 'background_color',
            'background_media', 'mask_feather')
    for clip in active_effects(cfg, time, 'background'):
        params = clip.get('params', {})
        result.append((replace(base, **{key: params[key] for key in keys if key in params}),
                       time - clip.get('time', 0)))
    return result


def timed_background(frame, mask, cfg, time):
    for settings, local in backgrounds_at(cfg, time):
        frame = apply_background(frame, mask, settings, local)
    return frame


def timed_visuals(frame, cfg, time):
    """Overlapping events compose in their saved timeline order."""
    out = frame.astype(np.float32)
    h, w = frame.shape[:2]
    for clip in active_effects(cfg, time):
        kind, params = clip['kind'], clip.get('params', {})
        if kind == 'background':
            continue
        local = time - clip.get('time', 0)
        duration = max(.001, clip.get('duration', 1))
        phase = min(1, max(0, local / duration))
        strength = float(params.get('strength', params.get('motion_strength', .65)))
        if kind == 'clarity':
            sharpness = float(params.get('sharpness', .35)) * strength
            contrast = float(params.get('contrast', 1.05))
            saturation = float(params.get('saturation', 1.08))
            blur = cv2.GaussianBlur(out, (0, 0), 1.1)
            out = (out + sharpness * (out - blur) - 127.5) * (1 + (contrast - 1) * strength) + 127.5
            gray = out.mean(-1, keepdims=True)
            out = gray + (out - gray) * (1 + (saturation - 1) * strength)
        elif kind == 'glow':
            light = np.clip(out - 155, 0, 100)
            out += cv2.GaussianBlur(light, (0, 0), max(1, h / 120)) * .5 * strength
        elif kind == 'flash':
            # A flash event defines its own short pulse, independent from freezing.
            pulse = max(0, 1 - phase)
            out += (255 - out) * pulse * strength
        elif kind == 'shake':
            envelope = math.sin(math.pi * phase)
            dx = math.sin(local * 85) * w * .009 * strength * envelope
            dy = math.cos(local * 71) * h * .008 * strength * envelope
            out = cv2.warpAffine(out, np.float32([[1, 0, dx], [0, 1, dy]]),
                                 (w, h), borderMode=cv2.BORDER_REFLECT)
        elif kind == 'scan':
            line = np.zeros_like(out)
            y = round((h - 1) * phase)
            cv2.line(line, (0, y), (w - 1, y), (255, 220, 100), max(1, h // 180))
            out += (line + cv2.GaussianBlur(line, (0, 0), max(1, h / 100))) * strength
        elif kind == 'bars':
            size = min(h // 2, max(1, round(h * float(params.get('size', .09)))))
            out[:size] = 0
            out[-size:] = 0
        out = np.clip(out, 0, 255)
    return out.astype(np.uint8)
