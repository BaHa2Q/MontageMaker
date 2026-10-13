"""Fixed-position video events and local effects, without ripple editing."""
import math


def video_span(cfg, info, clip):
    return float(clip.get('time', 0)), float(clip.get('duration', 0))


def effect_duration(clip):
    if clip.get('duration') is not None:
        return float(clip['duration'])
    if clip.get('kind') == 'slowmo':
        return float(clip.get('source_duration', .5)) / float(clip.get('speed', clip.get('params', {}).get('speed', .5)))
    return 1.0


def validate_video_clips(cfg, info):
    if not isinstance(cfg.video_clips, list):
        raise ValueError('Invalid video timeline.')
    if cfg.timeline_duration is not None and (not math.isfinite(float(cfg.timeline_duration)) or cfg.timeline_duration < 0):
        raise ValueError('Invalid timeline duration.')
    identifiers = set()
    for clip in cfg.video_clips:
        if not isinstance(clip, dict) or not clip.get('id') or str(clip['id']) in identifiers:
            raise ValueError('Video events need unique identifiers.')
        identifiers.add(str(clip['id']))
        values = [clip.get('time', 0), clip.get('source_in', 0), clip.get('duration', 0), clip.get('speed', 1)]
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError('Video event positions must be finite numbers.')
        time, source, duration, speed = values
        if speed != 0 and not .1 <= speed <= 4:
            raise ValueError('Invalid video event playback speed.')
        if time < 0 or not 0 <= source < info['duration'] or duration <= 0 or source + duration * speed > info['duration'] + .01:
            raise ValueError('A video event must use a positive interval inside its source.')


def event_plan(cfg, count):
    fps = cfg.output_fps
    videos, effects, boundaries = [], [], {0}
    extent = max(0, float(cfg.timeline_duration or 0))
    for clip in cfg.video_clips:
        start = max(0, round(clip.get('time', 0) * fps))
        length = max(1, round(clip.get('duration', 0) * fps))
        end = start + length
        videos.append((start, end, (clip.get('source_in', 0) - cfg.trim_start) * fps, clip, float(clip.get('speed', 1))))
        boundaries.update((start, end))
        extent = max(extent, end / fps)
    for clip in cfg.effect_clips or []:
        start = max(0, round(clip.get('time', 0) * fps))
        length = max(1, round(effect_duration(clip) * fps))
        end = start + length
        extent = max(extent, end / fps)
        if clip.get('kind') in ('freeze', 'slowmo') and not clip.get('muted', False):
            effects.append((start, end, clip))
            boundaries.update((start, end))
    for clip in cfg.sound_effects:
        extent = max(extent, clip.get('time', 0) + float(clip.get('duration') or 0))
    total = max(1, round(extent * fps))
    boundaries.add(total)
    segments = []
    points = sorted(value for value in boundaries if 0 <= value <= total)
    for start, stop in zip(points, points[1:]):
        if stop <= start:
            continue
        # Saved order determines overlaps, including muted video events.
        video = next((v for v in reversed(videos) if v[0] <= start < v[1]), None)
        active = next((effect for effect in reversed(effects) if effect[0] <= start < effect[1]), None)
        if video is None or video[3].get('muted', False):
            segments.append(dict(kind='gap', source_start=0, source_end=0, output_start=start,
                                 output_end=stop, speed=0, clip=None, video_clip=None))
            continue
        vstart, vend, source, vclip, speed = video
        kind, clip = 'hold' if speed == 0 else 'slowmo' if speed < 1 else 'video', None
        source += (start - vstart) * speed
        extra = {}
        if active:
            estart, eend, clip = active
            if clip['kind'] == 'freeze':
                kind, speed = 'freeze', 0
                source = (clip.get('source_time', cfg.trim_start + source / fps) - cfg.trim_start) * fps
                extra = dict(motion_start=round(clip.get('animation_time', clip.get('time', 0)) * fps),
                             motion_count=max(1, round(clip.get('animation_duration', effect_duration(clip)) * fps)))
            else:
                kind = 'slowmo'
                speed = float(clip.get('speed', clip.get('params', {}).get('speed', .5)))
                origin = max(estart, vstart)
                source = video[2] + (origin - vstart) * video[4]
                if estart >= vstart and clip.get('source_time') is not None:
                    source = (clip['source_time'] - cfg.trim_start) * fps
                source += (start - origin) * speed
        segments.append(dict(kind=kind, source_start=source,
                             source_end=source + (stop - start) * speed,
                             output_start=start, output_end=stop, speed=speed,
                             clip=clip, video_clip=vclip, **extra))
    first = next((s for s in segments if s['kind'] == 'freeze'), None)
    freeze = first['output_start'] / fps if first else total / fps
    resume = (first['motion_start'] + first['motion_count']) / fps if first else total / fps
    return dict(segments=segments, total_frames=total, duration=total / fps,
                ff=round(first['source_start']) if first else 0,
                before=first['output_start'] if first else total, ramp_src=0, ramp_out=0,
                motion=first['motion_count'] if first else 0,
                after=max(0, total - round(resume * fps)), freeze=freeze, resume=resume, ramp=freeze)


def source_output(cfg, source):
    """Return the first visible event containing a source moment, or its nearest edge."""
    videos = sorted((clip for clip in cfg.video_clips if not clip.get('muted', False)),
                    key=lambda clip: clip.get('time', 0))
    for clip in videos:
        begin, duration, speed = clip.get('source_in', 0), clip.get('duration', 0), clip.get('speed', 1)
        if speed == 0 and abs(source - begin) < 1 / cfg.output_fps:
            return float(clip.get('time', 0))
        if begin <= source < begin + duration * speed:
            return clip.get('time', 0) + (source - begin) / speed
    if not videos:
        return 0.0
    closest = min(videos, key=lambda clip: min(abs(source - clip.get('source_in', 0)),
                  abs(source - clip.get('source_in', 0) - clip.get('duration', 0) * clip.get('speed', 1))))
    offset = (source - closest.get('source_in', 0)) / closest.get('speed', 1) if closest.get('speed', 1) else 0
    return closest.get('time', 0) + max(0, min(closest.get('duration', 0), offset))
