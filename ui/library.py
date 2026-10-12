"""Left navigation and media/template library."""
import os
from PySide6.QtCore import Qt,Signal,QSize,QMimeData,QUrl,QPointF
from PySide6.QtGui import QIcon,QPixmap,QPainter,QColor,QPen,QPainterPath,QDrag,QLinearGradient
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QToolButton,QLabel,QPushButton,
    QLineEdit,QListWidget,QListWidgetItem,QStackedWidget,QScrollArea,QGridLayout,QMenu,QButtonGroup)
from engine.templates import MOTION_TEMPLATES,BACKGROUND_TEMPLATES
from engine.sound import BUILTIN_SOUNDS


def icon_for(key):
    pix=QPixmap(32,32); pix.fill(Qt.transparent)
    p=QPainter(pix); p.setRenderHint(QPainter.Antialiasing); p.setPen(QPen(QColor('#9bbddd'),2)); p.setBrush(Qt.NoBrush)
    if key=='media':
        path=QPainterPath(); path.moveTo(3,10); path.lineTo(3,26); path.lineTo(28,26); path.lineTo(28,9); path.lineTo(15,9); path.lineTo(12,5); path.lineTo(3,5); path.closeSubpath(); p.drawPath(path)
    elif key=='audio':
        p.drawLine(12,8,12,24); p.drawLine(26,5,26,20); p.drawLine(12,8,26,5); p.drawEllipse(5,21,7,5); p.drawEllipse(19,17,7,5)
    elif key=='motion':
        p.drawEllipse(5,5,22,22); p.drawLine(22,4,28,7); p.drawLine(28,7,25,13)
    elif key=='background':
        p.drawRoundedRect(3,4,26,24,3,3); p.drawEllipse(8,8,5,5); p.drawLine(4,25,15,15); p.drawLine(15,15,28,25)
    elif key=='mask':
        for x,y,dx,dy in [(4,4,7,7),(28,4,-7,7),(4,28,7,-7),(28,28,-7,-7)]: p.drawLine(x,y,x+dx,y); p.drawLine(x,y,x,y+dy)
        p.drawEllipse(12,12,8,8)
    else:
        p.drawLine(16,4,16,21); p.drawLine(10,15,16,21); p.drawLine(22,15,16,21); p.drawLine(4,23,4,28); p.drawLine(4,28,28,28); p.drawLine(28,28,28,23)
    p.end(); return QIcon(pix)


class AssetList(QListWidget):
    used=Signal(str)
    removed=Signal(str)

    def __init__(self):
        super().__init__(); self.setViewMode(QListWidget.IconMode); self.setResizeMode(QListWidget.Adjust)
        self.setIconSize(QSize(120,68)); self.setGridSize(QSize(136,110)); self.setSpacing(5)
        self.setWordWrap(True); self.setDragEnabled(True); self.setLayoutDirection(Qt.LeftToRight)
        self.itemDoubleClicked.connect(lambda item:self.used.emit(item.data(Qt.UserRole)))
        self.setContextMenuPolicy(Qt.CustomContextMenu); self.customContextMenuRequested.connect(self.context)

    def startDrag(self,actions):
        item=self.currentItem()
        if item:
            drag=QDrag(self); mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(item.data(Qt.UserRole))]); drag.setMimeData(mime); drag.exec(Qt.CopyAction)

    def context(self,pos):
        item=self.itemAt(pos)
        if not item: return
        kind=item.data(Qt.UserRole+1)
        menu=QMenu(self); action=menu.addAction('فتح الفيديو كمصدر' if kind=='video' else 'استخدام كخلفية' if kind=='image' else 'إضافة الصوت للمسار')
        menu.addSeparator(); remove=menu.addAction('إزالة من الوسائط'); chosen=menu.exec(self.mapToGlobal(pos))
        if chosen==action: self.used.emit(item.data(Qt.UserRole))
        elif chosen==remove: self.removed.emit(item.data(Qt.UserRole))

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Delete,Qt.Key_Backspace) and self.currentItem():
            self.removed.emit(self.currentItem().data(Qt.UserRole)); event.accept()
        else: super().keyPressEvent(event)


class Library(QWidget):
    imported=Signal(); used=Signal(str); removed=Signal(str); motion=Signal(str); background=Signal(str,str); page=Signal(str); sound=Signal(str)

    def __init__(self):
        super().__init__(); self.setObjectName('library'); self.assets={}
        root=QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(7); self.setLayoutDirection(Qt.LeftToRight)
        rail=QWidget(); rail.setObjectName('navrail'); rail.setFixedWidth(78); nav=QVBoxLayout(rail); nav.setContentsMargins(2,4,2,4); nav.setSpacing(6)
        self.stack=QStackedWidget(); self.nav_buttons={}; self.nav_group=QButtonGroup(self)
        for i,(key,title) in enumerate([('media','الوسائط'),('motion','الحركة'),('background','الخلفيات'),('audio','الصوت'),('mask','التحديد'),('export','التصدير')]):
            btn=QToolButton(); btn.setObjectName('navitem'); btn.setText(title); btn.setIcon(icon_for(key)); btn.setIconSize(QSize(30,30)); btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setCheckable(True); btn.setMinimumHeight(71); btn.setFixedWidth(74); self.nav_group.addButton(btn); self.nav_buttons[key]=btn
            btn.clicked.connect(lambda checked=False,k=key,n=i:self.select_page(k,n)); nav.addWidget(btn)
        nav.addStretch(); root.addWidget(rail); root.addWidget(self.stack,1)
        self.media_page,self.list,self.search=self.media_panel('الوسائط','استيراد وسائط')
        self.stack.addWidget(self.media_page)
        self.motion_page=QWidget(); grid=QGridLayout(self.motion_page); grid.setContentsMargins(8,10,8,10)
        for i,(key,title,glyph) in enumerate(MOTION_TEMPLATES):
            btn=QPushButton(f'{glyph}\n{title}'); btn.setMinimumHeight(95); btn.setObjectName('templatecard')
            btn.clicked.connect(lambda checked=False,k=key:self.motion.emit(k)); grid.addWidget(btn,i//2,i%2)
        grid.setRowStretch(4,1); self.stack.addWidget(self.scroll(self.motion_page))
        self.background_buttons={}; self.background_group=QButtonGroup(self)
        bg=QWidget(); grid=QGridLayout(bg); grid.setContentsMargins(8,10,8,10); grid.setSpacing(7)
        for i,(key,title,color) in enumerate(BACKGROUND_TEMPLATES):
            btn=QPushButton(title); btn.setObjectName('templatecard'); btn.setMinimumHeight(74); btn.setCheckable(True)
            btn.setStyleSheet(f'QPushButton {{border-top:3px solid {color};}} QPushButton:checked {{background:#254b68;border:1px solid {color};border-top:3px solid {color};}}')
            btn.clicked.connect(lambda checked=False,k=key,c=color:self.background.emit(k,c)); self.background_group.addButton(btn); self.background_buttons[key]=btn; grid.addWidget(btn,i//2,i%2)
        self.background_buttons['none'].setChecked(True); grid.setRowStretch(7,1); self.stack.addWidget(self.scroll(bg))
        self.audio_page,self.audio_list,_=self.media_panel('أصواتك','استيراد أصوات'); self.stack.addWidget(self.audio_page)
        sound_panel=QWidget(); sound_grid=QGridLayout(sound_panel); sound_grid.setContentsMargins(0,0,0,0)
        for i,(key,title,duration) in enumerate(BUILTIN_SOUNDS):
            btn=QPushButton('♫ '+title); btn.setMinimumHeight(36); btn.setToolTip('إضافة مقطع صوتي مستقل عند المؤشر')
            btn.clicked.connect(lambda checked=False,k=key:self.sound.emit(k)); sound_grid.addWidget(btn,i//2,i%2)
        self.audio_page.layout().insertWidget(2,sound_panel)
        for title,desc in [('تحديد العنصر','اختَر «حماية عنصر» فوق شاشة الفيديو. عجلة الماوس للتكبير، واسحب أي نقطة لتصحيح الحدود. رسمك يثبت تلقائيًا عند الانتقال.'),('التصدير','شاهد التعديل مباشرة ثم اختَر جودة الفيديو من الخصائص على اليمين.')]:
            panel=QWidget(); box=QVBoxLayout(panel); heading=QLabel(title); heading.setObjectName('section'); box.addWidget(heading); text=QLabel(desc); text.setWordWrap(True); box.addWidget(text); box.addStretch(); self.stack.addWidget(panel)
        self.nav_buttons['media'].setChecked(True)
        self.list.used.connect(self.used); self.audio_list.used.connect(self.used)
        self.list.removed.connect(self.removed); self.audio_list.removed.connect(self.removed)

    def scroll(self,widget):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(widget); return scroll

    def select_page(self,key,index):
        self.stack.setCurrentIndex(index); self.page.emit(key)

    def media_panel(self,title,button_text):
        panel=QWidget(); panel.setLayoutDirection(Qt.RightToLeft); box=QVBoxLayout(panel); box.setContentsMargins(9,10,9,9)
        header=QHBoxLayout(); caption=QLabel(title); caption.setObjectName('section'); header.addWidget(caption); header.addStretch()
        btn=QPushButton(button_text); btn.setObjectName('accent'); btn.clicked.connect(self.imported); header.addWidget(btn); box.addLayout(header)
        search=QLineEdit(); search.setPlaceholderText('بحث في ملفاتك…'); box.addWidget(search)
        items=AssetList(); box.addWidget(items,1)
        search.textChanged.connect(lambda value:[items.item(i).setHidden(value.casefold() not in items.item(i).text().casefold()) for i in range(items.count())])
        hint=QLabel('دبل كليك للإضافة، أو اسحب الصوت للمسار. Delete يزيل العنصر من المكتبة فقط.'); hint.setWordWrap(True); hint.setObjectName('muted'); box.addWidget(hint)
        return panel,items,search

    def add_file(self,path,kind=None):
        if path in self.assets: return
        ext=os.path.splitext(path)[1].lower()
        kind=kind or ('audio' if ext in ('.mp3','.wav','.m4a','.aac','.ogg','.flac','.opus') else 'image' if ext in ('.png','.jpg','.jpeg','.bmp','.webp') else 'video')
        pix=QPixmap(120,68); pix.fill(QColor('#16263a')); p=QPainter(pix); p.setPen(QColor('#72bfff')); p.drawText(pix.rect(),Qt.AlignCenter,'♫' if kind=='audio' else '▣'); p.end()
        icon=QIcon(pix)
        if kind=='image':
            picture=QPixmap(path)
            if not picture.isNull(): icon=QIcon(picture.scaled(120,68,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        item=QListWidgetItem(icon,os.path.basename(path)); item.setData(Qt.UserRole,path); item.setData(Qt.UserRole+1,kind); item.setToolTip(path); self.list.addItem(item)
        audio_item=None
        if kind=='audio':
            audio_item=QListWidgetItem(icon,os.path.basename(path)); audio_item.setData(Qt.UserRole,path); audio_item.setData(Qt.UserRole+1,kind); self.audio_list.addItem(audio_item)
        self.assets[path]={'kind':kind,'item':item,'audio_item':audio_item}

    def metadata(self,path,data):
        self.add_file(path)
        asset=self.assets[path]; icon=None
        if data.get('thumbnails'):
            icon=QIcon(QPixmap.fromImage(data['thumbnails'][0][1]))
        elif data.get('wave'):
            wave=data['wave']; pix=QPixmap(120,68); pix.fill(QColor('#142033')); p=QPainter(pix); p.setPen(QPen(QColor('#8f91ff'),1))
            for x in range(120):
                amp=min(1,wave[min(len(wave)-1,int(x/120*len(wave)))])*28
                p.drawLine(QPointF(x,34-amp),QPointF(x,34+amp))
            p.end(); icon=QIcon(pix)
        for item in (asset['item'],asset['audio_item']):
            if item:
                if icon: item.setIcon(icon)
                item.setText(f'{os.path.basename(path)}\n{int(data["duration"]//60):02}:{int(data["duration"]%60):02}')

    def remove_file(self,path):
        asset=self.assets.pop(path,None)
        if not asset: return False
        for items,key in ((self.list,'item'),(self.audio_list,'audio_item')):
            item=asset.get(key)
            if item is not None: items.takeItem(items.row(item))
        return True
