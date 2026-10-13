"""One edit pipeline for low-resolution previews and final montage exports."""
import math
import os
import shutil
import subprocess
import tempfile
import time
import cv2
import numpy as np
from .editing import Config, dimensions, media_info, timeline, validate
from .hud import HUDCleaner, region_mask
from .motion import Motion, finish
from .background import ProtectionTracker, apply_background, enabled as background_enabled
from .sound import make_soundtrack
from .templates import close_media
from .effects import motion_config, backgrounds_at, timed_background, timed_visuals
from .util import APP_DIR, CACHE_DIR, Cancelled, FFMPEG, popen, probe_frames, run


def extract_frame(video, t, width=960, height=None):
    if height is None:
        info = media_info(video)
        height = max(2, round(width * info['height'] / info['width']))
    proc = popen([FFMPEG, '-v', 'error', '-ss', f'{max(t, 0):.6f}', '-i', video,
                  '-frames:v', '1', '-vf', f'scale={width}:{height}',
                  '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw, err = proc.communicate()
    if len(raw) != width * height * 3:
        raise RuntimeError('Could not read this frame. ' + err.decode(errors='replace')[-300:])
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3).copy()


def run_montage(cfg, log=print, progress=lambda f: None, cancel=None):
    started = time.monotonic()
    info = media_info(cfg.video)
    end = validate(cfg, info)
    w, h = dimensions(info, cfg.output_height)
    fps = cfg.output_fps
    timeline_editor = cfg.effect_clips is not None or cfg.video_clips is not None
    source_start = 0.0 if cfg.video_clips is not None else cfg.trim_start
    source_end = info['duration'] if cfg.video_clips is not None else end
    out = os.path.abspath(cfg.out or os.path.splitext(cfg.video)[0] + '_montage.mp4')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # Unique work folders isolate previews/exports and never overwrite the source.
    cache = CACHE_DIR
    os.makedirs(cache, exist_ok=True)
    work = tempfile.mkdtemp(prefix='preview_' if cfg.preview else 'export_', dir=cache)
    cap = enc = cleaner = tracker = None

    def check():
        if cancel is not None and cancel.is_set():
            raise Cancelled('Cancelled by user.')

    def command(args):
        check()
        return run(args, cancel=cancel)

    def audio(path, dest, start=0, duration=None):
        args = [FFMPEG, '-v', 'error', '-y', '-ss', str(start), '-i', path]
        if duration is not None:
            args += ['-t', str(duration)]
        command(args + ['-vn', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', dest])
        return dest

    try:
        log(f'Preparing selected clip · {w} × {h} · {fps} fps')
        src = os.path.join(work, 'source.mp4')
        command([FFMPEG, '-v', 'error', '-y', '-ss', f'{source_start:.6f}',
                 '-i', cfg.video, '-t', f'{source_end - source_start:.6f}',
                 '-vf', f'scale={w}:{h}:flags=lanczos,setsar=1,fps={fps}',
                 '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '12',
                 '-pix_fmt', 'yuv420p', src])
        count = probe_frames(src)
        if count < 1:
            raise ValueError('The selected clip contains no decodable frames.')
        plan = timeline(cfg, count)
        progress(0.12)
        mask = region_mask((h, w), cfg.hud_regions if cfg.remove_hud else [])
        cleaner = HUDCleaner(mask, src, cfg.hud_method, check)
        has_background = background_enabled(cfg) if not timeline_editor else any(
            c.get('kind') == 'background' and not c.get('muted', False) for c in cfg.effect_clips or [])
        if has_background and cfg.protected_polygons:
            anchor = (cfg.protected_time if cfg.protected_time is not None else cfg.freeze_time) - source_start
            anchor = min(max(anchor, 0), (count - 1) / fps)
            tracker = ProtectionTracker(src, cfg.protected_polygons, anchor, (h, w), check)
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise RuntimeError('Could not open the prepared video.')
        freeze = None
        legacy_animation = not timeline_editor and cfg.animation
        if legacy_animation:
            cap.set(cv2.CAP_PROP_POS_FRAMES, plan['ff'])
            ok, freeze = cap.read()
            if not ok:
                raise RuntimeError('Could not decode the selected freeze frame.')
            freeze = cleaner.clean(freeze, plan['ff'] / fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        protected = tracker.at(plan['ff'] / fps, freeze) if tracker is not None and legacy_animation else None
        motion = Motion(freeze, cfg, plan['motion'], protected) if legacy_animation else None
        log('Creating audio mix')
        total = plan['duration']
        game = audio(cfg.video, os.path.join(work, 'game.wav'), source_start,
                     source_end - source_start) if cfg.game_audio and info['audio'] else None
        music = None
        if cfg.music and not cfg.music_muted:
            offset = cfg.drop_in_song - plan['resume'] if cfg.drop_in_song is not None else cfg.music_start
            music = os.path.join(work, 'music.wav')
            if offset >= 0:
                audio(cfg.music, music, offset, total + 1)
            else:
                delay = round(-offset * 1000)
                command([FFMPEG, '-v', 'error', '-y', '-i', cfg.music, '-t', str(total + 1),
                         '-af', f'adelay={delay}|{delay}', '-ac', '2', '-ar', '48000',
                         '-c:a', 'pcm_s16le', music])
        effects = []
        audio_tracks={str(track['id']):track for track in cfg.audio_tracks}
        for i, item in enumerate(cfg.sound_effects):
            check()
            if item['time'] >= total:
                log(f'Sound outside montage duration skipped: {os.path.basename(item["path"])}')
                continue
            track=audio_tracks.get(str(item.get('track')), {})
            if item.get('muted', False) or track.get('muted',False):
                continue
            length = min(total - item['time'], item.get('duration') or total)
            rate = item.get('rate', 1)
            dest = audio(item['path'], os.path.join(work, f'fx_{i}.wav'),
                         start=item.get('source_in', 0), duration=length * rate)
            effects.append((dest, item['time'], item['volume']*track.get('volume',1), item.get('fade_in', 0), item.get('fade_out', 0), rate, bool(track.get('background'))))
        if timeline_editor:
            segments = [(s['output_start'] / fps, s['source_start'] / fps + cfg.trim_start - source_start,
                         (s['output_end'] - s['output_start']) / fps, 0, 0, s['speed'])
                        for s in plan['segments'] if s['kind'] not in ('freeze', 'gap', 'hold')]
        elif cfg.animation:
            segments = [(0, 0, plan['ramp'], 0.01, 0.08),
                        (plan['resume'], (plan['ff'] + 1) / fps, plan['after'] / fps, 0.04, 0.1)]
        else:
            segments = [(0, 0, total, 0.01, 0.1)]
        sound = os.path.join(work, 'sound.wav')
        check()
        make_soundtrack(total, plan['ramp'], plan['freeze'], plan['resume'],
                        max(0.3, cfg.orbit_seconds - 0.3), music, game, segments, sound,
                        music_gain=cfg.music_volume, game_gain=cfg.game_volume,
                        fx_gain=cfg.fx_volume if cfg.builtin_fx and legacy_animation else 0,
                        custom_effects=effects, legacy_treatment=not timeline_editor)
        check()
        progress(0.2)
        log('Restoring selected HUD regions and rendering animation' if mask.any() else 'Rendering montage')
        video = os.path.join(work, 'video.mp4')
        preset, crf = {'draft': ('ultrafast', 26), 'small': ('fast', 24),
                       'high': ('medium', 18), 'best': ('slow', 14)}[cfg.quality]
        err_path = os.path.join(work, 'encoder.log')
        err_file = open(err_path, 'wb')
        enc = popen([FFMPEG, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                     '-s', f'{w}x{h}', '-r', str(fps), '-i', '-', '-an', '-c:v', 'libx264',
                     '-preset', preset, '-crf', str(crf), '-pix_fmt', 'yuv420p', video],
                    stdin=subprocess.PIPE, stderr=err_file)
        done = 0

        def send(frame, finish_frame=True):
            nonlocal done
            check()
            enc.stdin.write((finish(frame, cfg) if finish_frame else frame).tobytes())
            done += 1
            if done % max(1, fps // 2) == 0:
                progress(0.2 + 0.72 * done / plan['total_frames'])

        def source_frame(frame, index):
            cleaned = cleaner.clean(frame, index / fps)
            if background_enabled(cfg) and cfg.background_scope == 'all':
                protected = tracker.at(index / fps, frame) if tracker is not None else None
                cleaned = apply_background(cleaned, protected, cfg, index / fps)
            return cleaned

        if timeline_editor:
            cached_index, cached_frame = -1, None
            for segment in plan['segments']:
                check()
                motion = None
                for output_index in range(segment['output_start'], segment['output_end']):
                    check()
                    if segment['kind'] == 'gap':
                        send(np.zeros((h, w, 3), np.uint8), finish_frame=False)
                        continue
                    local_index = output_index - segment['output_start']
                    source_index = segment['source_start'] + local_index * segment['speed'] + (cfg.trim_start - source_start) * fps
                    index = max(0, min(count - 1, round(source_index)))
                    if index != cached_index:
                        if round(cap.get(cv2.CAP_PROP_POS_FRAMES)) != index:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                        ok, frame = cap.read()
                        if not ok:
                            raise RuntimeError(f'Could not decode source frame {index}/{count}.')
                        cached_frame = cleaner.clean(frame, index / fps)
                        cached_index = index
                    frame = cached_frame.copy()
                    output_time = output_index / fps
                    mask = tracker.at(index / fps, frame) if tracker is not None and backgrounds_at(cfg, output_time) else None
                    prepared = timed_background(frame, mask, cfg, output_time)
                    if segment['kind'] == 'freeze':
                        if motion is None:
                            motion = Motion(frame, motion_config(cfg, segment['clip']),
                                            segment.get('motion_count', segment['output_end'] - segment['output_start']))
                        prepared = motion.frame(output_index - segment.get('motion_start', segment['output_start']), source_frame=prepared)
                    send(timed_visuals(prepared, cfg, output_time), finish_frame=False)
        ramp_frames = []
        for index in range(count) if not timeline_editor else ():
            check()
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f'Video decoding ended early at frame {index}/{count}.')
            if not cfg.animation:
                send(source_frame(frame, index))
            elif index < plan['before']:
                send(source_frame(frame, index))
            elif index < plan['ff']:
                ramp_frames.append(source_frame(frame, index))
            elif index == plan['ff']:
                if ramp_frames:
                    ramp_frames.append(apply_background(freeze, protected, cfg, plan['ff'] / fps)
                                       if background_enabled(cfg) and cfg.background_scope == 'all' else freeze)
                    for j in range(plan['ramp_out']):
                        # Decelerating sample positions with a continuous endpoint.
                        q = j / max(1, plan['ramp_out'])
                        pos = (1 - (1 - q) ** 2) * plan['ramp_src']
                        a = min(int(pos), len(ramp_frames) - 1)
                        b = min(a + 1, len(ramp_frames) - 1)
                        send(cv2.addWeighted(ramp_frames[a], 1 - (pos - a),
                                             ramp_frames[b], pos - a, 0))
                    ramp_frames.clear()
                for j in range(plan['motion']):
                    send(motion.frame(j))
            else:
                send(source_frame(frame, index))
        if done != plan['total_frames']:
            raise RuntimeError('Rendered frame count did not match the edit plan.')
        enc.stdin.close()
        while enc.poll() is None:
            check()
            try:
                enc.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        err_file.close()
        if enc.returncode:
            with open(err_path, encoding='utf-8', errors='replace') as handle:
                raise RuntimeError(handle.read()[-1500:])
        progress(0.94)
        log('Saving MP4')
        result = os.path.join(work, 'complete.mp4')
        command([FFMPEG, '-v', 'error', '-y', '-i', video, '-i', sound, '-map', '0:v', '-map', '1:a',
                 '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', result])
        check()
        # Stage beside destination so replacement is atomic, even across drives.
        fd, staging = tempfile.mkstemp(prefix='.montagemaker_', suffix='.mp4', dir=os.path.dirname(out))
        os.close(fd)
        try:
            shutil.copyfile(result, staging)
            check()
            os.replace(staging, out)
        finally:
            if os.path.exists(staging):
                os.unlink(staging)
        progress(1)
        log(f'Done · {time.monotonic() - started:.1f} s · {out}')
        return out
    finally:
        close_media()
        if cap is not None:
            cap.release()
        if cleaner is not None:
            cleaner.close()
        if tracker is not None:
            tracker.close()
        if enc is not None and enc.poll() is None:
            enc.kill()
            enc.wait()
        if 'err_file' in locals() and not err_file.closed:
            err_file.close()
        shutil.rmtree(work, ignore_errors=True)
