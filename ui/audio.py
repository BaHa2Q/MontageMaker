"""Independent audio clip players synchronized to the edit playhead."""
import hashlib
import os
import tempfile
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


class LiveAudio(QObject):
    def __init__(self,parent):
        super().__init__(parent); self.players={}; self.master=.65; self.fx_path=None; self.fx_offset=0; self.fx_duration=0

    def voice(self,ident,path):
        entry=self.players.get(ident)
        if not entry or entry[2]!=path:
            if entry:
                entry[0].stop(); entry[0].deleteLater(); entry[1].deleteLater()
            player=QMediaPlayer(self); audio=QAudioOutput(self); player.setAudioOutput(audio)
            player.setSource(QUrl.fromLocalFile(path))
            entry=(player,audio,path); self.players[ident]=entry
        return entry

    def sync_voice(self,ident,path,source,volume,playing,active=True):
        if not path: return
        if not playing or not active or source<0:
            entry=self.players.get(ident)
            if entry: entry[0].pause()
            return
        player,audio,_=self.voice(ident,path)
        audio.setVolume(min(1,max(0,volume*self.master)))
        if abs(player.position()/1000-source)>.14 or player.playbackState()!=QMediaPlayer.PlayingState:
            player.setPosition(round(source*1000))
        player.setPlaybackRate(1)
        player.play()

    def sync(self,cfg,info,time,source,original,playing):
        if not cfg.video: return
        plan=edit_plan(cfg,info)
        phase='video' if original else sample_output(cfg,info,time)[1]
        active={'game'}
        self.sync_voice('game',cfg.video,source,cfg.game_volume,playing,cfg.game_audio and info['audio'] and phase=='video')
        if original:
            for ident,(player,_,_) in self.players.items():
                if ident!='game': player.pause()
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
        for ident,(player,_,_) in self.players.items():
            if ident not in active: player.pause()

    def pause(self):
        for player,_,_ in self.players.values(): player.pause()

    def close(self):
        for player,_,_ in self.players.values(): player.stop(); player.setSource(QUrl())
