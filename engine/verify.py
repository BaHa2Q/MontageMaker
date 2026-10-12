"""Internal distribution check, also usable by the Windows installer build."""
import json
import os
from .editing import Config, media_info
from .render import run_montage
from .util import APP_DIR, FFMPEG, FFPROBE, probe_frames


def verify_distribution(video, report):
    try:
        out = os.path.join(os.path.dirname(report), 'installed_check.mp4')
        cfg = Config(video=video, out=out, trim_start=0, trim_end=1,
                     freeze_time=.3, orbit_seconds=.8, orbit_angle=180,
                     output_height=360, output_fps=15, quality='draft',
                     builtin_fx=False, game_audio=False, slowmo=False,
                     protected_polygons=[[[.42,.38],[.7,.38],[.7,.8],[.42,.8]]],
                     protected_time=.3, background_blur=14, background_particles=True)
        run_montage(cfg, log=lambda message: None)
        info = media_info(out)
        # Check the bundled GUI and multimedia plugins, not only the rendering libraries.
        import time
        from PySide6.QtWidgets import QApplication
        from app import Studio
        studio = Studio()
        studio.choose_video(out)
        studio.builtin_fx.setChecked(False)
        deadline = time.monotonic() + 8
        while studio.view.image.isNull() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(.02)
        playback = not studio.view.image.isNull()
        cfg = studio.config()
        assert hasattr(studio,'audio_timeline') and hasattr(studio,'zoom_slider')
        studio.view.polygon=[[.3,.3],[.6,.3],[.6,.8],[.3,.8]]
        studio.source_position=.3
        studio.commit_selection()
        assert len(studio.config().protected_polygons)==1
        deadline=time.monotonic()+10
        while (studio.analysis_jobs or studio.fx_jobs or studio.proxy_jobs) and time.monotonic()<deadline:
            QApplication.processEvents(); time.sleep(.02)
        studio.close()
        QApplication.processEvents()
        if not playback:
            raise RuntimeError('The bundled video player did not decode a frame.')
        data = dict(ok=True, app=APP_DIR, ffmpeg=FFMPEG, ffprobe=FFPROBE,
                    output=info, frames=probe_frames(out), gui_playback=playback,
                    live_editor=True, selection_auto_commit=True)
    except Exception as ex:
        import traceback
        data = dict(ok=False, error=str(ex), traceback=traceback.format_exc())
    with open(report, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return data['ok']
