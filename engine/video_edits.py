"""Fixed-position video events and local effects, without ripple editing."""
import math


def video_span(cfg, info, clip):
    return float(clip.get('time', 0)), float(clip.get('duration', 0))


def ramp_points(clip):
    """Speed keyframes [[fraction of clip, speed], ...] or None for a constant speed."""
    points = clip.get('speed_points')
    return points if points and len(points) >= 2 else None


def speed_at(points, u):
    for (a, sa), (b, sb) in zip(points, points[1:]):
        if u <= b:
            return sa + (sb - sa) * (u - a) / (b - a) if b > a else sb
    return points[-1][1]


def source_offset(clip, u):
    """Source seconds consumed from the clip start to output fraction u (area under the speed curve)."""
    duration = float(clip.get('duration', 0))
    points = ramp_points(clip)
    if not points:
        return u * duration * float(clip.get('speed', 1))
    total = 0.0
    for (a, sa), (b, _) in zip(points, points[1:]):
        if u <= a:
            break
        end = min(u, b)
        total += (end - a) * (sa + speed_at(points, end)) / 2
    return total * duration


def average_speed(points):
    return source_offset(dict(speed_points=points, duration=1), 1)


def cut_points(points, a, b):
    """Keyframes of the [a, b] part of a clip, rescaled to 0..1."""
    inner = [[(u - a) / (b - a), v] for u, v in points if a < u < b]
    return [[0.0, speed_at(points, a)]] + inner + [[1.0, speed_at(points, b)]]


def sub_clip(clip, a, b):
    """Fields for the part of a video clip between output fractions a and b."""
    data = dict(source_in=clip.get('source_in', 0) + source_offset(clip, a),
                duration=clip['duration'] * (b - a))
    points = ramp_points(clip)
    if points:
        data['speed_points'] = cut_points(points, a, b)
        data['speed'] = average_speed(data['speed_points'])
    return data


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
        points = ramp_points(clip)
        if points:
            if (any(len(p) != 2 or not all(math.isfinite(float(v)) for v in p) for p in points)
                    or points[0][0] != 0 or points[-1][0] != 1
                    or any(b[0] < a[0] for a, b in zip(points, points[1:]))
                    or not all(.1 <= p[1] <= 4 for p in points)):
                raise ValueError('Invalid speed ramp.')
        if time < 0 or not 0 <= source < info['duration'] or duration <= 0 or source + source_offset(clip, 1) > info['duration'] + .01:
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
        if ramp_points(clip) and not clip.get('muted', False):
            # ponytail: constant-speed steps of 2 frames approximate the curve; smaller steps if ramps look stepped.
            boundaries.update(range(start, end, 2))
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
        if ramp_points(vclip):
            offset = lambda frame: source_offset(vclip, (frame - vstart) / (vend - vstart)) * fps
            speed = (offset(stop) - offset(start)) / (stop - start)
            source += offset(start)
        else:
            offset = lambda frame, rate=speed: (frame - vstart) * rate
            source += (start - vstart) * speed
        kind, clip = 'hold' if speed == 0 else 'slowmo' if speed < 1 else 'video', None
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
                source = video[2] + offset(origin)
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
        if ramp_points(clip) and begin <= source < begin + source_offset(clip, 1):
            low, high = 0.0, 1.0
            for _ in range(40):
                middle = (low + high) / 2
                low, high = (middle, high) if begin + source_offset(clip, middle) < source else (low, middle)
            return clip.get('time', 0) + low * duration
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
