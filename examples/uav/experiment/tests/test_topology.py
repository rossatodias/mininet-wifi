import math
import unittest

from experiment import config, topology

CENTER = topology.CENTER


def cfg(*argv):
    return config.parse_args(['--coppelia', 'off'] + list(argv))


def centroid(points):
    n = len(points)
    return tuple(sum(p[i] for p in points) / n for i in range(3))


def min_separation(points):
    return min(math.dist(a, b) for i, a in enumerate(points)
               for b in points[i + 1:])


class FormationTest(unittest.TestCase):
    def assertPoint(self, actual, expected):
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, places=9)

    def test_counts_and_plane(self):
        for kind in ('line', 'grid', 'circle', 'star', 'random'):
            for n in (1, 2, 3, 8, 13, 32):
                pos = topology.formation(kind, n, 20.0, 1)
                self.assertEqual(len(pos), n, (kind, n))
                self.assertEqual({p[2] for p in pos}, {0.0}, (kind, n))

    def test_deterministic(self):
        for kind in ('line', 'grid', 'circle', 'star', 'random'):
            self.assertEqual(topology.formation(kind, 6, 15.0, 3),
                             topology.formation(kind, 6, 15.0, 3))

    def test_centred(self):
        for kind in ('line', 'grid', 'circle', 'star'):
            for n in (1, 2, 3, 5, 8):
                if kind == 'star' and n > 1:
                    continue
                self.assertPoint(
                    centroid(topology.formation(kind, n, 20.0, 1)), CENTER)

    def test_line(self):
        pos = topology.formation('line', 4, 15.0, 1)
        self.assertEqual([p[1:] for p in pos], [(50.0, 0.0)] * 4)
        for a, b in zip(pos, pos[1:]):
            self.assertAlmostEqual(b[0] - a[0], 15.0)
        self.assertPoint(pos[0], (27.5, 50.0, 0.0))

    def test_grid(self):
        pos = topology.formation('grid', 9, 10.0, 1)
        self.assertPoint(pos[0], (40.0, 40.0, 0.0))
        self.assertPoint(pos[1], (50.0, 40.0, 0.0))
        self.assertPoint(pos[3], (40.0, 50.0, 0.0))
        self.assertPoint(pos[8], (60.0, 60.0, 0.0))

    def test_grid_columns_for_partial_rows(self):
        pos = topology.formation('grid', 5, 10.0, 1)
        self.assertEqual(len({round(p[1], 6) for p in pos}), 2)
        self.assertEqual(len({round(p[0], 6) for p in pos}), 3)
        self.assertAlmostEqual(math.dist(pos[0], pos[1]), 10.0)
        self.assertAlmostEqual(math.dist(pos[0], pos[3]), 10.0)

    def test_circle_neighbour_chord(self):
        for n in (2, 3, 4, 7, 12):
            pos = topology.formation('circle', n, 20.0, 1)
            ring = pos + pos[:1] if n > 2 else pos
            for a, b in zip(ring, ring[1:]):
                self.assertAlmostEqual(math.dist(a, b), 20.0, msg=n)

    def test_circle_single_drone(self):
        self.assertEqual(topology.formation('circle', 1, 20.0, 1), [CENTER])

    def test_star(self):
        pos = topology.formation('star', 6, 20.0, 1)
        self.assertEqual(pos[0], CENTER)
        for p in pos[1:]:
            self.assertAlmostEqual(math.dist(p, CENTER), 20.0)
        self.assertAlmostEqual(
            min_separation(pos[1:]), 2 * 20.0 * math.sin(math.pi / 5))

    def test_star_single_drone(self):
        self.assertEqual(topology.formation('star', 1, 20.0, 1), [CENTER])

    def test_random_same_seed(self):
        self.assertEqual(topology.formation('random', 8, 20.0, 7),
                         topology.formation('random', 8, 20.0, 7))

    def test_random_other_seed(self):
        self.assertNotEqual(topology.formation('random', 8, 20.0, 7),
                            topology.formation('random', 8, 20.0, 8))

    def test_random_bounds_and_separation(self):
        for seed in range(20):
            pos = topology.formation('random', 9, 20.0, seed)
            for p in pos:
                self.assertLessEqual(abs(p[0] - CENTER[0]), 30.0)
                self.assertLessEqual(abs(p[1] - CENTER[1]), 30.0)
            self.assertGreaterEqual(min_separation(pos), 10.0)

    def test_random_exhausted(self):
        original = topology.RANDOM_ATTEMPTS
        topology.RANDOM_ATTEMPTS = 1
        self.addCleanup(setattr, topology, 'RANDOM_ATTEMPTS', original)
        with self.assertRaisesRegex(ValueError, 'random formation'):
            topology.formation('random', 32, 20.0, 1)

    def test_unknown_formation(self):
        with self.assertRaises(NotImplementedError):
            topology.formation('scene', 4, 15.0, 1)


class TrajectoryTest(unittest.TestCase):
    BASES = [(10.0, 50.0, 0.0), (35.0, 62.5, 0.0), (50.0, 50.0, 0.0),
             (71.3, 21.7, 0.0)]

    def test_static_and_scene_return_base(self):
        for kind in ('static', 'scene'):
            self.assertEqual(topology.trajectory(kind, (1, 2, 3), 9.5),
                             (1, 2, 3))

    def test_origin_is_base(self):
        for kind in ('static', 'scene', 'sweep', 'orbit'):
            for base in self.BASES:
                self.assertEqual(topology.trajectory(kind, base, 0), base)

    def test_z_unchanged(self):
        for kind in ('sweep', 'orbit'):
            for t in (0, 7.3, 31, 90):
                self.assertEqual(
                    topology.trajectory(kind, (60.0, 40.0, 2.5), t)[2], 2.5)

    def test_sweep_scale_range(self):
        base = (10.0, 50.0, 0.0)
        scales = []
        for i in range(0, 601):
            x, y, _ = topology.trajectory('sweep', base, i * 0.1)
            scales.append((x - CENTER[0]) / (base[0] - CENTER[0]))
            self.assertAlmostEqual(y, 50.0)
        self.assertAlmostEqual(min(scales), 1.0)
        self.assertAlmostEqual(max(scales), topology.SWEEP_MAX_SCALE)

    def test_sweep_peak_and_period(self):
        base = (10.0, 70.0, 0.0)
        half = topology.SWEEP_PERIOD / 2
        peak = topology.trajectory('sweep', base, half)
        self.assertAlmostEqual(peak[0], 50.0 + (10.0 - 50.0) * 3)
        self.assertAlmostEqual(peak[1], 50.0 + (70.0 - 50.0) * 3)
        again = topology.trajectory('sweep', base, topology.SWEEP_PERIOD)
        for a, b in zip(again, base):
            self.assertAlmostEqual(a, b)

    def test_sweep_keeps_centre_drone(self):
        for t in (0, 10, 30, 45):
            self.assertEqual(
                topology.trajectory('sweep', CENTER, t), CENTER)

    def test_orbit_preserves_distance_to_centre(self):
        for base in self.BASES:
            for i in range(0, 241, 7):
                p = topology.trajectory('orbit', base, float(i))
                self.assertAlmostEqual(math.dist(p, CENTER),
                                       math.dist(base, CENTER))

    def test_orbit_preserves_pairwise_distances(self):
        a, b = self.BASES[0], self.BASES[3]
        for t in (5.0, 33.3, 61.0, 100.0):
            self.assertAlmostEqual(
                math.dist(topology.trajectory('orbit', a, t),
                          topology.trajectory('orbit', b, t)),
                math.dist(a, b))

    def test_orbit_period(self):
        base = (10.0, 50.0, 0.0)
        half = topology.trajectory('orbit', base, topology.ORBIT_PERIOD / 2)
        self.assertAlmostEqual(half[0], 90.0)
        self.assertAlmostEqual(half[1], 50.0)
        full = topology.trajectory('orbit', base, topology.ORBIT_PERIOD)
        self.assertAlmostEqual(full[0], base[0])
        self.assertAlmostEqual(full[1], base[1])

    def test_continuity(self):
        step = 0.1
        rates = {
            'sweep': math.pi * (topology.SWEEP_MAX_SCALE - 1) /
            topology.SWEEP_PERIOD,
            'orbit': 2 * math.pi / topology.ORBIT_PERIOD}
        for kind, rate in rates.items():
            for base in self.BASES:
                limit = rate * math.dist(base, CENTER) * step * 1.01
                previous = topology.trajectory(kind, base, 0.0)
                for i in range(1, 1500):
                    current = topology.trajectory(kind, base, i * step)
                    self.assertLessEqual(math.dist(previous, current),
                                         limit + 1e-9)
                    previous = current

    def test_unknown_trajectory(self):
        with self.assertRaises(NotImplementedError):
            topology.trajectory('zigzag', (1, 2, 3), 0)


class PropagationTest(unittest.TestCase):
    def test_log_distance(self):
        self.assertEqual(topology.propagation(cfg('--exp', '3')),
                         ('logDistance', {'exp': 3.0}))

    def test_log_distance_default_exponent(self):
        self.assertEqual(topology.propagation(cfg()),
                         ('logDistance', {'exp': 4.5}))

    def test_models_without_parameters(self):
        for model in ('friis', 'twoRayGround', 'ITU'):
            self.assertEqual(
                topology.propagation(
                    cfg('--model', model, '--exp', '3', '--variance', '4',
                        '--nakagami-m', '2')),
                (model, {}))

    def test_nakagami(self):
        self.assertEqual(
            topology.propagation(cfg('--model', 'nakagami')),
            ('nakagami', {'exp': 4.5}))
        self.assertEqual(
            topology.propagation(cfg('--model', 'nakagami', '--exp', '3',
                                     '--nakagami-m', '1.5')),
            ('nakagami', {'exp': 3.0, 'm': 1.5}))

    def test_nakagami_ignores_variance(self):
        self.assertEqual(
            topology.propagation(cfg('--model', 'nakagami', '--variance',
                                     '3')),
            ('nakagami', {'exp': 4.5}))

    def test_log_normal_shadowing(self):
        self.assertEqual(
            topology.propagation(cfg('--model', 'logNormalShadowing',
                                     '--exp', '3')),
            ('logNormalShadowing', {'exp': 3.0}))

    def test_log_distance_ignores_other_parameters(self):
        self.assertEqual(
            topology.propagation(cfg('--variance', '3', '--nakagami-m',
                                     '2')),
            ('logDistance', {'exp': 4.5}))

    def test_every_model_is_mapped(self):
        for model in config.MODELS:
            name, kwargs = topology.propagation(cfg('--model', model))
            self.assertEqual(name, model)
            self.assertIsInstance(kwargs, dict)


class PropagationErrorTest(unittest.TestCase):
    def error(self, model, exp=4.5, variance=None, nakagami_m=None):
        return topology.propagation_error(model, exp, variance, nakagami_m)

    def test_valid_parameters(self):
        self.assertIsNone(self.error('logDistance', exp=3))
        self.assertIsNone(self.error('nakagami', exp=3, nakagami_m=0.5))
        self.assertIsNone(self.error('nakagami', nakagami_m=1.23))
        self.assertIsNone(self.error('logNormalShadowing', exp=2.7))

    def test_exponent_resolution(self):
        for model in ('logDistance', 'nakagami', 'logNormalShadowing'):
            self.assertIn('--exp', self.error(model, exp=3.25))

    def test_log_normal_shadowing_rejects_variance(self):
        self.assertIn('--variance',
                      self.error('logNormalShadowing', variance=3))

    def test_nakagami_m_limits(self):
        self.assertIn('--nakagami-m', self.error('nakagami', nakagami_m=0.4))
        self.assertIn('--nakagami-m',
                      self.error('nakagami', nakagami_m=1.234))

    def test_unused_parameters_are_ignored(self):
        self.assertIsNone(self.error('friis', exp=3.25, variance=3,
                                     nakagami_m=0.1))
        self.assertIsNone(self.error('logDistance', variance=3,
                                     nakagami_m=0.1))
        self.assertIsNone(self.error('nakagami', variance=3))


if __name__ == '__main__':
    unittest.main()
