"""Montage Maker Studio command line; the GUI exposes the same edit settings."""
import argparse
from engine.pipeline import Config, run_montage


def main():
    parser = argparse.ArgumentParser(description='Trim, rotate, restore selected HUD, and mix your own audio.')
    parser.add_argument('video')
    parser.add_argument('--trim-start', type=float, default=0)
    parser.add_argument('--trim-end', type=float)
    parser.add_argument('--freeze', type=float, default=3, help='Absolute position in the original video, in seconds.')
    parser.add_argument('--orbit', type=float, default=3, help='Duration of the 2D freeze animation.')
    parser.add_argument('--angle', type=float, default=360, help='2D rotation in degrees: 0, 90, 180, 360, ...')
    parser.add_argument('--clockwise', action='store_true')
    parser.add_argument('--no-animation', action='store_true')
    parser.add_argument('--music')
    parser.add_argument('--music-start', type=float, default=0)
    parser.add_argument('--drop', type=float)
    parser.add_argument('--music-volume', type=float, default=.55)
    parser.add_argument('--game-volume', type=float, default=.6)
    parser.add_argument('--fx-volume', type=float, default=.6)
    parser.add_argument('--sound', nargs=3, action='append', default=[], metavar=('FILE', 'TIME', 'VOLUME'),
                        help='Sound effect file, start time in the output, and volume between 0 and 2.')
    parser.add_argument('--hud-box', type=float, nargs=4, action='append', default=[],
                        metavar=('X0', 'Y0', 'X1', 'Y1'), help='Selected HUD rectangle, coordinates normalized to 0..1.')
    parser.add_argument('--hud-method', choices=['temporal', 'fast'], default='temporal')
    parser.add_argument('--no-slowmo', action='store_true')
    parser.add_argument('--no-grade', action='store_true')
    parser.add_argument('--bars', action='store_true')
    parser.add_argument('--glow', action='store_true')
    parser.add_argument('--scan', action='store_true')
    parser.add_argument('--no-flash', action='store_true')
    parser.add_argument('--no-shake', action='store_true')
    parser.add_argument('--no-game-audio', action='store_true')
    parser.add_argument('--no-fx', action='store_true')
    parser.add_argument('--protect-box', type=float, nargs=4, action='append', default=[],
                        metavar=('X0', 'Y0', 'X1', 'Y1'), help='Normalized rectangle for the object that remains clear.')
    parser.add_argument('--protect-at', type=float)
    parser.add_argument('--background-blur', type=float, default=0)
    parser.add_argument('--background-dim', type=float, default=0)
    parser.add_argument('--background-desaturate', action='store_true')
    parser.add_argument('--background-glow', action='store_true')
    parser.add_argument('--background-particles', action='store_true')
    parser.add_argument('--background-scope', choices=['freeze', 'all'], default='freeze')
    parser.add_argument('--resolution', type=int, choices=[720, 1080, 1440, 2160], default=1080)
    parser.add_argument('--fps', type=int, choices=[24, 30, 60], default=60)
    parser.add_argument('--quality', choices=['small', 'high', 'best'], default='high')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--out')
    args = parser.parse_args()
    cfg = Config(video=args.video, out=args.out, trim_start=args.trim_start, trim_end=args.trim_end,
                 freeze_time=args.freeze, orbit_seconds=args.orbit, orbit_angle=args.angle,
                 rotation_direction=-1 if args.clockwise else 1, animation=not args.no_animation,
                 music=args.music, music_start=args.music_start, drop_in_song=args.drop,
                 music_volume=args.music_volume, game_volume=args.game_volume, fx_volume=args.fx_volume,
                 sound_effects=[dict(path=p, time=float(t), volume=float(v)) for p, t, v in args.sound],
                 remove_hud=bool(args.hud_box), hud_regions=args.hud_box, hud_method=args.hud_method,
                 slowmo=not args.no_slowmo, clarity=not args.no_grade, bars=args.bars, glow=args.glow,
                 scan=args.scan, flash=not args.no_flash, shake=not args.no_shake,
                 game_audio=not args.no_game_audio, builtin_fx=not args.no_fx,
                 protected_polygons=[[[x0,y0],[x1,y0],[x1,y1],[x0,y1]] for x0,y0,x1,y1 in args.protect_box],
                 protected_time=args.protect_at, background_blur=args.background_blur,
                 background_dim=args.background_dim, background_desaturate=args.background_desaturate,
                 background_glow=args.background_glow, background_particles=args.background_particles,
                 background_scope=args.background_scope,
                 output_height=args.resolution, output_fps=args.fps, quality=args.quality)
    if args.preview:
        import os
        cfg = cfg.draft(args.out or os.path.splitext(args.video)[0] + '_preview.mp4')
    run_montage(cfg)


if __name__ == '__main__':
    main()
