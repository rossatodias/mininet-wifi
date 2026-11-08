import csv
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from experiment import config, links, metrics

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def read(name):
    with open(os.path.join(DATA, name)) as f:
        return f.read()


def read_json(name):
    return json.loads(read(name))


def cfg(*argv):
    return config.parse_args(['--coppelia', 'off'] + list(argv))


def process(output='', returncode=0, running=False):
    proc = mock.Mock()
    proc.communicate.return_value = (output, '')
    proc.returncode = returncode
    proc.poll.return_value = None if running else returncode
    return proc


class FakeNode:
    """Node whose popen answers from a table keyed by the command name."""

    def __init__(self, name, position, answers=None):
        self.name = name
        self.position = position
        self.answers = answers or {}
        self.commands = []
        self.mac = '00:00:00:00:00:0%s' % name[-1]

    def intf(self, name):
        return self

    def popen(self, cmd, **kwargs):
        self.commands.append(cmd)
        answer = self.answers[cmd[0]]
        return answer if isinstance(answer, mock.Mock) else answer(cmd)


class ParsePingTest(unittest.TestCase):
    def test_summary(self):
        r = metrics.parse_ping(read('ping_ok.txt'))
        self.assertEqual((r['sent'], r['received']), (20, 20))
        self.assertEqual(r['loss_percent'], 0.0)
        self.assertEqual(r['rtt_ms'], {'min': 0.854, 'avg': 252.49,
                                       'max': 2037.908, 'mdev': 564.471})

    def test_partial_loss(self):
        text = read('ping_partial.txt')
        r = metrics.parse_ping(text)
        self.assertEqual((r['sent'], r['received']), (20, 7))
        self.assertEqual(r['loss_percent'], 65.0)
        self.assertEqual(r['rtt_ms']['max'], 109.64)
        replies = metrics.parse_ping_replies(text)
        self.assertEqual([seq for _, seq, _ in replies],
                         [1, 2, 3, 4, 5, 19, 20])
        self.assertEqual(replies[5][2], 110.0)
        lost = metrics.lost_times(replies, r['sent'], 0.5, replies[0][0])
        self.assertEqual(len(lost), 13)
        self.assertAlmostEqual(lost[0], replies[0][0] + 2.5)

    def test_no_reply(self):
        r = metrics.parse_ping(read('ping_unreachable.txt'))
        self.assertEqual((r['sent'], r['received']), (20, 0))
        self.assertEqual(r['loss_percent'], 100.0)
        self.assertIsNone(r['rtt_ms'])

    def test_garbage(self):
        r = metrics.parse_ping('ping: connect: Network is unreachable')
        self.assertEqual(r['sent'], 0)
        self.assertEqual(r['loss_percent'], 100.0)

    def test_replies(self):
        replies = metrics.parse_ping_replies(read('ping_ok.txt'))
        self.assertEqual(len(replies), 20)
        self.assertEqual(replies[0], (1794080598.684191, 2, 1507.0))
        self.assertEqual(sorted(seq for _, seq, _ in replies),
                         list(range(1, 21)))

    def test_replies_ignore_unreachable_lines(self):
        self.assertEqual(
            metrics.parse_ping_replies(read('ping_unreachable.txt')), [])


class ParseIperfTest(unittest.TestCase):
    def test_tcp(self):
        r = metrics.parse_iperf(read_json('iperf_tcp.json'))
        self.assertAlmostEqual(r['throughput_mbps'], 7.1226, places=3)
        self.assertEqual(r['retransmits'], 120)
        self.assertIsNone(r['error'])

    def test_udp(self):
        r = metrics.parse_iperf(read_json('iperf_udp.json'))
        self.assertAlmostEqual(r['throughput_mbps'], 1.0008, places=3)
        self.assertAlmostEqual(r['jitter_ms'], 0.196, places=3)
        self.assertEqual(r['lost_percent'], 0)
        self.assertIsNone(r['retransmits'])

    def test_error(self):
        for name in ('iperf_tcp_error.json', 'iperf_udp_error.json'):
            r = metrics.parse_iperf(read_json(name))
            self.assertIsNone(r['throughput_mbps'])
            self.assertIn('unable to connect', r['error'])

    def test_empty_object(self):
        r = metrics.parse_iperf({})
        self.assertIsNone(r['throughput_mbps'])
        self.assertEqual(r['error'], 'no iperf3 result')


class ParseRoutingTest(unittest.TestCase):
    def test_traceroute_hops(self):
        self.assertEqual(metrics.parse_traceroute(read('batctl_tr.txt')), 2)

    def test_traceroute_unresolved(self):
        self.assertIsNone(
            metrics.parse_traceroute(read('batctl_tr_unresolved.txt')))

    def test_mpath(self):
        paths = metrics.parse_mpath(read('mpath_dump.txt'))
        self.assertEqual(paths, [
            {'dest': '00:00:00:00:00:02', 'next_hop': '00:00:00:00:00:02',
             'metric': 820, 'hops': 1},
            {'dest': '00:00:00:00:00:03', 'next_hop': '00:00:00:00:00:02',
             'metric': 1640, 'hops': 2}])

    def test_mpath_header_only(self):
        self.assertEqual(metrics.parse_mpath(read('mpath_dump_empty.txt')),
                         [])


class HelpersTest(unittest.TestCase):
    def test_lost_times_from_first_reply(self):
        replies = [(100.0, 2, 1.0), (100.5, 3, 1.0), (101.5, 5, 1.0)]
        self.assertEqual(metrics.lost_times(replies, 5, 0.5, 90.0),
                         [99.5, 101.0])

    def test_lost_times_without_replies(self):
        self.assertEqual(metrics.lost_times([], 3, 0.5, 10.0),
                         [10.0, 10.5, 11.0])

    def test_distance_stats(self):
        stats = metrics.distance_stats([(1, 10.0), (2, 20.0), (3, 30.0)])
        self.assertEqual(stats, {'min': 10.0, 'mean': 20.0, 'max': 30.0})
        self.assertIsNone(metrics.distance_stats([]))

    def test_sampler_records_epoch_distance(self):
        a = FakeNode('dr1', [0, 0, 0])
        b = FakeNode('dr2', [3, 4, 0])
        sampler = metrics.DistanceSampler(a, b, period=0.01)
        sampler.start()
        samples = sampler.stop()
        self.assertGreaterEqual(len(samples), 1)
        self.assertEqual(samples[0][1], 5.0)
        self.assertGreater(samples[0][0], 1e9)


class RunToolTest(unittest.TestCase):
    def test_returns_output(self):
        node = FakeNode('dr1', [0, 0, 0], {'x': process('hello')})
        self.assertEqual(metrics.run_tool(node, ['x']), 'hello')

    def test_timeout_kills_and_returns_nothing(self):
        proc = process()
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired('x', 1), ('', '')]
        node = FakeNode('dr1', [0, 0, 0], {'x': proc})
        self.assertEqual(metrics.run_tool(node, ['x'], timeout=1), '')
        proc.kill.assert_called_once_with()


def iperf_nodes(client, server=None, listening=True):
    server = server or process(running=True)
    ss = process('LISTEN 0 1 0.0.0.0:5201\n' if listening else '')
    src = FakeNode('dr1', [0, 0, 0], {'iperf3': client})
    dst = FakeNode('dr3', [30, 0, 0], {'iperf3': server, 'ss': ss})
    return src, dst, server


class RunIperfTest(unittest.TestCase):
    def test_tcp_command_and_result(self):
        client = process(read('iperf_tcp.json'))
        src, dst, server = iperf_nodes(client)
        c = cfg('--iperf-time', '4')
        result = metrics.run_iperf(src, dst, '192.168.123.3', c, False)
        self.assertEqual(result, read_json('iperf_tcp.json'))
        self.assertEqual(src.commands[0],
                         ['iperf3', '-c', '192.168.123.3', '-J', '-t', '4',
                          '--connect-timeout', '3000'])
        self.assertEqual(dst.commands[0], ['iperf3', '-s', '-1'])
        self.assertEqual(client.communicate.call_args.kwargs['timeout'], 12)
        server.kill.assert_called_once_with()

    def test_udp_command(self):
        src, dst, _ = iperf_nodes(process(read('iperf_udp.json')))
        c = cfg('--udp-bandwidth', '2M')
        metrics.run_iperf(src, dst, '10.0.0.3', c, True)
        self.assertEqual(src.commands[0][-3:], ['-u', '-b', '2M'])

    def test_client_error_is_kept(self):
        client = process(read('iperf_tcp_error.json'), returncode=1)
        src, dst, _ = iperf_nodes(client)
        result = metrics.run_iperf(src, dst, '10.0.0.3', cfg(), False)
        self.assertIn('unable to connect', result['error'])

    def test_failure_without_json(self):
        client = process('', returncode=1)
        client.communicate.return_value = ('', 'iperf3: error - bad\n')
        src, dst, _ = iperf_nodes(client)
        result = metrics.run_iperf(src, dst, '10.0.0.3', cfg(), False)
        self.assertEqual(result, {'error': 'iperf3: error - bad'})

    def test_failure_without_json_or_message(self):
        src, dst, _ = iperf_nodes(process('', returncode=7))
        result = metrics.run_iperf(src, dst, '10.0.0.3', cfg(), False)
        self.assertEqual(result, {'error': 'iperf3 exit code 7'})

    def test_client_timeout_kills_both(self):
        client = process(running=True)
        client.communicate.side_effect = [
            subprocess.TimeoutExpired('iperf3', 1), ('', '')]
        src, dst, server = iperf_nodes(client)
        result = metrics.run_iperf(src, dst, '10.0.0.3', cfg(), False)
        self.assertIn('timed out', result['error'])
        client.kill.assert_called_once_with()
        server.kill.assert_called_once_with()

    def test_server_never_listens(self):
        src, dst, server = iperf_nodes(process(), listening=False)
        with mock.patch.object(metrics, 'SERVER_WAIT', 0.1):
            result = metrics.run_iperf(src, dst, '10.0.0.3', cfg(), False)
        self.assertEqual(result, {'error': 'iperf3 server did not start'})
        self.assertEqual(src.commands, [])
        server.kill.assert_called_once_with()

    def test_server_exits_early(self):
        src, dst, server = iperf_nodes(process(), process(returncode=1),
                                       listening=False)
        result = metrics.run_iperf(src, dst, '10.0.0.3', cfg(), False)
        self.assertEqual(result, {'error': 'iperf3 server did not start'})


class RoutingSnapshotTest(unittest.TestCase):
    def net(self, answers):
        nodes = {'dr1': FakeNode('dr1', [0, 0, 0], answers),
                 'dr3': FakeNode('dr3', [60, 0, 0])}
        net = mock.Mock()
        net.get.side_effect = nodes.get
        return net, nodes

    def test_batman_uses_destination_mac(self):
        net, nodes = self.net({
            'batctl': lambda cmd: process(
                read('batctl_o.txt') if cmd[1] == 'o'
                else read('batctl_tr.txt'))})
        info = links.LinkInfo('adhoc', 'batman_adv', 's', 5,
                              {'dr3': 'dr3-wlan0'}, 'bat0')
        text, hops = metrics.routing_snapshot(net, info, 'dr1', 'dr3',
                                              'before')
        self.assertEqual(hops, 2)
        self.assertEqual(nodes['dr1'].commands,
                         [['batctl', 'o'],
                          ['batctl', 'tr', '00:00:00:00:00:03']])
        self.assertTrue(text.startswith('=== before '))
        self.assertIn('Originator', text)

    def test_mesh_hops(self):
        net, nodes = self.net({'iw': process(read('mpath_dump.txt'))})
        info = links.LinkInfo('mesh', 'none', 's', 5,
                              {'dr1': 'dr1-mp0', 'dr3': 'dr3-mp0'})
        text, hops = metrics.routing_snapshot(net, info, 'dr1', 'dr3',
                                              'after')
        self.assertEqual(hops, 2)
        self.assertEqual(nodes['dr1'].commands,
                         [['iw', 'dev', 'dr1-mp0', 'mpath', 'dump']])

    def test_mesh_without_path(self):
        net, _ = self.net({'iw': process(read('mpath_dump_empty.txt'))})
        info = links.LinkInfo('mesh', 'none', 's', 5,
                              {'dr1': 'dr1-mp0', 'dr3': 'dr3-mp0'})
        self.assertIsNone(
            metrics.routing_snapshot(net, info, 'dr1', 'dr3', 'before')[1])

    def test_other_techs_run_nothing(self):
        net, nodes = self.net({})
        for tech in ('adhoc', 'infra'):
            info = links.LinkInfo(tech, 'none', 's', 5, {})
            text, hops = metrics.routing_snapshot(net, info, 'dr1', 'dr3',
                                                  'before')
            self.assertIsNone(hops)
        self.assertEqual(nodes['dr1'].commands, [])


class RunMetricsTest(unittest.TestCase):
    def run_pair(self, ping_file, tcp_file, udp_file, tcp_code=0):
        def iperf(cmd):
            if cmd[1] == '-s':
                return process(running=True)
            return (process(read(tcp_file), tcp_code) if '-u' not in cmd
                    else process(read(udp_file), tcp_code))

        answers = {
            'ping': process(read(ping_file)),
            'iperf3': iperf,
            'ss': process('LISTEN 0 1 0.0.0.0:5201\n'),
            'batctl': lambda cmd: process(
                read('batctl_o.txt') if cmd[1] == 'o'
                else read('batctl_tr.txt'))}
        nodes = {'dr1': FakeNode('dr1', [0, 0, 0], answers),
                 'dr3': FakeNode('dr3', [60, 0, 0], answers)}
        net = mock.Mock()
        net.get.side_effect = nodes.get
        c = cfg('--ping-count', '20', '--ping-interval', '0.5',
                '--iperf-time', '10')
        info = links.LinkInfo('adhoc', 'batman_adv', 's', 5,
                              {'dr1': 'dr1-wlan0', 'dr3': 'dr3-wlan0'},
                              'bat0')
        out = tempfile.TemporaryDirectory()
        self.addCleanup(out.cleanup)
        summary = metrics.run_metrics(net, c, info, out.name)
        return summary, out.name, nodes

    def test_outputs(self):
        summary, out, nodes = self.run_pair(
            'ping_ok.txt', 'iperf_tcp.json', 'iperf_udp.json')
        files = set(os.listdir(out))
        self.assertEqual(files, {
            'ping.csv', 'distance.csv', 'summary.json',
            'ping_dr1_dr3.txt', 'iperf_tcp_dr1_dr3.json',
            'iperf_udp_dr1_dr3.json', 'routing_dr1_dr3.txt',
            'rtt_dr1_dr3.png'})
        for name in files:
            self.assertGreater(os.path.getsize(os.path.join(out, name)), 0)
        with open(os.path.join(out, 'summary.json')) as f:
            self.assertEqual(json.load(f), summary)
        with open(os.path.join(out, 'ping_dr1_dr3.txt')) as f:
            self.assertEqual(f.read(), read('ping_ok.txt'))
        with open(os.path.join(out, 'ping.csv')) as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ['pair', 'epoch', 'seq', 'rtt_ms'])
        self.assertEqual(len(rows), 21)
        self.assertEqual(rows[1][0], 'dr1:dr3')
        with open(os.path.join(out, 'distance.csv')) as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ['pair', 'epoch', 'distance_m'])
        self.assertEqual(rows[1][2], '60.000')
        with open(os.path.join(out, 'routing_dr1_dr3.txt')) as f:
            text = f.read()
        self.assertIn('=== before ', text)
        self.assertIn('=== after ', text)

    def test_summary_schema(self):
        summary, _, nodes = self.run_pair(
            'ping_ok.txt', 'iperf_tcp.json', 'iperf_udp.json')
        self.assertEqual(set(summary), {'tech', 'routing', 'pairs'})
        pair = summary['pairs'][0]
        self.assertEqual(set(pair), {
            'src', 'dst', 'dst_ip', 'ping', 'tcp', 'udp', 'distance_m',
            'hops', 'errors', 'duration_s'})
        self.assertEqual(pair['dst_ip'], '192.168.123.3')
        self.assertEqual(pair['ping']['received'], 20)
        self.assertEqual(set(pair['tcp']),
                         {'throughput_mbps', 'retransmits', 'error'})
        self.assertEqual(set(pair['udp']), {
            'throughput_mbps', 'jitter_ms', 'lost_percent', 'error'})
        self.assertEqual(pair['hops'], {'before': 2, 'after': 2})
        self.assertEqual(pair['distance_m']['max'], 60.0)
        self.assertEqual(pair['errors'], [])
        self.assertEqual(nodes['dr1'].commands[2][:4],
                         ['ping', '-D', '-i', '0.5'])

    def test_unreachable_destination(self):
        summary, out, _ = self.run_pair(
            'ping_unreachable.txt', 'iperf_tcp_error.json',
            'iperf_udp_error.json', tcp_code=1)
        pair = summary['pairs'][0]
        self.assertEqual(pair['ping']['loss_percent'], 100.0)
        self.assertIsNone(pair['ping']['rtt_ms'])
        self.assertIsNone(pair['tcp']['throughput_mbps'])
        self.assertEqual(len(pair['errors']), 3)
        self.assertEqual(pair['errors'][0], 'no ping reply')
        self.assertTrue(pair['errors'][1].startswith('tcp: unable'))
        with open(os.path.join(out, 'ping.csv')) as f:
            self.assertEqual(len(list(csv.reader(f))), 1)
        self.assertTrue(os.path.getsize(
            os.path.join(out, 'rtt_dr1_dr3.png')) > 0)

    def test_runs_pairs_in_order(self):
        summary, _, _ = self.run_pair(
            'ping_ok.txt', 'iperf_tcp.json', 'iperf_udp.json')
        self.assertEqual([(p['src'], p['dst']) for p in summary['pairs']],
                         [('dr1', 'dr3')])


if __name__ == '__main__':
    unittest.main()
