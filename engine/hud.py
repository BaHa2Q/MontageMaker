"""User-selected HUD restoration. Never crop, resize, or zoom the picture."""
from collections import OrderedDict
import cv2
import numpy as np


def region_mask(shape, regions):
    h, w = shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for x0, y0, x1, y1 in regions:
        a, b = max(0, int(x0 * w)), max(0, int(y0 * h))
        c, d = min(w, int(np.ceil(x1 * w))), min(h, int(np.ceil(y1 * h)))
        mask[b:d, a:c] = 255
    return mask


class HUDCleaner:
    def __init__(self, mask, video=None, method='temporal', cancel=lambda: None):
        self.mask = mask
        self.enabled = bool(mask.any())
        self.method = method
        self.cancel = cancel
        self.cap = cv2.VideoCapture(video) if video and self.enabled and method == 'temporal' else None
        self.cache = OrderedDict()
        self.h, self.w = mask.shape
        if self.enabled:
            scale = min(1.0, 640 / self.w, 480 / self.h)
            self.sw, self.sh = max(32, round(self.w * scale)), max(32, round(self.h * scale))
            self.small_mask = cv2.resize(mask, (self.sw, self.sh), interpolation=cv2.INTER_NEAREST)
            self.gx, self.gy = np.meshgrid(np.arange(self.sw, dtype=np.float32), np.arange(self.sh, dtype=np.float32))

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def _reference(self, t):
        key = round(t * 4) / 4
        if key < 0:
            return None
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        self.cap.set(cv2.CAP_PROP_POS_MSEC, key * 1000)
        ok, ref = self.cap.read()
        if not ok:
            return None
        ref = cv2.resize(ref, (self.w, self.h))
        self.cache[key] = ref
        while len(self.cache) > 6:
            self.cache.popitem(last=False)
        return ref

    def clean(self, img, time=0.0):
        if not self.enabled:
            return img
        self.cancel()
        # Restore only selected pixels; every pixel outside the selection is preserved.
        result = cv2.inpaint(img, self.mask, max(3, round(self.w / 400)), cv2.INPAINT_TELEA)
        if self.cap is None or not self.cap.isOpened():
            return result
        cur = cv2.resize(result, (self.sw, self.sh), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)
        accum = np.zeros_like(img, np.float32)
        weights = np.zeros((self.h, self.w), np.float32)
        for dt in (-0.75, 0.75):
            self.cancel()
            ref = self._reference(time + dt)
            if ref is None:
                continue
            small = cv2.resize(ref, (self.sw, self.sh), interpolation=cv2.INTER_AREA)
            clean_ref = cv2.inpaint(small, self.small_mask, 3, cv2.INPAINT_TELEA)
            ref_gray = cv2.cvtColor(clean_ref, cv2.COLOR_BGR2GRAY)
            # Forward/backward consistency rejects mismatched and occluded background.
            flow = cv2.calcOpticalFlowFarneback(gray, ref_gray, None, 0.5, 3, 21, 3, 5, 1.2, 0)
            back = cv2.calcOpticalFlowFarneback(ref_gray, gray, None, 0.5, 3, 21, 3, 5, 1.2, 0)
            mx, my = self.gx + flow[..., 0], self.gy + flow[..., 1]
            reverse = cv2.remap(back, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            consistent = np.linalg.norm(flow + reverse, axis=2) < 1.5
            source_clear = cv2.remap(self.small_mask, mx, my, cv2.INTER_NEAREST, borderValue=255) == 0
            inside = (mx >= 1) & (mx < self.sw - 2) & (my >= 1) & (my < self.sh - 2)
            valid = consistent & source_clear & inside
            valid = cv2.erode(valid.astype(np.uint8), np.ones((3, 3), np.uint8))
            fx = cv2.resize(flow[..., 0], (self.w, self.h)) * self.w / self.sw
            fy = cv2.resize(flow[..., 1], (self.w, self.h)) * self.h / self.sh
            xx, yy = np.meshgrid(np.arange(self.w, dtype=np.float32), np.arange(self.h, dtype=np.float32))
            warped = cv2.remap(ref, xx + fx, yy + fy, cv2.INTER_LINEAR)
            weight = cv2.resize(valid.astype(np.float32), (self.w, self.h), interpolation=cv2.INTER_LINEAR)
            weight *= self.mask / 255.0
            accum += warped.astype(np.float32) * weight[..., None]
            weights += weight
        if weights.any():
            avg = accum / np.maximum(weights[..., None], 1e-6)
            alpha = np.clip(weights, 0, 1)[..., None]
            result = np.clip(result * (1 - alpha) + avg * alpha, 0, 255).astype(np.uint8)
        result[self.mask == 0] = img[self.mask == 0]
        return result
