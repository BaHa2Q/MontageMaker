"""Crisp editor icons rendered from SVG paths; no symbol fonts are required."""
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_DRAWINGS = {
    'undo': '<path d="M8 5 3 10l5 5M3.5 10H14a6 6 0 0 1 0 12" transform="translate(0 -2)"/>',
    'redo': '<path d="m16 5 5 5-5 5M20.5 10H10a6 6 0 0 0 0 12" transform="translate(0 -2)"/>',
    'play': '<path d="M8 4.8 20 12 8 19.2Z" fill="{color}" stroke-linejoin="round"/>',
    'pause': '<rect x="6" y="4.5" width="4" height="15" rx=".8" fill="{color}" stroke="none"/><rect x="14" y="4.5" width="4" height="15" rx=".8" fill="{color}" stroke="none"/>',
    'prev': '<path d="M5 5v14M19 5 8 12l11 7Z"/><path d="M19 5 8 12l11 7Z" fill="{color}"/>',
    'next': '<path d="M19 5v14M5 5l11 7-11 7Z"/><path d="M5 5l11 7-11 7Z" fill="{color}"/>',
    'eye': '<path d="M2 12c2.8-4.7 6-7 10-7s7.2 2.3 10 7c-2.8 4.7-6 7-10 7S4.8 16.7 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    'eye-off': '<path d="m3 3 18 18M9.8 5.2A12.6 12.6 0 0 1 12 5c4 0 7.2 2.3 10 7a17.5 17.5 0 0 1-3.5 4.3M6.3 6.3A18.5 18.5 0 0 0 2 12c2.8 4.7 6 7 10 7 1.9 0 3.6-.5 5.2-1.5M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    'lock': '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
    'unlock': '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 7.8-1.2M12 14v3"/>',
    'cut': '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="m8.2 8.2 12.3 12.3M8.2 15.8 20.5 3.5"/>',
    'delete': '<path d="M3.5 6h17M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7"/>',
    'duplicate': '<rect x="3" y="3" width="12" height="12" rx="2"/><rect x="9" y="9" width="12" height="12" rx="2"/>',
    'video': '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 9 5-3v12l-5-3Z"/>',
    'music': '<path d="M9 17V5l11-2v12M9 8l11-2"/><ellipse cx="6" cy="17.5" rx="3" ry="2.5"/><ellipse cx="17" cy="15.5" rx="3" ry="2.5"/>',
}

_ALIASES = {'previous': 'prev', 'eye_off': 'eye-off', 'eyeoff': 'eye-off',
            'trash': 'delete', 'remove': 'delete', 'split': 'cut', 'audio': 'music', 'copy': 'duplicate'}


def icon(key, size=20, color='#b9cbe0') -> QIcon:
    """Return a vector-path icon. A QApplication must already exist."""
    key=_ALIASES.get(str(key),str(key))
    if key not in _DRAWINGS:
        raise ValueError(f'Unknown editor icon: {key}')
    size=max(1,int(size)); ink=QColor(color)
    if not ink.isValid():
        raise ValueError(f'Invalid icon color: {color}')
    # Copy the cached QIcon, so callers can add states without mutating the cache.
    return QIcon(_render(key,size,ink.name(QColor.HexArgb)))


@lru_cache(maxsize=256)
def _render(key,size,argb):
    ink=QColor(argb); rgb=ink.name(QColor.HexRgb)
    drawing=_DRAWINGS[key].replace('{color}',rgb)
    svg=(f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
         f'<g fill="none" stroke="{rgb}" stroke-width="1.8" stroke-linecap="round" '
         f'stroke-linejoin="round" opacity="{ink.alphaF():.4f}">{drawing}</g></svg>')
    renderer=QSvgRenderer(QByteArray(svg.encode('utf-8')))
    pixmap=QPixmap(size*2,size*2); pixmap.setDevicePixelRatio(2); pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter,QRectF(0,0,size,size))
    finally:
        painter.end()
    return QIcon(pixmap)
