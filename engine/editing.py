"""Editing settings shared by playback, draft previews, and final exports."""
from dataclasses import dataclass, field, replace
import json
import math
import os
from fractions import Fraction
from .util import FFPROBE, run
from .video_edits import video_span, event_plan, effect_duration, validate_video_clips


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
    # None opens legacy projects; a list contains only the effects placed on the timeline.
    effect_clips: list | None = None
    video_clips: list | None = None
    timeline_duration: float | None = None
    lane_states: dict = field(default_factory=dict)
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
    if cfg.video_clips is not None:
        validate_video_clips(cfg, info)
    if cfg.video_clips is None and cfg.effect_clips is None and cfg.animation and not cfg.trim_start <= cfg.freeze_time < end:
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
        if not _finite(item.get('rate', 1)) or not .1 <= item.get('rate', 1) <= 4:
            raise ValueError('Audio clip playback rate must be between 10 and 400 percent.')
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
    if cfg.effect_clips is None and cfg.background_template == 'media' and (not cfg.background_media or not os.path.isfile(cfg.background_media)):
        raise ValueError('The selected background file is missing.')
    has_background = any([cfg.background_blur, cfg.background_dim, cfg.background_desaturate,
                          cfg.background_glow, cfg.background_particles, cfg.background_template != 'none'])
    if cfg.effect_clips is not None:
        _validate_effects(cfg, end, info)
        has_background = any(c.get('kind') == 'background' and not c.get('muted') for c in cfg.effect_clips)
    protection_start, protection_end = (0, info['duration']) if cfg.video_clips is not None else (cfg.trim_start, end)
    if has_background and cfg.protected_time is not None and not protection_start <= cfg.protected_time < protection_end:
        raise ValueError('The object selection frame must be inside the selected trim. Select it again at the freeze moment.')
    if cfg.effect_clips is None and has_background and cfg.background_scope == 'freeze' and not cfg.animation:
        raise ValueError('Enable animation or choose background effects for the whole video.')
    if cfg.out and os.path.normcase(os.path.abspath(cfg.out)) == os.path.normcase(os.path.abspath(cfg.video)):
        raise ValueError('Choose an output name different from the original video.')
    return end


def timeline(cfg, count):
    """A single frame-accurate edit plan for video, audio, and displayed timings."""
    if cfg.video_clips is not None:
        return event_plan(cfg, count)
    if cfg.effect_clips is not None:
        return _clip_timeline(cfg, count)
    fps = cfg.output_fps
    ff = min(max(round((cfg.freeze_time - cfg.trim_start) * fps), 0), count - 1)
    if not cfg.animation:
        return dict(segments=[dict(kind='video', source_start=0, source_end=count,
                                   output_start=0, output_end=count, speed=1, clip=None)],
                    ff=ff, before=count, ramp_src=0, ramp_out=0, motion=0,
                    after=0, total_frames=count, freeze=count / fps, resume=count / fps,
                    ramp=count / fps, duration=count / fps)
    rs = min(round(0.5 * fps), ff) if cfg.slowmo else 0
    ro = round(rs / fps * 2.25 * fps) if rs else 0
    before = ff - rs
    motion = round(cfg.orbit_seconds * fps)
    after = count - ff - 1
    total = before + ro + motion + after
    segments = []
    if before:
        segments.append(dict(kind='video', source_start=0, source_end=before,
                             output_start=0, output_end=before, speed=1, clip=None))
    if ro:
        segments.append(dict(kind='ramp', source_start=before, source_end=ff,
                             output_start=before, output_end=before + ro, speed=rs / ro, clip=None))
    if motion:
        segments.append(dict(kind='freeze', source_start=ff, source_end=ff + 1,
                             output_start=before + ro, output_end=before + ro + motion, speed=0, clip=None))
    if after:
        segments.append(dict(kind='video', source_start=ff + 1, source_end=count,
                             output_start=before + ro + motion, output_end=total, speed=1, clip=None))
    return dict(segments=segments, ff=ff, before=before, ramp_src=rs, ramp_out=ro, motion=motion,
                after=after, total_frames=total, freeze=(before + ro) / fps,
                resume=(before + ro + motion) / fps, ramp=before / fps, duration=total / fps)


EFFECT_KINDS = ('freeze', 'slowmo', 'motion','clarity', 'flash', 'shake', 'glow', 'bars', 'scan', 'background')
MOTION_STYLES = ('spin', 'hold', 'pulse', 'glitch', 'focus', 'tilt', 'scan')
BACKGROUND_STYLES = ('none', 'particles', 'snow', 'embers', 'bokeh', 'rain', 'stars',
                     'neon', 'grid', 'aurora', 'rays', 'vignette', 'media')


def _finite(value):
    return math.isfinite(float(value))


def _validate_effects(cfg, end, info=None):
    if not isinstance(cfg.effect_clips, list):
        raise ValueError('Invalid timeline effects.')
    identifiers, slow_intervals = set(), []
    for clip in cfg.effect_clips:
        if not isinstance(clip, dict) or clip.get('kind') not in EFFECT_KINDS:
            raise ValueError('Invalid timeline effect.')
        ident = str(clip.get('id', ''))
        if not ident or ident in identifiers:
            raise ValueError('Timeline effects need unique identifiers.')
        identifiers.add(ident)
        kind, params = clip['kind'], clip.get('params', {})
        if not isinstance(params, dict):
            raise ValueError('Invalid effect settings.')
        if not _finite(clip.get('animation_time', 0)) or not _finite(clip.get('animation_duration', 1)) or clip.get('animation_duration', 1) <= 0:
            raise ValueError('Invalid effect animation timing.')
        if kind in ('freeze', 'slowmo'):
            source = clip.get('source_time', cfg.trim_start)
            low, high = (0, info['duration']) if cfg.video_clips is not None else (cfg.trim_start, end)
            if not _finite(source) or not low <= source < high:
                raise ValueError('The effect source moment must be inside the selected video.')
            if cfg.video_clips is not None and (not _finite(clip.get('time', 0)) or clip.get('time', 0) < 0):
                raise ValueError('Effect times cannot be negative.')
            if kind == 'slowmo':
                duration = clip.get('source_duration', 1)
                speed = clip.get('speed', params.get('speed', .5))
                if not _finite(duration) or duration <= 0 or (cfg.video_clips is None and source + duration > end + .01):
                    raise ValueError('The slow-motion source interval must be inside the selected video.')
                if not _finite(speed) or not .1 <= speed <= 1:
                    raise ValueError('Slow-motion speed must be between 10 and 100 percent.')
                if cfg.video_clips is not None and (not _finite(effect_duration(clip)) or effect_duration(clip) <= 0):
                    raise ValueError('Slow-motion output duration must be positive.')
                if cfg.video_clips is None and not clip.get('muted', False):
                    slow_intervals.append((source, source + duration))
            else:
                duration = clip.get('duration', 1)
                if not _finite(duration) or not .05 <= duration <= 60:
                    raise ValueError('Freeze duration must be between 0.05 and 60 seconds.')
                if params.get('motion_style', cfg.motion_style) not in MOTION_STYLES:
                    raise ValueError('Invalid freeze motion template.')
                if not _finite(params.get('orbit_angle', cfg.orbit_angle)) or not 0 <= params.get('orbit_angle', cfg.orbit_angle) <= 1080:
                    raise ValueError('Rotation must be between 0 and 1080 degrees.')
                if params.get('rotation_direction', cfg.rotation_direction) not in (-1, 1):
                    raise ValueError('Invalid rotation direction.')
        else:
            if kind == 'motion' and params.get('motion_style', cfg.motion_style) not in MOTION_STYLES:
                raise ValueError('Invalid motion template.')
            if not _finite(clip.get('time', 0)) or clip.get('time', 0) < 0:
                raise ValueError('Effect times cannot be negative.')
            if not _finite(clip.get('duration', 1)) or clip.get('duration', 1) <= 0:
                raise ValueError('Effect duration must be positive.')
        for key in ('strength', 'motion_strength', 'background_strength'):
            if key in params and (not _finite(params[key]) or not 0 <= params[key] <= 1):
                raise ValueError('Effect strength must be between 0 and 100 percent.')
        if kind == 'background':
            if params.get('background_template', 'none') not in BACKGROUND_STYLES:
                raise ValueError('Invalid background template.')
            for key, low, high in (('background_blur', 0, 40), ('background_dim', 0, .85),
                                   ('background_speed', .1, 4), ('mask_feather', 0, 20)):
                if key in params and (not _finite(params[key]) or not low <= params[key] <= high):
                    raise ValueError('Invalid background settings.')
            if params.get('background_template') == 'media' and not os.path.isfile(params.get('background_media') or ''):
                raise ValueError('The selected background file is missing.')
    slow_intervals.sort()
    for previous, current in zip(slow_intervals, slow_intervals[1:]):
        if current[0] < previous[1] - 1e-7:
            raise ValueError('Slow-motion source intervals cannot overlap. Move or trim one of them.')


def _clip_timeline(cfg, count):
    fps, count = cfg.output_fps, max(1, count)
    freezes, slows = {}, []
    boundaries = {0, count}
    for clip in cfg.effect_clips:
        if clip.get('muted', False):
            continue
        kind = clip.get('kind')
        if kind not in ('freeze', 'slowmo'):
            continue
        start = min(count - 1, max(0, round((clip['source_time'] - cfg.trim_start) * fps)))
        if kind == 'freeze':
            freezes.setdefault(start, []).append(clip)
            boundaries.update((start, start + 1))
        else:
            stop = min(count, max(start + 1, round((clip['source_time'] + clip['source_duration'] - cfg.trim_start) * fps)))
            speed = float(clip.get('speed', clip.get('params', {}).get('speed', .5)))
            slows.append((start, stop, speed, clip))
            boundaries.update((start, stop))
    segments, cursor = [], 0

    def add(kind, start, stop, length, clip=None):
        nonlocal cursor
        length = max(1, length)
        segments.append(dict(kind=kind, source_start=start, source_end=stop,
                             output_start=cursor, output_end=cursor + length,
                             speed=0 if kind == 'freeze' else (stop - start) / length,
                             clip=clip))
        cursor += length

    points = sorted(boundaries)
    for start, stop in zip(points, points[1:]):
        if start in freezes:
            for clip in freezes[start]:
                add('freeze', start, start + 1, round(clip['duration'] * fps), clip)
            # The held source frame replaces this single original frame, once.
            continue
        if stop <= start:
            continue
        slow = next((s for s in slows if s[0] <= start < s[1]), None)
        add('slowmo' if slow else 'video', start, stop,
            round((stop - start) / slow[2]) if slow else stop - start,
            slow[3] if slow else None)
    first = next((s for s in segments if s['kind'] == 'freeze'), None)
    ff = first['source_start'] if first else 0
    freeze = first['output_start'] / fps if first else cursor / fps
    resume = first['output_end'] / fps if first else cursor / fps
    motion = first['output_end'] - first['output_start'] if first else 0
    return dict(segments=segments, total_frames=cursor, duration=cursor / fps,
                ff=ff, before=first['output_start'] if first else cursor,
                ramp_src=0, ramp_out=0, motion=motion,
                after=max(0, cursor - (first['output_end'] if first else cursor)),
                freeze=freeze, resume=resume, ramp=freeze)


def source_frame_to_output(plan, frame, fps):
    """Map an original source frame into the shared output plan."""
    frame = max(0, frame)
    for segment in plan['segments']:
        if segment['source_start'] <= frame < segment['source_end']:
            if segment['kind'] == 'freeze':
                return segment['output_start'] / fps
            return (segment['output_start'] + (frame - segment['source_start']) / segment['speed']) / fps
    return plan['duration']


def effect_span(cfg, info, clip):
    """Return output seconds for a timeline effect, including source-mapped holds."""
    if cfg.video_clips is not None:
        return float(clip.get('time', 0)), effect_duration(clip)
    if clip.get('kind') not in ('freeze', 'slowmo'):
        return float(clip.get('time', 0)), float(clip.get('duration', 1))
    end = cfg.trim_end if cfg.trim_end is not None else info['duration']
    count = max(1, round((end - cfg.trim_start) * cfg.output_fps))
    plan = timeline(cfg, count)
    spans = [s for s in plan.get('segments', []) if s.get('clip') and s['clip'].get('id') == clip.get('id')]
    if spans:
        return spans[0]['output_start'] / cfg.output_fps, (spans[-1]['output_end'] - spans[0]['output_start']) / cfg.output_fps
    source_frame = (clip.get('source_time', cfg.trim_start) - cfg.trim_start) * cfg.output_fps
    start = source_frame_to_output(plan, source_frame, cfg.output_fps) if 'segments' in plan else max(0, source_frame / cfg.output_fps)
    duration = clip.get('duration', 1) if clip['kind'] == 'freeze' else clip.get('source_duration', 1) / clip.get('speed', clip.get('params', {}).get('speed', .5))
    return start, duration
