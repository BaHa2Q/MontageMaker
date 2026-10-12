"""Procedural background templates shared by live viewing and export."""
import math
import os
import threading
import cv2
import numpy as np

MOTION_TEMPLATES = [
    ('spin', 'دوران', '↻'), ('hold', 'تجميد هادئ', 'Ⅱ'),
    ('pulse', 'نبض', '◉'), ('glitch', 'جليتش', 'ϟ'),
    ('focus', 'تركيز', '◎'), ('tilt', 'ميل سينمائي', '◈'),
    ('scan', 'مسح ضوئي', '▤'),
]
BACKGROUND_TEMPLATES = [
    ('none', 'بدون قالب', '#45546d'), ('particles', 'غبار ضوئي', '#73e0bd'),
    ('snow', 'ثلج', '#b9e6ff'), ('embers', 'شرر', '#ff9457'),
    ('bokeh', 'بوكيه', '#bb8cff'), ('rain', 'مطر', '#73baff'),
    ('stars', 'نجوم', '#ddd7ff'), ('neon', 'خطوط نيون', '#f083d7'),
    ('grid', 'شبكة رقمية', '#60decc'), ('aurora', 'شفق', '#83d8a4'),
    ('rays', 'أشعة ضوء', '#ffd697'), ('vignette', 'إضاءة سينمائية', '#aba1eb'),
    ('media', 'خلفية من ملف', '#78abe8'),
]
_media = threading.local()


def close_media():
    cap = getattr(_media, 'cap', None)
    if cap is not None:
        cap.release()
    _media.cap = None
    _media.path = None


def media_layer(path, t, w, h):
    if getattr(_media, 'path', None) != path:
        close_media()
        _media.path = path
        _media.image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR) if os.path.splitext(path)[1].lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp') else None
        if _media.image is None:
            _media.cap = cv2.VideoCapture(path)
    image = getattr(_media, 'image', None)
    if image is None and getattr(_media, 'cap', None) is not None:
        cap = _media.cap
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1
        index = int(t * fps) % max(1, int(count))
        if abs(cap.get(cv2.CAP_PROP_POS_FRAMES) - index) > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = cap.read()
        if not ok:
            image = None
    if image is None:
        return np.zeros((h, w, 3), np.float32)
    scale = max(w / image.shape[1], h / image.shape[0])
    resized = cv2.resize(image, (max(w, round(image.shape[1] * scale)), max(h, round(image.shape[0] * scale))))
    y, x = (resized.shape[0] - h) // 2, (resized.shape[1] - w) // 2
    return resized[y:y+h, x:x+w].astype(np.float32)


def overlay(background, cfg, t):
    style = cfg.background_template
    if style == 'none':
        return background
    h, w = background.shape[:2]
    t *= cfg.background_speed
    strength = cfg.background_strength
    try:
        color = tuple(int(cfg.background_color.lstrip('#')[i:i+2], 16) for i in (4, 2, 0))
    except (ValueError, IndexError):
        color = (189, 224, 115)
    layer = np.zeros((h, w, 3), np.float32)
    rng = np.random.default_rng(28)
    if style in ('particles', 'snow', 'embers', 'bokeh', 'stars', 'rain'):
        n = 65 if style != 'stars' else 115
        for x, y, speed, size, phase in rng.random((n, 5)):
            drift = .025 * math.sin(t * .65 + phase * 9)
            py = (y + t * (.03 + speed * .08) * (-1 if style in ('particles', 'embers', 'bokeh') else 1)) % 1
            px = (x + drift) % 1
            r = max(1, round((1 + size * (15 if style == 'bokeh' else 3)) * h / 720))
            tint = color if style not in ('snow', 'stars') else (240, 240, 240)
            if style == 'rain':
                cv2.line(layer, (int(px*w), int(py*h)), (int(px*w-w*.008), int(py*h+h*.027)), tint, max(1, h//800), cv2.LINE_AA)
            else:
                intensity = .35 + .65 * abs(math.sin(t * (1 + speed) + phase * 7)) if style == 'stars' else 1
                cv2.circle(layer, (int(px*w), int(py*h)), r, tuple(v * intensity for v in tint), -1, cv2.LINE_AA)
        if style == 'bokeh':
            layer = cv2.GaussianBlur(layer, (0, 0), max(1, h/100)) * .8
        else:
            layer += cv2.GaussianBlur(layer, (0, 0), max(.5, h/160)) * .75
    elif style in ('neon', 'grid'):
        for k in range(10):
            y = round(((k / 9 + t*.045) % 1) * h)
            cv2.line(layer, (0, y), (w, y), color, max(1, h//400), cv2.LINE_AA)
        if style == 'grid':
            for k in range(14):
                x = round(k/13*w)
                cv2.line(layer, (w//2, h//3), (x, h), color, max(1,h//500), cv2.LINE_AA)
        else:
            for k in range(5):
                x = round(((k/5 + t*.015) % 1)*w)
                cv2.line(layer, (x,0), (x+w//3,h), color, max(1,h//240), cv2.LINE_AA)
        layer += cv2.GaussianBlur(layer, (0,0), max(1,h/70)) * 1.3
    elif style in ('aurora', 'rays'):
        yy, xx = np.mgrid[0:h,0:w].astype(np.float32)
        xx /= max(1,w); yy /= max(1,h)
        if style == 'aurora':
            band = np.exp(-((yy-(.4+.16*np.sin(xx*9+t*.6)))/.12)**2)
            band += .5*np.exp(-((yy-(.58+.14*np.sin(xx*11-t*.4)))/.10)**2)
        else:
            angle = np.arctan2(yy-.1, (xx-.5)*w/h)
            band = np.maximum(0, np.sin(angle*18+t*.45))**6 * np.exp(-yy*.9)
        layer = band[...,None] * np.array(color,np.float32) * .6
    elif style == 'vignette':
        yy, xx = np.mgrid[0:h,0:w].astype(np.float32)
        radius = ((xx-w/2)/(w*.7))**2 + ((yy-h/2)/(h*.7))**2
        return background * (1-np.clip(radius*strength*.7,0,.85))[...,None]
    elif style == 'media':
        return background * (1-strength) + media_layer(cfg.background_media,t,w,h) * strength
    return background + layer * strength
