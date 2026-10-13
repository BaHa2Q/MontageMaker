"""Fixed-position video, gap and effect-split regressions without media files."""
import copy
import unittest
import numpy as np

from engine.editing import Config, effect_span, timeline, validate, video_span
from engine.live import LiveFrames, sample_output, source_to_output
from engine.effects import motion_config, timed_visuals
from engine.motion import Motion


INFO = dict(width=1920, height=1080, fps=30, duration=8.0, audio=False)


class VideoEventTests(unittest.TestCase):
    def config(self, video=None, effects=None):
        return Config(video_clips=[dict(id='video', time=0, source_in=0, duration=8)] if video is None else video,
                      effect_clips=effects or [], timeline_duration=8, trim_end=8, output_fps=30)

    def test_effect_move_and_resize_do_not_change_video_bounds(self):
        effect = dict(id='freeze', kind='freeze', time=1, source_time=2, duration=1, params={})
        cfg = self.config(effects=[effect])
        original = copy.deepcopy(cfg.video_clips)
        self.assertEqual(effect_span(cfg, INFO, effect), (1, 1))
        effect.update(time=3, duration=2)
        self.assertEqual(effect_span(cfg, INFO, effect), (3, 2))
        self.assertEqual(cfg.video_clips, original)
        self.assertEqual(video_span(cfg, INFO, cfg.video_clips[0]), (0, 8))
        self.assertEqual(timeline(cfg, 240)['total_frames'], 240)
        self.assertAlmostEqual(sample_output(cfg, INFO, 5)[0], 5)

    def test_deleted_video_stays_black_with_remaining_freeze(self):
        cfg = self.config(video=[], effects=[dict(id='freeze', kind='freeze', time=1,
                                                 source_time=2, duration=3, params={})])
        self.assertTrue(all(s['kind'] == 'gap' for s in timeline(cfg, 240)['segments']))
        self.assertEqual(sample_output(cfg, INFO, 2)[1], 'gap')
        frame, source = LiveFrames().render(cfg, INFO, 2, False, 360)
        self.assertEqual(int(frame.max()), 0)
        self.assertEqual(source, 0)

    def test_split_removal_leaves_a_gap_and_retains_later_source(self):
        cfg = self.config(video=[dict(id='right', time=4, source_in=4, duration=4)])
        self.assertEqual(sample_output(cfg, INFO, 2)[1], 'gap')
        source, phase, _, plan = sample_output(cfg, INFO, 6)
        self.assertAlmostEqual(source, 6)
        self.assertEqual(phase, 'video')
        self.assertEqual(plan['active_video_id'], 'right')
        self.assertEqual(plan['total_frames'], 240)

    def test_hold_and_slow_source_rates(self):
        cfg = self.config(video=[dict(id='hold', time=0, source_in=7, duration=3, speed=0),
                                 dict(id='slow', time=3, source_in=1, duration=4, speed=.5)])
        validate(cfg, INFO)
        self.assertEqual(sample_output(cfg, INFO, 2)[:2], (7, 'hold'))
        source, phase, _, sampled = sample_output(cfg, INFO, 5)
        self.assertEqual((source, phase), (2, 'ramp'))
        self.assertEqual(sampled['active_segment']['speed'], .5)
        self.assertEqual(source_to_output(cfg, INFO, 2), 5)

    def test_absolute_source_mapping_with_nonzero_global_trim(self):
        cfg = self.config()
        cfg.trim_start, cfg.trim_end = 2, 7
        validate(cfg, INFO)
        self.assertEqual(sample_output(cfg, INFO, 0)[0], 0)
        self.assertEqual(sample_output(cfg, INFO, 6)[0], 6)
        self.assertEqual(source_to_output(cfg, INFO, 1), 1)
        self.assertEqual(timeline(cfg, 150)['total_frames'], 240)

    def test_freeze_split_retains_original_motion_phase(self):
        original = dict(id='original', kind='freeze', time=1, source_time=2, duration=3,
                        params=dict(motion_style='spin', orbit_angle=180, rotation_direction=1, motion_strength=.65))
        cfg = self.config(effects=[original])
        left = copy.deepcopy(original)
        left.update(id='left', duration=1, animation_time=1, animation_duration=3)
        right = copy.deepcopy(original)
        right.update(id='right', time=2, duration=2, animation_time=1, animation_duration=3)
        split = self.config(effects=[left, right])
        before = sample_output(cfg, INFO, 2.5)
        after = sample_output(split, INFO, 2.5)
        self.assertEqual(before[:3], after[:3])
        self.assertEqual(before[3]['motion'], after[3]['motion'])
        frame = np.arange(24 * 32 * 3, dtype=np.uint8).reshape(24, 32, 3)
        reference = Motion(frame, motion_config(cfg, original), before[3]['motion']).frame(before[2])
        actual = Motion(frame, motion_config(split, right), after[3]['motion']).frame(after[2])
        np.testing.assert_array_equal(reference, actual)

    def test_visual_split_retains_original_flash_phase(self):
        event = dict(id='flash', kind='flash', time=1, duration=2, params={'strength':.8})
        cfg = self.config(effects=[event])
        left, right = copy.deepcopy(event), copy.deepcopy(event)
        left.update(id='left', duration=1, animation_time=1, animation_duration=2)
        right.update(id='right', time=2, duration=1, animation_time=1, animation_duration=2)
        split = self.config(effects=[left, right])
        frame = np.full((24, 32, 3), 60, np.uint8)
        np.testing.assert_array_equal(timed_visuals(frame, cfg, 2.5), timed_visuals(frame, split, 2.5))

    def test_motion_effect_moves_playing_video_without_freeze(self):
        event = dict(id='spin', kind='motion', time=1, duration=2,
                     params=dict(motion_style='spin', orbit_angle=180, rotation_direction=1, motion_strength=.65))
        cfg = self.config(effects=[event])
        validate(cfg, INFO)
        self.assertEqual(timeline(cfg, 240)['total_frames'], 240)
        self.assertAlmostEqual(sample_output(cfg, INFO, 2)[0], 2)
        frame = np.arange(24 * 32 * 3, dtype=np.uint8).reshape(24, 32, 3)
        self.assertFalse(np.array_equal(timed_visuals(frame, cfg, 2), frame))
        np.testing.assert_array_equal(timed_visuals(frame, cfg, 4), frame)

    def test_fast_video_event_skips_source(self):
        cfg = self.config(video=[dict(id='fast', time=0, source_in=0, duration=2, speed=2)])
        validate(cfg, INFO)
        self.assertAlmostEqual(sample_output(cfg, INFO, 1.5)[0], 3)
        self.assertEqual(sample_output(cfg, INFO, 1.5)[1], 'video')

    def test_speed_ramp_keeps_source_and_changes_gradually(self):
        from engine.video_edits import sub_clip, source_offset
        clip = dict(id='ramp', time=0, source_in=1, duration=4, speed=1.25, speed_points=[[0, .5], [1, 2]])
        cfg = self.config(video=[clip])
        validate(cfg, INFO)
        self.assertAlmostEqual(source_offset(clip, 1), 5)
        early = sample_output(cfg, INFO, .5)[3]['active_segment']['speed']
        late = sample_output(cfg, INFO, 3.5)[3]['active_segment']['speed']
        self.assertLess(early, 1)
        self.assertGreater(late, 1.5)
        self.assertAlmostEqual(sample_output(cfg, INFO, 4 - 1 / 30)[0], 6, delta=.1)
        self.assertAlmostEqual(source_to_output(cfg, INFO, 1 + source_offset(clip, .5)), 2, places=3)
        left, right = dict(clip, **sub_clip(clip, 0, .5)), dict(clip, **sub_clip(clip, .5, 1))
        self.assertAlmostEqual(source_offset(left, 1) + source_offset(right, 1), 5)
        self.assertAlmostEqual(right['source_in'], left['source_in'] + source_offset(left, 1))


if __name__ == '__main__':
    unittest.main()
