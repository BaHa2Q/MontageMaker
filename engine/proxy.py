"""Automatic edit proxies; changes remain visible while preparation runs."""
import hashlib
import os
import tempfile
import threading
from PySide6.QtCore import QThread,Signal
from .editing import media_info
from .util import FFMPEG,CACHE_DIR,run,Cancelled


class EditProxy(QThread):
    ready=Signal(str,str,object)
    failed=Signal(str)

    def __init__(self,path,parent=None):
        super().__init__(parent); self.path=path; self.cancel=threading.Event()

    def run(self):
        try:
            info=media_info(self.path)
            stat=os.stat(self.path)
            key=hashlib.sha256(str((self.path,stat.st_size,stat.st_mtime_ns)).encode()).hexdigest()[:24]
            folder=os.path.join(CACHE_DIR,'edit_proxies'); os.makedirs(folder,exist_ok=True)
            output=os.path.join(folder,key+'.avi')
            if not os.path.isfile(output):
                fd,temp=tempfile.mkstemp(suffix='.avi',dir=folder); os.close(fd)
                try:
                    height=min(540,info['height']); width=max(2,round(height*info['width']/info['height']/2)*2)
                    run([FFMPEG,'-v','error','-y','-i',self.path,'-vf',f'scale={width}:{height},fps=30',
                         '-an','-c:v','mjpeg','-q:v','7',temp],cancel=self.cancel)
                    if self.cancel.is_set(): raise Cancelled('cancelled')
                    if not os.path.isfile(output): os.replace(temp,output)
                finally:
                    if os.path.isfile(temp): os.unlink(temp)
            self.ready.emit(self.path,output,media_info(output))
        except Cancelled: pass
        except Exception as ex: self.failed.emit(str(ex))
