"""Editable waveform lanes: move, trim, split, mute, lock, and timeline zoom."""
import copy
import json
import math
import os
import subprocess
import uuid
import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal, QThread, QRectF, QRect, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QImage, QFont, QPixmap
from PySide6.QtWidgets import QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider,QMenu
from engine.util import FFMPEG, FFPROBE, run, popen
from engine.video_edits import source_offset
from .timeline_ops import TimelineOperations
from .icons import icon


EFFECT_NAMES = {
    'freeze': 'تجميد وحركة', 'slowmo': 'تباطؤ', 'motion': 'حركة', 'clarity': 'ألوان ووضوح',
    'flash': 'فلاش', 'shake': 'اهتزاز', 'glow': 'توهج', 'bars': 'إطار سينمائي',
    'scan': 'مسح ضوئي', 'background': 'خلفية',
}
EFFECT_FAMILIES = (
    ('الحركة والتباطؤ', '#b197ff', ('freeze', 'slowmo', 'motion')),
    ('التأثيرات', '#f5bc73', ('clarity', 'flash', 'shake', 'glow', 'bars', 'scan')),
    ('الخلفيات', '#66d7d1', ('background',)),
)


class MediaAnalysis(QThread):
    ready=Signal(str,object)
    failed=Signal(str,str)

    def __init__(self,path,video=False,parent=None):
        super().__init__(parent); self.path,self.video=path,video

    def run(self):
        try:
            data=json.loads(run([FFPROBE,'-v','error','-show_format','-show_streams','-of','json',self.path]))
            duration=float(data.get('format',{}).get('duration',0))
            if duration<=0: raise ValueError('الملف لا يحتوي على مدة صوت صالحة.')
            has_audio=any(s.get('codec_type')=='audio' for s in data.get('streams',[]))
            result={'duration':duration,'wave':[],'thumbnails':[],'audio':has_audio}
            if has_audio:
                proc=popen([FFMPEG,'-v','error','-i',self.path,'-vn','-ac','1','-ar','2000','-f','f32le','-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                raw,err=proc.communicate()
                if proc.returncode: raise RuntimeError(err.decode(errors='replace')[-300:])
                values=np.abs(np.frombuffer(raw,dtype=np.float32))
                if len(values):
                    samples=min(len(values),6000,max(100,round(duration*80)))
                    edges=np.linspace(0,len(values),samples+1).astype(int)
                    result['wave']=[float(values[a:max(a+1,b)].max()) for a,b in zip(edges[:-1],edges[1:])]
            if self.video:
                cap=cv2.VideoCapture(self.path)
                try:
                    for t in np.linspace(0,max(0,duration-.1),12):
                        cap.set(cv2.CAP_PROP_POS_MSEC,float(t)*1000)
                        ok,frame=cap.read()
                        if ok:
                            rgb=cv2.cvtColor(cv2.resize(frame,(144,81)),cv2.COLOR_BGR2RGB)
                            result['thumbnails'].append((float(t),QImage(rgb.data,144,81,rgb.strides[0],QImage.Format_RGB888).copy()))
                finally: cap.release()
            self.ready.emit(self.path,result)
        except Exception as ex: self.failed.emit(self.path,str(ex))


class TrackCanvas(TimelineOperations,QWidget):
    seek=Signal(float)
    selected=Signal(str)
    changed=Signal()
    gameMute=Signal(bool)
    musicMute=Signal(bool)
    addAudio=Signal(float,object)
    addTrack=Signal()
    trackSelected=Signal(object)
    videoSelected=Signal()
    gameSelected=Signal()
    musicSelected=Signal()
    trimStart=Signal(float)
    trimEnd=Signal(float)
    freezeHere=Signal(float)
    fileDropped=Signal(str,float,object)
    effectSelected=Signal(str)
    addEffect=Signal(str,float)
    statusMessage=Signal(str)
    selectedVideo=Signal(str)
    selectionChanged=Signal()

    HEADER=142
    ROW=52

    def __init__(self,parent=None):
        super().__init__(parent)
        self.duration,self.position=1,0
        self.clips=[]; self.tracks=[]; self.effects=[]; self.selected_id=None; self.selected_effect_id=None
        self.video_clips=None; self.selected_video_id=None; self.selection=set(); self.lane_states={}
        self.waveforms={}; self.thumbnails=[]
        self.video_cfg=None; self.game_audio=True; self.game_wave=[]
        self.music=None
        self.drag=None; self.zoom=1; self.available=700
        self.history=[]; self.future=[]; self.enabled_edit=True
        self.setLayoutDirection(Qt.LeftToRight)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def remember(self):
        self.history.append(self.snapshot()); self.history=self.history[-100:]; self.future=[]

    def snapshot(self):
        return copy.deepcopy({'clips':self.clips,'tracks':self.tracks,'effects':self.effects,'video_clips':self.video_clips,
                              'lane_states':self.lane_states,'selection':self.selection,
                              'selected_id':self.selected_id,'selected_effect_id':self.selected_effect_id,'selected_video_id':self.selected_video_id})

    def restore(self,data):
        self.clips[:]=data['clips']; self.tracks[:]=data['tracks']; self.effects[:]=data.get('effects',[])
        videos=data.get('video_clips')
        if videos is None: self.video_clips=None
        elif self.video_clips is None: self.video_clips=copy.deepcopy(videos)
        else: self.video_clips[:]=videos
        self.lane_states.clear(); self.lane_states.update(data.get('lane_states',{}))
        self.selection=set(data.get('selection',set()))
        self.selected_id=data.get('selected_id'); self.selected_effect_id=data.get('selected_effect_id'); self.selected_video_id=data.get('selected_video_id')
        self.sync_selection()
        self.changed.emit(); self.resize_content()

    def undo(self):
        if self.history:
            self.future.append(self.snapshot()); self.restore(self.history.pop())

    def redo(self):
        if self.future:
            self.history.append(self.snapshot()); self.restore(self.future.pop())

    def extent(self):
        return max(.1,self.duration,max((c['time']+c['duration'] for c in self.clips),default=0),
                   max((sum(self.video_span(c)) for c in self.video_clips or []),default=0),
                   max((sum(self.effect_span(c)) for c in self.effects),default=0))

    def effect_span(self,clip):
        if self.fixed_video_mode(): return max(0,float(clip.get('time',0))),max(.05,float(clip.get('duration',1)))
        if self.video_cfg:
            try:
                from engine.editing import effect_span
                return effect_span(*self.video_cfg,clip)
            except ImportError:
                from engine.live import source_to_output
                start=source_to_output(*self.video_cfg,float(clip.get('source_time',0))) if clip.get('kind') in ('freeze','slowmo') else float(clip.get('time',0))
                duration=float(clip.get('source_duration',.5))/max(.05,float(clip.get('speed',.4))) if clip.get('kind')=='slowmo' else float(clip.get('duration',1))
                return max(0,start),max(.05,duration)
        return max(0,float(clip.get('time',clip.get('source_time',0)))),max(.05,float(clip.get('duration',1)))

    def effect_layout(self):
        """Stack intersecting events inside each family; every event stays selectable."""
        if getattr(self,'_render_layout',None) is not None: return self._render_layout
        result=[]; y=32
        for label,color,kinds in EFFECT_FAMILIES:
            events=[]; ends=[]
            for clip in sorted((c for c in self.effects if c.get('kind') in kinds),key=lambda c:(self.effect_span(c)[0],str(c.get('id')))):
                start,duration=self.effect_span(clip)
                slot=next((i for i,end in enumerate(ends) if end<=start+.001),len(ends))
                if slot==len(ends): ends.append(start+duration)
                else: ends[slot]=start+duration
                events.append((clip,start,duration,slot))
            height=max(self.ROW,8+max(1,len(ends))*36)
            result.append((y,height,label,color,events)); y+=height
        return result

    def video_y(self):
        return sum(row[1] for row in self.effect_layout())+32

    def game_y(self): return self.video_y()+self.ROW
    def music_y(self): return self.video_y()+self.ROW*2

    def effect_rect(self,clip):
        for y,height,label,color,events in self.effect_layout():
            for event,start,duration,slot in events:
                if event is clip or event.get('id')==clip.get('id'):
                    return QRectF(self.x(start),y+5+slot*36,max(3,duration*self.scale()),32)
        return QRectF()

    def effect_at(self,pos):
        if pos.x()<self.HEADER: return None
        candidates=[c for c in reversed(self.effects) if not self.item_hidden('effect',c)]
        return next((c for c in candidates if self.effect_rect(c).contains(pos)),None) or next((c for c in candidates if self.effect_rect(c).adjusted(-4,0,4,0).contains(pos)),None)

    def scale(self):
        if getattr(self,'_render_scale',None) is not None: return self._render_scale
        if self.drag and hasattr(self,'drag_scale'): return self.drag_scale
        return max(1,(self.width()-self.HEADER-18)/self.extent())
    def x(self,t): return self.HEADER+t*self.scale()
    def time(self,x): return max(0,(x-self.HEADER)/self.scale())
    def track_ids(self):
        result=[t['id'] for t in self.tracks]
        for i,c in enumerate(self.clips):
            ident=c.get('track',i)
            if ident not in result: result.append(ident)
        return result

    def track_info(self,ident): return next((t for t in self.tracks if t['id']==ident),{})
    def clip_row(self,index):
        ids=self.track_ids(); ident=self.clips[index].get('track',index)
        return self.lane_y(ids.index(ident))
    def audio_offset(self): return 1 if self.fixed_video_mode() else 2+(1 if self.music else 0)
    def lane_y(self,index): return self.video_y()+self.ROW*(index+self.audio_offset())
    def lane_at(self,y):
        if y<self.video_y(): return None
        index=int((y-self.video_y())/self.ROW)-self.audio_offset()
        ids=self.track_ids(); return ids[index] if 0<=index<len(ids) else None

    def locked(self,clip): return clip.get('locked',False) or self.track_info(clip.get('track')).get('locked',False)

    def group_lane_at(self,y):
        for index,(begin,height,*_) in enumerate(self.effect_layout()):
            if begin<=y<begin+height: return ('motion','visual','background')[index]
        if self.video_y()<=y<self.video_y()+self.ROW: return 'video'
        if not self.fixed_video_mode() and self.game_y()<=y<self.game_y()+self.ROW: return 'game'
        if not self.fixed_video_mode() and self.music and self.music_y()<=y<self.music_y()+self.ROW: return 'music'
        return None

    def event_under_pointer(self,pos):
        effect=self.effect_at(pos)
        if effect: return 'effect',effect,self.effect_rect(effect)
        video=self.video_at(pos)
        if video: return 'video',video,self.video_rect(video)
        for tolerance in (0,4):
            for index,clip in reversed(list(enumerate(self.clips))):
                rect=QRectF(self.x(clip['time']),self.clip_row(index)+2,max(3,clip['duration']*self.scale()),self.ROW-10)
                if rect.adjusted(-tolerance,0,tolerance,0).contains(pos): return 'audio',clip,rect
        return None,None,None

    def hover_cursor(self,pos):
        if pos.x()<self.HEADER:
            self.setCursor(Qt.PointingHandCursor if pos.x()>=self.HEADER-48 else Qt.ArrowCursor)
            if self.HEADER-48<=pos.x()<self.HEADER-28: self.setToolTip('إخفاء / إظهار المسار')
            elif self.HEADER-28<=pos.x()<self.HEADER-5: self.setToolTip('قفل / فتح المسار')
            else: self.setToolTip('')
            return
        kind,clip,rect=self.event_under_pointer(pos)
        if clip and not self.item_locked(kind,clip):
            edge=abs(pos.x()-rect.left())<8 or abs(pos.x()-rect.right())<8
            self.setCursor(Qt.SizeHorCursor if edge else Qt.OpenHandCursor)
            self.setToolTip('اسحب الطرف لتحديد البداية أو النهاية' if edge else 'اسحب للتحريك؛ Ctrl + كليك لتحديد أكثر من مقطع')
        else: self.setCursor(Qt.ArrowCursor); self.setToolTip('')

    def paint_drag_overlay(self,p):
        overlay=getattr(self,'drag_overlay',None)
        if not self.drag or not overlay or overlay[2]=='move': return
        kind,ident,edge=overlay; clip=self.item(kind,ident)
        if not clip: return
        start,duration=self.video_span(clip) if kind=='video' else self.effect_span(clip) if kind=='effect' else (clip['time'],clip['duration'])
        rect=self.video_rect(clip) if kind=='video' else self.effect_rect(clip) if kind=='effect' else QRectF(self.x(start),self.clip_row(self.clips.index(clip)),duration*self.scale(),self.ROW)
        boundary=start if edge=='left' else start+duration
        label=f'{"البداية" if edge=="left" else "النهاية"}: {boundary:.2f} ث   |   المدة: {duration:.2f} ث'
        p.setPen(QPen(QColor('#ffe3a2'),2)); p.drawLine(QPointF(self.x(boundary),rect.top()-5),QPointF(self.x(boundary),rect.bottom()+5))
        width=min(330,max(160,self.width()-self.HEADER-10)); x=max(self.HEADER,min(self.x(boundary)-width/2,self.width()-width-6)); y=max(1,rect.top()-30)
        bubble=QRectF(x,y,width,27); p.setBrush(QColor('#18283c')); p.setPen(QPen(QColor('#ffe3a2'),1)); p.drawRoundedRect(bubble,5,5)
        p.setPen(QColor('#f5efdf')); p.drawText(bubble.adjusted(8,1,-8,-1),Qt.AlignCenter,label)

    def move_group(self,delta):
        group=getattr(self,'drag_group',[])
        if not group: return
        delta=max(delta,-min(span[0] for _,_,span in group))
        moving={old['id'] for _,old,_ in group}
        for kind,old,span in group:
            if kind in ('video','audio'):
                left,right=self.free_gap(kind,old,*span,exclude=moving)
                delta=min(max(delta,left-span[0]),right-span[0]-span[1])
        for kind,old,span in group:
            clip=self.item(kind,old['id'])
            if clip is None or self.item_locked(kind,clip): continue
            if kind=='effect' and not self.fixed_video_mode() and old.get('kind') in ('freeze','slowmo'):
                clip['source_time']=self.source_for_output(span[0]+delta)
            else: clip['time']=old.get('time',span[0])+delta
            if kind=='effect' and 'animation_time' in old: clip['animation_time']=old['animation_time']+delta

    def drag_video(self,time):
        _,ident,edge,begin,old,span=self.drag
        clip=self.item('video',ident)
        if clip is None or self.item_locked('video',clip): return
        start,duration=span; delta=time-begin; speed=old.get('speed',1)
        if abs(delta)<.0001: return
        self.remember_drag()
        from engine.video_edits import ramp_points,sub_clip
        if edge=='move': self.move_group(delta)
        elif ramp_points(old):
            # A ramped clip can only be shortened; extending would invent speeds outside its curve.
            if edge=='left':
                delta=max(0,min(delta,duration-.05)); clip.update(sub_clip(old,delta/duration,1)); clip['time']=old.get('time',0)+delta
            # Shortening never collides with a neighbour, so ramped clips need no gap check.
            else: clip.update(sub_clip(old,0,max(.05,min(duration,duration+delta))/duration))
        elif edge=='left':
            delta=max(-old.get('time',0),-old['source_in']/speed if speed else -float('inf'),min(delta,duration-.05))
            delta=max(delta,self.free_gap('video',old,start,duration)[0]-start)
            clip['time']=old.get('time',0)+delta; clip['source_in']=old['source_in']+delta*speed; clip['duration']=old['duration']-delta
        else:
            available=(self.video_cfg[1]['duration']-old['source_in'])/speed if self.video_cfg and speed else float('inf')
            clip['duration']=max(.05,min(duration+delta,available,self.free_gap('video',old,start,duration)[1]-start))
        self.changed.emit(); self.update()

    def resize_content(self):
        width=self.width() if self.drag else max(450,round(self.available*self.zoom))
        self.setFixedSize(width,self.video_y()+self.ROW*(self.audio_offset()+1+len(self.track_ids()))+12)
        self.update()

    def wave(self,p,rect,path,source_in,duration,source_length,color):
        wave=self.waveforms.get(path,[])
        if not wave: return
        p.setPen(QPen(QColor(color),1))
        n=max(1,int(rect.width()))
        for k in range(n):
            idx=int((source_in+k/n*duration)/max(.001,source_length)*len(wave))
            if 0<=idx<len(wave):
                amp=min(1,wave[idx])*rect.height()*.43
                p.drawLine(QPointF(rect.x()+k,rect.center().y()-amp),QPointF(rect.x()+k,rect.center().y()+amp))

    def row(self,p,y,label,color,muted=False,locked=False,key=None,height=None):
        state=self.lane_state(key) if key else {}
        hidden=state.get('hidden',False) or muted; locked=state.get('locked',False) or locked
        height=height or self.ROW
        p.fillRect(QRectF(4,y,self.HEADER-10,height-6),QColor('#162131'))
        p.setPen(QColor(color)); p.drawText(QRectF(12,y+7,self.HEADER-55,22),Qt.AlignVCenter,label)
        p.setPen(QColor('#b9c6da'))
        icon('eye-off' if hidden else 'eye',16,'#f1ba75' if hidden else '#b9c6da').paint(p,QRect(self.HEADER-46,int(y)+9,17,17))
        icon('lock' if locked else 'unlock',16,'#f1ba75' if locked else '#b9c6da').paint(p,QRect(self.HEADER-25,int(y)+9,17,17))

    def paint_effects(self,p):
        for family_index,(y,height,label,color,events) in enumerate(self.effect_layout()):
            key=('motion','visual','background')[family_index]
            self.row(p,y,label,color,key=key,height=height)
            p.setPen(QColor('#788ba6')); p.drawText(QRectF(12,y+30,self.HEADER-24,14),Qt.AlignVCenter,'كلك يمين للإضافة')
            if not events:
                p.setPen(QColor('#657995')); p.drawText(QRectF(self.HEADER+10,y+5,self.width()-self.HEADER-20,32),Qt.AlignVCenter,'أضف تأثيرًا وحدد مدته من الشريط')
            for clip,start,duration,slot in events:
                rect=QRectF(self.x(start),y+5+slot*36,max(3,duration*self.scale()),32)
                selected=self.is_selected('effect',clip.get('id'))
                p.setBrush(QColor('#202838') if clip.get('muted',False) else QColor(color).darker(245))
                p.setPen(QPen(QColor('#ffffff' if selected else color),2 if selected else 1))
                p.drawRoundedRect(rect,5,5)
                p.save(); p.setClipRect(rect)
                if self.item_hidden('effect',clip): p.setOpacity(.2)
                p.setPen(QColor('#8290a6' if clip.get('muted',False) else '#eef2fa'))
                name=clip.get('name') or EFFECT_NAMES.get(clip.get('kind'),clip.get('kind','تأثير'))
                p.drawText(rect.adjusted(8,2,-8,-2),Qt.AlignVCenter,name)
                if self.item_locked('effect',clip): icon('lock',14).paint(p,QRect(int(rect.right()-19),int(rect.top()+8),14,14))
                if selected and not self.item_locked('effect',clip):
                    p.fillRect(QRectF(rect.x(),rect.y()+6,4,rect.height()-12),QColor('#f6f3ff'))
                    p.fillRect(QRectF(rect.right()-4,rect.y()+6,4,rect.height()-12),QColor('#f6f3ff'))
                p.restore()

    def paintEvent(self,event):
        # Playback changes only the playhead. Cache thumbnails, waveforms and events
        # so waveform painting cannot starve the audio player's event loop.
        ratio=self.devicePixelRatioF()
        signature=(self.width(),self.height(),ratio,self.duration,repr(self.clips),repr(self.tracks),repr(self.effects),repr(self.video_clips),repr(self.lane_states),repr(sorted(self.selection)),
                   self.selected_id,self.selected_effect_id,repr(self.video_cfg),self.game_audio,
                   repr(self.music),id(self.game_wave),id(self.thumbnails),
                   tuple((path,id(values),len(values)) for path,values in self.waveforms.items()))
        if signature!=getattr(self,'_paint_signature',None):
            self._paint_signature=signature
            self._static=QPixmap(round(self.width()*ratio),round(self.height()*ratio)); self._static.setDevicePixelRatio(ratio); self._static.fill(QColor('#0c121e'))
            self._render_scale=self.scale(); self._render_layout=self.effect_layout()
            self._render_plan=None
            if self.video_cfg:
                from engine.live import edit_plan
                self._render_plan=edit_plan(*self.video_cfg)
            static_painter=QPainter(self._static)
            try: self.draw_content(static_painter)
            finally:
                static_painter.end(); self._render_scale=None; self._render_layout=None; self._render_plan=None
        p=QPainter(self); p.drawPixmap(0,0,self._static)
        p.setRenderHint(QPainter.Antialiasing)
        x=self.x(self.position); p.setPen(QPen(QColor('#ff697e'),2)); p.drawLine(QPointF(x,0),QPointF(x,self.height()))
        p.setBrush(QColor('#ff697e')); p.drawEllipse(QPointF(x,26),3,3)
        self.paint_drag_overlay(p)

    def media_sample(self,time):
        cfg,info=self.video_cfg
        plan=getattr(self,'_render_plan',None)
        if plan and 'segments' in plan:
            index=min(max(0,int(time*cfg.output_fps+1e-7)),plan['total_frames']-1)
            segment=next(s for s in plan['segments'] if s['output_start']<=index<s['output_end'])
            source=cfg.trim_start+(segment['source_start']+(index-segment['output_start'])*segment['speed'])/cfg.output_fps
            return source,'motion' if segment['kind']=='freeze' else 'ramp' if segment['kind']=='slowmo' else segment['kind'] if segment['kind'] in ('gap','hold') else 'video'
        from engine.live import sample_output
        return sample_output(cfg,info,time)[:2]

    def draw_content(self,p):
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor('#0c121e'))
        extent=self.extent()
        step=next((v for v in (.1,.25,.5,1,2,5,10,20,30,60,120,300) if v*self.scale()>=70),600)
        for t in np.arange(0,extent+.0001,step):
            x=self.x(float(t)); p.setPen(QPen(QColor('#233147'),1)); p.drawLine(QPointF(x,27),QPointF(x,self.height()))
            p.setPen(QColor('#91a2ba')); p.drawText(QRectF(x+3,1,70,24),Qt.AlignVCenter,f'{int(t//60):02}:{t%60:04.1f}')
        self.paint_effects(p)
        video_y=self.video_y()
        self.row(p,video_y,'الفيديو','#5ea7ff',key='video')
        video_events=self.video_clips if self.fixed_video_mode() else [None]
        for clip in video_events:
            start,duration=self.video_span(clip) if clip else (0,self.duration)
            rect=QRectF(self.x(start),video_y+2,max(3,duration*self.scale()),self.ROW-10)
            selected=clip and self.is_selected('video',clip['id'])
            p.fillRect(rect,QColor('#152941')); p.save(); p.setClipRect(rect)
            if self.lane_state('video').get('hidden') or clip and clip.get('muted'): p.setOpacity(.2)
            if self.thumbnails:
                for x in range(int(rect.x()),int(rect.right())+1,96):
                    out_t=self.time(x)
                    source=(clip['source_in']+source_offset(clip,max(0,min(1,(out_t-start)/max(.001,duration))))) if clip else self.media_sample(out_t)[0] if self.video_cfg else out_t
                    img=min(self.thumbnails,key=lambda pair:abs(pair[0]-source))[1]
                    p.drawImage(QRectF(x,rect.y(),96,rect.height()),img)
            if clip:
                p.fillRect(QRectF(rect.x()+3,rect.y()+2,min(150,rect.width()-6),17),QColor(13,17,29,175))
                p.setPen(QColor('#e6edf8')); p.drawText(rect.adjusted(7,1,-7,-20),Qt.AlignVCenter,clip.get('name') or 'الفيديو')
            p.restore(); p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor('#ffffff' if selected else '#5ea7ff'),2 if selected else 1.5)); p.drawRoundedRect(rect,5,5)
            if selected and not self.item_locked('video',clip):
                p.fillRect(QRectF(rect.x(),rect.y()+7,4,rect.height()-14),QColor('#f6f3ff')); p.fillRect(QRectF(rect.right()-4,rect.y()+7,4,rect.height()-14),QColor('#f6f3ff'))
        if not self.fixed_video_mode():
            y=self.game_y()
            self.row(p,y,'صوت الفيديو','#37ddbf',not self.game_audio,key='game')
            rect=QRectF(self.x(0),y+2,max(2,self.duration*self.scale()),self.ROW-10)
            p.fillRect(rect,QColor('#11352f') if self.game_audio else QColor('#19222d'))
            if self.game_wave and self.video_cfg:
                cfg,info=self.video_cfg
                p.setPen(QColor('#34d9b5'))
                for k in range(max(1,int(rect.width()))):
                    source,phase=self.media_sample(k/self.scale())
                    idx=min(len(self.game_wave)-1,max(0,int(source/info['duration']*len(self.game_wave))))
                    amp=0 if phase not in ('video','ramp') else min(1,self.game_wave[idx])*17
                    p.drawLine(QPointF(rect.x()+k,rect.center().y()-amp),QPointF(rect.x()+k,rect.center().y()+amp))
            p.setPen(QColor('#37ddbf')); p.drawRoundedRect(rect,5,5)
            if self.music:
                y=self.music_y(); self.row(p,y,'الموسيقى','#d495ff',self.music.get('muted',False),key='music')
                rect=QRectF(self.x(0),y+2,max(2,self.duration*self.scale()),self.ROW-10)
                p.fillRect(rect,QColor('#34233f'))
                self.wave(p,rect,self.music['path'],self.music.get('source_in',0),self.duration,self.music.get('source_duration',self.duration),'#d495ff')
                p.setPen(QColor('#d495ff')); p.drawRoundedRect(rect,5,5)
        colors=['#a99aff','#66c8ff','#f3b878','#ea89bb','#75e0b5']
        for i,ident in enumerate(self.track_ids()):
            track=self.track_info(ident); self.row(p,self.lane_y(i),track.get('name',f'صوت {i+1}'),colors[i%len(colors)],track.get('muted',False),track.get('locked',False))
        for i,clip in enumerate(self.clips):
            y=self.clip_row(i); color=colors[self.track_ids().index(clip.get('track',i))%len(colors)]
            r=QRectF(self.x(clip['time']),y+2,max(3,clip['duration']*self.scale()),self.ROW-10)
            p.setBrush(QColor('#1e273c') if clip.get('muted') or self.track_info(clip.get('track')).get('muted') else QColor('#342c57'))
            selected=self.is_selected('audio',clip['id'])
            p.setPen(QPen(QColor('#ffffff' if selected else color),2 if selected else 1))
            p.drawRoundedRect(r,5,5)
            p.save(); p.setClipRect(r)
            self.wave(p,r,clip['path'],clip.get('source_in',0),clip['duration']*clip.get('rate',1),clip.get('source_duration') or clip['duration'],color)
            p.fillRect(QRectF(r.x()+3,r.y()+2,min(150,r.width()-6),17),QColor(13,17,29,170))
            p.setPen(QColor('#e6edf8')); p.drawText(QRectF(r.x()+6,r.y()+2,r.width()-12,17),Qt.AlignVCenter,clip.get('display_name') or clip.get('name') or os.path.basename(clip['path']))
            if selected and not self.locked(clip):
                p.fillRect(QRectF(r.x(),r.y()+7,5,r.height()-14),QColor('#f6f3ff'))
                p.fillRect(QRectF(r.right()-5,r.y()+7,5,r.height()-14),QColor('#f6f3ff'))
            p.restore()
        if not self.clips:
            p.setPen(QColor('#788ba6')); p.drawText(QRectF(self.HEADER,self.lane_y(0),self.width()-self.HEADER,36),Qt.AlignVCenter,'أضف أصواتك، ثم اسحب المقطع أو أطرافه للقص')

    def mousePressEvent(self,event):
        self.setFocus(); x,y=event.position().x(),event.position().y()
        if not self.enabled_edit: return
        if event.button()==Qt.RightButton:
            self.context(event.position().toPoint()); return
        if x<self.HEADER:
            key=self.group_lane_at(y); ident=self.lane_at(y)
            flag='hidden' if self.HEADER-48<=x<self.HEADER-28 else 'locked' if self.HEADER-28<=x<self.HEADER-5 else None
            if key and flag:
                self.toggle_lane(key,flag); return
            if ident is not None:
                track=self.track_info(ident)
                if flag: self.change_flag(track,'muted' if flag=='hidden' else 'locked',not track.get('muted' if flag=='hidden' else 'locked',False))
                else: self.clear_selection(); self.trackSelected.emit(ident)
                return
            self.clear_selection()
            if key=='video': self.videoSelected.emit()
            elif key=='game': self.gameSelected.emit()
            elif key=='music': self.musicSelected.emit()
            return
        effect=self.effect_at(event.position())
        video=self.video_at(event.position()) if not effect else None
        audio=None
        if not effect and not video:
            hit_kind,hit_clip,_=self.event_under_pointer(event.position())
            if hit_kind=='audio': audio=hit_clip
        kind,clip=('effect',effect) if effect else ('video',video) if video else ('audio',audio)
        if clip:
            ctrl=bool(event.modifiers()&Qt.ControlModifier)
            self.select_item(kind,clip['id'],ctrl=ctrl,preserve=not ctrl)
            if (kind,clip['id']) not in self.selection: return
            if not self.item_locked(kind,clip):
                rect=self.effect_rect(clip) if kind=='effect' else self.video_rect(clip) if kind=='video' else QRectF(self.x(clip['time']),self.clip_row(self.clips.index(clip))+2,max(3,clip['duration']*self.scale()),self.ROW-10)
                edge='left' if abs(x-rect.left())<8 else 'right' if abs(x-rect.right())<8 else 'move'
                self.drag_snapshot=self.snapshot(); self.drag_remembered=False; self.drag_scale=self.scale(); self.drag_width=self.width()
                # Moving an effect never shifts video or audio, including a mixed
                # selection. A deliberate group drag stays within its media type.
                self.drag_group=[(selected_kind,copy.deepcopy(item),self.video_span(item) if selected_kind=='video' else self.effect_span(item) if selected_kind=='effect' else (item['time'],item['duration'])) for selected_kind,item in self.editable_selection() if selected_kind==kind]
                cfg_info=copy.deepcopy(self.video_cfg)
                if kind=='effect':
                    if cfg_info and getattr(cfg_info[0],'effect_clips',None) is not None:
                        cfg_info[0].effect_clips=[c for c in cfg_info[0].effect_clips if c.get('id')!=clip['id']]
                    self.drag=('effect',clip['id'],edge,self.time(x),copy.deepcopy(clip),self.effect_span(clip),cfg_info)
                elif kind=='video': self.drag=('video',clip['id'],edge,self.time(x),copy.deepcopy(clip),self.video_span(clip))
                else: self.drag=(clip['id'],edge,self.time(x),copy.deepcopy(clip))
                self.drag_overlay=(kind,clip['id'],edge)
                self.setCursor(Qt.SizeHorCursor if edge!='move' else Qt.ClosedHandCursor)
            else: self.seek.emit(min(self.duration,self.time(x)))
            self.update(); return
        if not event.modifiers()&Qt.ControlModifier: self.clear_selection()
        if self.video_y()<=y<self.video_y()+self.ROW:
            self.videoSelected.emit()
        if x>=self.HEADER:
            self.drag_scale=self.scale(); self.drag=('playhead',); self.seek.emit(min(self.duration,self.time(x)))

    def mouseMoveEvent(self,event):
        if not self.drag: self.hover_cursor(event.position()); return
        t=self.time(event.position().x())
        if self.drag[0]=='playhead': self.seek.emit(min(self.duration,t)); return
        if self.drag[0]=='effect':
            self.drag_effect(t); return
        if self.drag[0]=='video':
            self.drag_video(t); return
        ident,edge,begin,old=self.drag
        clip=next((c for c in self.clips if c['id']==ident),None)
        if clip is None: return
        delta=t-begin
        if abs(delta)<.0001: return
        self.remember_drag()
        if edge=='move': self.move_group(delta)
        elif edge=='left':
            rate=max(.01,old.get('rate',1))
            delta=max(-old['source_in']/rate,-old['time'],min(delta,old['duration']-.05))
            delta=max(delta,self.free_gap('audio',old,old['time'],old['duration'])[0]-old['time'])
            clip['time']=old['time']+delta; clip['source_in']=old['source_in']+delta*rate; clip['duration']=old['duration']-delta
        else:
            rate=max(.01,old.get('rate',1))
            clip['duration']=max(.05,min(old['duration']+delta,((old.get('source_duration') or old['source_in']+old['duration']*rate)-old['source_in'])/rate,
                                         self.free_gap('audio',old,old['time'],old['duration'])[1]-old['time']))
        self.changed.emit(); self.update()

    def mouseReleaseEvent(self,event):
        self.drag=None; self.drag_overlay=None; self.drag_group=[]; self.drag_snapshot=None; self.drag_remembered=False; self.resize_content(); self.hover_cursor(event.position())

    def remember_drag(self):
        if getattr(self,'drag_remembered',False): return
        self.history.append(copy.deepcopy(getattr(self,'drag_snapshot',None) or self.snapshot())); self.history=self.history[-100:]; self.future=[]; self.drag_remembered=True

    def select_effect(self,clip): self.select_item('effect',clip['id'])

    def source_for_output(self,t,cfg_info=None):
        cfg_info=cfg_info or self.video_cfg
        if cfg_info:
            from engine.live import sample_output
            cfg,info=cfg_info
            end=info['duration'] if self.fixed_video_mode() else cfg.trim_end if cfg.trim_end is not None else info['duration']
            start=0 if self.fixed_video_mode() else cfg.trim_start
            return min(end-1/max(1,cfg.output_fps),max(start,sample_output(cfg,info,max(0,t))[0]))
        return max(0,t)

    def slowmo_position(self,clip,source,duration,cfg_info=None):
        """Find the nearest free source interval instead of making an invalid overlap."""
        cfg_info=cfg_info or self.video_cfg
        low=cfg_info[0].trim_start if cfg_info else 0
        high=(cfg_info[0].trim_end if cfg_info[0].trim_end is not None else cfg_info[1]['duration']) if cfg_info else self.duration
        source=max(low,min(source,high-duration))
        others=[(c['source_time'],c['source_time']+c['source_duration']) for c in self.effects
                if c.get('kind')=='slowmo' and c.get('id')!=clip.get('id') and not c.get('muted',False)]
        if clip.get('muted',False): return source
        candidates=[source,low,high-duration]+[value for begin,end in others for value in (end,begin-duration)]
        valid=[candidate for candidate in candidates if low-.0001<=candidate<=high-duration+.0001 and
               all(candidate+duration<=begin+.0001 or candidate>=end-.0001 for begin,end in others)]
        return min(valid,key=lambda candidate:abs(candidate-source)) if valid else None

    def drag_effect(self,t):
        _,ident,edge,begin,old,span,cfg_info=self.drag
        clip=next((c for c in self.effects if c.get('id')==ident),None)
        if clip is None or self.item_locked('effect',clip): return
        start,duration=span; delta=t-begin; kind=old.get('kind')
        if abs(delta)<.0001: return
        self.remember_drag()
        if self.fixed_video_mode():
            if edge=='move': self.move_group(delta)
            elif edge=='left':
                if kind!='slowmo':
                    clip['animation_time']=old.get('animation_time',start); clip['animation_duration']=old.get('animation_duration',duration)
                delta=max(-start,min(delta,duration-.05))
                if kind=='freeze': delta=max(duration-60,delta)
                if kind=='slowmo':
                    speed=max(.1,old.get('speed',old.get('params',{}).get('speed',.5)))
                    source=old.get('source_time',0); high=self.video_cfg[1]['duration']-.001 if self.video_cfg else float('inf')
                    delta=max(-source/speed,min(delta,(high-source)/speed))
                    clip['source_time']=source+delta*speed
                clip['time']=start+delta; clip['duration']=duration-delta
                if kind=='slowmo': clip['source_duration']=clip['duration']*speed
            else:
                if kind!='slowmo':
                    clip['animation_time']=old.get('animation_time',start); clip['animation_duration']=old.get('animation_duration',duration)
                clip['duration']=max(.05,min(60 if kind=='freeze' else float('inf'),duration+delta))
                if kind=='slowmo': clip['source_duration']=clip['duration']*old.get('speed',old.get('params',{}).get('speed',.5))
            self.changed.emit(); self.update(); return
        if kind in ('freeze','slowmo'):
            if edge=='move':
                clip['source_time']=self.source_for_output(max(0,start+delta),cfg_info)
                if kind=='slowmo' and cfg_info:
                    end=cfg_info[0].trim_end if cfg_info[0].trim_end is not None else cfg_info[1]['duration']
                    clip['source_time']=max(cfg_info[0].trim_start,min(clip['source_time'],end-old['source_duration']))
                    free=self.slowmo_position(clip,clip['source_time'],old['source_duration'],cfg_info)
                    clip['source_time']=old['source_time'] if free is None else free
            elif kind=='freeze':
                if edge=='left':
                    delta=max(-start,duration-60,min(delta,duration-.05))
                    clip['source_time']=self.source_for_output(start+delta,cfg_info); clip['duration']=duration-delta
                else: clip['duration']=max(.05,min(60,duration+delta))
            else:
                speed=max(.05,float(old.get('speed',.4)))
                low=cfg_info[0].trim_start if cfg_info else 0
                high=(cfg_info[0].trim_end if cfg_info[0].trim_end is not None else cfg_info[1]['duration']) if cfg_info else max(old['source_time']+old['source_duration'],self.duration)
                if not clip.get('muted',False):
                    for other in self.effects:
                        if other.get('kind')!='slowmo' or other.get('id')==ident or other.get('muted',False): continue
                        other_start=other['source_time']; other_end=other_start+other['source_duration']
                        if other_end<=old['source_time']+.0001: low=max(low,other_end)
                        if other_start>=old['source_time']+old['source_duration']-.0001: high=min(high,other_start)
                if edge=='left':
                    source_delta=max(low-old['source_time'],min(delta*speed,old['source_duration']-.05*speed))
                    clip['source_time']=old['source_time']+source_delta; clip['source_duration']=old['source_duration']-source_delta
                else:
                    clip['source_duration']=max(.05*speed,min(old['source_duration']+delta*speed,high-old['source_time']))
        elif edge=='move': clip['time']=max(0,old.get('time',0)+delta)
        elif edge=='left':
            delta=max(-start,min(delta,duration-.05)); clip['time']=start+delta; clip['duration']=duration-delta
        else: clip['duration']=max(.05,duration+delta)
        self.changed.emit(); self.update()

    def split(self):
        return self.split_selected()

    def keyPressEvent(self,event):
        if not self.enabled_edit: return super().keyPressEvent(event)
        if event.key()==Qt.Key_Z and event.modifiers()&Qt.ControlModifier:
            self.redo() if event.modifiers()&Qt.ShiftModifier else self.undo()
        elif event.key()==Qt.Key_Y and event.modifiers()&Qt.ControlModifier: self.redo()
        elif event.key()==Qt.Key_A and event.modifiers()==Qt.ControlModifier: self.select_all()
        elif event.key() in (Qt.Key_Delete,Qt.Key_Backspace): self.remove_selected()
        else: super().keyPressEvent(event)

    def remove_selected(self):
        return TimelineOperations.remove_selected(self)

    def make_menu(self,pos):
        if not self.enabled_edit: return
        t=min(self.duration,self.time(pos.x())); ident=self.lane_at(pos.y())
        effect=self.effect_at(QPointF(pos))
        video=self.video_at(QPointF(pos))
        clip=next((c for i,c in reversed(list(enumerate(self.clips))) if self.clip_row(i)<=pos.y()<self.clip_row(i)+self.ROW and self.x(c['time'])<=pos.x()<=self.x(c['time']+c['duration'])),None)
        menu=QMenu(self)
        if effect:
            self.select_item('effect',effect['id'],preserve=True)
            menu.addSection(effect.get('name') or EFFECT_NAMES.get(effect.get('kind'),'تأثير'))
            menu.addAction('خصائص التأثير',lambda:self.effectSelected.emit(effect['id']))
            split=menu.addAction(icon('cut'),'تقسيم المحدد هنا',lambda:self.split_at(t)); split.setEnabled(self.can_split_at(t))
            duplicate=menu.addAction(icon('duplicate'),'نسخ المحدد',self.duplicate_selected); duplicate.setEnabled(bool(self.editable_selection()))
            active=menu.addAction('تفعيل التأثير'); active.setCheckable(True); active.setChecked(not effect.get('muted',False)); active.setEnabled(not self.item_locked('effect',effect)); active.triggered.connect(lambda checked:self.change_flag(effect,'muted',not checked))
            lock=menu.addAction('قفل التأثير'); lock.setCheckable(True); lock.setChecked(effect.get('locked',False)); lock.triggered.connect(lambda checked:self.change_flag(effect,'locked',checked))
            lock.setEnabled(not self.lane_state(self.lane_key('effect',effect)).get('locked',False))
            remove=menu.addAction(icon('delete'),'حذف المحدد',self.remove_selected); remove.setEnabled(bool(self.editable_selection()))
            menu.addSeparator()
        elif video:
            self.select_item('video',video['id'],preserve=True)
            menu.addSection(video.get('name') or 'الفيديو')
            menu.addAction('خصائص مقطع الفيديو',lambda:self.selectedVideo.emit(video['id']))
            split=menu.addAction(icon('cut'),'تقسيم المحدد هنا',lambda:self.split_at(t)); split.setEnabled(self.can_split_at(t))
            start,duration=self.video_span(video); interior=not self.item_locked('video',video) and start+.05<t<start+duration-.05
            left=menu.addAction('قص البداية إلى هنا',lambda:self.trim_video(video,t,True)); left.setEnabled(interior)
            right=menu.addAction('قص النهاية هنا',lambda:self.trim_video(video,t,False)); right.setEnabled(interior)
            duplicate=menu.addAction(icon('duplicate'),'نسخ المحدد',self.duplicate_selected); duplicate.setEnabled(bool(self.editable_selection()))
            hidden=menu.addAction('إخفاء المقطع'); hidden.setCheckable(True); hidden.setChecked(video.get('muted',False)); hidden.setEnabled(not self.item_locked('video',video)); hidden.triggered.connect(lambda checked:self.change_flag(video,'muted',checked))
            lock=menu.addAction('قفل المقطع'); lock.setCheckable(True); lock.setChecked(video.get('locked',False)); lock.triggered.connect(lambda checked:self.change_flag(video,'locked',checked))
            lock.setEnabled(not self.lane_state('video').get('locked',False))
            remove=menu.addAction(icon('delete'),'حذف المحدد',self.remove_selected); remove.setEnabled(bool(self.editable_selection()))
            menu.addAction('تجميد عند هذه اللقطة',lambda:self.addEffect.emit('freeze',t)); menu.addSeparator()
        elif clip:
            self.select_item('audio',clip['id'],preserve=True)
            menu.addSection(clip.get('display_name') or clip.get('name') or os.path.basename(clip['path']))
            menu.addAction('خصائص المقطع',lambda:self.selected.emit(clip['id']))
            split=menu.addAction(icon('cut'),'تقسيم المحدد هنا',lambda:self.split_at(t)); split.setEnabled(self.can_split_at(t))
            interior=not self.locked(clip) and clip['time']+.05<t<clip['time']+clip['duration']-.05
            left=menu.addAction('قص البداية إلى هنا',lambda:self.trim_clip(clip,t,True)); left.setEnabled(interior)
            right=menu.addAction('قص النهاية هنا',lambda:self.trim_clip(clip,t,False)); right.setEnabled(interior)
            duplicate=menu.addAction(icon('duplicate'),'نسخ المحدد',self.duplicate_selected); duplicate.setEnabled(bool(self.editable_selection()))
            mute=menu.addAction('كتم المقطع'); mute.setCheckable(True); mute.setChecked(clip.get('muted',False)); mute.setEnabled(not self.locked(clip)); mute.triggered.connect(lambda checked:self.change_flag(clip,'muted',checked))
            lock=menu.addAction('قفل المقطع'); lock.setCheckable(True); lock.setChecked(clip.get('locked',False)); lock.triggered.connect(lambda checked:self.change_flag(clip,'locked',checked))
            lock.setEnabled(not self.track_info(clip.get('track')).get('locked',False))
            remove=menu.addAction(icon('delete'),'حذف المحدد',self.remove_selected); remove.setEnabled(bool(self.editable_selection()))
            menu.addSeparator()
        elif self.video_y()<=pos.y()<self.video_y()+self.ROW and self.video_cfg and not self.fixed_video_mode():
            from engine.live import sample_output
            source=sample_output(*self.video_cfg,t)[0]
            menu.addAction('خصائص الفيديو',self.videoSelected.emit)
            menu.addAction('القص يبدأ من هنا',lambda:self.trimStart.emit(source))
            menu.addAction('القص ينتهي هنا',lambda:self.trimEnd.emit(source))
            menu.addAction('تجميد عند هذه اللقطة',lambda:self.addEffect.emit('freeze',t)); menu.addSeparator()
        elif self.video_y()<=pos.y()<self.video_y()+self.ROW and self.fixed_video_mode():
            menu.addAction('خصائص الفيديو',self.videoSelected.emit)
            menu.addAction('تجميد عند هذه اللقطة',lambda:self.addEffect.emit('freeze',t)); menu.addSeparator()
        elif not self.fixed_video_mode() and self.game_y()<=pos.y()<self.game_y()+self.ROW:
            menu.addAction('إعدادات صوت الفيديو',self.gameSelected.emit)
            mute=menu.addAction('كتم صوت الفيديو'); mute.setCheckable(True); mute.setChecked(not self.game_audio); mute.triggered.connect(self.gameMute.emit); menu.addSeparator()
        elif not self.fixed_video_mode() and self.music and self.music_y()<=pos.y()<self.music_y()+self.ROW:
            menu.addAction('إعدادات الموسيقى',self.musicSelected.emit)
            mute=menu.addAction('كتم الموسيقى'); mute.setCheckable(True); mute.setChecked(self.music.get('muted',False)); mute.triggered.connect(self.musicMute.emit); menu.addSeparator()
        if ident is not None:
            track=self.track_info(ident)
            menu.addAction('إعدادات المسار',lambda:self.trackSelected.emit(ident))
            if track:
                mute=menu.addAction('كتم المسار'); mute.setCheckable(True); mute.setChecked(track.get('muted',False)); mute.triggered.connect(lambda checked:self.change_flag(track,'muted',checked))
                lock=menu.addAction('قفل المسار'); lock.setCheckable(True); lock.setChecked(track.get('locked',False)); lock.triggered.connect(lambda checked:self.change_flag(track,'locked',checked))
                remove=menu.addAction('حذف المسار',lambda:self.remove_track(ident)); remove.setEnabled(not track.get('locked'))
            menu.addSeparator()
        lane=self.group_lane_at(pos.y())
        if lane:
            hidden=menu.addAction(icon('eye-off'),'إخفاء المسار'); hidden.setCheckable(True); hidden.setChecked(self.lane_state(lane).get('hidden',False)); hidden.triggered.connect(lambda checked:self.set_lane_flag(lane,'hidden',checked))
            lock=menu.addAction(icon('lock'),'قفل المسار'); lock.setCheckable(True); lock.setChecked(self.lane_state(lane).get('locked',False)); lock.triggered.connect(lambda checked:self.set_lane_flag(lane,'locked',checked))
            menu.addSeparator()
        add=menu.addAction('إضافة صوت هنا…',lambda:self.addAudio.emit(t,ident)); add.setEnabled(not self.track_info(ident).get('locked',False))
        menu.addAction('إضافة مسار صوت فارغ',self.addTrack.emit)
        menu.addSeparator()
        effects_menu=menu.addMenu('إضافة تأثير هنا')
        for kind,name in EFFECT_NAMES.items():
            effects_menu.addAction(name,lambda checked=False,kind=kind:self.addEffect.emit(kind,t))
        menu.addSeparator(); menu.addAction('تحديد جميع المقاطع',self.select_all)
        return menu

    def context(self,pos):
        menu=self.make_menu(pos)
        if menu: menu.exec(self.mapToGlobal(pos))

    def split_at(self,t): self.position=t; self.seek.emit(t); self.split()
    def trim_clip(self,clip,t,left):
        if self.locked(clip): return
        self.remember(); delta=t-clip['time']
        if left: clip['source_in']+=delta*clip.get('rate',1); clip['duration']-=delta; clip['time']=t
        else: clip['duration']=delta
        self.changed.emit(); self.resize_content()
    def duplicate(self,clip):
        self.remember(); new=copy.deepcopy(clip); new['id']=uuid.uuid4().hex; new['time']+=new['duration']; self.clips.append(new); self.changed.emit(); self.resize_content()
    def change_flag(self,item,key,value):
        if not self.fixed_video_mode() and key=='muted' and not value and item.get('kind')=='slowmo':
            candidate=copy.deepcopy(item); candidate['muted']=False
            available=self.slowmo_position(candidate,item['source_time'],item['source_duration'])
            if available is None or abs(available-item['source_time'])>1e-6:
                if hasattr(self,'statusMessage'): self.statusMessage.emit('التباطؤ يتداخل مع مقطع آخر؛ غيّر مكانه قبل تفعيله.')
                return
        self.remember(); item[key]=value; self.changed.emit(); self.update()
    def remove_clip(self,clip):
        if self.locked(clip): return
        self.remember(); self.clips.remove(clip)
        if self.selected_id==clip['id']: self.selected_id=None
        self.selection.discard(('audio',clip['id'])); self.sync_selection()
        self.changed.emit(); self.resize_content()
    def duplicate_effect(self,clip):
        if self.item_locked('effect',clip): return
        if self.fixed_video_mode():
            self.select_item('effect',clip['id']); return self.duplicate_selected()
        self.remember(); new=copy.deepcopy(clip); new['id']=uuid.uuid4().hex
        start,duration=self.effect_span(clip)
        if clip.get('kind') in ('freeze','slowmo'):
            new['source_time']=self.source_for_output(start+duration+1/max(1,self.video_cfg[0].output_fps if self.video_cfg else 30))
            if clip.get('kind')=='slowmo' and self.video_cfg:
                end=self.video_cfg[0].trim_end if self.video_cfg[0].trim_end is not None else self.video_cfg[1]['duration']
                new['source_duration']=max(.001,min(new['source_duration'],end-new['source_time']))
                free=self.slowmo_position(new,new['source_time'],new['source_duration'])
                if free is None: self.history.pop(); return
                new['source_time']=free
        else: new['time']=start+duration
        self.effects.append(new); self.select_effect(new); self.changed.emit(); self.resize_content()
    def remove_effect(self,clip):
        if self.item_locked('effect',clip): return
        self.remember(); self.effects.remove(clip)
        if self.selected_effect_id==clip['id']: self.selected_effect_id=None; self.effectSelected.emit('')
        self.selection.discard(('effect',clip['id'])); self.sync_selection()
        self.changed.emit(); self.resize_content()
    def remove_track(self,ident):
        if self.track_info(ident).get('locked'): return
        self.remember(); self.clips[:]=[c for c in self.clips if c.get('track')!=ident]; self.tracks[:]=[t for t in self.tracks if t['id']!=ident]; self.changed.emit(); self.resize_content()
        self.sync_selection()
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dragMoveEvent(self,event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self,event):
        t=self.time(event.position().x()); ident=self.lane_at(event.position().y())
        for url in event.mimeData().urls():
            if url.isLocalFile(): self.fileDropped.emit(url.toLocalFile(),t,ident)
        event.acceptProposedAction()


class AudioTimeline(QWidget):
    seek=Signal(float); selected=Signal(str); changed=Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(3)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(False)
        self.scroll.setMinimumHeight(240); self.scroll.setMaximumHeight(440)
        self.scroll.setLayoutDirection(Qt.LeftToRight)
        self.canvas=TrackCanvas(); self.scroll.setWidget(self.canvas); layout.addWidget(self.scroll)
        self.canvas.seek.connect(self.seek); self.canvas.selected.connect(self.selected); self.canvas.changed.connect(self.changed)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.canvas.available=max(450,self.width()-2)
        self.canvas.resize_content()

    def set_zoom(self,value):
        self.canvas.zoom=value/100; self.canvas.resize_content()
