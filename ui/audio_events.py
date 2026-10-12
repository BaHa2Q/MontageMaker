"""Convert former implicit source/music audio into independent editable events."""
import copy
import json
import math
import os
import uuid
import wave

from engine.live import edit_plan
from engine.util import FFPROBE, run


def _track(role,name,volume=1.0,muted=False):
    return dict(id=uuid.uuid4().hex,name=name,role=role,volume=float(volume),
                muted=bool(muted),locked=False)


def _clip(path,track,start,source,duration,source_duration,role,rate=1.0,muted=False):
    return dict(id=uuid.uuid4().hex,path=path,track=track['id'],role=role,
                display_name=('الصوت الأصلي · ' if role=='game' else 'موسيقى · ')+os.path.basename(path),
                time=float(start),source_in=float(source),duration=float(duration),
                source_duration=float(source_duration),rate=float(rate),volume=1.0,
                fade_in=0.,fade_out=0.,muted=bool(muted),locked=False,
                source_audio=role=='game')


def _has_role(clips,tracks,role):
    # Retained empty tracks are also migration markers: deleting the last event
    # must not cause the old implicit audio to reappear on the next project load.
    return any(item.get('role')==role for item in clips) or any(item.get('role')==role for item in tracks)


def _media_duration(path,fallback):
    try:
        with wave.open(path,'rb') as source:
            value=source.getnframes()/source.getframerate()
            if math.isfinite(value) and value>0: return value
    except (OSError,wave.Error,EOFError):
        pass
    try:
        data=json.loads(run([FFPROBE,'-v','error','-show_format','-of','json',path]))
        value=float(data.get('format',{}).get('duration',0))
        if math.isfinite(value) and value>0: return value
    except (OSError,RuntimeError,ValueError,TypeError):
        pass
    # Keep a legacy project's referenced media instead of silently discarding it
    # when its duration metadata is unavailable; normal validation reports files.
    return max(0.,float(fallback))


def seed_source_audio(path,info,trim_in=0.,trim_out=None):
    """Return (clips, tracks) for a new source; its audio is independent of video."""
    if not path or not info.get('audio',False): return [],[]
    end=float(info['duration'] if trim_out is None else trim_out)
    start=max(0.,float(trim_in)); end=min(float(info['duration']),end)
    if end<=start: return [],[]
    track=_track('game','صوت الفيديو')
    clip=_clip(path,track,0.,start,end-start,info['duration'],'game')
    return [clip],[track]


def migrate_audio_events(cfg,info,video_clips,audio_clips,tracks):
    """Preserve existing events and materialize legacy streams once per role.

    The caller disables cfg.game_audio and clears cfg.music after storing the
    returned models. Editing video/effects later never rewrites these events.
    """
    clips=copy.deepcopy(audio_clips or []); result_tracks=copy.deepcopy(tracks or [])
    plan=edit_plan(cfg,info)
    if cfg.video and info.get('audio',False) and not _has_role(clips,result_tracks,'game'):
        track=_track('game','صوت الفيديو',cfg.game_volume,not cfg.game_audio)
        result_tracks.append(track)
        for video in video_clips or []:
            rate=float(video.get('speed',1.))
            duration=float(video.get('duration',0.))
            if rate<=0 or duration<=0 or video.get('kind') in ('hold','freeze','gap'):
                continue
            source=float(video.get('source_in',0.))
            # Source duration is physical seconds; event duration is output time.
            duration=min(duration,max(0.,(float(info['duration'])-source)/rate))
            if duration<=0: continue
            clip=_clip(video.get('path') or cfg.video,track,video.get('time',0.),source,duration,
                       video.get('source_duration') or info['duration'],'game',rate,
                       muted=video.get('muted',False))
            clip['origin_video_id']=video.get('id')
            clips.append(clip)
    if cfg.music and not _has_role(clips,result_tracks,'music'):
        track=_track('music','الموسيقى',cfg.music_volume,cfg.music_muted)
        result_tracks.append(track)
        # Exactly the former playback/export alignment, including negative offsets.
        offset=float(cfg.drop_in_song-plan['resume'] if cfg.drop_in_song is not None else cfg.music_start)
        start=max(0.,-offset); source=max(0.,offset)
        source_duration=_media_duration(cfg.music,float(plan['duration'])+source)
        duration=min(max(0.,float(plan['duration'])-start),max(0.,source_duration-source))
        if duration>0:
            clips.append(_clip(cfg.music,track,start,source,duration,source_duration,'music'))
    return clips,result_tracks
