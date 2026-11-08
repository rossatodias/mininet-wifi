import contextlib
import io
import os
import tempfile
import unittest

from experiment import config


def parse(*argv):
    return config.parse_args(['--coppelia', 'off'] + list(argv))


def rejected(*argv):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            parse(*argv)
        except SystemExit:
            return err.getvalue()
    raise AssertionError('accepted: %s' % (argv,))


class DefaultsTest(unittest.TestCase):
    def test_match_original_demo(self):
        cfg = parse()
        self.assertEqual(cfg.drones, 3)
        self.assertEqual(cfg.tech, 'adhoc')
        self.assertEqual(cfg.routing, 'batman_adv')
        self.assertEqual(cfg.model, 'logDistance')
        self.assertEqual(cfg.exp, 4.5)
        self.assertEqual(cfg.channel, 5)
        self.assertEqual(cfg.formation, 'scene')
        self.assertEqual(cfg.trajectory, 'scene')

    def test_default_pair_uses_last_drone(self):
        self.assertEqual(
            parse('--drones', '5', '--formation', 'line').pairs,
            (('dr1', 'dr5'),))

    def test_single_drone_has_no_pair(self):
        self.assertEqual(parse('--drones', '1').pairs, ())

    def test_routing_resolved_per_tech(self):
        self.assertEqual(parse('--tech', 'mesh').routing, 'none')
        self.assertEqual(parse('--tech', 'infra').routing, 'none')
        self.assertEqual(parse('--tech', 'adhoc').routing, 'batman_adv')

    def test_explicit_pairs(self):
        cfg = parse('--drones', '4', '--formation', 'line',
                    '--pairs', 'dr1:dr2,dr3:dr4')
        self.assertEqual(cfg.pairs, (('dr1', 'dr2'), ('dr3', 'dr4')))

    def test_output_dir_is_absolute(self):
        self.assertTrue(os.path.isabs(parse().output_dir))

    def test_config_is_frozen(self):
        with self.assertRaises(Exception):
            parse().drones = 4


class ValidationTest(unittest.TestCase):
    def test_drone_count_bounds(self):
        rejected('--drones', '0')
        rejected('--drones', '33')
        self.assertEqual(parse('--drones', '32', '--formation',
                               'line').drones, 32)

    def test_scene_formation_limited_to_three(self):
        self.assertIn('scene', rejected('--drones', '4'))
        parse('--drones', '4', '--formation', 'line')

    def test_pair_names(self):
        rejected('--pairs', 'a:b')
        rejected('--pairs', 'dr1:dr9')
        rejected('--pairs', 'dr1:dr1')
        rejected('--pairs', 'dr1')

    def test_incompatible_routing(self):
        rejected('--tech', 'mesh', '--routing', 'batman_adv')
        rejected('--tech', 'infra', '--routing', 'batman_adv')
        parse('--tech', 'mesh', '--routing', 'none')

    def test_positive_values(self):
        for flag in ('--spacing', '--duration', '--ping-count',
                     '--ping-interval', '--iperf-time', '--exp'):
            rejected(flag, '0')
        rejected('--warmup', '-1')
        rejected('--udp-bandwidth', 'fast')

    def test_propagation_flags_are_checked_up_front(self):
        self.assertIn('--variance', rejected(
            '--model', 'logNormalShadowing', '--variance', '3'))
        self.assertIn('--exp', rejected('--exp', '3.25'))
        self.assertIn('--nakagami-m', rejected(
            '--model', 'nakagami', '--nakagami-m', '0.4'))
        self.assertIn('--nakagami-m', rejected(
            '--model', 'nakagami', '--nakagami-m', '1.234'))
        parse('--model', 'friis', '--exp', '3.25', '--variance', '3')

    def test_follow_up_choices_are_accepted(self):
        parse('--formation', 'circle', '--trajectory', 'orbit',
              '--model', 'nakagami', '--tech', 'mesh')

    def test_output_dir_must_be_new_or_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(parse('--output-dir', d).output_dir, d)
            new = os.path.join(d, 'a', 'b')
            self.assertEqual(parse('--output-dir', new).output_dir, new)
            open(os.path.join(d, 'f'), 'w').close()
            self.assertIn('--output-dir', rejected('--output-dir', d + '/.'))
            self.assertIn('--output-dir',
                          rejected('--output-dir', os.path.join(d, 'f')))

    def test_coppelia_dir_and_scene_are_checked(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    config.parse_args(['--coppelia-dir', d])
            open(os.path.join(d, 'coppeliaSim.sh'), 'w').close()
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    config.parse_args(['--coppelia-dir', d,
                                       '--scene', os.path.join(d, 'x.ttt')])
            scene = os.path.join(d, 'x.ttt')
            open(scene, 'w').close()
            cfg = config.parse_args(['--coppelia-dir', d, '--scene', scene])
            self.assertEqual(cfg.coppelia_dir, d)

    def test_coppelia_dir_from_environment(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'coppeliaSim.sh'), 'w').close()
            old = os.environ.get('COPPELIASIM_ROOT')
            os.environ['COPPELIASIM_ROOT'] = d
            try:
                cfg = config.parse_args([])
            finally:
                if old is None:
                    del os.environ['COPPELIASIM_ROOT']
                else:
                    os.environ['COPPELIASIM_ROOT'] = old
            self.assertEqual(cfg.coppelia_dir, d)


if __name__ == '__main__':
    unittest.main()
