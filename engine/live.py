"""Coalesced, on-demand frame rendering; no preview video is generated."""
import dataclasses
import copy
import math
import threading
import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from .editing import timeline, dimensions, source_frame_to_output
from .hud import region_mask
from .background import ProtectionTracker, polygon_mask, apply_background, enabled
from .motion import Motion, finish
from .templates import close_media
from .effects import motion_config, backgrounds_at, timed_background, timed_visuals


def edit_plan(cfg, info):
    count = max(1, round(((cfg.trim_end if cfg.trim_end is not None else info['duration'])-cfg.trim_start)*cfg.output_fps))
    return timeline(cfg, count)


def sample_output(cfg, info, time):
    plan = edit_plan(cfg, info)
    fps = cfg.output_fps
    index = min(max(0, int(time*fps + 1e-7)), plan['total_frames']-1)
    if cfg.effect_clips is not None:
        segment = next(s for s in plan['segments'] if s['output_start'] <= index < s['output_end'])
        k = index - segment['output_start']
        frame = segment['source_start'] + k * segment['speed']
        phase = 'motion' if segment['kind'] == 'freeze' else 'ramp' if segment['kind'] == 'slowmo' else 'video'
        plan['active_segment'] = segment
        plan['active_motion'] = segment['clip'] if phase == 'motion' else None
        if phase == 'motion':
            plan['motion'] = segment['output_end'] - segment['output_start']
        return cfg.trim_start + frame / fps, phase, k if phase == 'motion' else 0, plan
    if not cfg.animation or index < plan['before']:
        frame, phase, k = index, 'video', 0
    elif index < plan['before']+plan['ramp_out']:
        q = (index-plan['before'])/max(1,plan['ramp_out'])
        frame = plan['before']+(1-(1-q)**2)*plan['ramp_src']
        phase, k = 'ramp', 0
    elif index < plan['before']+plan['ramp_out']+plan['motion']:
        frame = plan['ff']
        phase, k = 'motion', index-plan['before']-plan['ramp_out']
    else:
        frame = plan['ff']+1+index-plan['before']-plan['ramp_out']-plan['motion']
        phase, k = 'video', 0
    return cfg.trim_start+frame/fps, phase, k, plan


def source_to_output(cfg, info, source):
    plan = edit_plan(cfg, info)
    frame = max(0,(source-cfg.trim_start)*cfg.output_fps)
    if cfg.effect_clips is not None:
        return source_frame_to_output(plan, frame, cfg.output_fps)
    if not cfg.animation or frame <= plan['before']:
        return frame/cfg.output_fps
    if frame < plan['ff'] and plan['ramp_src']:
        q = 1-math.sqrt(max(0,1-(frame-plan['before'])/plan['ramp_src']))
        return (plan['before']+q*plan['ramp_out'])/cfg.output_fps
    if frame <= plan['ff']:
        return plan['freeze']
    return plan['resume']+(frame-plan['ff']-1)/cfg.output_fps


class LiveFrames(QThread):
    ready = Signal(object, float, float, int)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.condition = threading.Condition()
        self.pending = None
        self.stopping = False
        self.cap = None
        self.path = None
        self.tracker = None
        self.tracker_key = None
        self.motion = None
        self.motion_key = None
        self.last_index = -1
        self.last_raw = None
        self.proxies = {}

    def set_proxy(self,path,proxy,info):
        with self.condition: self.proxies[path]=(proxy,dict(info))

    def request(self, cfg, info, t, original, height, revision):
        with self.condition:
            self.pending = (copy.deepcopy(cfg), dict(info), t, original, height, revision)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()

    def read(self, path, info, t):
        if path != self.path:
            if self.cap is not None:
                self.cap.release()
            self.cap = cv2.VideoCapture(path)
            if not self.cap.isOpened():
                raise RuntimeError('تعذّر قراءة الفيديو للعرض المباشر.')
            self.path, self.last_index, self.last_raw = path, -1, None
        index = max(0,min(round(t*info['fps']),max(0,int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))-1)))
        if index == self.last_index and self.last_raw is not None:
            return self.last_raw.copy()
        next_index = round(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        if index < next_index or index-next_index > 12:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES,index)
            next_index = index
        raw = None
        for _ in range(max(1,index-next_index+1)):
            ok, raw = self.cap.read()
            if not ok:
                raise RuntimeError('تعذّر قراءة إطار الفيديو.')
        self.last_index, self.last_raw = index, raw
        return raw.copy()

    def foreground(self, cfg, info, frame, t):
        if not cfg.protected_polygons:
            return None
        anchor = cfg.protected_time if cfg.protected_time is not None else cfg.freeze_time
        if abs(t-anchor)<.025: return polygon_mask(frame.shape,cfg.protected_polygons)
        if cfg.video not in self.proxies:
            return polygon_mask(frame.shape,cfg.protected_polygons)
        video=self.proxies.get(cfg.video,(cfg.video,info))[0]
        key = (video,str(cfg.protected_polygons),anchor,frame.shape[:2])
        if key != self.tracker_key:
            if self.tracker:
                self.tracker.close()
            self.tracker = ProtectionTracker(video,cfg.protected_polygons,anchor,frame.shape,tracking_width=320)
            self.tracker_key = key
        return self.tracker.at(t,frame)

    def render(self, cfg, info, t, original, height):
        source, phase, k, plan = (t,'video',0,edit_plan(cfg,info)) if original else sample_output(cfg,info,t)
        video,decode_info=cfg.video,info
        if not original and phase!='motion' and cfg.video in self.proxies:
            proxy,proxy_info=self.proxies[cfg.video]
            if height<=proxy_info['height']: video,decode_info=proxy,proxy_info
        raw = self.read(video,decode_info,source)
        w, h = dimensions(info,min(info['height'],height))
        frame = cv2.resize(raw,(w,h),interpolation=cv2.INTER_AREA)
        if original:
            return frame,source
        if cfg.remove_hud and cfg.hud_regions:
            mask = region_mask(frame.shape,cfg.hud_regions)
            frame = cv2.inpaint(frame,mask,max(1,round(h/360)),cv2.INPAINT_TELEA)
        if cfg.effect_clips is not None:
            mask = self.foreground(cfg, info, frame, source) if backgrounds_at(cfg, t) else None
            prepared = timed_background(frame, mask, cfg, t)
            if phase == 'motion':
                clip = plan['active_motion']
                key = (str(dataclasses.asdict(cfg)), str(clip.get('id')), (h, w))
                if key != self.motion_key:
                    self.motion = Motion(frame, motion_config(cfg, clip), plan['motion'])
                    self.motion_key = key
                prepared = self.motion.frame(k, source_frame=prepared)
            return timed_visuals(prepared, cfg, t), source
        if phase == 'motion':
            key = (str(dataclasses.asdict(cfg)),(h,w))
            if key != self.motion_key:
                mask = self.foreground(cfg,info,frame,source) if enabled(cfg) else None
                self.motion = Motion(frame,cfg,plan['motion'],mask)
                self.motion_key = key
            frame = self.motion.frame(k)
        elif enabled(cfg) and cfg.background_scope == 'all':
            frame = apply_background(frame,self.foreground(cfg,info,frame,source),cfg,(source-cfg.trim_start))
        return finish(frame,cfg),source

    def run(self):
        try:
            while True:
                with self.condition:
                    while self.pending is None and not self.stopping:
                        self.condition.wait()
                    if self.stopping:
                        break
                    cfg,info,t,original,height,revision = self.pending
                    self.pending = None
                try:
                    frame,source = self.render(cfg,info,t,original,height)
                    self.ready.emit(frame,source,t,revision)
                except Exception as ex:
                    self.failed.emit(str(ex))
        finally:
            if self.cap:
                self.cap.release()
            if self.tracker:
                self.tracker.close()
            close_media()
