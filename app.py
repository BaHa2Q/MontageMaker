"""Montage Maker Studio: Arabic desktop editor with playback and draft previews."""
import dataclasses
import json
import os
import sys
import tempfile
import threading
import traceback
import uuid
from pathlib import Path
import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QKeySequence, QShortcut, QPainterPath, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QDoubleSpinBox, QComboBox, QCheckBox, QSlider,
    QProgressBar, QMessageBox, QScrollArea, QFormLayout, QFrame, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit,
    QButtonGroup, QSpinBox,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
from engine.editing import Config, media_info, timeline, validate
from engine.pipeline import run_montage
from engine.hud import HUDCleaner, region_mask
from engine.util import APP_DIR, CACHE_DIR, Cancelled


STYLE = '''
QWidget { background: #101319; color: #e9edf5; font-family: "Segoe UI"; font-size: 13px; }
QMainWindow { background: #101319; }
QLabel#brand { font-size: 23px; font-weight: 700; letter-spacing: 2px; }
QLabel#muted { color: #8d97aa; }
QLabel#section { color: #f5f6fb; font-size: 14px; font-weight: 600; }
QLabel#pill { background: #24352f; color: #90e7b4; border-radius: 10px; padding: 5px 10px; }
QFrame#card { background: #191e28; border: 1px solid #29313e; border-radius: 12px; }
QFrame#card QLabel { background: transparent; }
QPushButton { background: #252d3b; border: 1px solid #364153; padding: 9px 12px; border-radius: 7px; font-weight: 600; }
QPushButton:hover { background: #323e50; border-color: #69778b; }
QPushButton:pressed, QPushButton:checked { background: #334750; border-color: #61d6b2; color: #92f3d4; }
QPushButton:disabled { color: #576070; background: #1b222c; border-color: #262e3a; }
QPushButton#accent { background: #73e0bd; color: #0a2520; border-color: #73e0bd; }
QPushButton#accent:hover { background: #a0efd4; }
QPushButton#accent:disabled { background: #28453f; color: #56776e; border-color: #28453f; }
QPushButton#play { background: #73e0bd; color: #0a2520; min-width: 64px; }
QDoubleSpinBox, QSpinBox, QComboBox { background: #111720; border: 1px solid #384455; border-radius: 6px; padding: 6px; min-height: 20px; }
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus { border-color: #73e0bd; }
QComboBox QAbstractItemView { background: #1b2330; selection-background-color: #364c4b; }
QCheckBox { padding: 4px 0; spacing: 7px; background: transparent; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #566479; border-radius: 4px; background: #111720; }
QCheckBox::indicator:checked { background: #73e0bd; border-color: #73e0bd; }
QSlider::groove:horizontal { height: 5px; background: #303b4c; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -4px 0; background: #73e0bd; border-radius: 6px; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #191f29; padding: 9px 14px; margin: 2px; border-radius: 6px; color: #a4adbd; }
QTabBar::tab:selected { background: #2c3e40; color: #91e8cc; }
QScrollArea { border: 0; }
QScrollBar:vertical { width: 7px; background: #161b24; }
QScrollBar::handle:vertical { background: #3d4a5f; min-height: 24px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QProgressBar { background: #222c3a; border: 0; height: 7px; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: #73e0bd; border-radius: 3px; }
QPlainTextEdit, QTableWidget { background: #111721; border: 1px solid #303b4b; border-radius: 7px; }
QHeaderView::section { background: #242e3d; padding: 7px; border: 0; }
QSplitter::handle { background: #101319; width: 12px; }
QMenuBar { background: #0c121b; padding: 4px; }
QMenuBar::item { padding: 5px 15px; }
QMenuBar::item:selected, QMenu::item:selected { background: #254b68; }
QMenu { background: #152033; border: 1px solid #364963; padding: 5px; }
QMenu::item { padding: 8px 25px; }
QLineEdit { background: #111b2b; border: 1px solid #33445e; border-radius: 6px; padding: 7px; }
QWidget#library { background: #111b27; border-radius: 9px; }
QWidget#navrail { background: #0c141f; border-radius: 8px; }
QToolButton#navitem { background: transparent; border: 0; border-radius: 7px; padding: 8px; color: #9fb0c7; }
QToolButton#navitem:hover { background: #1b2d44; }
QToolButton#navitem:checked { background: #123653; color: #71dbff; border-left: 3px solid #26bfff; }
QPushButton#sectionheader { background: #182435; text-align: right; padding: 8px; border: 0; color: #baccdf; }
QPushButton#sectionheader:checked { background: #213149; color: #f0f5ff; }
QPushButton#templatecard { background: #1e2c41; padding: 10px; border-radius: 7px; }
QPushButton#accent { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #079fe8,stop:1 #6550ff); color: white; border: 0; }
QListWidget { background: #111b27; border: 0; }
QListWidget::item { padding: 4px; border-radius: 5px; }
QListWidget::item:selected { background: #204965; border: 1px solid #46b8ff; }
'''


def stamp(seconds):
    seconds = max(0, seconds)
    return f'{int(seconds // 60):02}:{seconds % 60:06.3f}'


def label(text, kind=None):
    item = QLabel(text)
    if kind:
        item.setObjectName(kind)
    item.setWordWrap(True)
    return item


def button(text, callback, kind=None):
    item = QPushButton(text)
    if kind:
        item.setObjectName(kind)
    item.clicked.connect(callback)
    return item


def seconds(value=0, maximum=86400):
    item = QDoubleSpinBox()
    item.setRange(0, maximum)
    item.setDecimals(3)
    item.setSingleStep(0.1)
    item.setSuffix(' s')
    item.setValue(value)
    item.setLayoutDirection(Qt.LeftToRight)
    return item




class Timeline(QWidget):
    seek = Signal(float)
    startChanged = Signal(float)
    endChanged = Signal(float)
    freezeChanged = Signal(float)

    def __init__(self):
        super().__init__()
        self.duration = 1
        self.position = 0
        self.start, self.end, self.freeze = 0, 1, 0
        self.editable = False
        self.active = None
        self.setMinimumHeight(78)
        self.setLayoutDirection(Qt.LeftToRight)
        self.setToolTip('اسحب الحدين الأخضرين للقص، أو العلامة البرتقالية للتجميد. اضغط على المسار للتنقّل.')

    def x(self, time):
        return 20 + time / max(0.001, self.duration) * max(1, self.width() - 40)

    def time(self, x):
        return float(np.clip((x - 20) / max(1, self.width() - 40), 0, 1) * self.duration)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#283141'))
        p.drawRoundedRect(QRectF(20, 29, self.width() - 40, 22), 5, 5)
        if self.editable:
            p.setBrush(QColor('#2a6559'))
            p.drawRoundedRect(QRectF(self.x(self.start), 29, self.x(self.end) - self.x(self.start), 22), 4, 4)
            p.setPen(QPen(QColor('#78e9c1'), 4))
            for t in (self.start, self.end):
                p.drawLine(int(self.x(t)), 24, int(self.x(t)), 55)
            p.setPen(QPen(QColor('#ffb77c'), 2))
            p.drawLine(int(self.x(self.freeze)), 19, int(self.x(self.freeze)), 55)
            p.drawText(QRectF(self.x(self.freeze) - 25, 0, 50, 19), Qt.AlignCenter, 'تجميد')
        p.setPen(QPen(QColor('#f3f7fb'), 2))
        p.drawLine(int(self.x(self.position)), 24, int(self.x(self.position)), 55)
        p.setPen(QColor('#8995a8'))
        for i in range(5):
            t = self.duration * i / 4
            p.drawText(QRectF(self.x(t) - 36, 57, 72, 20), Qt.AlignCenter, stamp(t)[:-4])

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x = event.position().x()
        self.active = 'seek'
        if self.editable:
            if event.position().y() < 23 and abs(x - self.x(self.freeze)) < 15:
                self.active = 'freeze'
            elif abs(x - self.x(self.start)) < 10:
                self.active = 'start'
            elif abs(x - self.x(self.end)) < 10:
                self.active = 'end'
        self.change(x)

    def mouseMoveEvent(self, event):
        if self.active:
            self.change(event.position().x())

    def mouseReleaseEvent(self, event):
        self.active = None

    def change(self, x):
        t = self.time(x)
        if self.active == 'start':
            self.startChanged.emit(min(t, self.end - 0.25))
        elif self.active == 'end':
            self.endChanged.emit(max(t, self.start + 0.25))
        elif self.active == 'freeze':
            self.freezeChanged.emit(float(np.clip(t, self.start, self.end - 0.001)))
        else:
            self.seek.emit(t)


class RenderJob(QThread):
    message = Signal(str)
    progress = Signal(float)
    done = Signal(str)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.cancel = threading.Event()

    def run(self):
        try:
            path = run_montage(self.cfg, self.message.emit, self.progress.emit, self.cancel)
            self.done.emit(path)
        except Cancelled:
            self.stopped.emit()
        except Exception as ex:
            self.error.emit(str(ex))
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(os.path.join(CACHE_DIR, 'last_error.txt'), 'w', encoding='utf-8') as handle:
                traceback.print_exc(file=handle)


class BaseStudio(QMainWindow):
    timeline_type = Timeline
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Montage Maker Studio')
        self.resize(1360, 950)
        self.setMinimumSize(980, 740)
        self.setWindowIcon(QIcon(os.path.join(APP_DIR, 'assets', 'montage.ico')))
        self.video = None
        self.info = None
        self.mode = 'source'
        self.preview_path = None
        self.worker = None
        self.busy = False
        self.revision = 0
        self.fx_paths = []
        self.protected_time = None
        self.source_position = 0
        self.pending_seek = None
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.65)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.video_frame)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playbackStateChanged.connect(self.play_state)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.errorOccurred.connect(self.player_error)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.build()
        self.shortcut_space = QShortcut(QKeySequence('Space'), self)
        self.shortcut_space.activated.connect(self.toggle_play)
        self.shortcut_left = QShortcut(QKeySequence(Qt.Key_Left), self)
        self.shortcut_left.activated.connect(lambda: self.step(-1))
        self.shortcut_right = QShortcut(QKeySequence(Qt.Key_Right), self)
        self.shortcut_right.activated.connect(lambda: self.step(1))
        self.restore_state()


    def tab(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setLayoutDirection(Qt.RightToLeft)
        box = QVBoxLayout(content)
        box.setContentsMargins(14, 15, 14, 15)
        box.setSpacing(12)
        scroll.setWidget(content)
        self.sidebar.addTab(scroll, title)
        return box





    def make_export_tab(self):
        box = self.tab('التصدير')
        box.addWidget(label('إعدادات الفيديو النهائي', 'section'))
        form = QFormLayout()
        self.resolution = QComboBox()
        for title, height in [('HD · 720p', 720), ('Full HD · 1080p', 1080),
                              ('QHD · 1440p', 1440), ('4K · 2160p', 2160)]:
            self.resolution.addItem(title, height)
        self.resolution.setCurrentIndex(1)
        self.fps = QComboBox()
        for value in (24, 30, 60):
            self.fps.addItem(f'{value} fps', value)
        self.fps.setCurrentIndex(2)
        self.quality = QComboBox()
        for title, value in [('جودة عالية', 'high'), ('أعلى جودة — حجم أكبر', 'best'),
                             ('حجم أصغر', 'small')]:
            self.quality.addItem(title, value)
        form.addRow('الدقة', self.resolution)
        form.addRow('معدل الإطارات', self.fps)
        form.addRow('الجودة', self.quality)
        box.addLayout(form)
        self.output_info = label('MP4 · H.264 · AAC\nنسبة أبعاد الفيديو الأصلي محفوظة.', 'muted')
        box.addWidget(self.output_info)
        box.addWidget(label('معاينة قبل التصدير', 'section'))
        box.addWidget(label('زر المعاينة ينشئ نسخة خفيفة 360p / 15fps بنفس القص والحركة والصوت. تتحدّث بعد أي تعديل. التصدير يستخدم الجودة اللي تختارها هنا.', 'muted'))
        box.addWidget(label('سجل العملية', 'section'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setMinimumHeight(200)
        self.log.setLayoutDirection(Qt.LeftToRight)
        box.addWidget(self.log)
        box.addWidget(button('فتح مجلد البرنامج', lambda: self.open_folder(APP_DIR)))
        self.resolution.currentIndexChanged.connect(self.output_changed)
        box.addStretch()

    def restore_state(self):
        self.update_track()

    def invalidate(self, *args):
        self.revision += 1
        self.draft_btn.setEnabled(False)
        if self.preview_path:
            self.status.setText('الإعدادات تغيّرت — أنشئ معاينة جديدة لمشاهدة النتيجة الحالية.')
            if self.mode == 'preview':
                self.mode_label.setText('معاينة سابقة')
        self.update_summary()

    def edit_changed(self, *args):
        if not self.info:
            return
        start, end = self.trim_in.value(), self.trim_out.value()
        if end <= start:
            self.summary.setText('النهاية لازم تكون بعد البداية')
        self.freeze.setRange(start, max(start, end - 1 / self.info['fps']))
        self.update_track()
        self.invalidate()

    def update_summary(self):
        if not self.info or not hasattr(self, 'summary'):
            return
        duration = max(0, self.trim_out.value() - self.trim_in.value())
        cfg = self.config()
        if duration > 0:
            cfg.output_fps = 60
            plan = timeline(cfg, max(1, round(duration * 60)))
            self.summary.setText(f'القص: {duration:.2f} ث  ·  الناتج ≈ {plan["duration"]:.2f} ث')

    def output_changed(self):
        if self.info:
            height = self.resolution.currentData()
            warning = '\nالدقة أعلى من المصدر؛ تكبير الصورة لا يضيف تفاصيل جديدة.' if height > self.info['height'] else ''
            self.output_info.setText('MP4 · H.264 · AAC\nنسبة أبعاد الفيديو الأصلي محفوظة.' + warning)

    def update_track(self):
        if not hasattr(self, 'track'):
            return
        if self.mode == 'source' and self.info:
            self.track.duration = self.info['duration']
            self.track.start, self.track.end = self.trim_in.value(), self.trim_out.value()
            self.track.freeze = self.freeze.value()
            self.track.editable = not self.busy
        else:
            self.track.duration = max(0.001, self.player.duration() / 1000)
            self.track.editable = False
        self.track.update()

    def choose_video(self, path):
        info = media_info(path)
        self.player.stop()
        self.video, self.info = os.path.abspath(path), info
        self.mode = 'source'
        self.view.image = QImage()
        self.view.source = True
        self.view.regions = []
        self.view.protected = []
        self.view.polygon = []
        self.protected_time = None
        self.source_position = 0
        self.pending_seek = None
        self.object_label.setText('بدون تحديد')
        self.preview_path = None
        for item in (self.trim_in, self.trim_out, self.freeze):
            item.blockSignals(True)
            item.setRange(0, max(0.001, info['duration']))
        self.trim_in.setValue(0)
        self.trim_out.setValue(info['duration'])
        self.freeze.setValue(min(3, info['duration'] / 2))
        for item in (self.trim_in, self.trim_out, self.freeze):
            item.blockSignals(False)
        self.regions_changed([])
        self.file_label.setText(f'{os.path.basename(path)}  ·  {info["width"]} × {info["height"]}  ·  {info["fps"]:.2f} fps  ·  {info["duration"]:.2f} ث')
        self.player.setSource(QUrl.fromLocalFile(self.video))
        self.player.pause()
        self.original_btn.setChecked(True)
        self.mode_label.setText('ORIGINAL')
        self.preview_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.update_track()
        self.update_summary()
        self.output_changed()
        self.status.setText('شغّل الفيديو، وحدّد البداية والنهاية ولحظة التجميد.')

    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(self, 'اختيار الفيديو', '', 'Video (*.mp4 *.mov *.mkv *.avi *.webm);;All files (*)')
        if path:
            try:
                self.choose_video(path)
            except Exception as ex:
                self.error(str(ex))

    def video_frame(self, frame):
        if frame.isValid():
            image = frame.toImage()
            if not image.isNull():
                self.view.frame(image)

    def position_changed(self, ms):
        self.track.position = ms / 1000
        self.track.update()
        duration = self.info['duration'] if self.mode == 'source' and self.info else self.player.duration() / 1000
        self.clock.setText(f'{stamp(ms / 1000)}  /  {stamp(duration)}')

    def duration_changed(self, ms):
        self.update_track()
        self.position_changed(self.player.position())

    def media_status(self, status):
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self.pending_seek is not None:
            time = self.pending_seek
            self.pending_seek = None
            self.player.setPosition(round(time * 1000))

    def play_state(self, state):
        self.play_btn.setText('إيقاف' if state == QMediaPlayer.PlayingState else 'تشغيل')

    def toggle_play(self):
        if not self.video:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self.player.position() >= self.player.duration() - 80:
                self.player.setPosition(0)
            self.player.play()

    def seek(self, seconds):
        if self.video:
            if self.player.mediaStatus() == QMediaPlayer.LoadingMedia:
                self.pending_seek = seconds
            self.player.setPosition(round(seconds * 1000))

    def step(self, direction):
        if not self.video:
            return
        self.player.pause()
        rate = self.info['fps'] if self.mode == 'source' else 15
        self.seek(max(0, self.player.position() / 1000 + direction / rate))

    def source_here(self):
        if self.mode != 'source':
            self.switch('source')
        self.player.pause()
        return self.player.position() / 1000

    def mark_in(self):
        if self.video:
            t = self.source_here()
            self.trim_in.setValue(min(t, self.trim_out.value() - 0.25))

    def mark_out(self):
        if self.video:
            t = self.source_here()
            self.trim_out.setValue(max(t, self.trim_in.value() + 0.25))

    def mark_freeze(self):
        if self.video:
            self.set_freeze(self.source_here())

    def set_freeze(self, time):
        self.freeze.setValue(time)
        if self.mode == 'source':
            self.seek(self.freeze.value())

    def switch(self, mode):
        if not self.video or (mode == 'preview' and (not self.preview_path or not self.draft_btn.isEnabled())):
            return
        self.player.pause()
        if self.mode == 'source':
            self.source_position = self.player.position() / 1000
        self.mode = mode
        self.view.source = mode == 'source'
        self.view.image = QImage()
        self.player.setSource(QUrl.fromLocalFile(self.video if mode == 'source' else self.preview_path))
        self.player.pause()
        self.seek(self.source_position if mode == 'source' else 0)
        self.original_btn.setChecked(mode == 'source')
        self.draft_btn.setChecked(mode == 'preview')
        self.mode_label.setText('ORIGINAL' if mode == 'source' else 'DRAFT · 360p')
        self.trim_toolbar.setEnabled(mode == 'source' and not self.busy)
        for item in (self.select_hud, self.undo_hud, self.clear_hud, self.protect_btn, self.undo_object, self.clear_object):
            item.setEnabled(mode == 'source' and not self.busy)
        self.update_track()
        self.view.update()

    def select_regions(self):
        self.player.pause()
        if self.select_hud.isChecked():
            self.protect_btn.setChecked(False)
            self.view.protecting = False
        self.view.selecting = self.select_hud.isChecked()
        self.view.setCursor(Qt.CrossCursor if self.view.selecting else Qt.ArrowCursor)
        self.view.update()

    def select_objects(self):
        if not self.video or self.busy:
            return
        if self.mode != 'source':
            self.switch('source')
        self.player.pause()
        if self.protect_btn.isChecked():
            self.select_hud.setChecked(False)
            self.view.selecting = False
            self.seek(self.protected_time if self.view.protected and self.protected_time is not None else self.freeze.value())
            self.hud_hint.setText('اضغط حول حدود العنصر؛ دبل كليك أو Enter لإكمال التحديد. Escape يلغي الرسم الجاري.')
            self.sidebar.setCurrentIndex(1)
        else:
            self.hud_hint.setText('تحديد HUD يخفي العناصر، وتحديد العنصر الواضح يحميه من مؤثرات الخلفية.')
        self.view.protecting = self.protect_btn.isChecked()
        self.view.setCursor(Qt.CrossCursor if self.view.protecting else Qt.ArrowCursor)
        self.view.update()

    def objects_changed(self, polygons):
        self.view.protected = list(polygons)
        if polygons and self.protected_time is None:
            self.protected_time = self.player.position() / 1000
        if not polygons:
            self.protected_time = None
        self.object_label.setText(f'{len(polygons)} عناصر واضحة' if polygons else 'بدون تحديد')
        self.invalidate()
        self.view.update()

    def undo_protected(self):
        if not self.busy:
            if self.view.polygon:
                self.view.polygon = self.view.polygon[:-1]
                self.view.update()
            elif self.view.protected:
                self.objects_changed(self.view.protected[:-1])

    def clear_protected(self):
        if not self.busy:
            self.view.polygon = []
            self.objects_changed([])

    def regions_changed(self, regions):
        self.view.regions = list(regions)
        self.region_label.setText(f'{len(regions)} مناطق')
        self.hud_enabled.setChecked(bool(regions))
        self.invalidate()
        self.view.update()

    def undo_region(self):
        if not self.busy and self.view.regions:
            self.regions_changed(self.view.regions[:-1])

    def clear_regions(self):
        if not self.busy:
            self.regions_changed([])

    def show_restore(self, checked):
        self.view.restored = checked and self.hud_enabled.isChecked()
        self.view.update()

    def pick_music(self):
        path, _ = QFileDialog.getOpenFileName(self, 'اختيار موسيقى', '', 'Audio (*.mp3 *.m4a *.wav *.aac *.ogg *.flac)')
        if path:
            self.music_path = path
            self.music_name.setText(os.path.basename(path))
            self.invalidate()

    def clear_music(self):
        self.music_path = None
        self.music_name.setText('لم تضف موسيقى')
        self.invalidate()

    def add_fx(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'إضافة مؤثرات صوتية', '', 'Audio (*.mp3 *.m4a *.wav *.aac *.ogg *.flac)')
        for path in paths:
            self.fx_row(path, 0, 100)
        if paths:
            self.invalidate()

    def fx_row(self, path, time, volume):
        row = self.fx_table.rowCount()
        self.fx_table.insertRow(row)
        name = QTableWidgetItem(os.path.basename(path))
        name.setToolTip(path)
        name.setFlags(name.flags() & ~Qt.ItemIsEditable)
        self.fx_table.setItem(row, 0, name)
        t = seconds(time)
        t.setMinimumWidth(75)
        t.valueChanged.connect(self.invalidate)
        vol = QSpinBox()
        vol.setRange(0, 200)
        vol.setValue(int(volume))
        vol.setMinimumWidth(65)
        vol.valueChanged.connect(self.invalidate)
        self.fx_table.setCellWidget(row, 1, t)
        self.fx_table.setCellWidget(row, 2, vol)
        self.fx_paths.append(path)

    def remove_fx(self):
        row = self.fx_table.currentRow()
        if row >= 0:
            self.fx_table.removeRow(row)
            self.fx_paths.pop(row)
            self.invalidate()

    def config(self):
        return Config(video=self.video or '', trim_start=self.trim_in.value(), trim_end=self.trim_out.value(),
                      freeze_time=self.freeze.value(), orbit_seconds=self.motion_duration.value(),
                      orbit_angle=self.angle.value(), rotation_direction=self.direction.currentData(),
                      animation=self.animation.isChecked(), remove_hud=self.hud_enabled.isChecked(),
                      hud_regions=list(self.view.regions), hud_method=self.hud_method.currentData(),
                      protected_polygons=list(self.view.protected), protected_time=self.protected_time,
                      background_blur=self.bg_blur.value(), background_dim=self.bg_dim.value() / 100,
                      background_scope=self.bg_scope.currentData(), **{k: v.isChecked() for k, v in self.bg_options.items()},
                      music=self.music_path, music_start=self.music_start.value(),
                      drop_in_song=self.drop_time.value() if self.drop_enabled.isChecked() else None,
                      music_volume=self.music_volume.value() / 100,
                      game_audio=self.game_enabled.isChecked(), game_volume=self.game_volume.value() / 100,
                      builtin_fx=self.builtin_fx.isChecked(), fx_volume=self.fx_volume.value() / 100,
                      sound_effects=[dict(path=path, time=self.fx_table.cellWidget(i, 1).value(),
                                          volume=self.fx_table.cellWidget(i, 2).value() / 100)
                                     for i, path in enumerate(self.fx_paths)],
                      output_height=self.resolution.currentData(), output_fps=self.fps.currentData(),
                      quality=self.quality.currentData(), **{k: v.isChecked() for k, v in self.options.items()})

    def start_job(self, preview):
        if self.busy or not self.video:
            return
        cfg = self.config()
        try:
            validate(cfg, self.info)
        except Exception as ex:
            self.error(str(ex))
            return
        if preview:
            preview_dir = os.path.join(CACHE_DIR, 'drafts')
            os.makedirs(preview_dir, exist_ok=True)
            path = os.path.join(preview_dir, uuid.uuid4().hex + '.mp4')
            cfg = cfg.draft(path)
        else:
            default = str(Path(self.video).with_name(Path(self.video).stem + '_montage.mp4'))
            path, _ = QFileDialog.getSaveFileName(self, 'حفظ المونتاج', default, 'MP4 (*.mp4)')
            if not path:
                return
            cfg.out = path if path.lower().endswith('.mp4') else path + '.mp4'
            try:
                validate(cfg, self.info)
            except Exception as ex:
                self.error(str(ex))
                return
        self.player.pause()
        self.job_preview = preview
        self.job_revision = self.revision
        self.busy = True
        self.set_busy(True)
        self.log.clear()
        self.progress.setValue(0)
        self.status.setText('جاري إنشاء معاينة خفيفة…' if preview else 'جاري تصدير المونتاج…')
        self.worker = RenderJob(cfg)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.progress.connect(lambda value: self.progress.setValue(round(value * 1000)))
        self.worker.done.connect(self.job_done)
        self.worker.error.connect(self.job_error)
        self.worker.stopped.connect(self.job_stopped)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def set_busy(self, busy):
        self.open_btn.setEnabled(not busy)
        self.project_open.setEnabled(not busy)
        self.project_save.setEnabled(not busy)
        self.sidebar.setEnabled(not busy)
        self.preview_btn.setEnabled(not busy and bool(self.video))
        self.export_btn.setEnabled(not busy and bool(self.video))
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        self.trim_toolbar.setEnabled(not busy and self.mode == 'source')
        for item in (self.select_hud, self.undo_hud, self.clear_hud, self.protect_btn, self.undo_object, self.clear_object):
            item.setEnabled(not busy and self.mode == 'source')
        self.view.selecting = self.select_hud.isChecked() and not busy
        self.view.protecting = self.protect_btn.isChecked() and not busy
        self.update_track()

    def job_done(self, path):
        self.progress.setValue(1000)
        if self.job_preview:
            old = self.preview_path
            self.preview_path = path
            self.draft_btn.setEnabled(self.job_revision == self.revision)
            self.switch('preview')
            self.player.play()
            self.status.setText('المعاينة جاهزة — شاهد الحركة والصوت، ثم صدّر بالجودة اللي اخترتها.')
            if old and old != path:
                try:
                    os.unlink(old)
                except OSError:
                    pass
        else:
            self.status.setText('تم حفظ المونتاج: ' + path)
            dialog = QMessageBox(self)
            dialog.setWindowTitle('التصدير اكتمل')
            dialog.setText('تم حفظ الفيديو بنجاح.')
            dialog.setInformativeText(path)
            open_button = dialog.addButton('فتح الفيديو', QMessageBox.ActionRole)
            folder_button = dialog.addButton('فتح المجلد', QMessageBox.ActionRole)
            dialog.addButton('تمام', QMessageBox.AcceptRole)
            dialog.exec()
            if dialog.clickedButton() == open_button:
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            elif dialog.clickedButton() == folder_button:
                self.open_folder(os.path.dirname(path))

    def worker_finished(self):
        self.busy = False
        self.set_busy(False)
        self.worker.deleteLater()
        self.worker = None

    def job_error(self, message):
        self.status.setText('تعذّرت العملية — راجع الرسالة وحاول مرة أخرى.')
        self.error(message)

    def job_stopped(self):
        self.status.setText('تم الإلغاء. الفيديو الأصلي محفوظ.')
        self.progress.setValue(0)

    def cancel_job(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_btn.setEnabled(False)
            self.status.setText('جاري إلغاء العملية…')

    def player_error(self, error, message):
        if error != QMediaPlayer.NoError:
            self.status.setText('تعذّر تشغيل الفيديو: ' + message)

    def error(self, message):
        translated = {
            'The trim must be inside the original video and the end must follow the start.': 'حدّد بداية ونهاية داخل الفيديو، وتأكد إن النهاية بعد البداية.',
            'Select at least 0.25 seconds of video.': 'اختَر جزءاً مدته ربع ثانية على الأقل.',
            'The freeze moment must be inside the selected trim.': 'لحظة التجميد لازم تكون بين بداية القص ونهايته.',
            'Choose an output name different from the original video.': 'اختَر اسماً مختلفاً عن الفيديو الأصلي للحفظ.',
            'The selected music file is missing.': 'ملف الموسيقى غير موجود في موقعه. اختَره من جديد.',
            'A selected sound effect is missing.': 'أحد ملفات المؤثرات الصوتية غير موجود في موقعه. أضفه من جديد.',
            'The selected file does not contain video.': 'الملف المختار لا يحتوي على فيديو.',
            'Select the object that should remain clear before applying background effects.': 'حدّد العنصر اللي بدك يضل واضح قبل تطبيق الغباش أو مؤثرات الخلفية.',
            'Invalid audio track settings.': 'إعدادات مسار الصوت غير صالحة.',
            'Invalid audio trim or fade.': 'تأكد من بداية الصوت ومدد الدخول والخروج التدريجي.',
            'Invalid audio clip duration.': 'مدة مقطع الصوت يجب أن تكون أكبر من صفر.',
            'The selected background file is missing.': 'ملف الخلفية انتقل أو اختفى. اختر مكانه من جديد.',
            'Invalid template controls.': 'راجع قوة القالب وسرعته.',
            'The object selection frame must be inside the selected trim. Select it again at the freeze moment.': 'لحظة تحديد العنصر خارج القص. امسح التحديد وارسمه من جديد عند لحظة التجميد.',
            'Enable animation or choose background effects for the whole video.': 'فعّل التجميد والحركة، أو غيّر تطبيق مؤثرات الخلفية إلى «كل الفيديو».',
        }
        message = translated.get(message, message)
        QMessageBox.warning(self, 'Montage Maker', message)

    @staticmethod
    def open_folder(path):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))



    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            # Keep the UI responsive until FFmpeg and the render thread release their files.
            self.cancel_job()
            self.status.setText('جاري إلغاء العملية قبل إغلاق البرنامج…')
            event.ignore()
            QTimer.singleShot(300, self.close_when_ready)
            return
        self.player.stop()
        self.player.setSource(QUrl())
        if self.preview_path:
            try:
                os.unlink(self.preview_path)
            except OSError:
                pass
        event.accept()

    def close_when_ready(self):
        if self.worker and self.worker.isRunning():
            QTimer.singleShot(300, self.close_when_ready)
        else:
            self.close()


from ui.studio import StudioFeatures


class Studio(StudioFeatures, BaseStudio):
    pass


def main():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MontageMaker.Studio.3')
    app = QApplication(sys.argv)
    cv2.setNumThreads(2)
    app.setApplicationName('Montage Maker Studio')
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(STYLE)
    if len(sys.argv) == 4 and sys.argv[1] == '--self-test':
        from engine.verify import verify_distribution
        return 0 if verify_distribution(sys.argv[2], sys.argv[3]) else 1
    window = Studio()
    if len(sys.argv)==3 and sys.argv[1]=='--open' and os.path.isfile(sys.argv[2]):
        try:
            path=sys.argv[2]
            if path.lower().endswith('.json'):
                with open(path,encoding='utf-8') as handle: data=json.load(handle)
                known={f.name for f in dataclasses.fields(Config)}
                cfg=Config(**{k:v for k,v in data['settings'].items() if k in known})
                validate(cfg,media_info(cfg.video)); window.apply_project(cfg)
            else: window.choose_video(path)
        except Exception as ex: window.error(str(ex))
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
