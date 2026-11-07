import json
import os
import tempfile
import unittest
from unittest import mock

from experiment import config, links, metrics

PING_OK = """PING 192.168.123.3 (192.168.123.3) 56(84) bytes of data.
[1.0] 64 bytes from 192.168.123.3: icmp_seq=1 ttl=64 time=1.5 ms

--- 192.168.123.3 ping statistics ---
10 packets transmitted, 9 received, 10% packet loss, time 4512ms
rtt min/avg/max/mdev = 0.928/2.065/3.937/1.014 ms
"""

PING_DOWN = """--- 192.168.123.3 ping statistics ---
20 packets transmitted, 0 received, +12 errors, 100% packet loss, time 9731ms
"""


def cfg(*argv):
    return config.parse_args(['--coppelia', 'off'] + list(argv))


def node(name, output=''):
    n = mock.Mock()
    n.name = name
    n.popen.return_value.communicate.return_value = (output, None)
    return n


class ParsePingTest(unittest.TestCase):
    def test_reply(self):
        r = metrics.parse_ping(PING_OK)
        self.assertEqual((r['transmitted'], r['received']), (10, 9))
        self.assertEqual(r['loss_percent'], 10.0)
        self.assertEqual(r['rtt_ms']['avg'], 2.065)

    def test_no_reply(self):
        r = metrics.parse_ping(PING_DOWN)
        self.assertEqual(r['loss_percent'], 100.0)
        self.assertIsNone(r['rtt_ms'])

    def test_garbage(self):
        r = metrics.parse_ping('ping: connect: Network is unreachable')
        self.assertEqual(r['transmitted'], 0)
        self.assertEqual(r['loss_percent'], 100.0)


class RunMetricsTest(unittest.TestCase):
    def test_writes_summary_and_raw_output(self):
        nodes = {'dr1': node('dr1', PING_OK), 'dr3': node('dr3')}
        net = mock.Mock()
        net.get.side_effect = nodes.get
        c = cfg('--ping-count', '10', '--ping-interval', '0.2')
        info = links.LinkInfo('adhoc', 'batman_adv', 'adhocNet', 5, {})
        with tempfile.TemporaryDirectory() as out:
            metrics.run_metrics(net, c, info, out)
            with open(os.path.join(out, 'summary.json')) as f:
                summary = json.load(f)
            with open(os.path.join(out, 'ping_dr1_dr3.txt')) as f:
                self.assertEqual(f.read(), PING_OK)
        ping = summary['pings'][0]
        self.assertEqual(ping['dst_ip'], '192.168.123.3')
        self.assertEqual(ping['received'], 9)
        cmd = nodes['dr1'].popen.call_args.args[0]
        self.assertEqual(cmd, ['ping', '-D', '-c', '10', '-i', '0.2',
                               '-W', '1', '192.168.123.3'])


if __name__ == '__main__':
    unittest.main()
