import unittest
from unittest import mock

from experiment import config, drones


def cfg(*argv):
    return config.parse_args(['--coppelia', 'off'] + list(argv))


class DronesTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(drones.drone_names(3), ['dr1', 'dr2', 'dr3'])
        self.assertEqual(drones.drone_names(0), [])

    def test_addresses(self):
        self.assertEqual(drones.drone_mac(1), '00:00:00:00:00:01')
        self.assertEqual(drones.drone_mac(32), '00:00:00:00:00:20')
        self.assertEqual(drones.drone_ip(7), '10.0.0.7/8')
        macs = {drones.drone_mac(i) for i in range(1, 33)}
        self.assertEqual(len(macs), 32)

    def test_names_and_addresses_up_to_32_drones(self):
        names = drones.drone_names(32)
        self.assertEqual(names[-1], 'dr32')
        self.assertEqual(len(set(names)), 32)
        ips = {drones.drone_ip(i) for i in range(1, 33)}
        self.assertEqual(len(ips), 32)
        self.assertEqual(drones.drone_ip(32), '10.0.0.32/8')

    def test_scene_formation_uses_original_positions(self):
        self.assertEqual(drones.initial_positions(cfg()),
                         [(30.0, 60.0, 0.0), (70.0, 30.0, 0.0),
                          (10.0, 20.0, 0.0)])
        self.assertEqual(drones.initial_positions(cfg('--drones', '2')),
                         [(30.0, 60.0, 0.0), (70.0, 30.0, 0.0)])

    def test_other_formations_use_topology(self):
        pos = drones.initial_positions(
            cfg('--drones', '4', '--formation', 'line', '--spacing', '5'))
        self.assertEqual(len(pos), 4)
        self.assertEqual(pos[1][0] - pos[0][0], 5)

    def test_add_stations_for_32_drones(self):
        net = mock.Mock()
        net.addStation.side_effect = lambda name, **kw: name
        stations = drones.add_stations(
            net, cfg('--drones', '32', '--formation', 'line'))
        self.assertEqual(stations, drones.drone_names(32))
        macs = [c.kwargs['mac'] for c in net.addStation.call_args_list]
        self.assertEqual(len(set(macs)), 32)

    def test_add_stations(self):
        net = mock.Mock()
        net.addStation.side_effect = lambda name, **kw: name
        stations = drones.add_stations(net, cfg())
        self.assertEqual(stations, ['dr1', 'dr2', 'dr3'])
        net.addStation.assert_any_call(
            'dr1', mac='00:00:00:00:00:01', ip='10.0.0.1/8',
            position='30.0,60.0,0.0')


if __name__ == '__main__':
    unittest.main()
