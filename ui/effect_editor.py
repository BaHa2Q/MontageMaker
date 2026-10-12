"""Independent effect instances and their contextual inspector."""
import copy
import uuid
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox,QLineEdit,QCheckBox,QDoubleSpinBox,QSlider,QFormLayout,QHBoxLayout,QPushButton,QLabel
from engine.editing import effect_span
from engine.live import edit_plan,sample_output,source_to_output
from engine.templates import MOTION_TEMPLATES
from engine.sound import builtin_sound
from .panels import Section

EFFECT_NAMES={'freeze':'تجميد وحركة','slowmo':'تباطؤ','clarity':'ألوان ووضوح','flash':'فلاش','shake':'اهتزاز','glow':'وهج','scan':'مسح ضوئي','bars':'أشرطة سينما','background':'خلفية'}

def spin(value=0,minimum=0,maximum=86400,suffix=' ث'):
    w=QDoubleSpinBox(); w.setRange(minimum,maximum); w.setDecimals(3); w.setSingleStep(.1); w.setSuffix(suffix); w.setValue(value); return w

class EffectEditor:
    def make_effect_tab(self):
        box=self.tab('التأثير'); self.effect_tab_index=self.sidebar.count()-1
        self.effect_combo=QComboBox(); self.effect_combo.currentIndexChanged.connect(self.effect_combo_changed); box.addWidget(self.effect_combo)
        timing=Section('المقطع المحدد',True); box.addWidget(timing); form=QFormLayout()
        self.effect_name=QLineEdit(); self.effect_name.editingFinished.connect(self.effect_fields_changed)
        self.effect_time=spin(); self.effect_length=spin(3,.05); self.effect_source=spin(); self.effect_source_length=spin(.5,.02)
        for text,w in [('الاسم',self.effect_name),('المكان على المسار',self.effect_time),('المدة في الناتج',self.effect_length),('لقطة الأصل',self.effect_source),('جزء الأصل للبطء',self.effect_source_length)]:
            form.addRow(text,w)
            if w is not self.effect_name: w.valueChanged.connect(self.effect_fields_changed)
        self.effect_timing_form=form; timing.box.addLayout(form)
        row=QHBoxLayout(); self.effect_enabled=QCheckBox('تفعيل'); self.effect_locked=QCheckBox('قفل')
        for w in (self.effect_enabled,self.effect_locked): w.toggled.connect(self.effect_fields_changed); row.addWidget(w)
        timing.box.addLayout(row)
        self.effect_motion=Section('الحركة',True); box.addWidget(self.effect_motion); form=QFormLayout()
        self.effect_style=QComboBox()
        for key,title,glyph in MOTION_TEMPLATES: self.effect_style.addItem(title,key)
        self.effect_style.currentIndexChanged.connect(self.effect_fields_changed)
        self.effect_angle=spin(360,0,1080,'°'); self.effect_angle.setSingleStep(15); self.effect_angle.valueChanged.connect(self.effect_fields_changed)
        self.effect_direction=QComboBox(); self.effect_direction.addItem('مع عقارب الساعة',-1); self.effect_direction.addItem('عكس عقارب الساعة',1); self.effect_direction.currentIndexChanged.connect(self.effect_fields_changed)
        form.addRow('القالب',self.effect_style); form.addRow('الزاوية',self.effect_angle); form.addRow('الاتجاه',self.effect_direction); self.effect_motion.box.addLayout(form)
        self.effect_slow=Section('سرعة الفيديو',True); box.addWidget(self.effect_slow); form=QFormLayout()
        self.effect_speed=spin(.45,.1,1,' ×'); self.effect_speed.valueChanged.connect(self.effect_fields_changed); form.addRow('السرعة',self.effect_speed); self.effect_slow.box.addLayout(form)
        self.effect_strength_box=Section('قوة التأثير',True); box.addWidget(self.effect_strength_box)
        self.effect_strength=QSlider(Qt.Horizontal); self.effect_strength.setRange(0,100); self.effect_strength.setValue(65); self.effect_strength.valueChanged.connect(self.effect_fields_changed); self.effect_strength_box.box.addWidget(self.effect_strength)
        self.effect_look=Section('الألوان والوضوح',True); box.addWidget(self.effect_look); form=QFormLayout()
        self.effect_contrast=spin(1.05,0,3,' ×'); self.effect_saturation=spin(1.08,0,3,' ×'); self.effect_sharpness=spin(.35,0,2,' ×')
        for text,w in [('التباين',self.effect_contrast),('تشبع الألوان',self.effect_saturation),('الوضوح',self.effect_sharpness)]: form.addRow(text,w); w.valueChanged.connect(self.effect_fields_changed)
        self.effect_look.box.addLayout(form)
        self.effect_bg_note=QLabel('لون الخلفية والغباش وسرعتها في تبويب الخلفية؛ التوقيت من هنا.'); self.effect_bg_note.setWordWrap(True); box.addWidget(self.effect_bg_note)
        row=QHBoxLayout()
        for text,fn in [('نسخ',self.duplicate_selected_effect),('حذف',self.remove_selected_effect)]:
            btn=QPushButton(text); btn.clicked.connect(fn); row.addWidget(btn)
        box.addLayout(row); box.addStretch(); self.refresh_effect_fields()

    def selected_effect(self):
        ident=getattr(self.audio_timeline.canvas,'selected_effect_id',None)
        return next((clip for clip in self.effect_clips if clip['id']==ident),None)

    def refresh_effect_combo(self):
        if not hasattr(self,'effect_combo'): return
        self.effect_combo.blockSignals(True); self.effect_combo.clear()
        for i,clip in enumerate(self.effect_clips): self.effect_combo.addItem(f'{i+1} · {clip.get("name",EFFECT_NAMES.get(clip["kind"],clip["kind"]))}',clip['id'])
        self.effect_combo.setCurrentIndex(self.effect_combo.findData(getattr(self.audio_timeline.canvas,'selected_effect_id',None)))
        self.effect_combo.blockSignals(False)

    def effect_combo_changed(self,index):
        if index>=0: self.select_effect(self.effect_combo.itemData(index))

    def select_effect(self,ident):
        self.audio_timeline.canvas.selected_effect_id=ident; self.audio_timeline.canvas.selected_id=None
        clip=self.selected_effect()
        if not clip: return
        self.refresh_effect_combo(); self.refresh_effect_fields(); self.refresh_audio_fields()
        self.inspector_caption.setText(clip.get('name',EFFECT_NAMES[clip['kind']]))
        self.sidebar.setCurrentIndex(self.effect_tab_index); self.audio_timeline.canvas.update()
        if clip['kind']=='background': self.populate_background_fields(clip)

    def refresh_effect_fields(self):
        if not hasattr(self,'effect_time'): return
        self.syncing_effect=True
        try:
            clip=self.selected_effect(); has=clip is not None; kind=clip['kind'] if clip else ''
            controls=(self.effect_name,self.effect_time,self.effect_length,self.effect_source,self.effect_source_length,self.effect_enabled,self.effect_locked,self.effect_style,self.effect_angle,self.effect_direction,self.effect_speed,self.effect_strength,self.effect_contrast,self.effect_saturation,self.effect_sharpness)
            for w in controls: w.setEnabled(has)
            self.effect_motion.setVisible(kind=='freeze'); self.effect_slow.setVisible(kind=='slowmo'); self.effect_look.setVisible(kind=='clarity'); self.effect_bg_note.setVisible(kind=='background'); self.effect_strength_box.setVisible(kind not in ('slowmo','background',''))
            self.effect_timing_form.setRowVisible(self.effect_source,kind in ('freeze','slowmo')); self.effect_timing_form.setRowVisible(self.effect_source_length,kind=='slowmo')
            if clip:
                p=clip.get('params',{}); start,length=effect_span(self.config(),self.info,clip)
                self.effect_name.setText(clip.get('name',EFFECT_NAMES[kind])); self.effect_time.setValue(start)
                self.effect_length.setMaximum(60 if kind=='freeze' else 86400); self.effect_length.setValue(length); self.effect_length.setReadOnly(kind=='slowmo')
                self.effect_source.setRange(self.trim_in.value(),max(self.trim_in.value(),self.trim_out.value()-.001)); self.effect_source.setValue(clip.get('source_time',self.source_position))
                self.effect_source_length.setMaximum(max(.02,self.trim_out.value()-clip.get('source_time',self.source_position))); self.effect_source_length.setValue(clip.get('source_duration',.5))
                self.effect_enabled.setChecked(not clip.get('muted',False)); self.effect_locked.setChecked(clip.get('locked',False))
                self.effect_style.setCurrentIndex(self.effect_style.findData(p.get('motion_style','hold'))); self.effect_angle.setValue(p.get('orbit_angle',360)); self.effect_direction.setCurrentIndex(self.effect_direction.findData(p.get('rotation_direction',1)))
                self.effect_speed.setValue(clip.get('speed',.45)); self.effect_strength.setValue(round(p.get('strength',p.get('motion_strength',.65))*100))
                self.effect_contrast.setValue(p.get('contrast',1.05)); self.effect_saturation.setValue(p.get('saturation',1.08)); self.effect_sharpness.setValue(p.get('sharpness',.35))
                if clip.get('locked'):
                    for w in controls:
                        if w not in (self.effect_enabled,self.effect_locked): w.setEnabled(False)
        finally: self.syncing_effect=False

    def effect_fields_changed(self,*args):
        if self.syncing_effect or self.loading_project or self.busy: return
        clip=self.selected_effect()
        if not clip: return
        sender=self.sender(); old=copy.deepcopy(clip); self.audio_timeline.canvas.remember()
        clip.update(name=self.effect_name.text() or EFFECT_NAMES[clip['kind']],muted=not self.effect_enabled.isChecked(),locked=self.effect_locked.isChecked())
        p=clip.setdefault('params',{}); p['strength']=self.effect_strength.value()/100
        if clip['kind'] in ('freeze','slowmo'):
            if sender is self.effect_time:
                cfg=self.config(); cfg.effect_clips=[c for c in cfg.effect_clips if c['id']!=clip['id']]
                source=sample_output(cfg,self.info,self.effect_time.value())[0]
            else: source=self.effect_source.value()
            clip['source_time']=max(self.trim_in.value(),min(source,self.trim_out.value()-.001))
        else: clip['time']=self.effect_time.value()
        if clip['kind']=='freeze':
            clip['duration']=self.effect_length.value(); p.update(motion_style=self.effect_style.currentData(),orbit_angle=self.effect_angle.value(),rotation_direction=self.effect_direction.currentData(),motion_strength=self.effect_strength.value()/100)
        elif clip['kind']=='slowmo':
            clip['speed']=self.effect_speed.value(); clip['source_duration']=min(self.effect_source_length.value(),self.trim_out.value()-clip['source_time'])
            if not clip.get('muted'):
                desired=clip['source_time']; available=self.audio_timeline.canvas.slowmo_position(clip,desired,clip['source_duration'])
                if available is None or abs(available-desired)>1e-6:
                    clip.clear(); clip.update(old); self.refresh_effect_fields(); self.status.setText('الجزء المحدد يتداخل مع تباطؤ آخر؛ اختَر جزءًا مختلفًا.'); return
        else: clip['duration']=self.effect_length.value()
        if clip['kind']=='clarity': p.update(contrast=self.effect_contrast.value(),saturation=self.effect_saturation.value(),sharpness=self.effect_sharpness.value())
        mapping=('muted','source_time','duration') if clip['kind']=='freeze' else ('muted','source_time','source_duration','speed') if clip['kind']=='slowmo' else ()
        if any(clip.get(k)!=old.get(k) for k in mapping):
            self.pause_live()
            if self.live_audio: self.live_audio.discontinuity()
        self.refresh_effect_fields(); self.refresh_effect_combo(); self.invalidate()

    def add_effect(self,kind,t=None,params=None,source=None):
        if self.busy or not self.info: return
        self.pause_live(); t=self.output_position if t is None else t
        plan=edit_plan(self.config(),self.info); t=max(0,min(t,max(0,plan['duration']-.05)))
        clip=dict(id=uuid.uuid4().hex,kind=kind,name=EFFECT_NAMES[kind],muted=False,locked=False,params=copy.deepcopy(params or {}))
        if kind in ('freeze','slowmo'):
            source=sample_output(self.config(),self.info,t)[0] if source is None else source
            clip['source_time']=min(max(self.trim_in.value(),source),self.trim_out.value()-.001)
            if kind=='freeze': clip['duration']=3; clip['params'].setdefault('motion_style','hold'); clip['params'].setdefault('motion_strength',.65)
            else:
                clip['source_duration']=min(.5,self.trim_out.value()-clip['source_time']); clip['speed']=.45
                for other in self.effect_clips:
                    if other['kind']=='slowmo' and not other.get('muted') and other['source_time']<clip['source_time']+clip['source_duration'] and clip['source_time']<other['source_time']+other['source_duration']:
                        self.status.setText('هذا الجزء فيه تباطؤ بالفعل؛ حدّد مقطعه لتعديل السرعة.'); return
        else: clip.update(time=t,duration=max(.05,min(3,plan['duration']-t)))
        self.audio_timeline.canvas.remember(); self.effect_clips.append(clip); self.refresh_effect_combo(); self.select_effect(clip['id']); self.invalidate()
        self.seek_output(effect_span(self.config(),self.info,clip)[0]); return clip

    def remove_selected_effect(self):
        clip=self.selected_effect()
        if clip and not clip.get('locked'): self.audio_timeline.canvas.remove_effect(clip)

    def duplicate_selected_effect(self):
        clip=self.selected_effect()
        if clip and not clip.get('locked'): self.audio_timeline.canvas.duplicate_effect(clip)

    def add_builtin_sound(self,key):
        if self.busy or not self.info: return
        path,title,duration=builtin_sound(key); self.audio_timeline.canvas.remember()
        self.fx_row(path,self.output_position,100,item=dict(duration=duration,source_duration=duration,builtin=key,display_name=title))
        self.clips_changed(); self.select_audio_clip(self.audio_clips[-1]['id'])

    def populate_background_fields(self,clip):
        self.syncing_effect=True
        try:
            p=clip.get('params',{}); self.selected_background=p.get('background_template','none'); self.background_color=p.get('background_color','#73e0bd'); self.background_media=p.get('background_media')
            self.background_buttons[self.selected_background].setChecked(True); self.bg_strength.setValue(round(p.get('background_strength',.65)*100)); self.bg_speed.setValue(p.get('background_speed',1)); self.bg_blur.setValue(p.get('background_blur',0)); self.bg_dim.setValue(round(p.get('background_dim',0)*100)); self.mask_feather.setValue(p.get('mask_feather',2))
            for k,w in self.bg_options.items(): w.setChecked(p.get(k,False))
        finally: self.syncing_effect=False

    def background_params(self):
        return dict(background_template=self.selected_background,background_color=self.background_color,background_media=self.background_media,background_strength=self.bg_strength.value()/100,background_speed=self.bg_speed.value(),background_blur=self.bg_blur.value(),background_dim=self.bg_dim.value()/100,mask_feather=self.mask_feather.value(),**{k:w.isChecked() for k,w in self.bg_options.items()})

    def background_fields_changed(self,*args):
        if self.syncing_effect or self.loading_project: return
        clip=self.selected_effect()
        if clip and clip['kind']=='background' and not clip.get('locked'):
            self.audio_timeline.canvas.remember(); clip['params']=self.background_params(); self.invalidate()

    def legacy_effects(self,cfg):
        clips=[]; old=edit_plan(cfg,self.info)
        def add(kind,**data):
            clip=dict(id=uuid.uuid4().hex,kind=kind,name=EFFECT_NAMES[kind],muted=False,locked=False,params={}); clip.update(data); clips.append(clip); return clip
        if cfg.animation:
            add('freeze',source_time=cfg.freeze_time,duration=cfg.orbit_seconds,params=dict(motion_style=cfg.motion_style,orbit_angle=cfg.orbit_angle,rotation_direction=cfg.rotation_direction,motion_strength=cfg.motion_strength))
            if cfg.slowmo and old['ramp_src']: add('slowmo',source_time=cfg.trim_start+old['before']/cfg.output_fps,source_duration=old['ramp_src']/cfg.output_fps,speed=old['ramp_src']/old['ramp_out'])
        for kind in ('clarity','glow','bars'):
            if getattr(cfg,kind): add(kind,time=0,duration=old['duration'])
        for kind in ('flash','shake','scan'):
            if cfg.animation and getattr(cfg,kind): add(kind,time=old['freeze'] if kind!='shake' else max(0,old['resume']-.3),duration=.15 if kind=='flash' else .3 if kind=='shake' else min(1,cfg.orbit_seconds),params={'strength':cfg.motion_strength})
        if any([cfg.background_template!='none',cfg.background_blur,cfg.background_dim,cfg.background_desaturate,cfg.background_glow,cfg.background_particles]):
            p={k:getattr(cfg,k) for k in ('background_template','background_color','background_media','background_strength','background_speed','background_blur','background_dim','background_desaturate','background_glow','background_particles','mask_feather')}
            add('background',time=old['freeze'] if cfg.background_scope=='freeze' else 0,duration=cfg.orbit_seconds if cfg.background_scope=='freeze' else old['duration'],params=p)
        return clips
