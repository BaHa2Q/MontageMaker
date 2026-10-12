"""Fixed timeline video events and project migration."""
import copy
import dataclasses
import os
import uuid
from PySide6.QtWidgets import QFormLayout,QCheckBox,QHBoxLayout,QDoubleSpinBox
from .panels import Section
from engine.editing import effect_span
from engine.live import edit_plan


class VideoEditor:
    def make_video_properties(self,box):
        self.video_section=Section('مقطع الفيديو المحدد',True); box.addWidget(self.video_section)
        form=QFormLayout()
        def field():
            w=QDoubleSpinBox(); w.setRange(0,86400); w.setDecimals(3); w.setSingleStep(.1); w.setSuffix(' ث')
            w.valueChanged.connect(self.video_fields_changed); return w
        self.video_time=field(); self.video_in=field(); self.video_length=field()
        self.video_length.setMinimum(.05)
        for text,w in [('مكانه على المسار',self.video_time),('بداية الأصل',self.video_in),('مدة المقطع',self.video_length)]: form.addRow(text,w)
        self.video_section.box.addLayout(form); row=QHBoxLayout()
        self.video_mute=QCheckBox('إخفاء المقطع'); self.video_lock=QCheckBox('قفل المقطع')
        for w in (self.video_mute,self.video_lock): w.toggled.connect(self.video_fields_changed); row.addWidget(w)
        self.video_section.box.addLayout(row); self.refresh_video_fields()

    def selected_video(self):
        ident=getattr(self.audio_timeline.canvas,'selected_video_id',None)
        return next((clip for clip in self.video_clips if clip['id']==ident),None)

    def select_video_event(self,ident):
        canvas=self.audio_timeline.canvas
        if ('video',ident) not in canvas.selection:
            canvas.select_item('video',ident); return
        canvas.selected_video_id=ident
        self.inspector_caption.setText('خصائص مقطع الفيديو'); self.sidebar.setCurrentIndex(0)
        self.refresh_video_fields()

    def refresh_video_fields(self):
        if not hasattr(self,'video_time'): return
        self.syncing_video=True
        try:
            clip=self.selected_video(); controls=(self.video_time,self.video_in,self.video_length,self.video_mute,self.video_lock)
            for w in controls: w.setEnabled(clip is not None)
            if clip:
                self.video_time.setValue(clip.get('time',0)); self.video_in.setMaximum(max(0,self.info['duration']-.05)); self.video_in.setValue(clip['source_in'])
                speed=clip.get('speed',1)
                self.video_length.setMaximum((self.info['duration']-clip['source_in'])/speed if speed else 86400)
                self.video_length.setValue(clip['duration']); self.video_mute.setChecked(clip.get('muted',False)); self.video_lock.setChecked(clip.get('locked',False))
                if self.audio_timeline.canvas.item_locked('video',clip):
                    for w in (self.video_time,self.video_in,self.video_length): w.setEnabled(False)
                if self.lane_states.get('video',{}).get('locked'):
                    for w in controls: w.setEnabled(False)
        finally: self.syncing_video=False

    def video_fields_changed(self,*args):
        if self.syncing_video or self.loading_project or self.busy: return
        clip=self.selected_video()
        if not clip: return
        if self.lane_states.get('video',{}).get('locked'): self.refresh_video_fields(); return
        if clip.get('locked') and self.sender() is not self.video_lock: self.refresh_video_fields(); return
        self.audio_timeline.canvas.remember(); speed=clip.get('speed',1); source=self.video_in.value()
        maximum=(self.info['duration']-source)/speed if speed else 86400
        clip.update(time=self.video_time.value(),source_in=source,duration=max(.05,min(self.video_length.value(),maximum)),muted=self.video_mute.isChecked(),locked=self.video_lock.isChecked())
        self.pause_live(); self.live_audio.discontinuity(); self.refresh_video_fields(); self.invalidate()

    def seed_video(self):
        self.video_clips[:]=[dict(id=uuid.uuid4().hex,name=os.path.basename(self.video),time=0,source_in=0,duration=self.info['duration'],muted=False,locked=False)]
        self.timeline_duration=None; self.lane_states.clear()
        self.audio_timeline.canvas.clear_selection()
        self.audio_clips[:]=[c for c in self.audio_clips if not c.get('source_audio')]
        self.audio_tracks[:]=[t for t in self.audio_tracks if t.get('role')!='game']
        if self.info['audio']:
            from .audio_events import seed_source_audio
            clips,tracks=seed_source_audio(self.video,self.info,0,self.info['duration'])
            self.audio_clips.extend(clips); self.audio_tracks.extend(tracks)

    def migrate_timeline(self,cfg):
        if cfg.video_clips is not None:
            self.video_clips[:]=copy.deepcopy(cfg.video_clips)
            self.timeline_duration=cfg.timeline_duration
            self.effect_clips[:]=copy.deepcopy(cfg.effect_clips or [])
            self.lane_states.clear(); self.lane_states.update(copy.deepcopy(cfg.lane_states))
            return copy.deepcopy(cfg.sound_effects),copy.deepcopy(cfg.audio_tracks)
        old=edit_plan(cfg,self.info); fps=cfg.output_fps
        self.video_clips[:]=[dict(id=uuid.uuid4().hex,name=os.path.basename(self.video),time=s['output_start']/fps,source_in=cfg.trim_start+s['source_start']/fps,duration=(s['output_end']-s['output_start'])/fps,speed=s['speed'],muted=False,locked=False) for s in old['segments']]
        self.timeline_duration=None; self.lane_states.clear()
        effects=copy.deepcopy(cfg.effect_clips) if cfg.effect_clips is not None else self.legacy_effects(cfg)
        for clip in effects:
            start,duration=effect_span(cfg,self.info,clip); clip.update(time=start,duration=duration)
        self.effect_clips[:]=effects
        from .audio_events import migrate_audio_events
        return migrate_audio_events(cfg,self.info,self.video_clips,cfg.sound_effects,cfg.audio_tracks)

    def project_settings(self):
        data=dataclasses.asdict(self.config())
        data.update(video_clips=copy.deepcopy(self.video_clips),effect_clips=copy.deepcopy(self.effect_clips),sound_effects=copy.deepcopy(self.audio_clips),audio_tracks=copy.deepcopy(self.audio_tracks),lane_states=copy.deepcopy(self.lane_states),timeline_duration=self.timeline_duration)
        return data

    def timeline_selection_changed(self):
        canvas=self.audio_timeline.canvas
        count=len(canvas.selection)
        if count>1: self.inspector_caption.setText(f'{count} مقاطع محددة · Ctrl+S تقسيم · Delete حذف')
        self.refresh_video_fields(); self.refresh_audio_fields(); self.refresh_effect_fields()
