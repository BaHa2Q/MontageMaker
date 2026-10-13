"""Live editor interface built on the existing export and project services."""
import copy
import dataclasses
import json
import os
import time
import sys
import uuid
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QColor, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QFrame,
    QSplitter,QTabWidget,QButtonGroup,QCheckBox,QSlider,QComboBox,QProgressBar,
    QFormLayout,QDoubleSpinBox,QSpinBox,QFileDialog,QColorDialog,QTableWidget,QGridLayout,QLineEdit)
from engine.editing import Config,media_info,validate
from engine.live import LiveFrames,edit_plan,sample_output,source_to_output
from engine.templates import MOTION_TEMPLATES,BACKGROUND_TEMPLATES
from engine.util import CACHE_DIR
from engine.proxy import EditProxy
from .viewport import Viewport
from .tracks import AudioTimeline,MediaAnalysis
from .audio import LiveAudio,EffectsAudio
from .library import Library
from .panels import Section
from .effect_editor import EffectEditor,EFFECT_NAMES
from .video_editor import VideoEditor
from .icons import icon
from engine.sound import builtin_sound


def label(text,kind=None):
    w=QLabel(text); w.setWordWrap(True)
    if kind: w.setObjectName(kind)
    return w


def button(text,callback,kind=None):
    w=QPushButton(text); w.clicked.connect(callback)
    if kind: w.setObjectName(kind)
    return w


def seconds(value=0):
    w=QDoubleSpinBox(); w.setRange(0,86400); w.setDecimals(3); w.setSingleStep(.1)
    w.setSuffix(' ث'); w.setValue(value); w.setLayoutDirection(Qt.LeftToRight); return w


def stamp(value): return f'{int(max(0,value)//60):02}:{max(0,value)%60:06.3f}'


class StudioFeatures(VideoEditor,EffectEditor):
    def __init__(self):
        self.video_clips=[]; self.lane_states={}; self.timeline_duration=None; self.syncing_video=False
        self.effect_clips=[]; self.syncing_effect=False; self.removed_assets=set()
        self.audio_clips=[]; self.audio_tracks=[]; self.selected_track_id=None; self.syncing_track=False
        self.pending_audio_track=None; self.pending_audio_time=None
        self.analysis={}; self.analysis_jobs=[]; self.fx_jobs=[]
        self.live_frames=None; self.live_audio=None; self.live_playing=False
        self.output_position=0; self.frame_revision=0; self.syncing_clip=False
        self.analysis_cache={}; self.fx_key=None; self.background_media=None
        self.proxy_jobs=[]
        self.background_color='#73e0bd'; self.loading_project=False
        super().__init__()
        from .updater import VERSION; self.setWindowTitle('Montage Maker Studio '+VERSION)
        self.resize(1530,1010); self.setMinimumSize(1100,820)
        self.player.stop(); self.player.setVideoSink(None)
        self.live_frames=LiveFrames(self)
        self.live_frames.ready.connect(self.live_frame_ready)
        self.live_frames.failed.connect(lambda message:self.status.setText(message))
        self.live_frames.start()
        self.live_audio=LiveAudio(self)
        self.tick_timer=QTimer(self); self.tick_timer.setTimerType(Qt.PreciseTimer)
        self.tick_timer.timeout.connect(self.tick); self.tick_timer.start(33)
        self.refresh_timer=QTimer(self); self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.request_live)
        self.fx_timer=QTimer(self); self.fx_timer.setSingleShot(True)
        self.fx_timer.timeout.connect(self.prepare_fx)
        self.shortcut_save=QShortcut(QKeySequence('Ctrl+Shift+S'),self)
        self.shortcut_save.activated.connect(self.save_project)
        self.shortcut_split=QShortcut(QKeySequence('Ctrl+S'),self)
        self.shortcut_split.activated.connect(self.split_audio)
        self.shortcut_left.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_right.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_left.setEnabled(False); self.shortcut_right.setEnabled(False)
        self.frame_shortcuts=[]
        for surface in (self.view,self.audio_timeline.canvas):
            for key,offset in [('Left',-1),('Right',1)]:
                shortcut=QShortcut(QKeySequence(key),surface); shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                shortcut.activated.connect(lambda d=offset:self.step(d)); self.frame_shortcuts.append(shortcut)
        self.last_tick=time.monotonic()
        if '--self-test' not in sys.argv and not os.environ.get('MONTAGEMAKER_TESTING'):
            QTimer.singleShot(0,self.restore_session)
        self.update_jobs=[]
        if getattr(sys,'frozen',False) and not os.environ.get('MONTAGEMAKER_TESTING'):
            QTimer.singleShot(1500,self.check_update)



    def build(self):
        central=QWidget(); self.setCentralWidget(central); root=QVBoxLayout(central)
        root.setContentsMargins(10,9,10,9); root.setSpacing(7)
        self.setMenuBar(self.editor_menu())
        header=QHBoxLayout(); brand=label('MONTAGE STUDIO','brand'); brand.setWordWrap(False); header.addWidget(brand)
        self.file_label=label('مشروع جديد','muted'); header.addWidget(self.file_label,1)
        self.open_btn=button('استيراد',self.import_media)
        self.project_open=button('فتح مشروع',self.load_project); self.project_save=button('حفظ',self.save_project)
        self.export_btn=button('تصدير',lambda:self.start_job(False),'accent'); self.export_btn.setEnabled(False)
        for w in (self.project_open,self.project_save,self.open_btn,self.export_btn): header.addWidget(w)
        root.addLayout(header)
        vertical=QSplitter(Qt.Vertical); vertical.setLayoutDirection(Qt.LeftToRight); root.addWidget(vertical,1)
        self.main_splitter=QSplitter(Qt.Horizontal); self.main_splitter.setLayoutDirection(Qt.LeftToRight)
        self.library=Library(); self.library.setMinimumWidth(290); self.library.setMaximumWidth(440)
        self.library.imported.connect(self.import_media); self.library.used.connect(self.use_asset); self.library.removed.connect(self.remove_asset); self.library.sound.connect(self.add_builtin_sound)
        self.library.motion.connect(self.choose_motion); self.library.background.connect(self.choose_background); self.library.page.connect(self.library_page)
        self.background_buttons=self.library.background_buttons; self.background_group=self.library.background_group
        self.main_splitter.addWidget(self.library)
        editor=QFrame(); editor.setObjectName('card'); editor.setLayoutDirection(Qt.RightToLeft)
        box=QVBoxLayout(editor); box.setContentsMargins(9,9,9,9); box.setSpacing(6)
        modes=QHBoxLayout(); self.original_btn=button('الأصلي',lambda:self.switch('source')); self.draft_btn=button('عرض مباشر',lambda:self.switch('live'))
        self.mode_group=QButtonGroup(self)
        for w in (self.original_btn,self.draft_btn): w.setCheckable(True); self.mode_group.addButton(w); modes.addWidget(w)
        self.draft_btn.setChecked(True); modes.addStretch(); self.mode_label=label('LIVE','pill'); modes.addWidget(self.mode_label)
        self.live_quality=QComboBox()
        for text,h in [('360 · سريع',360),('540 · متوازن',540),('720 · واضح',720),('1080 · كامل',1080)]: self.live_quality.addItem(text,h)
        self.live_quality.setMaximumWidth(120); self.live_quality.currentIndexChanged.connect(self.invalidate); modes.addWidget(self.live_quality); box.addLayout(modes)
        self.view=Viewport(); self.view.setMinimumWidth(380); self.view.regionsChanged.connect(self.regions_changed); self.view.objectsChanged.connect(self.objects_changed); self.view.drawingChanged.connect(self.drawing_changed); box.addWidget(self.view,1)
        tools=QHBoxLayout(); self.select_hud=button('HUD',self.select_regions); self.select_hud.setCheckable(True)
        self.protect_btn=button('حماية عنصر',self.select_objects); self.protect_btn.setCheckable(True); self.finish_object_btn=button('تثبيت',self.finish_selection,'accent')
        for w in (self.select_hud,self.protect_btn,self.finish_object_btn): tools.addWidget(w)
        self.undo_hud=button('تراجع HUD',self.undo_region); self.clear_hud=button('مسح HUD',self.clear_regions); self.undo_object=button('تراجع العنصر',self.undo_protected); self.clear_object=button('مسح العنصر',self.clear_protected)
        for w in (self.undo_hud,self.clear_hud,self.undo_object,self.clear_object): w.hide()
        self.selection_actions=QComboBox(); self.selection_actions.addItems(['أدوات…','تراجع العنصر','مسح العنصر','تراجع HUD','مسح HUD']); self.selection_actions.activated.connect(self.selection_action); self.selection_actions.setMaximumWidth(85); tools.addWidget(self.selection_actions)
        tools.addStretch(); self.zoom_slider=QSlider(Qt.Horizontal); self.zoom_slider.setRange(100,800); self.zoom_slider.setValue(100); self.zoom_slider.setMaximumWidth(70); self.zoom_slider.valueChanged.connect(self.view.set_zoom); self.view.zoomChanged.connect(self.update_zoom)
        tools.addWidget(self.zoom_slider); self.zoom_label=label('100%','muted'); tools.addWidget(self.zoom_label); tools.addWidget(button('ملاءمة',self.view.fit)); box.addLayout(tools)
        labels=QHBoxLayout(); self.object_label=label('بدون عنصر محمي','muted'); self.region_label=label('0 مناطق HUD','muted'); labels.addWidget(self.object_label); labels.addWidget(self.region_label); labels.addStretch(); box.addLayout(labels)
        self.show_hud=QCheckBox('إظهار ترميم HUD أثناء التحديد'); self.show_hud.toggled.connect(self.show_restore); self.show_hud.hide()
        self.hud_hint=label('عجلة الماوس للتكبير · Alt مع السحب للتحريك','muted'); box.addWidget(self.hud_hint)
        play=QHBoxLayout(); play.setDirection(QHBoxLayout.LeftToRight)
        prev=button('',lambda:self.step(-1)); prev.setIcon(icon('prev')); prev.setToolTip('الإطار السابق'); play.addWidget(prev)
        self.play_btn=button('',self.toggle_play,'play'); self.play_btn.setIcon(icon('play')); self.play_btn.setToolTip('تشغيل / إيقاف · Space'); play.addWidget(self.play_btn)
        nxt=button('',lambda:self.step(1)); nxt.setIcon(icon('next')); nxt.setToolTip('الإطار التالي'); play.addWidget(nxt)
        self.clock=label('00:00.000 / 00:00.000','muted'); self.clock.setLayoutDirection(Qt.LeftToRight); play.addWidget(self.clock); play.addStretch()
        self.master_slider=QSlider(Qt.Horizontal); self.master_slider.setRange(0,100); self.master_slider.setValue(65); self.master_slider.setMaximumWidth(65); self.master_slider.valueChanged.connect(self.master_changed); play.addWidget(label('♫','muted')); play.addWidget(self.master_slider); box.addLayout(play)
        self.master_slider.setToolTip('صوت العرض داخل المحرر')
        self.main_splitter.addWidget(editor)
        inspector=QFrame(); inspector.setObjectName('card'); inspector.setMinimumWidth(300); inspector.setMaximumWidth(405)
        inspector_box=QVBoxLayout(inspector); inspector_box.setContentsMargins(5,9,5,5)
        self.inspector_caption=label('خصائص الفيديو','section'); inspector_box.addWidget(self.inspector_caption)
        self.sidebar=QTabWidget(); self.sidebar.setLayoutDirection(Qt.RightToLeft); inspector_box.addWidget(self.sidebar,1)
        self.main_splitter.addWidget(inspector); self.main_splitter.setSizes([385,735,350]); vertical.addWidget(self.main_splitter)
        bottom=QFrame(); bottom.setObjectName('card'); timeline_box=QVBoxLayout(bottom); timeline_box.setContentsMargins(8,7,8,7); timeline_box.setSpacing(4)
        toolbar=QHBoxLayout()
        for key,tip,fn in [('undo','تراجع · Ctrl+Z',self.undo_audio),('redo','إعادة · Ctrl+Y',self.redo_audio)]:
            b=button('',fn); b.setIcon(icon(key)); b.setToolTip(tip); b.setFixedWidth(38); toolbar.addWidget(b)
        self.summary=label('','muted'); toolbar.addWidget(self.summary,1)
        self.cut_toggle=button('قص الأصل',lambda:None); self.cut_toggle.hide()
        toolbar.addWidget(label('تكبير المسار','muted')); self.track_zoom=QSlider(Qt.Horizontal); self.track_zoom.setRange(100,800); self.track_zoom.setValue(100); self.track_zoom.setMaximumWidth(105); toolbar.addWidget(self.track_zoom); timeline_box.addLayout(toolbar)
        self.source_cut=QWidget(); source_box=QVBoxLayout(self.source_cut); source_box.setContentsMargins(0,0,0,0)
        self.track=self.timeline_type(); self.track.setMinimumHeight(78); self.track.setMaximumHeight(78); self.track.seek.connect(self.seek_source); self.track.startChanged.connect(lambda v:self.trim_in.setValue(v)); self.track.endChanged.connect(lambda v:self.trim_out.setValue(v)); self.track.freezeChanged.connect(self.set_freeze); source_box.addWidget(self.track)
        self.trim_toolbar=QWidget(); trimrow=QHBoxLayout(self.trim_toolbar); trimrow.setContentsMargins(0,0,0,0)
        for text,fn in [('البداية من هنا',self.mark_in),('النهاية هنا',self.mark_out),('تجميد هنا',self.mark_freeze)]: trimrow.addWidget(button(text,fn))
        trimrow.addStretch(); source_box.addWidget(self.trim_toolbar); self.source_cut.hide(); timeline_box.addWidget(self.source_cut)
        self.audio_timeline=AudioTimeline(); self.audio_timeline.scroll.setMaximumHeight(16777215); self.audio_timeline.setMinimumHeight(180)
        self.audio_timeline.canvas.clips=self.audio_clips; self.audio_timeline.canvas.tracks=self.audio_tracks; self.audio_timeline.canvas.effects=self.effect_clips
        self.audio_timeline.canvas.video_clips=self.video_clips; self.audio_timeline.canvas.lane_states=self.lane_states
        self.audio_timeline.seek.connect(self.seek_output); self.audio_timeline.selected.connect(self.select_audio_clip); self.audio_timeline.changed.connect(self.clips_changed); self.track_zoom.valueChanged.connect(self.audio_timeline.set_zoom)
        canvas=self.audio_timeline.canvas; canvas.statusMessage.connect(lambda text:self.status.setText(text)); canvas.effectSelected.connect(self.select_effect); canvas.addEffect.connect(self.add_effect); canvas.addAudio.connect(self.add_audio_at); canvas.addTrack.connect(self.add_empty_track); canvas.trackSelected.connect(self.select_audio_track); canvas.videoSelected.connect(self.select_video)
        canvas.gameSelected.connect(self.select_game_audio); canvas.musicSelected.connect(self.select_music_audio)
        canvas.selectedVideo.connect(self.select_video_event); canvas.selectionChanged.connect(self.timeline_selection_changed)
        canvas.trimStart.connect(lambda t:self.trim_in.setValue(min(t,self.trim_out.value()-.25))); canvas.trimEnd.connect(lambda t:self.trim_out.setValue(max(t,self.trim_in.value()+.25))); canvas.freezeHere.connect(self.set_freeze); canvas.fileDropped.connect(self.drop_asset)
        timeline_box.addWidget(self.audio_timeline,1); vertical.addWidget(bottom); vertical.setSizes([560,360])
        self.make_edit_tab(); self.make_background_tab(); self.make_audio_tab(); self.make_export_tab(); self.make_effect_tab()
        canvas.gameMute.connect(lambda muted:self.game_enabled.setChecked(not muted)); canvas.musicMute.connect(self.music_mute.setChecked)
        footer=QHBoxLayout(); self.status=label('كلك يمين على المسارات للإضافة والتحكم. كل تعديل يظهر مباشرة.','muted'); footer.addWidget(self.status,1)
        self.preview_btn=QPushButton(''); self.preview_btn.hide(); self.cancel_btn=button('إلغاء',self.cancel_job); self.cancel_btn.hide(); footer.addWidget(self.cancel_btn)
        root.addLayout(footer); self.progress=QProgressBar(); self.progress.setRange(0,1000); self.progress.setTextVisible(False); self.progress.setMaximumHeight(4); root.addWidget(self.progress)


    def volume_control(self,box,title,value):
        row=QHBoxLayout(); row.addWidget(label(title,'muted'))
        slider=QSlider(Qt.Horizontal); slider.setRange(0,200); slider.setValue(value)
        spin=QSpinBox(); spin.setRange(0,200); spin.setSuffix('%'); spin.setValue(value); spin.setMaximumWidth(80)
        slider.valueChanged.connect(spin.setValue); spin.valueChanged.connect(slider.setValue); spin.valueChanged.connect(self.invalidate)
        row.addWidget(slider,1); row.addWidget(spin); box.addLayout(row); return spin


    def make_export_tab(self):
        super().make_export_tab()
        box=self.sidebar.widget(3).widget().layout()
        for i in range(box.count()):
            w=box.itemAt(i).widget()
            if isinstance(w,QLabel):
                if 'زر المعاينة' in w.text(): w.setText('شاهد التعديلات مباشرة. التصدير يحفظ MP4 بالجودة التي تختارها. ترميم HUD الزمني في التصدير يستخدم الإطارات المجاورة؛ العرض المباشر يستخدم ترميمًا سريعًا.')
                elif w.text()=='معاينة قبل التصدير': w.setText('عرض مباشر أثناء التحرير')

    def make_edit_tab(self):
        box=self.tab('الفيديو'); self.make_video_properties(box); trim=Section('قص الأصل',True); box.addWidget(trim); trim.hide()
        form=QFormLayout(); self.trim_in=seconds(); self.trim_out=seconds(); self.freeze=seconds(3)
        for text,w in [('البداية',self.trim_in),('النهاية',self.trim_out)]: form.addRow(text,w); w.valueChanged.connect(self.edit_changed)
        trim.box.addLayout(form)
        # Retained fields load old projects; new changes are timeline instances.
        self.animation=QCheckBox(); self.motion_section=Section('',False); self.rotation_section=Section('',False)
        self.motion_template=QComboBox()
        for key,title,glyph in MOTION_TEMPLATES: self.motion_template.addItem(title,key)
        self.motion_duration=seconds(3); self.motion_strength=QSlider(Qt.Horizontal); self.motion_strength.setRange(0,100); self.motion_strength.setValue(65)
        self.angle=QDoubleSpinBox(); self.angle.setRange(0,1080); self.angle.setValue(360); self.direction=QComboBox(); self.direction.addItem('مع عقارب الساعة',-1); self.direction.addItem('عكس عقارب الساعة',1)
        self.options={key:QCheckBox() for key in ('slowmo','clarity','flash','glow','scan','shake','bars')}
        add=Section('إضافة تأثير عند المؤشر',True); box.addWidget(add); grid=QGridLayout()
        for i,(kind,title) in enumerate(EFFECT_NAMES.items()):
            if kind=='background': continue
            grid.addWidget(button('+ '+title,lambda checked=False,k=kind:self.add_effect(k)),i//2,i%2)
        add.box.addLayout(grid); add.box.addWidget(label('تقدر تضيف كل تأثير أكثر من مرة. اسحب مقطعه أو طرفيه فوق مسار الفيديو، أو كلك يمين لخصائصه.','muted'))
        hud=Section('إخفاء HUD',False); box.addWidget(hud); self.hud_enabled=QCheckBox('إخفاء المناطق المحددة'); self.hud_enabled.toggled.connect(self.invalidate); hud.box.addWidget(self.hud_enabled)
        self.hud_method=QComboBox(); self.hud_method.addItem('ترميم زمني','temporal'); self.hud_method.addItem('ترميم سريع','fast'); self.hud_method.currentIndexChanged.connect(self.invalidate); hud.box.addWidget(self.hud_method)
        hud.box.addWidget(label('الكادر يبقى كاملًا. الترميم في المناطق المختارة تقديري.','muted')); box.addStretch()

    def motion_changed(self,*args):
        if hasattr(self,'rotation_section'): self.rotation_section.setVisible(self.motion_template.currentData()=='spin')
        self.invalidate()

    def make_background_tab(self):
        box=self.tab('الخلفية'); box.addWidget(label('اختَر القالب من المكتبة على اليسار.','muted'))
        template=Section('إعدادات القالب',True); box.addWidget(template); form=QFormLayout()
        self.bg_strength=QSlider(Qt.Horizontal); self.bg_strength.setRange(0,100); self.bg_strength.setValue(65); self.bg_strength.valueChanged.connect(self.invalidate)
        self.bg_speed=QDoubleSpinBox(); self.bg_speed.setRange(.1,4); self.bg_speed.setValue(1); self.bg_speed.setSingleStep(.1); self.bg_speed.setSuffix(' ×'); self.bg_speed.valueChanged.connect(self.invalidate)
        self.bg_color_btn=button('اختيار اللون',self.pick_background_color)
        self.bg_scope=QComboBox(); self.bg_scope.addItem('أثناء التجميد','freeze'); self.bg_scope.addItem('كل الفيديو مع التتبع','all'); self.bg_scope.currentIndexChanged.connect(self.invalidate)
        self.bg_scope.hide()
        for text,w in [('القوة',self.bg_strength),('السرعة',self.bg_speed),('اللون',self.bg_color_btn)]: form.addRow(text,w)
        template.box.addLayout(form)
        look=Section('الغباش والإضاءة',True); box.addWidget(look); form=QFormLayout()
        self.bg_blur=QDoubleSpinBox(); self.bg_blur.setRange(0,40); self.bg_blur.setSingleStep(2); self.bg_blur.valueChanged.connect(self.invalidate)
        self.bg_dim=QSpinBox(); self.bg_dim.setRange(0,85); self.bg_dim.setSuffix('%'); self.bg_dim.valueChanged.connect(self.invalidate)
        form.addRow('الغباش',self.bg_blur); form.addRow('التعتيم',self.bg_dim); look.box.addLayout(form); self.bg_options={}
        for key,text in [('background_desaturate','خلفية أبيض وأسود'),('background_glow','وهج'),('background_particles','غبار ضوئي إضافي')]:
            w=QCheckBox(text); w.toggled.connect(self.invalidate); self.bg_options[key]=w; look.box.addWidget(w)
        selection=Section('حدود العنصر المحمي',False); box.addWidget(selection); form=QFormLayout()
        self.mask_feather=QDoubleSpinBox(); self.mask_feather.setRange(0,20); self.mask_feather.setValue(2); self.mask_feather.setSingleStep(.5); self.mask_feather.setSuffix(' px'); self.mask_feather.valueChanged.connect(self.invalidate); form.addRow('نعومة الحدود',self.mask_feather); selection.box.addLayout(form)
        for control in (self.bg_strength,self.bg_speed,self.bg_blur,self.bg_dim,self.mask_feather): control.valueChanged.connect(self.background_fields_changed)
        for control in self.bg_options.values(): control.toggled.connect(self.background_fields_changed)
        box.addWidget(button('توقيت الخلفية المحددة',lambda:self.select_effect(self.selected_effect()['id']) if self.selected_effect() else None))
        selection.box.addWidget(label('حماية العنصر فوق شاشة الفيديو. تكبير الشاشة لا يغيّر حجم الفيديو النهائي.','muted')); box.addStretch()

    def make_audio_tab(self):
        box=self.tab('الصوت'); self.track_section=Section('إعدادات المسار',False); box.addWidget(self.track_section)
        self.track_name=QLineEdit(); self.track_name.setPlaceholderText('اسم المسار'); self.track_name.editingFinished.connect(self.track_fields_changed); self.track_section.box.addWidget(self.track_name)
        self.track_volume=self.volume_control(self.track_section.box,'صوت المسار',100); self.track_volume.valueChanged.connect(self.track_fields_changed)
        row=QHBoxLayout(); self.track_mute=QCheckBox('كتم المسار'); self.track_lock=QCheckBox('قفل المسار')
        for w in (self.track_mute,self.track_lock): w.toggled.connect(self.track_fields_changed); row.addWidget(w)
        self.track_section.box.addLayout(row)
        self.clip_section=Section('المقطع المحدد',True); box.addWidget(self.clip_section)
        self.clip_combo=QComboBox(); self.clip_combo.currentIndexChanged.connect(self.audio_combo_changed); self.clip_section.box.addWidget(self.clip_combo)
        form=QFormLayout(); self.clip_time=seconds(); self.clip_in=seconds(); self.clip_out=seconds()
        for text,w in [('مكانه على المسار',self.clip_time),('بداية الصوت',self.clip_in),('نهاية الصوت',self.clip_out)]: form.addRow(text,w); w.valueChanged.connect(self.audio_fields_changed)
        self.clip_section.box.addLayout(form); self.clip_volume=self.volume_control(self.clip_section.box,'صوت المقطع',100); self.clip_volume.valueChanged.connect(self.audio_fields_changed)
        row=QHBoxLayout(); self.clip_mute=QCheckBox('كتم المقطع'); self.clip_lock=QCheckBox('قفل المقطع')
        for w in (self.clip_mute,self.clip_lock): w.toggled.connect(self.audio_fields_changed); row.addWidget(w)
        self.clip_section.box.addLayout(row)
        fade=Section('دخول وخروج تدريجي',False); self.clip_section.box.addWidget(fade); form=QFormLayout()
        self.clip_fade_in=seconds(); self.clip_fade_out=seconds()
        for text,w in [('الدخول',self.clip_fade_in),('الخروج',self.clip_fade_out)]: form.addRow(text,w); w.valueChanged.connect(self.audio_fields_changed)
        fade.box.addLayout(form)
        source=Section('صوت الفيديو والمؤثرات',False); self.source_audio_section=source; box.addWidget(source)
        self.game_enabled=QCheckBox('صوت الفيديو'); self.game_enabled.setChecked(True); self.game_enabled.toggled.connect(self.invalidate); source.box.addWidget(self.game_enabled); self.game_volume=self.volume_control(source.box,'المستوى',60)
        self.builtin_fx=QCheckBox('مؤثرات التجميد والرجعة'); self.builtin_fx.setChecked(False); self.builtin_fx.toggled.connect(self.invalidate); source.box.addWidget(self.builtin_fx); self.fx_volume=self.volume_control(source.box,'المؤثرات',60); self.builtin_fx.hide(); self.fx_volume.hide(); source.box.addWidget(label('أصوات التجميد والرجعة تختارها من مكتبة الصوت، كمقاطع مستقلة.','muted'))
        music=Section('موسيقى رئيسية',False); self.music_section=music; box.addWidget(music); self.music_path=None; self.music_name=label('لم تضف موسيقى','muted'); music.box.addWidget(self.music_name)
        row=QHBoxLayout(); row.addWidget(button('اختيار',self.pick_music)); row.addWidget(button('إزالة',self.clear_music)); music.box.addLayout(row)
        self.music_start=seconds(); self.drop_time=seconds(); self.drop_enabled=QCheckBox('مزامنة الدروب مع الرجعة')
        form=QFormLayout(); form.addRow('بداية الأغنية',self.music_start); form.addRow('وقت الدروب',self.drop_time); music.box.addLayout(form); music.box.addWidget(self.drop_enabled)
        self.music_volume=self.volume_control(music.box,'الموسيقى',55); self.music_mute=QCheckBox('كتم الموسيقى'); self.music_mute.toggled.connect(self.invalidate); music.box.addWidget(self.music_mute)
        self.music_start.valueChanged.connect(self.invalidate); self.drop_time.valueChanged.connect(self.invalidate); self.drop_enabled.toggled.connect(self.invalidate)
        source.hide(); music.hide()
        box.addWidget(button('إضافة صوت',self.add_fx))
        self.fx_table=QTableWidget(0,3); self.fx_table.hide(); box.addStretch(); self.refresh_audio_fields(); self.refresh_track_fields()

    def editor_menu(self):
        bar=self.menuBar(); bar.setLayoutDirection(Qt.RightToLeft)
        files=bar.addMenu('ملف'); files.addAction('استيراد وسائط…',self.import_media); files.addAction('فتح فيديو…',self.open_video); files.addAction('فتح مشروع…',self.load_project); files.addAction('حفظ المشروع · Ctrl+Shift+S',self.save_project); files.addAction('تصدير…',lambda:self.start_job(False))
        edit=bar.addMenu('تحرير'); edit.addAction('تراجع',self.undo_audio); edit.addAction('إعادة',self.redo_audio); edit.addAction('تقسيم المحدد · Ctrl+S',self.split_audio); edit.addAction('حذف المحدد',lambda:self.audio_timeline.canvas.remove_selected())
        view=bar.addMenu('عرض'); view.addAction('العرض المباشر',lambda:self.switch('live')); view.addAction('الفيديو الأصلي',lambda:self.switch('source'))
        from .help import show_help
        help_menu=bar.addMenu('مساعدة'); help_menu.addAction('طريقة الاستخدام',lambda:show_help(self)); return bar

    def import_media(self):
        if self.busy: return
        audio_only=hasattr(self,'library') and self.library.stack.currentIndex()==3
        filter='Audio (*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.opus)' if audio_only else 'Media (*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.m4a *.aac *.ogg *.flac *.opus *.jpg *.jpeg *.png *.webp *.bmp)'
        paths,_=QFileDialog.getOpenFileNames(self,'استيراد وسائط','',filter)
        for path in paths:
            self.removed_assets.discard(path); self.library.add_file(path); kind=self.library.assets[path]['kind']
            if kind!='image': self.analyze(path,kind=='video')
            if kind=='video' and not self.video: self.choose_video(path)

    def use_asset(self,path):
        self.drop_asset(path,self.output_position,None)

    def drop_asset(self,path,t,track):
        if self.busy: return
        self.removed_assets.discard(path); self.library.add_file(path); kind=self.library.assets[path]['kind']
        if kind=='audio':
            if track is not None and self.audio_timeline.canvas.track_info(track).get('locked'): self.status.setText('أزل قفل المسار أولًا.'); return
            self.audio_timeline.canvas.remember(); self.pending_audio_track=track
            try: self.fx_row(path,t,100)
            finally: self.pending_audio_track=None
            self.clips_changed(); self.select_audio_clip(self.audio_clips[-1]['id'])
        elif kind=='image':
            self.background_media=path; self.selected_background='media'; self.background_buttons['media'].setChecked(True); self.add_effect('background',t=t,params=self.background_params()); self.sidebar.setCurrentIndex(1)
        else: self.choose_video(path)

    def library_page(self,key):
        if key in ('motion','mask'): self.sidebar.setCurrentIndex(0)
        elif key=='background': self.sidebar.setCurrentIndex(1)
        elif key=='audio': self.sidebar.setCurrentIndex(2)
        elif key=='export': self.sidebar.setCurrentIndex(3)

    def choose_motion(self,key):
        self.add_effect('motion',params=dict(motion_style=key,orbit_angle=360,rotation_direction=1,motion_strength=.65))

    def select_video(self):
        self.inspector_caption.setText('خصائص الفيديو'); self.sidebar.setCurrentIndex(0)

    def select_game_audio(self):
        self.sidebar.setCurrentIndex(2); self.inspector_caption.setText('صوت الفيديو'); self.source_audio_section.set_open(True); self.clip_section.set_open(False); self.track_section.set_open(False)

    def select_music_audio(self):
        self.sidebar.setCurrentIndex(2); self.inspector_caption.setText('الموسيقى الرئيسية'); self.music_section.set_open(True); self.clip_section.set_open(False); self.track_section.set_open(False)

    def add_track_model(self,ident=None):
        if ident is None: ident=max([int(t['id']) for t in self.audio_tracks if str(t['id']).isdigit()]+[0])+1
        if not any(t['id']==ident for t in self.audio_tracks): self.audio_tracks.append({'id':ident,'name':f'صوت {len(self.audio_tracks)+1}','volume':1.0,'muted':False,'locked':False})
        return ident

    def add_empty_track(self):
        if self.busy: return
        self.audio_timeline.canvas.remember(); ident=self.add_track_model(); self.select_audio_track(ident); self.clips_changed()

    def add_audio_at(self,t,track):
        if self.audio_timeline.canvas.track_info(track).get('locked',False): self.status.setText('أزل قفل المسار أولًا.'); return
        self.pending_audio_time=t; self.pending_audio_track=track
        try: self.add_fx()
        finally: self.pending_audio_time=self.pending_audio_track=None

    def select_audio_track(self,ident):
        self.selected_track_id=ident; self.audio_timeline.canvas.selected_id=None
        self.clip_combo.blockSignals(True); self.clip_combo.setCurrentIndex(-1); self.clip_combo.blockSignals(False)
        track=self.audio_timeline.canvas.track_info(ident); self.inspector_caption.setText('مسار: '+track.get('name','الصوت'))
        self.sidebar.setCurrentIndex(2); self.track_section.set_open(True); self.clip_section.set_open(False); self.refresh_track_fields(); self.refresh_audio_fields()

    def refresh_track_fields(self):
        if not hasattr(self,'track_name'): return
        self.syncing_track=True; track=self.audio_timeline.canvas.track_info(self.selected_track_id)
        for w in (self.track_name,self.track_volume,self.track_mute,self.track_lock): w.setEnabled(bool(track))
        if track.get('locked'):
            for w in (self.track_name,self.track_volume,self.track_mute): w.setEnabled(False)
        self.track_name.setText(track.get('name','')); self.track_volume.setValue(round(track.get('volume',1)*100)); self.track_mute.setChecked(track.get('muted',False)); self.track_lock.setChecked(track.get('locked',False)); self.syncing_track=False

    def track_fields_changed(self,*args):
        if self.syncing_track: return
        track=self.audio_timeline.canvas.track_info(self.selected_track_id)
        if not track: return
        if track.get('locked') and self.sender() is not self.track_lock: self.refresh_track_fields(); return
        self.audio_timeline.canvas.remember(); track.update(name=self.track_name.text() or 'صوت',volume=self.track_volume.value()/100,muted=self.track_mute.isChecked(),locked=self.track_lock.isChecked())
        self.refresh_audio_fields(); self.invalidate()

    def redo_audio(self):
        if not self.busy: self.audio_timeline.canvas.redo()

    def config(self):
        cfg=super().config()
        cfg.effect_clips=copy.deepcopy(self.effect_clips); cfg.builtin_fx=False
        cfg.video_clips=copy.deepcopy(self.video_clips); cfg.timeline_duration=self.timeline_duration; cfg.lane_states=copy.deepcopy(self.lane_states)
        if self.info: cfg.trim_start=0; cfg.trim_end=self.info['duration']
        cfg.game_audio=False; cfg.music=None
        if self.lane_states.get('video',{}).get('hidden'):
            for clip in cfg.video_clips: clip['muted']=True
        for clip in cfg.effect_clips:
            key='motion' if clip['kind'] in ('freeze','slowmo','motion') else 'background' if clip['kind']=='background' else 'visual'
            if self.lane_states.get(key,{}).get('hidden'): clip['muted']=True
        cfg.sound_effects=copy.deepcopy(self.audio_clips)
        cfg.audio_tracks=copy.deepcopy(self.audio_tracks)
        cfg.protected_polygons=copy.deepcopy(self.view.protected)
        if hasattr(self,'motion_template'): cfg.motion_style=self.motion_template.currentData() or 'spin'
        if hasattr(self,'motion_strength'): cfg.motion_strength=self.motion_strength.value()/100
        cfg.background_template=getattr(self,'selected_background','none')
        cfg.background_color=self.background_color; cfg.background_media=self.background_media
        if hasattr(self,'music_mute'): cfg.music_muted=self.music_mute.isChecked()
        if hasattr(self,'bg_strength'): cfg.background_strength=self.bg_strength.value()/100
        if hasattr(self,'bg_speed'): cfg.background_speed=self.bg_speed.value()
        if hasattr(self,'mask_feather'): cfg.mask_feather=self.mask_feather.value()
        return cfg

    def update_summary(self):
        if self.info and hasattr(self,'summary') and hasattr(self,'quality'):
            cfg=self.config(); plan=edit_plan(cfg,self.info)
            self.summary.setText(f'المصدر: {self.trim_out.value()-self.trim_in.value():.2f} ث  ·  المونتاج: {plan["duration"]:.2f} ث')

    def edit_changed(self,*args):
        if not self.info: return
        start,end=self.trim_in.value(),self.trim_out.value()
        if end-start<.25: return
        if not self.loading_project:
            self.pause_live(); kept=[]
            for clip in self.effect_clips:
                if clip['kind']=='freeze' and not start<=clip['source_time']<end: continue
                if clip['kind']=='slowmo':
                    a=max(start,clip['source_time']); b=min(end,clip['source_time']+clip['source_duration'])
                    if b-a<.02: continue
                    clip.update(source_time=a,source_duration=b-a)
                kept.append(clip)
            self.effect_clips[:]=kept
            if self.protected_time is not None and not start<=self.protected_time<end:
                self.protected_time=start
            if self.live_audio: self.live_audio.discontinuity()
        self.freeze.setRange(start,max(start,end-1/self.info['fps']))
        self.refresh_effect_combo(); self.refresh_effect_fields(); self.update_track(); self.invalidate()

    def update_track(self):
        if not hasattr(self,'track') or not self.info: return
        self.track.duration=self.info['duration']; self.track.start=self.trim_in.value(); self.track.end=self.trim_out.value()
        self.track.freeze=self.freeze.value(); self.track.position=self.source_position
        self.track.editable=not self.busy; self.track.update()
        if hasattr(self,'quality') and hasattr(self,'audio_timeline'):
            cfg=self.config(); canvas=self.audio_timeline.canvas; plan=edit_plan(cfg,self.info)
            canvas.duration=plan['duration']; canvas.position=self.output_position; canvas.video_cfg=(cfg,self.info)
            canvas.game_audio=cfg.game_audio and self.info['audio']; canvas.enabled_edit=not self.busy
            canvas.music={'path':cfg.music,'source_in':cfg.music_start,'source_duration':self.analysis_cache.get(cfg.music,{}).get('duration',plan['duration']),'muted':cfg.music_muted} if cfg.music else None
            if canvas.drag is None: canvas.resize_content()
            else: canvas.update()

    def invalidate(self,*args):
        self.revision+=1; self.frame_revision+=1
        if hasattr(self,'quality'): self.update_summary(); self.update_track()
        if self.live_frames and self.info and not self.loading_project:
            self.refresh_timer.start(30)
            self.fx_timer.start(400)
            if hasattr(self,'status'):
                self.status.setText('التعديل يظهر مباشرة.' if self.mode!='source' else 'تحديد على الفيديو الأصلي — اضغط «عرض مباشر» لرؤية مؤثراتك.')

    def choose_video(self,path):
        if self.busy: return
        self.pause_live()
        super().choose_video(path)
        if not self.loading_project:
            self.effect_clips.clear(); self.seed_video()
        self.audio_timeline.canvas.history.clear(); self.audio_timeline.canvas.future.clear(); self.selected_track_id=None
        self.library.add_file(self.video,'video')
        self.game_enabled.setEnabled(self.info['audio'])
        self.view.history.clear()
        self.output_position=0; self.source_position=0; self.mode='live'; self.view.source=False; self.view.fit()
        self.original_btn.setChecked(False); self.draft_btn.setChecked(True); self.draft_btn.setEnabled(True); self.mode_label.setText('LIVE')
        self.refresh_audio_combo(); self.analyze(path,True)
        self.refresh_audio_fields(); self.refresh_track_fields()
        proxy=EditProxy(self.video,self); self.proxy_jobs.append(proxy)
        proxy.ready.connect(self.proxy_ready)
        proxy.finished.connect(lambda j=proxy:self.proxy_finished(j)); proxy.start()
        self.status.setText('جاهز — اسحب المؤشر، وكل تعديل يظهر مباشرة.')
        self.invalidate(); self.request_live(); self.prepare_fx()

    def request_live(self):
        if not self.live_frames or not self.info or self.busy: return
        original=self.mode=='source'
        position=self.source_position if original else self.output_position
        height=max(self.live_quality.currentData(),min(self.info['height'],1080)) if original else self.live_quality.currentData()
        self.live_frames.request(self.config(),self.info,position,original,height,self.frame_revision)

    def proxy_ready(self,path,proxy,info):
        if self.live_frames:
            self.live_frames.set_proxy(path,proxy,info); self.request_live()

    def proxy_finished(self,job):
        if job in self.proxy_jobs: self.proxy_jobs.remove(job)
        job.deleteLater()

    def live_frame_ready(self,frame,source,output,revision):
        if revision!=self.frame_revision or self.busy: return
        import cv2
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
        image=QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format_RGB888).copy()
        self.view.frame(image)
        if self.mode!='source' and not self.live_playing: self.source_position=source
        self.update_positions()

    def tick(self):
        now=time.monotonic(); dt=max(0,now-self.last_tick); self.last_tick=now
        if not self.info or self.busy: return
        cfg=self.config(); plan=edit_plan(cfg,self.info)
        if self.live_playing:
            if self.mode=='source':
                self.source_position=min(self.info['duration'],self.source_position+dt)
                self.output_position=min(plan['duration'],source_to_output(cfg,self.info,self.source_position))
                at_end=self.source_position>=self.info['duration']
            else:
                self.output_position=min(plan['duration'],self.output_position+dt)
                self.source_position=sample_output(cfg,self.info,self.output_position)[0]
                at_end=self.output_position>=plan['duration']
            if at_end: self.pause_live()
            self.request_live(); self.update_positions()
        if self.live_audio:
            self.live_audio.sync(cfg,self.info,self.source_position if self.mode=='source' else self.output_position,
                                 self.source_position,self.mode=='source',self.live_playing)

    def update_positions(self):
        if not self.info: return
        self.track.position=self.source_position; self.track.update()
        canvas=self.audio_timeline.canvas; canvas.position=self.output_position; canvas.update()
        total=self.info['duration'] if self.mode=='source' else edit_plan(self.config(),self.info)['duration']
        current=self.source_position if self.mode=='source' else self.output_position
        self.clock.setText(f'{stamp(current)} / {stamp(total)}')

    def pause_live(self):
        self.live_playing=False
        if hasattr(self,'play_btn'): self.play_btn.setText(''); self.play_btn.setIcon(icon('play'))
        if self.live_audio: self.live_audio.pause()
        if hasattr(self,'player'): self.player.pause()

    def toggle_play(self):
        if not self.video or self.busy: return
        self.commit_selection()
        if self.live_playing: self.pause_live(); return
        self.view.protecting=self.view.selecting=False
        self.protect_btn.setChecked(False); self.select_hud.setChecked(False)
        if self.mode=='source':
            self.output_position=source_to_output(self.config(),self.info,self.source_position)
            self.switch('live')
        total=edit_plan(self.config(),self.info)['duration']
        if self.output_position>=total-.02: self.output_position=0
        self.live_playing=True; self.last_tick=time.monotonic(); self.play_btn.setText(''); self.play_btn.setIcon(icon('pause')); self.request_live()

    def seek(self,t): self.seek_source(t)

    def seek_source(self,t):
        if not self.info: return
        self.pause_live(); self.commit_selection()
        self.mode='source'; self.view.source=True; self.view.protecting=False; self.protect_btn.setChecked(False)
        self.original_btn.setChecked(True); self.draft_btn.setChecked(False); self.mode_label.setText('SOURCE')
        self.source_position=max(0,min(self.info['duration']-.001,t))
        self.output_position=max(0,min(edit_plan(self.config(),self.info)['duration'],source_to_output(self.config(),self.info,self.source_position)))
        self.frame_revision+=1; self.request_live(); self.update_positions()

    def seek_output(self,t):
        if not self.info: return
        self.commit_selection(); self.pause_live()
        if self.live_audio: self.live_audio.discontinuity()
        self.mode='live'; self.view.source=False; self.view.protecting=self.view.selecting=False
        self.protect_btn.setChecked(False); self.select_hud.setChecked(False)
        self.draft_btn.setChecked(True); self.original_btn.setChecked(False); self.mode_label.setText('LIVE')
        self.output_position=max(0,min(edit_plan(self.config(),self.info)['duration'],t))
        self.source_position=sample_output(self.config(),self.info,self.output_position)[0]
        self.frame_revision+=1; self.request_live(); self.update_positions()

    def step(self,direction):
        if not self.info: return
        if self.mode=='source': self.seek_source(self.source_position+direction/self.info['fps'])
        else: self.seek_output(self.output_position+direction/self.fps.currentData())

    def position_changed(self,ms): pass
    def duration_changed(self,ms): pass
    def media_status(self,status): pass
    def play_state(self,state): pass
    def video_frame(self,frame): pass

    def source_here(self):
        self.pause_live(); return self.source_position

    def switch(self,mode):
        if not self.video or self.busy: return
        self.pause_live(); self.commit_selection()
        if self.live_audio: self.live_audio.discontinuity()
        if mode!='source':
            if self.mode=='source': self.output_position=source_to_output(self.config(),self.info,self.source_position)
            self.mode='live'; self.view.source=False; self.view.protecting=self.view.selecting=False
            self.protect_btn.setChecked(False); self.select_hud.setChecked(False)
        else: self.mode='source'; self.view.source=True
        self.draft_btn.setChecked(self.mode=='live'); self.original_btn.setChecked(self.mode=='source'); self.mode_label.setText('LIVE' if self.mode=='live' else 'SOURCE')
        self.frame_revision+=1; self.request_live(); self.update_positions()

    def select_regions(self):
        self.pause_live(); self.commit_selection()
        checked=self.select_hud.isChecked()
        if checked:
            self.mode='source'; self.view.source=True; self.draft_btn.setChecked(False); self.original_btn.setChecked(True)
            self.protect_btn.setChecked(False); self.view.protecting=False; self.mode_label.setText('HUD')
        self.view.selecting=checked; self.view.setCursor(Qt.CrossCursor if checked else Qt.ArrowCursor)
        self.frame_revision+=1; self.request_live(); self.view.update()

    def select_objects(self):
        if not self.video or self.busy: return
        self.pause_live(); self.commit_selection()
        checked=self.protect_btn.isChecked()
        if checked:
            self.mode='source'; self.view.source=True; self.select_hud.setChecked(False); self.view.selecting=False
            self.show_hud.setChecked(False); self.draft_btn.setChecked(False); self.original_btn.setChecked(True); self.mode_label.setText('MASK')
            self.source_position=self.protected_time if self.view.protected and self.protected_time is not None else self.freeze.value()
            self.sidebar.setCurrentIndex(1)
        self.view.protecting=checked; self.view.setCursor(Qt.CrossCursor if checked else Qt.ArrowCursor)
        self.hud_hint.setText('اضغط حول العنصر. اسحب النقاط لتعديلها. Enter أو «تثبيت التحديد» لإكماله؛ عند الخروج يثبت تلقائيًا.')
        self.frame_revision+=1; self.request_live(); self.view.update()

    def commit_selection(self):
        if hasattr(self,'view') and len(self.view.polygon)>=3:
            self.view.finish_object()

    def finish_selection(self):
        if not self.video: return
        self.commit_selection(); self.switch('live')

    def drawing_changed(self,count):
        if count:
            self.object_label.setText(f'رسم جاري · {count} نقاط — يثبت عند الخروج')
        else: self.object_label.setText(f'{len(self.view.protected)} عناصر محمية' if self.view.protected else 'بدون عنصر محمي')

    def objects_changed(self,polygons):
        self.view.protected=copy.deepcopy(polygons)
        if polygons and self.protected_time is None: self.protected_time=self.source_position
        if not polygons: self.protected_time=None
        self.object_label.setText(f'{len(polygons)} عناصر محمية' if polygons else 'بدون عنصر محمي')
        self.invalidate(); self.view.update()

    def regions_changed(self,regions):
        self.view.regions=copy.deepcopy(regions)
        self.region_label.setText(f'{len(regions)} مناطق HUD'); self.hud_enabled.setChecked(bool(regions))
        self.invalidate(); self.view.update()

    def undo_protected(self):
        if not self.busy: self.view.undo()

    def selection_action(self,index):
        actions=[None,self.undo_protected,self.clear_protected,self.undo_region,self.clear_regions]
        if index: actions[index]()
        self.selection_actions.setCurrentIndex(0)

    def update_zoom(self,value):
        self.zoom_slider.blockSignals(True); self.zoom_slider.setValue(value); self.zoom_slider.blockSignals(False)
        self.zoom_label.setText(f'{value}%')

    def master_changed(self,value):
        if self.live_audio: self.live_audio.master=value/100

    def choose_background(self,key,color):
        if self.busy: return
        if key=='media':
            path,_=QFileDialog.getOpenFileName(self,'خلفية صورة أو فيديو','','Media (*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.mkv *.webm)')
            if not path: return
            self.background_media=path
        self.selected_background=key; self.background_color=color
        clip=self.add_effect('background',params=self.background_params())
        if clip: self.sidebar.setCurrentIndex(1); self.inspector_caption.setText('خصائص الخلفية المحددة')

    def pick_background_color(self):
        color=QColorDialog.getColor(QColor(self.background_color),self,'لون القالب')
        if color.isValid(): self.background_color=color.name(); self.background_fields_changed(); self.invalidate()

    def analyze(self,path,video=False):
        if path in self.analysis_cache:
            self.analysis_ready(path,self.analysis_cache[path]); return
        if path in self.analysis: return
        job=MediaAnalysis(path,video,self); self.analysis[path]=job; self.analysis_jobs.append(job)
        job.ready.connect(self.analysis_ready); job.failed.connect(self.analysis_failed)
        job.finished.connect(lambda j=job,p=path:self.analysis_finished(j,p)); job.start()

    def analysis_finished(self,job,path):
        self.analysis.pop(path,None)
        if job in self.analysis_jobs: self.analysis_jobs.remove(job)
        job.deleteLater()

    def analysis_failed(self,path,message):
        self.status.setText('تعذّر قراءة الملف: '+os.path.basename(path)+' — '+message)

    def analysis_ready(self,path,data):
        self.analysis_cache[path]=data
        if path not in self.removed_assets: self.library.metadata(path,data)
        canvas=self.audio_timeline.canvas; canvas.waveforms[path]=data['wave']
        if path==self.video:
            canvas.game_wave=data['wave']; canvas.thumbnails=data['thumbnails']
        for clip in self.audio_clips:
            if clip['path']==path:
                old=clip.get('source_duration')
                clip['source_duration']=data['duration']
                if old is None:
                    clip['duration']=max(.05,data['duration']-clip.get('source_in',0))
                else:
                    clip['duration']=min(clip['duration'],max(.05,(data['duration']-clip['source_in'])/clip.get('rate',1)))
        self.refresh_audio_combo(); self.refresh_audio_fields(); self.invalidate()

    def add_fx(self):
        if self.busy: return
        paths,_=QFileDialog.getOpenFileNames(self,'إضافة مقاطع صوتية','','Audio (*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.opus)')
        if not paths: return
        self.audio_timeline.canvas.remember()
        for path in paths: self.fx_row(path,self.pending_audio_time if self.pending_audio_time is not None else self.output_position,100)
        self.refresh_audio_combo(); self.select_audio_clip(self.audio_clips[-1]['id']); self.invalidate()

    def fx_row(self,path,t,volume,item=None):
        source=copy.deepcopy(item or {})
        source.update(path=path,time=t,volume=volume/100)
        source.setdefault('id',uuid.uuid4().hex); source.setdefault('source_in',0)
        source.setdefault('duration',self.analysis_cache.get(path,{}).get('duration') or 3)
        source.setdefault('source_duration',self.analysis_cache.get(path,{}).get('duration'))
        if source['source_duration'] is None:
            from engine.util import FFPROBE,run
            meta=json.loads(run([FFPROBE,'-v','error','-show_format','-of','json',path]))
            source['source_duration']=float(meta['format']['duration'])
            if not item: source['duration']=max(.05,source['source_duration']-source['source_in'])
        if not item and self.info:
            source['duration']=max(.05,min(source['duration'],edit_plan(self.config(),self.info)['duration']-t))
        source.setdefault('fade_in',0); source.setdefault('fade_out',0)
        source.setdefault('muted',False); source.setdefault('locked',False)
        self.removed_assets.discard(path)
        source['track']=self.add_track_model(source.get('track') if source.get('track') is not None else self.pending_audio_track)
        self.library.add_file(path,'audio')
        self.audio_clips.append(source); self.analyze(path)

    def selected_clip(self):
        ident=self.audio_timeline.canvas.selected_id
        return next((c for c in self.audio_clips if c['id']==ident),None)

    def refresh_audio_combo(self):
        if not hasattr(self,'clip_combo'): return
        ident=self.audio_timeline.canvas.selected_id
        self.clip_combo.blockSignals(True); self.clip_combo.clear()
        for i,clip in enumerate(self.audio_clips): self.clip_combo.addItem(f'{i+1} · {clip.get("display_name",os.path.basename(clip["path"]))}',clip['id'])
        index=self.clip_combo.findData(ident)
        choose_first=False
        self.clip_combo.setCurrentIndex(index if index>=0 else (0 if choose_first else -1)); self.clip_combo.blockSignals(False)
        if choose_first and index<0: self.audio_timeline.canvas.selected_id=self.audio_clips[0]['id']
        self.audio_timeline.canvas.resize_content()

    def audio_combo_changed(self,index):
        if index>=0:
            self.audio_timeline.canvas.select_item('audio',self.clip_combo.itemData(index))
            self.refresh_audio_fields(); self.audio_timeline.canvas.update()

    def select_audio_clip(self,ident):
        canvas=self.audio_timeline.canvas
        if ('audio',ident) not in canvas.selection:
            canvas.select_item('audio',ident); return
        canvas.selected_id=ident
        clip=self.selected_clip()
        if clip:
            self.selected_track_id=clip.get('track'); self.inspector_caption.setText(os.path.basename(clip['path']))
        self.clip_combo.blockSignals(True); self.clip_combo.setCurrentIndex(self.clip_combo.findData(ident)); self.clip_combo.blockSignals(False)
        self.refresh_audio_fields(); self.refresh_track_fields(); self.clip_section.set_open(True); self.sidebar.setCurrentIndex(2)

    def refresh_audio_fields(self):
        if not hasattr(self,'clip_time'): return
        clip=self.selected_clip(); self.syncing_clip=True
        controls=(self.clip_time,self.clip_in,self.clip_out,self.clip_volume,self.clip_fade_in,self.clip_fade_out,self.clip_mute,self.clip_lock)
        for w in controls: w.setEnabled(clip is not None)
        if clip:
            length=clip.get('source_duration') or clip['source_in']+clip['duration']
            self.clip_time.setValue(clip['time']); self.clip_in.setMaximum(max(0,length-.05)); self.clip_in.setValue(clip['source_in'])
            self.clip_out.setMaximum(length); self.clip_out.setValue(clip['source_in']+clip['duration']*clip.get('rate',1))
            self.clip_volume.setValue(round(clip['volume']*100)); self.clip_fade_in.setValue(clip.get('fade_in',0)); self.clip_fade_out.setValue(clip.get('fade_out',0))
            self.clip_mute.setChecked(clip.get('muted',False)); self.clip_lock.setChecked(clip.get('locked',False))
            for w in (self.clip_time,self.clip_in,self.clip_out): w.setEnabled(not self.audio_timeline.canvas.locked(clip))
        self.syncing_clip=False

    def audio_fields_changed(self,*args):
        if self.syncing_clip: return
        clip=self.selected_clip()
        if not clip: return
        if self.audio_timeline.canvas.track_info(clip.get('track')).get('locked'): self.refresh_audio_fields(); return
        if clip.get('locked') and self.sender() is not self.clip_lock: self.refresh_audio_fields(); return
        self.audio_timeline.canvas.remember()
        length=clip.get('source_duration') or clip['source_in']+clip['duration']
        start=min(max(0,self.clip_in.value()),max(0,length-.05))
        end=min(length,max(start+.05,self.clip_out.value()))
        clip.update(time=self.clip_time.value(),source_in=start,duration=(end-start)/clip.get('rate',1),
                    volume=self.clip_volume.value()/100,fade_in=self.clip_fade_in.value(),fade_out=self.clip_fade_out.value(),
                    muted=self.clip_mute.isChecked(),locked=self.clip_lock.isChecked())
        self.refresh_audio_fields(); self.invalidate()

    def clips_changed(self):
        self.refresh_audio_combo(); self.refresh_audio_fields(); self.refresh_track_fields(); self.refresh_effect_combo(); self.refresh_effect_fields(); self.refresh_video_fields(); self.invalidate()

    def split_audio(self):
        if self.busy: return
        if self.audio_timeline.canvas.split_selected(): self.clips_changed()

    def undo_audio(self):
        if not self.busy: self.audio_timeline.canvas.undo()

    def remove_fx(self):
        clip=self.selected_clip()
        if not clip: return
        if self.audio_timeline.canvas.locked(clip): self.status.setText('أزل القفل أولًا.'); return
        self.audio_timeline.canvas.remember(); self.audio_clips.remove(clip)
        self.clips_changed()

    def pick_music(self):
        self.add_fx()

    def prepare_fx(self):
        if not self.info or not self.live_audio or self.loading_project: return
        cfg=self.config()
        if cfg.effect_clips is not None or not cfg.animation or not cfg.builtin_fx: self.live_audio.fx_path=None; return
        job=EffectsAudio(cfg,self.info,self)
        if job.key==self.fx_key or any(j.key==job.key for j in self.fx_jobs): job.deleteLater(); return
        self.fx_key=job.key; self.live_audio.fx_path=None; self.fx_jobs.append(job)
        job.ready.connect(self.fx_ready); job.failed.connect(lambda message:self.status.setText('صوت المؤثرات: '+message))
        job.finished.connect(lambda j=job:self.fx_finished(j)); job.start()

    def fx_ready(self,key,path,offset,duration):
        if self.fx_key==key:
            self.live_audio.fx_path=path; self.live_audio.fx_offset=offset; self.live_audio.fx_duration=duration

    def fx_finished(self,job):
        if job in self.fx_jobs: self.fx_jobs.remove(job)
        job.deleteLater()

    def start_job(self,preview=False):
        if preview: self.switch('live'); return
        self.pause_live(); self.commit_selection()
        super().start_job(False)

    def set_busy(self,busy):
        super().set_busy(busy)
        self.library.setEnabled(not busy)
        self.audio_timeline.setEnabled(not busy); self.track_zoom.setEnabled(not busy)
        self.audio_timeline.canvas.enabled_edit=not busy
        self.finish_object_btn.setEnabled(not busy); self.selection_actions.setEnabled(not busy)
        self.play_btn.setEnabled(not busy); self.draft_btn.setEnabled(not busy and bool(self.video))
        self.trim_toolbar.setEnabled(not busy)
        for w in (self.select_hud,self.protect_btn,self.undo_hud,self.clear_hud,self.undo_object,self.clear_object): w.setEnabled(not busy)
        if not busy: self.request_live()

    def save_project(self):
        if not self.video: self.error('افتح فيديو أولًا.'); return
        self.commit_selection()
        path,_=QFileDialog.getSaveFileName(self,'حفظ المشروع',str(Path(self.video).with_suffix('.montage.json')),'Montage project (*.montage.json)')
        if path:
            with open(path,'w',encoding='utf-8') as handle: json.dump({'version':5,'settings':self.project_settings(),'assets':list(self.library.assets)},handle,ensure_ascii=False,indent=2)
            self.status.setText('تم حفظ المشروع مع مسارات الصوت والتحديدات والقوالب.')

    def load_project(self):
        if self.busy: return
        path,_=QFileDialog.getOpenFileName(self,'فتح مشروع','','Montage project (*.json)')
        if not path: return
        try:
            with open(path,encoding='utf-8') as handle: data=json.load(handle)
            known={f.name for f in dataclasses.fields(Config)}
            cfg=Config(**{k:v for k,v in data['settings'].items() if k in known})
            if not os.path.isfile(cfg.video):
                replacement,_=QFileDialog.getOpenFileName(self,'اختر مكان الفيديو الجديد','','Video (*.mp4 *.mov *.mkv *.webm)')
                if not replacement: return
                cfg.video=replacement
            for item in cfg.sound_effects:
                if item.get('builtin') and not os.path.isfile(item['path']): item['path']=builtin_sound(item['builtin'])[0]
            validate(cfg,media_info(cfg.video))
            self.apply_project(cfg)
            for asset in data.get('assets',[]):
                if os.path.isfile(asset): self.library.add_file(asset)
            self.status.setText('تم فتح المشروع، والعرض المباشر جاهز.')
        except Exception as ex: self.error(str(ex))

    def apply_project(self,cfg):
        self.pause_live(); self.loading_project=True
        try:
            self.choose_video(cfg.video)
            restored_audio,restored_tracks=self.migrate_timeline(cfg)
            self.audio_timeline.canvas.clear_selection()
            self.audio_timeline.canvas.selected_effect_id=None
            self.trim_in.setValue(cfg.trim_start); self.trim_out.setValue(cfg.trim_end if cfg.trim_end is not None else self.info['duration'])
            self.freeze.setValue(cfg.freeze_time); self.motion_duration.setValue(cfg.orbit_seconds); self.angle.setValue(cfg.orbit_angle)
            self.direction.setCurrentIndex(self.direction.findData(cfg.rotation_direction)); self.animation.setChecked(cfg.animation)
            self.motion_template.setCurrentIndex(self.motion_template.findData(cfg.motion_style)); self.motion_strength.setValue(round(cfg.motion_strength*100))
            self.regions_changed(cfg.hud_regions); self.hud_enabled.setChecked(cfg.remove_hud); self.hud_method.setCurrentIndex(self.hud_method.findData(cfg.hud_method))
            self.objects_changed(cfg.protected_polygons); self.protected_time=cfg.protected_time
            self.bg_blur.setValue(cfg.background_blur); self.bg_dim.setValue(round(cfg.background_dim*100)); self.bg_scope.setCurrentIndex(self.bg_scope.findData(cfg.background_scope))
            self.selected_background=cfg.background_template; self.background_color=cfg.background_color; self.background_media=cfg.background_media
            self.background_buttons[cfg.background_template].setChecked(True); self.bg_strength.setValue(round(cfg.background_strength*100)); self.bg_speed.setValue(cfg.background_speed); self.mask_feather.setValue(cfg.mask_feather)
            for k,w in self.bg_options.items(): w.setChecked(getattr(cfg,k))
            for k,w in self.options.items(): w.setChecked(getattr(cfg,k))
            self.music_path=cfg.music; self.music_name.setText(os.path.basename(cfg.music) if cfg.music else 'لم تضف موسيقى')
            self.music_start.setValue(cfg.music_start); self.drop_enabled.setChecked(cfg.drop_in_song is not None); self.drop_time.setValue(cfg.drop_in_song or 0)
            self.music_volume.setValue(round(cfg.music_volume*100)); self.game_enabled.setChecked(cfg.game_audio); self.game_volume.setValue(round(cfg.game_volume*100))
            self.music_mute.setChecked(cfg.music_muted)
            self.builtin_fx.setChecked(False); self.fx_volume.setValue(round(cfg.fx_volume*100))
            self.audio_clips.clear()
            self.audio_tracks.clear(); self.audio_tracks.extend(restored_tracks)
            for item in restored_audio: self.fx_row(item['path'],item['time'],item['volume']*100,item)
            if cfg.effect_clips is None and cfg.animation and cfg.builtin_fx:
                path,title,duration=builtin_sound('freeze_mix'); old_plan=edit_plan(cfg,self.info)
                self.fx_row(path,max(0,old_plan['ramp']-.1),cfg.fx_volume*100,item=dict(duration=duration,source_duration=duration,builtin='freeze_mix',display_name=title))
            if cfg.music: self.analyze(cfg.music)
            for combo,value,title in [(self.resolution,cfg.output_height,f'{cfg.output_height}p'),(self.fps,cfg.output_fps,f'{cfg.output_fps} fps'),(self.quality,cfg.quality,cfg.quality)]:
                if combo.findData(value)<0: combo.addItem(title,value)
                combo.setCurrentIndex(combo.findData(value))
            self.selected_track_id=None; self.refresh_effect_combo(); self.refresh_effect_fields(); self.refresh_audio_combo(); self.refresh_audio_fields(); self.refresh_track_fields(); self.output_position=source_to_output(self.config(),self.info,cfg.freeze_time)
            self.source_position=cfg.freeze_time; self.mode='live'; self.view.source=False
        finally: self.loading_project=False
        self.invalidate(); self.request_live(); self.prepare_fx()

    def closeEvent(self,event):
        self.pause_live()
        if self.worker and self.worker.isRunning():
            super().closeEvent(event); return
        for job in self.proxy_jobs: job.cancel.set()
        jobs=list(self.analysis_jobs)+list(self.fx_jobs)+list(self.proxy_jobs)+list(getattr(self,'update_jobs',[]))
        if jobs:
            event.ignore(); self.status.setText('جاري إغلاق ملفات الصوت…'); QTimer.singleShot(200,self.close); return
        if self.live_frames:
            self.live_frames.stop()
            if not self.live_frames.wait(100): event.ignore(); QTimer.singleShot(100,self.close); return
        if self.live_audio: self.live_audio.close()
        self.save_session()
        super().closeEvent(event)

    def save_session(self):
        if not self.video or '--self-test' in sys.argv or os.environ.get('MONTAGEMAKER_TESTING'): return
        self.commit_selection()
        try:
            os.makedirs(CACHE_DIR,exist_ok=True)
            path=os.path.join(CACHE_DIR,'session_v41.json'); temp=path+'.tmp'
            data={'version':5,'settings':self.project_settings(),'position':self.output_position,'assets':list(self.library.assets)}
            with open(temp,'w',encoding='utf-8') as handle: json.dump(data,handle,ensure_ascii=False)
            os.replace(temp,path)
        except OSError: pass

    def restore_session(self):
        path=os.path.join(CACHE_DIR,'session_v41.json')
        if not os.path.isfile(path): path=os.path.join(CACHE_DIR,'session_v4.json')
        if not os.path.isfile(path): path=os.path.join(CACHE_DIR,'session_v3.json')
        if not os.path.isfile(path) or self.video: return
        try:
            with open(path,encoding='utf-8') as handle: data=json.load(handle)
            cfg=Config(**data['settings'])
            if not os.path.isfile(cfg.video): return
            validate(cfg,media_info(cfg.video)); self.apply_project(cfg)
            for asset in data.get('assets',[]):
                if os.path.isfile(asset): self.library.add_file(asset)
            self.seek_output(data.get('position',0)); self.status.setText('تمت استعادة آخر جلسة تحرير.')
        except Exception: self.status.setText('افتح فيديو أو مشروعًا محفوظًا للبدء.')

    def check_update(self):
        from .updater import UpdateCheck
        job=UpdateCheck(self); self.update_jobs.append(job); job.found.connect(self.offer_update)
        job.finished.connect(lambda j=job:self.update_jobs.remove(j)); job.start()

    def offer_update(self,data):
        from PySide6.QtWidgets import QMessageBox
        from .updater import start_update,VERSION
        notes=data.get('notes','')
        answer=QMessageBox.question(self,'تحديث جديد',f'الإصدار {data["version"]} متوفر (عندك {VERSION}).\n{notes}\n\nتحدّث الآن؟ البرنامج رح يسكّر، يتنزّل التحديث ويتنصّب، وبعدين يفتح لحاله.')
        if answer!=QMessageBox.Yes: return
        try: start_update(data)
        except OSError as ex: self.status.setText('تعذّر بدء التحديث: '+str(ex)); return
        self.close()  # closeEvent saves the session before exiting.

    def set_freeze(self,time):
        if self.info: self.add_effect('freeze',source=time)

    def remove_asset(self,path):
        if self.busy: return
        if self.library.remove_file(path):
            self.removed_assets.add(path); self.status.setText('تمت الإزالة من الوسائط؛ المقاطع المستخدمة وملف الجهاز يبقوا محفوظين.')
