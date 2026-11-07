import unittest

from experiment import config, topology


def cfg(*argv):
    return config.parse_args(['--coppelia', 'off'] + list(argv))


class FormationTest(unittest.TestCase):
    def test_line(self):
        pos = topology.formation('line', 4, 15.0, 1)
        self.assertEqual(len(pos), 4)
        xs = [p[0] for p in pos]
        self.assertEqual([b - a for a, b in zip(xs, xs[1:])], [15.0] * 3)
        self.assertEqual({p[1:] for p in pos}, {(50.0, 0.0)})

    def test_unimplemented_formation(self):
        with self.assertRaises(NotImplementedError):
            topology.formation('grid', 4, 15.0, 1)


class TrajectoryTest(unittest.TestCase):
    def test_static_and_scene_return_base(self):
        for kind in ('static', 'scene'):
            self.assertEqual(topology.trajectory(kind, (1, 2, 3), 9.5),
                             (1, 2, 3))

    def test_unimplemented_trajectory(self):
        with self.assertRaises(NotImplementedError):
            topology.trajectory('orbit', (1, 2, 3), 0)


class PropagationTest(unittest.TestCase):
    def test_log_distance(self):
        self.assertEqual(topology.propagation(cfg('--exp', '3')),
                         ('logDistance', {'exp': 3.0}))

    def test_friis(self):
        self.assertEqual(topology.propagation(cfg('--model', 'friis')),
                         ('friis', {}))

    def test_unimplemented_model(self):
        with self.assertRaises(NotImplementedError):
            topology.propagation(cfg('--model', 'nakagami'))


if __name__ == '__main__':
    unittest.main()
