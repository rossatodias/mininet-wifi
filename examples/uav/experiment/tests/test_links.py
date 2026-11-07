import unittest
from unittest import mock

from experiment import config, links


def cfg(*argv):
    return config.parse_args(['--coppelia', 'off'] + list(argv))


def station(name):
    node = mock.Mock()
    node.name = name
    return node


class BuildLinksTest(unittest.TestCase):
    def test_adhoc_batman(self):
        net = mock.Mock()
        stations = [station('dr1'), station('dr2')]
        info = links.build_links(net, stations, cfg('--channel', '6'))
        self.assertEqual(net.addLink.call_count, 2)
        args, kwargs = net.addLink.call_args_list[1]
        self.assertEqual(args, (stations[1],))
        self.assertEqual(kwargs['intf'], 'dr2-wlan0')
        self.assertEqual(kwargs['proto'], 'batman_adv')
        self.assertEqual(kwargs['ssid'], 'adhocNet')
        self.assertEqual(kwargs['mode'], 'g')
        self.assertEqual(kwargs['channel'], 6)
        self.assertEqual(kwargs['ht_cap'], 'HT40+')
        self.assertEqual(info.interfaces,
                         {'dr1': 'dr1-wlan0', 'dr2': 'dr2-wlan0'})
        self.assertEqual(info.routing_iface, 'bat0')

    def test_adhoc_without_routing(self):
        net = mock.Mock()
        info = links.build_links(net, [station('dr1')],
                                 cfg('--routing', 'none'))
        self.assertNotIn('proto', net.addLink.call_args.kwargs)
        self.assertIsNone(info.routing_iface)
        self.assertEqual(info.routing, 'none')

    def test_mesh(self):
        net = mock.Mock()
        stations = [station('dr1'), station('dr2')]
        info = links.build_links(net, stations,
                                 cfg('--tech', 'mesh', '--channel', '6'))
        self.assertEqual(net.addLink.call_count, 2)
        args, kwargs = net.addLink.call_args_list[1]
        self.assertEqual(args, (stations[1],))
        self.assertIs(kwargs['cls'], links.mesh)
        self.assertEqual(kwargs['intf'], 'dr2-wlan0')
        self.assertEqual(kwargs['ssid'], 'meshNet')
        self.assertEqual(kwargs['channel'], 6)
        self.assertNotIn('proto', kwargs)
        self.assertEqual(info.tech, 'mesh')
        self.assertEqual(info.routing, 'none')
        self.assertEqual(info.ssid, 'meshNet')
        self.assertEqual(info.interfaces,
                         {'dr1': 'dr1-mp0', 'dr2': 'dr2-mp0'})
        self.assertIsNone(info.routing_iface)

    def test_infra_adds_no_links(self):
        net = mock.Mock()
        stations = [station('dr1'), station('dr2')]
        info = links.build_links(net, stations, cfg('--tech', 'infra'))
        net.addLink.assert_not_called()
        self.assertEqual(info.tech, 'infra')
        self.assertEqual(info.ssid, 'infraNet')
        self.assertEqual(info.channel, 5)
        self.assertEqual(info.interfaces,
                         {'dr1': 'dr1-wlan0', 'dr2': 'dr2-wlan0'})
        self.assertIsNone(info.routing_iface)


class AccessPointTest(unittest.TestCase):
    def test_centroid_ignores_z(self):
        self.assertEqual(
            links.centroid([(0, 0, 5), (10, 20, 7), (20, 10, 9)]),
            (10.0, 10.0, 0.0))

    def test_coverage_reaches_farthest_drone_with_margin(self):
        positions = [(10, 50, 0), (50, 50, 0), (90, 50, 0)]
        self.assertEqual(links.coverage(positions),
                         ((50.0, 50.0, 0.0), 45))

    def test_coverage_range_is_rounded_up(self):
        positions = [(0, 0, 0), (1, 0, 9)]
        self.assertEqual(links.coverage(positions)[1], 6)

    def test_infra_ap_at_the_centroid(self):
        net = mock.Mock()
        positions = [(10.0, 50.0, 0.0), (50.0, 50.0, 0.0),
                     (90.0, 50.0, 0.0)]
        with mock.patch.object(links.drones, 'initial_positions',
                               return_value=positions):
            links.add_access_points(net, [], cfg('--tech', 'infra',
                                                 '--channel', '6'))
        net.addAccessPoint.assert_called_once_with(
            'ap1', ssid='infraNet', mode='g', channel=6,
            failMode='standalone', position='50.000,50.000,0.000',
            range=45)

    def test_other_technologies_add_no_ap(self):
        for tech in ('adhoc', 'mesh'):
            net = mock.Mock()
            links.add_access_points(net, [], cfg('--tech', tech))
            net.addAccessPoint.assert_not_called()


class StartTest(unittest.TestCase):
    def test_infra_starts_ap_without_controller_and_associates(self):
        net = mock.Mock()
        ap = net.get.return_value
        ap.wintfs = {0: mock.Mock()}
        net.stations = [station('dr1'), station('dr2')]
        for sta in net.stations:
            sta.wintfs = {0: mock.Mock()}
        info = links.LinkInfo('infra', 'none', 'infraNet', 5, {})
        links.start(net, info)
        net.get.assert_called_once_with('ap1')
        ap.start.assert_called_once_with([])
        for sta in net.stations:
            sta.wintfs[0].disconnect.assert_called_once_with(ap.wintfs[0])
            sta.setAssociation.assert_called_once_with(ap)

    def test_other_technologies_do_nothing(self):
        for tech in ('adhoc', 'mesh'):
            net = mock.Mock()
            links.start(net, links.LinkInfo(tech, 'none', 's', 5, {}))
            self.assertEqual(net.mock_calls, [])


class EndpointTest(unittest.TestCase):
    def test_batman_uses_bat0_address(self):
        self.assertEqual(links.endpoint_ip(station('dr3'), cfg()),
                         '192.168.123.3')
        self.assertEqual(links.endpoint_ip(station('dr12'), cfg()),
                         '192.168.123.12')

    def test_plain_uses_station_address(self):
        self.assertEqual(
            links.endpoint_ip(station('dr3'), cfg('--routing', 'none')),
            '10.0.0.3')

    def test_mesh_and_infra_use_station_address(self):
        for tech in ('mesh', 'infra'):
            self.assertEqual(
                links.endpoint_ip(station('dr12'), cfg('--tech', tech)),
                '10.0.0.12')


if __name__ == '__main__':
    unittest.main()
