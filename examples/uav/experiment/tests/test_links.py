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

    def test_unimplemented_tech(self):
        for tech in ('infra', 'mesh'):
            with self.assertRaises(NotImplementedError):
                links.build_links(mock.Mock(), [], cfg('--tech', tech))


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


if __name__ == '__main__':
    unittest.main()
