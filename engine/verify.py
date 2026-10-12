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
                     protected_time=.3, background_blur=14, background_particles=True,
                     effect_clips=[
                         dict(id='freeze1',kind='freeze',source_time=.2,duration=.4,params=dict(motion_style='hold')),
                         dict(id='freeze2',kind='freeze',source_time=.7,duration=.4,params=dict(motion_style='spin',orbit_angle=180)),
                         dict(id='bg',kind='background',time=0,duration=3,params=dict(background_template='particles',background_blur=14))])
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
        first=studio.add_effect('freeze',t=.2,source=.3)
        studio.effect_length.setValue(.3)
        studio.add_effect('freeze',t=.7,source=.8)
        studio.effect_length.setValue(.3)
        studio.add_builtin_sound('whoosh')
        studio.add_builtin_sound('impact')
        assert len(studio.config().effect_clips)==2 and len([c for c in studio.audio_clips if c.get('builtin')])==2
        canvas=studio.audio_timeline.canvas
        import copy
        before_video=copy.deepcopy(studio.video_clips); before_audio=copy.deepcopy(studio.audio_clips)
        studio.select_effect(first['id']); studio.effect_length.setValue(.2)
        assert studio.video_clips==before_video and studio.audio_clips==before_audio
        canvas.select_item('video',studio.video_clips[0]['id'])
        canvas.select_item('effect',first['id'],ctrl=True)
        canvas.position=.3
        assert canvas.split_selected() and len(studio.video_clips)==2 and len(studio.effect_clips)==3
        canvas.undo(); assert len(studio.video_clips)==1 and len(studio.effect_clips)==2
        canvas.set_lane_flag('motion','hidden',True)
        assert all(c['muted'] for c in studio.config().effect_clips)
        assert not any(c.get('muted') for c in studio.project_settings()['effect_clips'])
        canvas.set_lane_flag('motion','hidden',False)
        canvas.select_item('video',studio.video_clips[0]['id']); assert canvas.remove_selected()
        assert studio.config().video_clips==[]
        canvas.undo(); assert len(studio.video_clips)==1
        fixed_cfg=studio.config(); fixed_cfg.out=os.path.join(os.path.dirname(report),'fixed_timeline_check.mp4')
        fixed_cfg.output_height=360; fixed_cfg.output_fps=15; fixed_cfg.quality='draft'
        run_montage(fixed_cfg,log=lambda message:None)
        deadline=time.monotonic()+10
        while (studio.analysis_jobs or studio.fx_jobs or studio.proxy_jobs) and time.monotonic()<deadline:
            QApplication.processEvents(); time.sleep(.02)
        studio.close()
        QApplication.processEvents()
        if not playback:
            raise RuntimeError('The bundled video player did not decode a frame.')
        data = dict(ok=True, app=APP_DIR, ffmpeg=FFMPEG, ffprobe=FFPROBE,
                    output=info, frames=probe_frames(out), gui_playback=playback,
                    live_editor=True, selection_auto_commit=True,
                    repeated_effects=True,builtin_sound_clips=True,
                    independent_effect_trim=True,mixed_split=True,editable_video_delete=True,
                    lane_visibility_persistence=True,fixed_timeline_export=media_info(fixed_cfg.out))
    except Exception as ex:
        import traceback
        data = dict(ok=False, error=str(ex), traceback=traceback.format_exc())
    with open(report, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return data['ok']
