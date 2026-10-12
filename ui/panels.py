from PySide6.QtWidgets import QWidget,QVBoxLayout,QPushButton


class Section(QWidget):
    def __init__(self,title,opened=True,parent=None):
        super().__init__(parent)
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(6)
        self.header=QPushButton(title); self.header.setObjectName('sectionheader'); self.header.setCheckable(True); self.header.setChecked(opened)
        self.body=QWidget(); self.box=QVBoxLayout(self.body); self.box.setContentsMargins(3,4,3,8); self.box.setSpacing(9)
        self.header.toggled.connect(self.body.setVisible); self.body.setVisible(opened)
        layout.addWidget(self.header); layout.addWidget(self.body)

    def set_open(self,opened): self.header.setChecked(opened)
