"""Source/output mapping regressions; no media files or audio device required.

Run from the source root: python -m unittest discover -s tests -v
"""
import copy
import unittest

from engine.editing import Config, effect_span, timeline, validate
from engine.live import sample_output, source_to_output


INFO = dict(width=1920, height=1080, fps=30, duration=8.0, audio=False)
FPS = 30
SOURCE_COUNT = round(INFO['duration'] * FPS)


def freeze(ident, source, duration):
    return dict(id=ident, kind='freeze', source_time=source, duration=duration,
                params=dict(motion_style='hold'))


def slowmo(ident, source, duration, speed=.5):
    return dict(id=ident, kind='slowmo', source_time=source,
                source_duration=duration, speed=speed, params={})


class TimedEffectsTests(unittest.TestCase):
    def config(self, clips):
        return Config(effect_clips=clips, trim_end=INFO['duration'], output_fps=FPS)

    def test_empty_effect_list_has_no_implicit_freeze_or_slowmo(self):
        cfg = self.config([])
        # Old global switches remain true by default and must not add effects.
        self.assertTrue(cfg.animation)
        self.assertTrue(cfg.slowmo)
        plan = timeline(cfg, SOURCE_COUNT)
        self.assertEqual(plan['total_frames'], SOURCE_COUNT)
        self.assertEqual([s['kind'] for s in plan['segments']], ['video'])
        source, phase, _, _ = sample_output(cfg, INFO, cfg.freeze_time)
        self.assertEqual(phase, 'video')
        self.assertAlmostEqual(source, cfg.freeze_time)
        validate(cfg, INFO)

    def test_two_freezes_have_exact_frames_and_keep_final_source(self):
        clips = [freeze('first', 1, 1.5), freeze('second', 5, 2)]
        cfg = self.config(clips)
        plan = timeline(cfg, SOURCE_COUNT)
        self.assertEqual(plan['total_frames'], SOURCE_COUNT - 2 + 45 + 60)
        holds = [s for s in plan['segments'] if s['kind'] == 'freeze']
        self.assertEqual([s['clip']['id'] for s in holds], ['first', 'second'])
        for segment, clip in zip(holds, clips):
            start, duration = effect_span(cfg, INFO, clip)
            self.assertAlmostEqual(start, segment['output_start'] / FPS)
            self.assertAlmostEqual(duration, clip['duration'])
            source, phase, _, sampled = sample_output(cfg, INFO, start)
            self.assertAlmostEqual(source, clip['source_time'])
            self.assertEqual(phase, 'motion')
            self.assertEqual(sampled['active_motion']['id'], clip['id'])
        last = plan['segments'][-1]
        self.assertEqual(last['kind'], 'video')
        self.assertEqual(last['source_end'], SOURCE_COUNT)
        source, phase, _, _ = sample_output(cfg, INFO, (plan['total_frames'] - 1) / FPS)
        self.assertEqual(phase, 'video')
        self.assertAlmostEqual(source, (SOURCE_COUNT - 1) / FPS)
        validate(cfg, INFO)

    def test_independent_slowmo_maps_source_and_output(self):
        clip = slowmo('slow', 2, 1, .25)
        cfg = self.config([clip])
        plan = timeline(cfg, SOURCE_COUNT)
        self.assertEqual(plan['total_frames'], SOURCE_COUNT - FPS + 4 * FPS)
        self.assertEqual(effect_span(cfg, INFO, clip), (2, 4))
        source, phase, _, sampled = sample_output(cfg, INFO, 3.5)
        self.assertEqual(phase, 'ramp')
        self.assertAlmostEqual(source, 2.375)
        self.assertEqual(sampled['active_segment']['kind'], 'slowmo')
        self.assertAlmostEqual(sampled['active_segment']['speed'], .25)
        self.assertAlmostEqual(source_to_output(cfg, INFO, 2.5), 4)
        self.assertAlmostEqual(sample_output(cfg, INFO, 6)[0], 3)
        validate(cfg, INFO)

    def test_same_anchor_freezes_play_in_saved_order_and_skip_frame_once(self):
        clips = [freeze('first', 2, 1), freeze('second', 2, .5)]
        cfg = self.config(clips)
        plan = timeline(cfg, SOURCE_COUNT)
        self.assertEqual(plan['total_frames'], SOURCE_COUNT - 1 + 30 + 15)
        self.assertEqual(effect_span(cfg, INFO, clips[0]), (2, 1))
        self.assertEqual(effect_span(cfg, INFO, clips[1]), (3, .5))
        self.assertEqual(sample_output(cfg, INFO, 2.5)[3]['active_motion']['id'], 'first')
        self.assertEqual(sample_output(cfg, INFO, 3.25)[3]['active_motion']['id'], 'second')
        self.assertAlmostEqual(sample_output(cfg, INFO, 3.5)[0], 2 + 1 / FPS)
        validate(cfg, INFO)

    def test_freeze_inside_slowmo_preserves_both_source_sides(self):
        cfg = self.config([slowmo('slow', 1, 2, .5), freeze('hold', 2, 1)])
        plan = timeline(cfg, SOURCE_COUNT)
        # A slowed source frame would occupy two output frames; the hold replaces it.
        self.assertEqual(plan['total_frames'], SOURCE_COUNT + 2 * FPS - 2 + FPS)
        slow_segments = [s for s in plan['segments'] if s['kind'] == 'slowmo']
        self.assertEqual(len(slow_segments), 2)
        self.assertEqual(slow_segments[0]['source_start'], FPS)
        self.assertEqual(slow_segments[-1]['source_end'], 3 * FPS)
        self.assertAlmostEqual(sample_output(cfg, INFO, plan['duration'] - 1 / FPS)[0],
                               (SOURCE_COUNT - 1) / FPS)

    def test_enabled_slowmo_overlap_rejected_but_muted_overlap_ignored(self):
        cfg = self.config([slowmo('one', 1, 2), slowmo('two', 2.5, 1)])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            validate(cfg, INFO)
        cfg.effect_clips[1]['muted'] = True
        validate(cfg, INFO)
        expected = self.config([copy.deepcopy(cfg.effect_clips[0])])
        self.assertEqual(timeline(cfg, SOURCE_COUNT)['total_frames'],
                         timeline(expected, SOURCE_COUNT)['total_frames'])

    def test_none_preserves_legacy_freeze_and_ramp(self):
        cfg = self.config(None)
        self.assertIsNone(cfg.effect_clips)
        plan = timeline(cfg, SOURCE_COUNT)
        self.assertEqual(plan['ff'], 90)
        self.assertEqual(plan['ramp_src'], 15)
        self.assertEqual(plan['ramp_out'], 34)
        self.assertEqual(plan['total_frames'], 75 + 34 + 90 + 149)
        source, phase, _, _ = sample_output(cfg, INFO, plan['freeze'])
        self.assertEqual(phase, 'motion')
        self.assertAlmostEqual(source, cfg.freeze_time)
        self.assertAlmostEqual(source_to_output(cfg, INFO, cfg.freeze_time), plan['freeze'])
        cfg.animation = False
        self.assertEqual(timeline(cfg, SOURCE_COUNT)['total_frames'], SOURCE_COUNT)
        validate(cfg, INFO)


if __name__ == '__main__':
    unittest.main()
