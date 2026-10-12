"""Source-coordinate selections with zoom, pan, and editable vertices."""
import copy
import math
import numpy as np
import cv2
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QPainterPath
from PySide6.QtWidgets import QWidget


class Viewport(QWidget):
    regionsChanged = Signal(list)
    objectsChanged = Signal(list)
    drawingChanged = Signal(int)
    zoomChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(360,180)
        self.image = QImage()
        self.regions, self.protected, self.polygon = [], [], []
        self.selecting = self.protecting = self.restored = False
        self.source = True
        self.drag = self.end = self.vertex = None
        self.zoom, self.pan = 1.0, QPointF()
        self.pan_start = None
        self.history = []
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip('عجلة الماوس للتكبير · الزر الأوسط أو Alt + سحب لتحريك الصورة · اسحب نقاط التحديد لتعديلها')

    def frame(self,image):
        self.image = image.copy()
        self.update()

    def bounds(self):
        if self.image.isNull():
            return QRectF()
        scale = min(self.width()/self.image.width(),self.height()/self.image.height())*self.zoom
        w,h = self.image.width()*scale,self.image.height()*scale
        return QRectF((self.width()-w)/2+self.pan.x(),(self.height()-h)/2+self.pan.y(),w,h)

    def normalized(self,pos):
        r=self.bounds()
        if r.isEmpty() or not r.contains(pos):
            return None
        return [(pos.x()-r.x())/r.width(),(pos.y()-r.y())/r.height()]

    def point(self,event):
        return self.normalized(event.position())

    def set_zoom(self,percent,anchor=None):
        anchor=anchor or QPointF(self.width()/2,self.height()/2)
        r=self.bounds()
        point=self.normalized(anchor)
        self.zoom=max(1,min(8,percent/100))
        if point:
            new=self.bounds()
            self.pan += anchor-QPointF(new.x()+point[0]*new.width(),new.y()+point[1]*new.height())
        self.zoomChanged.emit(round(self.zoom*100))
        self.update()

    def fit(self):
        self.pan=QPointF()
        self.zoom=1
        self.zoomChanged.emit(100)
        self.update()

    def wheelEvent(self,event):
        self.set_zoom(self.zoom*100*(1.2 if event.angleDelta().y()>0 else 1/1.2),event.position())
        event.accept()

    def paintEvent(self,event):
        p=QPainter(self)
        p.fillRect(self.rect(),QColor('#080c13'))
        if self.image.isNull():
            p.setPen(QColor('#9aa7bc'))
            p.drawText(self.rect(),Qt.AlignCenter,'افتح فيديو لتبدأ\nكل تعديل يظهر مباشرة')
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform,self.zoom<=1.3)
        image=self.image
        if self.source and self.restored and self.regions:
            from engine.hud import region_mask
            rgb=image.convertToFormat(QImage.Format_RGB888)
            data=np.frombuffer(rgb.constBits(),np.uint8).reshape(rgb.height(),rgb.bytesPerLine())
            raw=data[:,:rgb.width()*3].reshape(rgb.height(),rgb.width(),3).copy()
            raw=cv2.resize(raw,(min(960,raw.shape[1]),max(2,round(min(960,raw.shape[1])*raw.shape[0]/raw.shape[1]))))
            raw=cv2.inpaint(raw,region_mask(raw.shape,self.regions),3,cv2.INPAINT_TELEA)
            image=QImage(raw.data,raw.shape[1],raw.shape[0],raw.strides[0],QImage.Format_RGB888).copy()
        r=self.bounds()
        p.drawImage(r,image)
        if self.source and self.selecting:
            p.setPen(QPen(QColor('#ffb575'),2)); p.setBrush(QColor(255,170,100,24))
            for a in self.regions:
                p.drawRect(QRectF(r.x()+a[0]*r.width(),r.y()+a[1]*r.height(),(a[2]-a[0])*r.width(),(a[3]-a[1])*r.height()))
            if self.drag and self.end:
                p.drawRect(QRectF(QPointF(r.x()+self.drag[0]*r.width(),r.y()+self.drag[1]*r.height()),QPointF(r.x()+self.end[0]*r.width(),r.y()+self.end[1]*r.height())).normalized())
        if self.source and self.protecting:
            p.setRenderHint(QPainter.Antialiasing)
            for poly in self.protected+([self.polygon] if self.polygon else []):
                path=QPainterPath()
                path.moveTo(r.x()+poly[0][0]*r.width(),r.y()+poly[0][1]*r.height())
                for x,y in poly[1:]:
                    path.lineTo(r.x()+x*r.width(),r.y()+y*r.height())
                if poly is not self.polygon: path.closeSubpath()
                p.setPen(QPen(QColor('#6df0bd'),2))
                p.setBrush(QColor(70,230,165,22)); p.drawPath(path)
                p.setBrush(QColor('#101824'))
                for x,y in poly:
                    p.drawEllipse(QPointF(r.x()+x*r.width(),r.y()+y*r.height()),3.5,3.5)
        if self.zoom>1:
            p.fillRect(QRectF(12,12,175,25),QColor(8,12,19,190))
            p.setPen(QColor('#c7d9ef')); p.drawText(QRectF(18,12,163,25),Qt.AlignVCenter,f'{self.zoom*100:.0f}%  ·  تكبير للتحديد فقط')

    def nearest(self,pos):
        r=self.bounds()
        for i,poly in enumerate(self.protected):
            for j,(x,y) in enumerate(poly):
                if math.hypot(pos.x()-r.x()-x*r.width(),pos.y()-r.y()-y*r.height())<=8:
                    return i,j
        return None

    def remember(self):
        self.history.append(copy.deepcopy((self.protected,self.polygon)))
        self.history=self.history[-100:]

    def undo(self):
        if self.history:
            self.protected,self.polygon=self.history.pop()
        elif self.polygon: self.polygon.pop()
        elif self.protected: self.protected.pop()
        self.objectsChanged.emit(copy.deepcopy(self.protected))
        self.drawingChanged.emit(len(self.polygon)); self.update()

    def mousePressEvent(self,event):
        self.setFocus()
        if event.button()==Qt.MiddleButton or (event.button()==Qt.LeftButton and event.modifiers()&Qt.AltModifier):
            self.pan_start=(event.position(),QPointF(self.pan)); self.setCursor(Qt.ClosedHandCursor); return
        if not self.source: return
        if self.protecting:
            if event.button()==Qt.RightButton: self.finish_object(); return
            point=self.point(event)
            if point is None: return
            self.vertex=self.nearest(event.position())
            self.remember()
            if self.vertex is None:
                self.polygon.append(point); self.drawingChanged.emit(len(self.polygon))
            self.update()
        elif self.selecting and event.button()==Qt.LeftButton:
            self.drag=self.point(event); self.end=self.drag; self.update()

    def mouseMoveEvent(self,event):
        if self.pan_start:
            self.pan=self.pan_start[1]+event.position()-self.pan_start[0]; self.update(); return
        point=self.point(event)
        if self.vertex and point:
            i,j=self.vertex; self.protected[i][j]=point; self.update()
        elif self.drag:
            r=self.bounds()
            self.end=[float(np.clip((event.position().x()-r.x())/r.width(),0,1)),float(np.clip((event.position().y()-r.y())/r.height(),0,1))]
            self.update()

    def mouseReleaseEvent(self,event):
        if self.pan_start:
            self.pan_start=None; self.setCursor(Qt.CrossCursor if self.protecting or self.selecting else Qt.ArrowCursor)
        if self.vertex:
            self.vertex=None; self.objectsChanged.emit(copy.deepcopy(self.protected))
        if self.drag and self.end:
            a,b=self.drag,self.end
            rect=[min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])]
            if rect[2]-rect[0]>.001 and rect[3]-rect[1]>.001:
                self.regions.append(rect); self.regionsChanged.emit(copy.deepcopy(self.regions))
        self.drag=self.end=None; self.update()

    def mouseDoubleClickEvent(self,event):
        if self.protecting: self.finish_object()

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and self.protecting: self.finish_object()
        elif event.key()==Qt.Key_Escape:
            self.polygon=[]; self.drawingChanged.emit(0); self.update()
        elif event.key()==Qt.Key_Backspace or (event.key()==Qt.Key_Z and event.modifiers()&Qt.ControlModifier): self.undo()
        else: super().keyPressEvent(event)

    def finish_object(self):
        if len(self.polygon)>=3:
            self.protected.append(copy.deepcopy(self.polygon)); self.polygon=[]
            self.objectsChanged.emit(copy.deepcopy(self.protected))
            self.drawingChanged.emit(0)
        self.update()
