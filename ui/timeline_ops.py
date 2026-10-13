"""Selection and reversible timeline edits shared by video, audio and effects."""
import copy
import uuid
from PySide6.QtCore import QRectF


class TimelineOperations:
    def fixed_video_mode(self):
        return self.video_clips is not None

    def lane_key(self,kind,clip=None):
        if kind=='video': return 'video'
        if kind=='effect':
            name=(clip or {}).get('kind')
            return 'motion' if name in ('freeze','slowmo','motion') else 'background' if name=='background' else 'visual'
        return None

    def lane_state(self,key):
        return self.lane_states.get(key,{})

    def item_list(self,kind):
        if kind=='video': return self.video_clips if self.video_clips is not None else []
        return self.effects if kind=='effect' else self.clips

    def item(self,kind,ident):
        return next((clip for clip in self.item_list(kind) if clip.get('id')==ident),None)

    def item_locked(self,kind,clip):
        if kind=='audio': return self.locked(clip)
        return clip.get('locked',False) or self.lane_state(self.lane_key(kind,clip)).get('locked',False)

    def item_hidden(self,kind,clip):
        return self.lane_state(self.lane_key(kind,clip)).get('hidden',False) if kind!='audio' else False

    def video_span(self,clip):
        if self.video_cfg:
            try:
                from engine.editing import video_span
                return video_span(*self.video_cfg,clip)
            except ImportError: pass
        return float(clip.get('time',0)),float(clip.get('duration',1))

    def video_rect(self,clip):
        start,duration=self.video_span(clip)
        return QRectF(self.x(start),self.video_y()+2,max(3,duration*self.scale()),self.ROW-10)

    def video_at(self,pos):
        if pos.x()<self.HEADER or self.lane_state('video').get('hidden',False): return None
        clips=list(reversed(self.video_clips or []))
        return next((clip for clip in clips if self.video_rect(clip).contains(pos)),None) or next((clip for clip in clips if self.video_rect(clip).adjusted(-4,0,4,0).contains(pos)),None)

    def is_selected(self,kind,ident):
        return (kind,ident) in self.selection

    def clear_selection(self,notify=True):
        self.selection.clear(); self.selected_id=None; self.selected_effect_id=None; self.selected_video_id=None
        if notify: self.selectionChanged.emit()
        self.update()

    def select_item(self,kind,ident,ctrl=False,preserve=False):
        token=(kind,ident)
        if self.item(kind,ident) is None: return
        if ctrl:
            if token in self.selection: self.selection.remove(token)
            else: self.selection.add(token)
        elif not preserve or token not in self.selection:
            self.selection={token}
        self.selected_id=None; self.selected_effect_id=None; self.selected_video_id=None
        primary=token if token in self.selection else next(iter(sorted(self.selection)),None)
        if primary:
            self.emit_primary(*primary)
        self.selectionChanged.emit(); self.update()

    def emit_primary(self,kind,ident):
        self.selected_id=ident if kind=='audio' else None
        self.selected_effect_id=ident if kind=='effect' else None
        self.selected_video_id=ident if kind=='video' else None
        if kind=='audio': self.selected.emit(ident)
        elif kind=='effect': self.effectSelected.emit(ident)
        else: self.selectedVideo.emit(ident); self.videoSelected.emit()

    def select_all(self):
        self.selection={(kind,clip['id']) for kind in ('video','audio','effect') for clip in self.item_list(kind)}
        if self.selection: self.emit_primary(*next(iter(sorted(self.selection))))
        else: self.selected_id=None; self.selected_effect_id=None; self.selected_video_id=None
        self.selectionChanged.emit(); self.update()

    def toggle_lane(self,key,flag):
        self.set_lane_flag(key,flag,not self.lane_state(key).get(flag,False))

    def set_lane_flag(self,key,flag,value):
        self.remember(); state=self.lane_states.setdefault(key,{})
        state[flag]=bool(value)
        self.changed.emit(); self.resize_content()

    def editable_selection(self):
        return [(kind,clip) for kind,ident in sorted(self.selection) if (clip:=self.item(kind,ident)) is not None and not self.item_locked(kind,clip)]

    def remove_selected(self):
        if not self.enabled_edit: return False
        items=self.editable_selection()
        if not items:
            self.statusMessage.emit('حدد المقاطع التي تريد حذفها أولًا.' if not self.selection else 'المقاطع المحددة مقفلة؛ أزل القفل أولًا.')
            return False
        self.remember()
        for kind,clip in items:
            self.item_list(kind).remove(clip); self.selection.discard((kind,clip['id']))
        self.sync_selection(); self.changed.emit(); self.resize_content(); return True

    def sync_selection(self):
        self.selection={token for token in self.selection if self.item(*token) is not None}
        primary=next((token for token in (('video',self.selected_video_id),('effect',self.selected_effect_id),('audio',self.selected_id)) if token in self.selection),None)
        if not primary: primary=next(iter(sorted(self.selection)),None)
        self.selected_id=None; self.selected_effect_id=None; self.selected_video_id=None
        if primary: self.emit_primary(*primary)
        else: self.effectSelected.emit('')
        self.selectionChanged.emit(); self.update()

    def split_selected(self):
        if not self.enabled_edit: return False
        if not self.selection:
            self.statusMessage.emit('حدد المقطع أو المقاطع أولًا، ثم ضع المؤشر داخلها واضغط Ctrl+S.')
            return False
        candidates=[]
        for kind,clip in self.editable_selection():
            start,duration=self.video_span(clip) if kind=='video' else self.effect_span(clip) if kind=='effect' else (clip['time'],clip['duration'])
            left=self.position-start
            if .05<left<duration-.05: candidates.append((kind,clip,start,duration,left))
        if not candidates:
            self.statusMessage.emit('ضع مؤشر التشغيل داخل المقاطع المحددة، وأزل قفلها إن كانت مقفلة.')
            return False
        self.remember(); primary=None
        for kind,clip,start,duration,left in candidates:
            right=copy.deepcopy(clip); right['id']=uuid.uuid4().hex; right['time']=self.position
            if kind=='audio':
                right['source_in']=clip.get('source_in',0)+left*clip.get('rate',1); right['duration']=duration-left
                right['fade_in']=0; clip['fade_out']=0; clip['duration']=left
            elif kind=='video':
                from engine.video_edits import sub_clip
                u=left/duration; right.update(sub_clip(clip,u,1)); clip.update(sub_clip(clip,0,u))
            elif self.fixed_video_mode() or clip.get('kind') not in ('freeze','slowmo'):
                if clip.get('kind')!='slowmo':
                    clock=clip.get('animation_time',start); length=clip.get('animation_duration',duration)
                    clip['animation_time']=right['animation_time']=clock; clip['animation_duration']=right['animation_duration']=length
                right['duration']=duration-left; clip['duration']=left
                if clip.get('kind')=='slowmo':
                    speed=clip.get('speed',clip.get('params',{}).get('speed',.5))
                    if 'source_time' in clip:
                        high=self.video_cfg[1]['duration']-.001 if self.video_cfg else float('inf')
                        right['source_time']=min(high,clip['source_time']+left*speed)
                    if 'source_duration' in clip:
                        right['source_duration']=max(.001,clip['source_duration']-left*speed); clip['source_duration']=left*speed
            elif clip['kind']=='freeze':
                right['source_time']=clip['source_time']; right['duration']=duration-left; clip['duration']=left
            else:
                speed=clip.get('speed',.5); right['source_time']=clip['source_time']+left*speed
                right['source_duration']=clip['source_duration']-left*speed; clip['source_duration']=left*speed
            self.item_list(kind).insert(self.item_list(kind).index(clip)+1,right)
            self.selection.discard((kind,clip['id'])); self.selection.add((kind,right['id'])); primary=(kind,right['id'])
        if primary: self.emit_primary(*primary)
        self.selectionChanged.emit(); self.changed.emit(); self.resize_content(); return True

    def can_split_at(self,time):
        return any(start+.05<time<start+duration-.05 for kind,clip in self.editable_selection()
                   for start,duration in [self.video_span(clip) if kind=='video' else self.effect_span(clip) if kind=='effect' else (clip['time'],clip['duration'])])

    def split(self): return self.split_selected()

    def duplicate_selected(self):
        items=self.editable_selection()
        if not items:
            self.statusMessage.emit('حدد المقاطع التي تريد نسخها أولًا.'); return False
        self.remember(); selected=set()
        for kind,clip in items:
            start,duration=self.video_span(clip) if kind=='video' else self.effect_span(clip) if kind=='effect' else (clip['time'],clip['duration'])
            duplicate=copy.deepcopy(clip); duplicate['id']=uuid.uuid4().hex; duplicate['time']=start+duration
            if kind=='effect' and 'animation_time' in duplicate: duplicate['animation_time']+=duration
            self.item_list(kind).append(duplicate); selected.add((kind,duplicate['id']))
        self.selection=selected; self.sync_selection(); self.changed.emit(); self.resize_content(); return True

    def trim_video(self,clip,time,left):
        if self.item_locked('video',clip): return
        start,duration=self.video_span(clip); amount=time-start
        if not .05<amount<duration-.05: return
        from engine.video_edits import sub_clip
        self.remember(); u=amount/duration
        if left: clip.update(sub_clip(clip,u,1)); clip['time']=time
        else: clip.update(sub_clip(clip,0,u))
        self.changed.emit(); self.resize_content()

    def remove_video(self,clip):
        self.select_item('video',clip['id']); return self.remove_selected()
