"""Editing settings shared by playback, draft previews, and final exports."""
from dataclasses import dataclass, field, replace
import json
import math
import os
from fractions import Fraction
from .util import FFPROBE, run


@dataclass
class Config:
    video: str = ''
    out: str | None = None
    trim_start: float = 0.0
    trim_end: float | None = None
    freeze_time: float = 3.0  # Absolute position in the original clip.
    orbit_seconds: float = 3.0
    orbit_angle: float = 360.0  # 2D image rotation, never a reconstructed 3D view.
    rotation_direction: int = 1
    animation: bool = True
    slowmo: bool = True
    remove_hud: bool = False
    hud_regions: list = field(default_factory=list)  # Normalized rectangles.
    hud_method: str = 'temporal'
    clarity: bool = True
    bars: bool = False
    flash: bool = True
    glow: bool = False
    scan: bool = False
    shake: bool = True
    motion_style: str = 'spin'
    motion_strength: float = 0.65
    protected_polygons: list = field(default_factory=list)
    protected_time: float | None = None
    background_blur: float = 0.0
    background_dim: float = 0.0
    background_desaturate: bool = False
    background_glow: bool = False
    background_particles: bool = False
    background_scope: str = 'freeze'
    background_template: str = 'none'
    background_strength: float = 0.65
    background_speed: float = 1.0
    background_color: str = '#73e0bd'
    background_media: str | None = None
    mask_feather: float = 2.0
    music: str | None = None
    music_start: float = 0.0
    drop_in_song: float | None = None
    music_volume: float = 0.55
    music_muted: bool = False
    game_audio: bool = True
    game_volume: float = 0.6
    builtin_fx: bool = True
    fx_volume: float = 0.6
    sound_effects: list = field(default_factory=list)
    audio_tracks: list = field(default_factory=list)
    output_height: int = 1080
    output_fps: int = 60
    quality: str = 'high'
    workers: int = 1  # Retained for old CLI compatibility.
    click: tuple | None = None
    preview: bool = False

    def draft(self, out):
        return replace(self, out=out, preview=True, output_height=360,
                       output_fps=15, quality='draft')


def media_info(path):
    data = json.loads(run([FFPROBE, '-v', 'error', '-show_streams',
                           '-show_format', '-of', 'json', path]))
    stream = next((s for s in data.get('streams', []) if s['codec_type'] == 'video'), None)
    if stream is None:
        raise ValueError('The selected file does not contain video.')
    rate = stream.get('avg_frame_rate', '0/1')
    try:
        fps = float(Fraction(rate)) or 30.0
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    rotation = next((s.get('rotation', 0) for s in stream.get('side_data_list', [])
                     if 'rotation' in s), int(stream.get('tags', {}).get('rotate', 0)))
    w, h = int(stream['width']), int(stream['height'])
    if abs(int(rotation)) % 180 == 90:
        w, h = h, w
    return dict(width=w, height=h, fps=fps,
                duration=float(data.get('format', {}).get('duration') or stream.get('duration', 0)),
                audio=any(s['codec_type'] == 'audio' for s in data.get('streams', [])))


def dimensions(info, height):
    h = int(height) // 2 * 2
    w = max(2, round(h * info['width'] / info['height'] / 2) * 2)
    return w, h


def validate(cfg, info):
    end = info['duration'] if cfg.trim_end is None else cfg.trim_end
    numeric = [cfg.trim_start, end, cfg.freeze_time, cfg.orbit_seconds, cfg.orbit_angle,
               cfg.music_start, cfg.music_volume, cfg.game_volume, cfg.fx_volume]
    if cfg.drop_in_song is not None:
        numeric.append(cfg.drop_in_song)
    numeric += [cfg.background_blur, cfg.background_dim, cfg.motion_strength,
                cfg.background_strength, cfg.background_speed, cfg.mask_feather]
    if cfg.protected_time is not None:
        numeric.append(cfg.protected_time)
    if not all(math.isfinite(float(v)) for v in numeric):
        raise ValueError('All times and settings must be finite numbers.')
    if not 0 <= cfg.trim_start < end <= info['duration'] + 0.01:
        raise ValueError('The trim must be inside the original video and the end must follow the start.')
    if end - cfg.trim_start < 0.25:
        raise ValueError('Select at least 0.25 seconds of video.')
    if cfg.animation and not cfg.trim_start <= cfg.freeze_time < end:
        raise ValueError('The freeze moment must be inside the selected trim.')
    if not 0.8 <= cfg.orbit_seconds <= 15:
        raise ValueError('Animation duration must be between 0.8 and 15 seconds.')
    if not 0 <= cfg.orbit_angle <= 1080:
        raise ValueError('Rotation must be between 0 and 1080 degrees.')
    if cfg.motion_style not in ('spin', 'hold', 'pulse', 'glitch', 'focus', 'tilt', 'scan'):
        raise ValueError('Invalid motion template.')
    if not 0 <= cfg.motion_strength <= 1 or not 0 <= cfg.background_strength <= 1 or not .1 <= cfg.background_speed <= 4:
        raise ValueError('Invalid template controls.')
    if not 0 <= cfg.mask_feather <= 20:
        raise ValueError('Invalid selection feathering.')
    if cfg.output_fps not in (15, 24, 30, 60) or cfg.output_height not in (360, 480, 720, 1080, 1440, 2160):
        raise ValueError('Unsupported output size or frame rate.')
    if cfg.hud_method not in ('temporal', 'fast') or cfg.quality not in ('draft', 'small', 'high', 'best'):
        raise ValueError('Unsupported quality or HUD restoration method.')
    if cfg.music_start < 0 or (cfg.drop_in_song is not None and cfg.drop_in_song < 0):
        raise ValueError('Music times cannot be negative.')
    if any(not 0 <= v <= 2 for v in (cfg.music_volume, cfg.game_volume, cfg.fx_volume)):
        raise ValueError('Audio levels must be between 0 and 200 percent.')
    if cfg.music and not os.path.isfile(cfg.music):
        raise ValueError('The selected music file is missing.')
    for rect in cfg.hud_regions:
        if len(rect) != 4 or not all(math.isfinite(float(v)) and 0 <= v <= 1 for v in rect):
            raise ValueError('Invalid HUD selection.')
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            raise ValueError('HUD selections must have a positive area.')
    track_ids=set()
    for track in cfg.audio_tracks:
        ident=str(track['id'])
        if ident in track_ids or not math.isfinite(float(track.get('volume',1))) or not 0<=track.get('volume',1)<=2:
            raise ValueError('Invalid audio track settings.')
        track_ids.add(ident)
    for item in cfg.sound_effects:
        if not os.path.isfile(item['path']):
            raise ValueError('A selected sound effect is missing.')
        if not math.isfinite(float(item['time'])) or item['time'] < 0:
            raise ValueError('Sound effect times cannot be negative.')
        if not math.isfinite(float(item['volume'])) or not 0 <= item['volume'] <= 2:
            raise ValueError('Invalid sound effect volume.')
        for key in ('source_in', 'fade_in', 'fade_out'):
            if not math.isfinite(float(item.get(key, 0))) or item.get(key, 0) < 0:
                raise ValueError('Invalid audio trim or fade.')
        if item.get('duration') is not None and (not math.isfinite(float(item['duration'])) or item['duration'] <= 0):
            raise ValueError('Invalid audio clip duration.')
    if not 0 <= cfg.background_blur <= 40 or not 0 <= cfg.background_dim <= .85:
        raise ValueError('Invalid background effect strength.')
    if cfg.background_scope not in ('freeze', 'all'):
        raise ValueError('Invalid background scope.')
    for polygon in cfg.protected_polygons:
        if len(polygon) < 3 or any(len(point) != 2 or not all(math.isfinite(float(v)) and 0 <= v <= 1 for v in point) for point in polygon):
            raise ValueError('Invalid protected object selection.')
    if cfg.background_template not in ('none', 'particles', 'snow', 'embers', 'bokeh', 'rain',
                                       'stars', 'neon', 'grid', 'aurora', 'rays', 'vignette', 'media'):
        raise ValueError('Invalid background template.')
    if cfg.background_template == 'media' and (not cfg.background_media or not os.path.isfile(cfg.background_media)):
        raise ValueError('The selected background file is missing.')
    has_background = any([cfg.background_blur, cfg.background_dim, cfg.background_desaturate,
                          cfg.background_glow, cfg.background_particles, cfg.background_template != 'none'])
    if has_background and cfg.protected_time is not None and not cfg.trim_start <= cfg.protected_time < end:
        raise ValueError('The object selection frame must be inside the selected trim. Select it again at the freeze moment.')
    if has_background and cfg.background_scope == 'freeze' and not cfg.animation:
        raise ValueError('Enable animation or choose background effects for the whole video.')
    if cfg.out and os.path.normcase(os.path.abspath(cfg.out)) == os.path.normcase(os.path.abspath(cfg.video)):
        raise ValueError('Choose an output name different from the original video.')
    return end


def timeline(cfg, count):
    """A single frame-accurate edit plan for video, audio, and displayed timings."""
    fps = cfg.output_fps
    ff = min(max(round((cfg.freeze_time - cfg.trim_start) * fps), 0), count - 1)
    if not cfg.animation:
        return dict(ff=ff, before=count, ramp_src=0, ramp_out=0, motion=0,
                    after=0, total_frames=count, freeze=count / fps, resume=count / fps,
                    ramp=count / fps, duration=count / fps)
    rs = min(round(0.5 * fps), ff) if cfg.slowmo else 0
    ro = round(rs / fps * 2.25 * fps) if rs else 0
    before = ff - rs
    motion = round(cfg.orbit_seconds * fps)
    after = count - ff - 1
    total = before + ro + motion + after
    return dict(ff=ff, before=before, ramp_src=rs, ramp_out=ro, motion=motion,
                after=after, total_frames=total, freeze=(before + ro) / fps,
                resume=(before + ro + motion) / fps, ramp=before / fps, duration=total / fps)
