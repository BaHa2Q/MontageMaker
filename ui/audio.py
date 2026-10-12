"""Independent audio clip players synchronized to the edit playhead."""
import hashlib
import os
import tempfile
import time as clock
from dataclasses import dataclass
from PySide6.QtCore import QObject, QThread, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from engine.live import edit_plan, sample_output
from engine.sound import make_soundtrack
from engine.util import CACHE_DIR


class EffectsAudio(QThread):
    ready=Signal(str,str,float,float)
    failed=Signal(str)

    def __init__(self,cfg,info,parent=None):
        super().__init__(parent); self.cfg,self.info=cfg,info
        plan=edit_plan(cfg,info)
        self.offset=max(0,plan['ramp']-.1)
        self.duration=min(plan['duration']-self.offset,plan['resume']-self.offset+1.3)
        self.key=hashlib.sha256(str((plan['ramp'],plan['freeze'],plan['resume'],self.duration,cfg.orbit_seconds)).encode()).hexdigest()[:20]

    def run(self):
        try:
            cfg=self.cfg; plan=edit_plan(cfg,self.info)
            os.makedirs(os.path.join(CACHE_DIR,'live_audio'),exist_ok=True)
            path=os.path.join(CACHE_DIR,'live_audio',self.key+'.wav')
            if not os.path.isfile(path):
                fd,temp=tempfile.mkstemp(suffix='.wav',dir=os.path.dirname(path)); os.close(fd)
                try:
                    make_soundtrack(self.duration,plan['ramp']-self.offset,plan['freeze']-self.offset,plan['resume']-self.offset,
                                    max(.3,cfg.orbit_seconds-.3),None,None,[],temp,fx_gain=1)
                    os.replace(temp,path)
                finally:
                    if os.path.exists(temp): os.unlink(temp)
            self.ready.emit(self.key,path,self.offset,self.duration)
        except Exception as ex: self.failed.emit(str(ex))


@dataclass
class _VoiceTransport:
    """Requested transport, independent of Qt's asynchronous loading state."""
    playing: bool = False
    generation: int = -1
    source: float | None = None
    timeline: float | None = None
    synced_at: float = 0.0
    rate: float = 1.0
    pending_seek: bool = False
    volume: float | None = None


class LiveAudio(QObject):
    def __init__(self,parent):
        super().__init__(parent); self.players={}; self.master=.65; self.fx_path=None; self.fx_offset=0; self.fx_duration=0
        self.transports={}; self.seek_generation=0; self.timeline_position=None

    def discontinuity(self):
        """Call after a seek or a change to the edit's source-time mapping."""
        self.seek_generation+=1

    def media_ready(self,ident,player,status):
        entry=self.players.get(ident); transport=self.transports.get(ident)
        if not entry or entry[0] is not player or not transport or not transport.pending_seek:
            return
        if status not in (QMediaPlayer.LoadedMedia,QMediaPlayer.BufferedMedia):
            return
        # Loading can complete several ticks after play was requested. Reapply the
        # latest desired offset once, instead of seeking on every loading tick.
        transport.pending_seek=False
        if transport.playing and transport.source is not None:
            source=transport.source+max(0,clock.monotonic()-transport.synced_at)*transport.rate
            player.setPosition(round(max(0,source)*1000))

    def voice(self,ident,path):
        entry=self.players.get(ident)
        if not entry or entry[2]!=path:
            if entry:
                entry[0].stop(); entry[0].deleteLater(); entry[1].deleteLater()
            player=QMediaPlayer(self); audio=QAudioOutput(self); player.setAudioOutput(audio)
            entry=(player,audio,path); self.players[ident]=entry
            self.transports[ident]=_VoiceTransport()
            player.mediaStatusChanged.connect(lambda status,i=ident,p=player:self.media_ready(i,p,status))
            if ident=='game' and hasattr(player,'setPitchCompensation'):
                player.setPitchCompensation(False)
            player.setSource(QUrl.fromLocalFile(path))
        return entry

    def sync_voice(self,ident,path,source,volume,playing,active=True,rate=1.0):
        if not path: return
        if not playing or not active or source<0:
            entry=self.players.get(ident)
            transport=self.transports.get(ident)
            if entry and transport and transport.playing:
                entry[0].pause(); transport.playing=False; transport.pending_seek=False
            return
        player,audio,_=self.voice(ident,path)
        transport=self.transports[ident]; now=clock.monotonic()
        gain=min(1,max(0,volume*self.master))
        if transport.volume!=gain:
            audio.setVolume(gain); transport.volume=gain
        rate=max(.05,min(4,float(rate)))
        changed=transport.generation!=self.seek_generation
        if transport.source is not None and transport.timeline is not None and self.timeline_position is not None:
            expected=(self.timeline_position-transport.timeline)*transport.rate
            # Compare requested timeline positions, not sparse backend position
            # updates. The latter can lag while streaming perfectly normally.
            changed=changed or abs((source-transport.source)-expected)>.08
        starting=not transport.playing
        if starting or changed:
            player.setPosition(round(source*1000))
            transport.pending_seek=player.mediaStatus() in (QMediaPlayer.NoMedia,QMediaPlayer.LoadingMedia)
        if starting or abs(transport.rate-rate)>.0001:
            player.setPlaybackRate(rate)
        transport.source=float(source); transport.timeline=self.timeline_position
        transport.synced_at=now; transport.rate=rate; transport.generation=self.seek_generation
        if starting:
            transport.playing=True; player.play()

    def sync(self,cfg,info,time,source,original,playing):
        if not cfg.video: return
        self.timeline_position=float(time)
        plan=edit_plan(cfg,info)
        sampled=None if original else sample_output(cfg,info,time)
        phase='video' if original else sampled[1]
        game_rate=1.0; game_active=phase=='video'
        if not original:
            source=sampled[0]
            segment=sampled[3].get('active_segment')
            if segment and segment.get('kind') in ('video','slowmo'):
                game_rate=segment.get('speed',1)
                game_active=True
                # Audio uses continuous output time, without frame quantization.
                output_frame=max(segment['output_start'],min(segment['output_end'],time*cfg.output_fps))
                source=cfg.trim_start+(segment['source_start']+(output_frame-segment['output_start'])*game_rate)/cfg.output_fps
        active={'game'}
        self.sync_voice('game',cfg.video,source,cfg.game_volume,playing,cfg.game_audio and info['audio'] and game_active,game_rate)
        if original:
            self.pause_missing(active)
            return
        if cfg.music:
            active.add('music')
            offset=cfg.drop_in_song-plan['resume'] if cfg.drop_in_song is not None else cfg.music_start
            self.sync_voice('music',cfg.music,time+offset,cfg.music_volume,playing,not cfg.music_muted)
        tracks={str(track['id']):track for track in cfg.audio_tracks}
        for i,clip in enumerate(cfg.sound_effects):
            track=tracks.get(str(clip.get('track')), {})
            ident=clip.get('id',str(i)); active.add(ident)
            elapsed=time-clip['time']; duration=clip.get('duration') or plan['duration']
            gain=clip['volume']*track.get('volume',1)
            if clip.get('fade_in'): gain*=min(1,max(0,elapsed/clip['fade_in']))
            if clip.get('fade_out'): gain*=min(1,max(0,(duration-elapsed)/clip['fade_out']))
            self.sync_voice(ident,clip['path'],clip.get('source_in',0)+elapsed,gain,playing,
                            0<=elapsed<duration and not clip.get('muted',False) and not track.get('muted',False))
        if self.fx_path:
            active.add('builtin')
            self.sync_voice('builtin',self.fx_path,time-self.fx_offset,cfg.fx_volume,playing,
                            cfg.builtin_fx and cfg.animation and self.fx_offset<=time<self.fx_offset+self.fx_duration)
        self.pause_missing(active)

    def pause_missing(self,active):
        for ident,(player,_,_) in self.players.items():
            transport=self.transports[ident]
            if ident not in active and transport.playing:
                player.pause(); transport.playing=False; transport.pending_seek=False

    def pause(self):
        self.pause_missing(set())

    def close(self):
        for player,_,_ in self.players.values(): player.stop(); player.setSource(QUrl())
        for transport in self.transports.values(): transport.playing=False; transport.pending_seek=False
