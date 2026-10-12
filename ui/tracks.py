"""Editable waveform lanes: move, trim, split, mute, lock, and timeline zoom."""
import copy
import json
import math
import os
import subprocess
import uuid
import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal, QThread, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QImage, QFont
from PySide6.QtWidgets import QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider,QMenu
from engine.util import FFMPEG, FFPROBE, run, popen


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


class TrackCanvas(QWidget):
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

    HEADER=142
    ROW=52

    def __init__(self,parent=None):
        super().__init__(parent)
        self.duration,self.position=1,0
        self.clips=[]; self.tracks=[]; self.selected_id=None
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
        self.history.append(copy.deepcopy({'clips':self.clips,'tracks':self.tracks})); self.history=self.history[-100:]; self.future=[]

    def undo(self):
        if self.history:
            self.future.append(copy.deepcopy({'clips':self.clips,'tracks':self.tracks})); data=self.history.pop()
            self.clips[:]=data['clips']; self.tracks[:]=data['tracks']; self.changed.emit(); self.resize_content()

    def redo(self):
        if self.future:
            self.history.append(copy.deepcopy({'clips':self.clips,'tracks':self.tracks})); data=self.future.pop()
            self.clips[:]=data['clips']; self.tracks[:]=data['tracks']; self.changed.emit(); self.resize_content()

    def extent(self):
        return max(.1,self.duration,max((c['time']+c['duration'] for c in self.clips),default=0))

    def scale(self):
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
    def lane_y(self,index): return 32+self.ROW*(index+2+(1 if self.music else 0))
    def lane_at(self,y):
        index=int((y-32)/self.ROW)-2-(1 if self.music else 0)
        ids=self.track_ids(); return ids[index] if 0<=index<len(ids) else None

    def locked(self,clip): return clip.get('locked',False) or self.track_info(clip.get('track')).get('locked',False)

    def resize_content(self):
        self.setFixedSize(max(450,round(self.available*self.zoom)),32+self.ROW*(3+len(self.track_ids())+(1 if self.music else 0))+12)
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

    def row(self,p,y,label,color,muted=False,locked=False):
        p.fillRect(QRectF(4,y,self.HEADER-10,self.ROW-6),QColor('#162131'))
        p.setPen(QColor(color)); p.drawText(QRectF(12,y+7,self.HEADER-55,22),Qt.AlignVCenter,label)
        p.setPen(QColor('#b9c6da'))
        p.drawText(QRectF(self.HEADER-44,y+7,18,20),Qt.AlignCenter,'×' if muted else '♫')
        p.drawText(QRectF(self.HEADER-23,y+7,15,20),Qt.AlignCenter,'▣' if locked else '□')

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor('#0c121e'))
        extent=self.extent()
        step=next((v for v in (.1,.25,.5,1,2,5,10,20,30,60,120,300) if v*self.scale()>=70),600)
        for t in np.arange(0,extent+.0001,step):
            x=self.x(float(t)); p.setPen(QPen(QColor('#233147'),1)); p.drawLine(QPointF(x,27),QPointF(x,self.height()))
            p.setPen(QColor('#91a2ba')); p.drawText(QRectF(x+3,1,70,24),Qt.AlignVCenter,f'{int(t//60):02}:{t%60:04.1f}')
        self.row(p,32,'الفيديو','#5ea7ff',locked=True)
        rect=QRectF(self.x(0),34,max(2,self.duration*self.scale()),self.ROW-10)
        p.setClipRect(rect)
        if self.thumbnails:
            for x in range(int(rect.x()),int(rect.right())+1,96):
                out_t=self.time(x)
                source=out_t
                if self.video_cfg:
                    from engine.live import sample_output
                    source=sample_output(*self.video_cfg,out_t)[0]
                img=min(self.thumbnails,key=lambda pair:abs(pair[0]-source))[1]
                p.drawImage(QRectF(x,rect.y(),96,rect.height()),img)
        p.setClipping(False); p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor('#5ea7ff'),1.5)); p.drawRoundedRect(rect,5,5)
        y=32+self.ROW
        self.row(p,y,'صوت الفيديو','#37ddbf',not self.game_audio,True)
        rect=QRectF(self.x(0),y+2,max(2,self.duration*self.scale()),self.ROW-10)
        p.fillRect(rect,QColor('#11352f') if self.game_audio else QColor('#19222d'))
        if self.game_wave and self.video_cfg:
            cfg,info=self.video_cfg
            from engine.live import sample_output
            p.setPen(QColor('#34d9b5'))
            for k in range(max(1,int(rect.width()))):
                source,phase,_,_=sample_output(cfg,info,k/self.scale())
                idx=min(len(self.game_wave)-1,max(0,int(source/info['duration']*len(self.game_wave))))
                amp=0 if phase!='video' else min(1,self.game_wave[idx])*17
                p.drawLine(QPointF(rect.x()+k,rect.center().y()-amp),QPointF(rect.x()+k,rect.center().y()+amp))
        p.setPen(QColor('#37ddbf')); p.drawRoundedRect(rect,5,5)
        if self.music:
            y=32+self.ROW*2; self.row(p,y,'الموسيقى','#d495ff',self.music.get('muted',False),True)
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
            p.setPen(QPen(QColor('#ffffff' if clip['id']==self.selected_id else color),2 if clip['id']==self.selected_id else 1))
            p.drawRoundedRect(r,5,5)
            p.save(); p.setClipRect(r)
            self.wave(p,r,clip['path'],clip.get('source_in',0),clip['duration'],clip.get('source_duration') or clip['duration'],color)
            p.fillRect(QRectF(r.x()+3,r.y()+2,min(150,r.width()-6),17),QColor(13,17,29,170))
            p.setPen(QColor('#e6edf8')); p.drawText(QRectF(r.x()+6,r.y()+2,r.width()-12,17),Qt.AlignVCenter,os.path.basename(clip['path']))
            if clip['id']==self.selected_id:
                p.fillRect(QRectF(r.x(),r.y()+7,5,r.height()-14),QColor('#f6f3ff'))
                p.fillRect(QRectF(r.right()-5,r.y()+7,5,r.height()-14),QColor('#f6f3ff'))
            p.restore()
        x=self.x(self.position); p.setPen(QPen(QColor('#ff697e'),2)); p.drawLine(QPointF(x,0),QPointF(x,self.height()))
        p.setBrush(QColor('#ff697e')); p.drawEllipse(QPointF(x,26),3,3)
        if not self.clips:
            p.setPen(QColor('#788ba6')); p.drawText(QRectF(self.HEADER,32+self.ROW*2,self.width()-self.HEADER,36),Qt.AlignVCenter,'أضف أصواتك، ثم اسحب المقطع أو أطرافه للقص')

    def mousePressEvent(self,event):
        self.setFocus(); x,y=event.position().x(),event.position().y()
        if not self.enabled_edit: return
        if event.button()==Qt.RightButton:
            self.context(event.position().toPoint()); return
        if self.HEADER-45<x<self.HEADER-26:
            if 32+self.ROW<=y<32+self.ROW*2:
                self.gameMute.emit(self.game_audio); return
            if self.music and 32+self.ROW*2<=y<32+self.ROW*3:
                self.musicMute.emit(not self.music.get('muted',False)); return
        ident=self.lane_at(y)
        if x<self.HEADER and ident is not None:
            self.trackSelected.emit(ident); track=self.track_info(ident)
            if track and self.HEADER-45<x<self.HEADER-26:
                self.remember(); track['muted']=not track.get('muted',False); self.changed.emit()
            elif track and x>=self.HEADER-26:
                self.remember(); track['locked']=not track.get('locked',False); self.changed.emit()
            self.update(); return
        for i in range(len(self.clips)-1,-1,-1):
            clip=self.clips[i]
            cy=self.clip_row(i)
            if cy<=y<cy+self.ROW and self.x(clip['time'])-5<=x<=self.x(clip['time']+clip['duration'])+5:
                self.selected_id=clip['id']; self.selected.emit(clip['id'])
                if x<self.HEADER:
                    if self.HEADER-45<x<self.HEADER-26:
                        self.remember(); clip['muted']=not clip.get('muted',False); self.changed.emit()
                    elif x>=self.HEADER-26:
                        self.remember(); clip['locked']=not clip.get('locked',False); self.changed.emit()
                elif not self.locked(clip):
                    edge='left' if abs(x-self.x(clip['time']))<8 else 'right' if abs(x-self.x(clip['time']+clip['duration']))<8 else 'move'
                    self.remember(); self.drag_scale=self.scale(); self.drag=(clip['id'],edge,self.time(x),copy.deepcopy(clip))
                else: self.seek.emit(min(self.duration,self.time(x)))
                self.update(); return
        if 32<=y<32+self.ROW: self.videoSelected.emit()
        if x>=self.HEADER:
            self.drag_scale=self.scale(); self.drag=('playhead',); self.seek.emit(min(self.duration,self.time(x)))

    def mouseMoveEvent(self,event):
        if not self.drag: return
        t=self.time(event.position().x())
        if self.drag[0]=='playhead': self.seek.emit(min(self.duration,t)); return
        ident,edge,begin,old=self.drag
        clip=next((c for c in self.clips if c['id']==ident),None)
        if clip is None: return
        delta=t-begin
        if edge=='move': clip['time']=max(0,old['time']+delta)
        elif edge=='left':
            delta=max(-old['source_in'],-old['time'],min(delta,old['duration']-.05))
            clip['time']=old['time']+delta; clip['source_in']=old['source_in']+delta; clip['duration']=old['duration']-delta
        else:
            clip['duration']=max(.05,min(old['duration']+delta,(old.get('source_duration') or old['source_in']+old['duration'])-old['source_in']))
        self.changed.emit(); self.update()

    def mouseReleaseEvent(self,event):
        self.drag=None; self.resize_content()

    def split(self):
        clip=next((c for c in self.clips if c['id']==self.selected_id),None)
        if not clip or self.locked(clip): return False
        left=self.position-clip['time']
        if not .05<left<clip['duration']-.05: return False
        self.remember(); right=copy.deepcopy(clip); right['id']=uuid.uuid4().hex
        right['time']=self.position; right['source_in']+=left; right['duration']-=left
        right['fade_in']=0; clip['fade_out']=0
        clip['duration']=left; self.clips.insert(self.clips.index(clip)+1,right)
        self.selected_id=right['id']; self.selected.emit(right['id']); self.changed.emit(); self.resize_content(); return True

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Z and event.modifiers()&Qt.ControlModifier: self.undo()
        elif event.key()==Qt.Key_S and event.modifiers()&Qt.ControlModifier: self.split()
        else: super().keyPressEvent(event)

    def make_menu(self,pos):
        if not self.enabled_edit: return
        t=min(self.duration,self.time(pos.x())); ident=self.lane_at(pos.y())
        clip=next((c for i,c in reversed(list(enumerate(self.clips))) if self.clip_row(i)<=pos.y()<self.clip_row(i)+self.ROW and self.x(c['time'])<=pos.x()<=self.x(c['time']+c['duration'])),None)
        menu=QMenu(self)
        if clip:
            self.selected_id=clip['id']; self.selected.emit(clip['id']); self.update()
            menu.addSection(os.path.basename(clip['path']))
            menu.addAction('خصائص المقطع',lambda:self.selected.emit(clip['id']))
            split=menu.addAction('تقسيم هنا',lambda:self.split_at(t)); split.setEnabled(not self.locked(clip) and clip['time']+.05<t<clip['time']+clip['duration']-.05)
            left=menu.addAction('قص البداية إلى هنا',lambda:self.trim_clip(clip,t,True)); left.setEnabled(split.isEnabled())
            right=menu.addAction('قص النهاية هنا',lambda:self.trim_clip(clip,t,False)); right.setEnabled(split.isEnabled())
            duplicate=menu.addAction('نسخ المقطع',lambda:self.duplicate(clip)); duplicate.setEnabled(not self.locked(clip))
            mute=menu.addAction('كتم المقطع'); mute.setCheckable(True); mute.setChecked(clip.get('muted',False)); mute.triggered.connect(lambda checked:self.change_flag(clip,'muted',checked))
            lock=menu.addAction('قفل المقطع'); lock.setCheckable(True); lock.setChecked(clip.get('locked',False)); lock.triggered.connect(lambda checked:self.change_flag(clip,'locked',checked))
            remove=menu.addAction('حذف المقطع',lambda:self.remove_clip(clip)); remove.setEnabled(not self.locked(clip))
            menu.addSeparator()
        elif 32<=pos.y()<32+self.ROW and self.video_cfg:
            from engine.live import sample_output
            source=sample_output(*self.video_cfg,t)[0]
            menu.addAction('خصائص الفيديو',self.videoSelected.emit)
            menu.addAction('القص يبدأ من هنا',lambda:self.trimStart.emit(source))
            menu.addAction('القص ينتهي هنا',lambda:self.trimEnd.emit(source))
            menu.addAction('تجميد عند هذه اللقطة',lambda:self.freezeHere.emit(source)); menu.addSeparator()
        elif 32+self.ROW<=pos.y()<32+self.ROW*2:
            menu.addAction('إعدادات صوت الفيديو',self.gameSelected.emit)
            mute=menu.addAction('كتم صوت الفيديو'); mute.setCheckable(True); mute.setChecked(not self.game_audio); mute.triggered.connect(self.gameMute.emit); menu.addSeparator()
        elif self.music and 32+self.ROW*2<=pos.y()<32+self.ROW*3:
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
        add=menu.addAction('إضافة صوت هنا…',lambda:self.addAudio.emit(t,ident)); add.setEnabled(not self.track_info(ident).get('locked',False))
        menu.addAction('إضافة مسار صوت فارغ',self.addTrack.emit)
        return menu

    def context(self,pos):
        menu=self.make_menu(pos)
        if menu: menu.exec(self.mapToGlobal(pos))

    def split_at(self,t): self.position=t; self.seek.emit(t); self.split()
    def trim_clip(self,clip,t,left):
        if self.locked(clip): return
        self.remember(); delta=t-clip['time']
        if left: clip['source_in']+=delta; clip['duration']-=delta; clip['time']=t
        else: clip['duration']=delta
        self.changed.emit(); self.resize_content()
    def duplicate(self,clip):
        self.remember(); new=copy.deepcopy(clip); new['id']=uuid.uuid4().hex; new['time']+=new['duration']; self.clips.append(new); self.changed.emit(); self.resize_content()
    def change_flag(self,item,key,value): self.remember(); item[key]=value; self.changed.emit(); self.update()
    def remove_clip(self,clip): self.remember(); self.clips.remove(clip); self.changed.emit(); self.resize_content()
    def remove_track(self,ident):
        if self.track_info(ident).get('locked'): return
        self.remember(); self.clips[:]=[c for c in self.clips if c.get('track')!=ident]; self.tracks[:]=[t for t in self.tracks if t['id']!=ident]; self.changed.emit(); self.resize_content()
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
        self.scroll.setMinimumHeight(180); self.scroll.setMaximumHeight(290)
        self.scroll.setLayoutDirection(Qt.LeftToRight)
        self.canvas=TrackCanvas(); self.scroll.setWidget(self.canvas); layout.addWidget(self.scroll)
        self.canvas.seek.connect(self.seek); self.canvas.selected.connect(self.selected); self.canvas.changed.connect(self.changed)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.canvas.available=max(450,self.width()-2)
        self.canvas.resize_content()

    def set_zoom(self,value):
        self.canvas.zoom=value/100; self.canvas.resize_content()
