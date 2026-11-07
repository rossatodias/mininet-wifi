"""Traffic measurements between drone pairs."""

import csv
import json
import math
import os
import re
import subprocess
import threading
import time

from experiment import links

IPERF_PORT = 5201
CONNECT_TIMEOUT = 3.0
IPERF_MARGIN = 5.0
SERVER_WAIT = 5.0
TOOL_TIMEOUT = 15.0
SAMPLE_PERIOD = 1.0

LOSS_RE = re.compile(r'(\d+) packets transmitted, (\d+) (?:packets )?'
                     r'received.*?([\d.]+)% packet loss')
RTT_RE = re.compile(r'rtt min/avg/max/mdev = '
                    r'([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms')
REPLY_RE = re.compile(r'^\[(\d+\.\d+)\] \d+ bytes from \S+ icmp_seq=(\d+) '
                      r'.*?time=([\d.]+) ms\s*$', re.MULTILINE)
MAC = r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}'
ORIGINATOR_RE = re.compile(
    r'^\s*(\*)?\s*(%s)\s+([\d.]+)s\s+\(\s*(\d+)\)\s+(%s)\s+\[\s*(\S+)\]'
    % (MAC, MAC), re.MULTILINE)
TRACE_HOP_RE = re.compile(r'^\s*(\d+):\s+' + MAC, re.MULTILINE)
MPATH_RE = re.compile(
    r'^(%s)\s+(%s)\s+(\S+)\s+\d+\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+'
    r'0x[0-9a-f]+(?:\s+(\d+))?' % (MAC, MAC), re.MULTILINE)


def rtt_stats(match):
    return dict(zip(('min', 'avg', 'max', 'mdev'), map(float, match.groups())))


def parse_ping(text):
    """Extract sent, received, loss and RTT statistics from ping output."""
    result = {'sent': 0, 'received': 0, 'loss_percent': 100.0,
              'rtt_ms': None}
    loss = LOSS_RE.search(text)
    if loss:
        result['sent'] = int(loss.group(1))
        result['received'] = int(loss.group(2))
        result['loss_percent'] = float(loss.group(3))
    rtt = RTT_RE.search(text)
    if rtt:
        result['rtt_ms'] = rtt_stats(rtt)
    return result


def parse_ping_replies(text):
    """Return (epoch, seq, rtt_ms) for every reply of a `ping -D` run."""
    return [(float(t), int(seq), float(rtt))
            for t, seq, rtt in REPLY_RE.findall(text)]


def parse_iperf(data):
    """Reduce an iperf3 JSON result (or error object) to its metrics.

    Throughput is the receiver side. The result keys are stable:
    throughput_mbps, retransmits, jitter_ms, lost_percent and error.
    """
    end = data.get('end') or {}
    received = end.get('sum_received')
    result = {'throughput_mbps': None, 'retransmits': None,
              'jitter_ms': None, 'lost_percent': None,
              'error': data.get('error')}
    if received:
        result['throughput_mbps'] = received['bits_per_second'] / 1e6
        result['retransmits'] = end['sum_sent'].get('retransmits')
        result['jitter_ms'] = received.get('jitter_ms')
        result['lost_percent'] = received.get('lost_percent')
    elif result['error'] is None:
        result['error'] = 'no iperf3 result'
    return result


def parse_originators(text):
    """Parse `batctl o` into a list of originator dicts."""
    return [{'originator': orig, 'best': bool(best),
             'last_seen_s': float(seen), 'quality': int(quality),
             'next_hop': hop, 'interface': iface}
            for best, orig, seen, quality, hop, iface
            in ORIGINATOR_RE.findall(text)]


def parse_traceroute(text):
    """Return the hop count of a `batctl tr` output, or None."""
    hops = TRACE_HOP_RE.findall(text)
    return int(hops[-1]) if hops else None


def parse_mpath(text):
    """Parse `iw dev <intf> mpath dump` into a list of path dicts.

    `hops` is None when this version of iw has no HOP_COUNT column.
    """
    return [{'dest': dest, 'next_hop': hop, 'metric': int(metric),
             'hops': int(hops) if hops else None}
            for dest, hop, _, metric, hops in MPATH_RE.findall(text)]


def lost_times(replies, sent, interval, start):
    """Estimate the send time of every ping that got no reply."""
    seen = {seq for _, seq, _ in replies}
    if replies:
        epoch, seq, _ = replies[0]
    else:
        epoch, seq = start, 1
    return [epoch + (n - seq) * interval
            for n in range(1, sent + 1) if n not in seen]


def distance_stats(samples):
    """Return min, mean and max of (epoch, distance) samples, or None."""
    values = [d for _, d in samples]
    if not values:
        return None
    return {'min': min(values), 'mean': sum(values) / len(values),
            'max': max(values)}


def distance(a, b):
    return math.dist(a.position, b.position)


class DistanceSampler(threading.Thread):
    """Record the distance between two nodes at about 1 Hz."""

    def __init__(self, a, b, period=SAMPLE_PERIOD):
        super().__init__(daemon=True, name='distance-sampler')
        self.a = a
        self.b = b
        self.period = period
        self.samples = []
        self.stop_event = threading.Event()

    def run(self):
        while True:
            self.samples.append((time.time(), distance(self.a, self.b)))
            if self.stop_event.wait(self.period):
                break

    def stop(self):
        self.stop_event.set()
        self.join()
        return self.samples


def run_tool(node, cmd, timeout=TOOL_TIMEOUT):
    """Run `cmd` on `node`; return its output, or '' after a timeout."""
    proc = node.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      universal_newlines=True)
    try:
        return proc.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return ''


def ping_pair(src, dst_ip, cfg):
    cmd = ['ping', '-D', '-i', str(cfg.ping_interval),
           '-c', str(cfg.ping_count), '-W', '1', dst_ip]
    timeout = cfg.ping_count * cfg.ping_interval + 2 * cfg.ping_count + 10
    return run_tool(src, cmd, timeout)


def stop_process(proc):
    if proc.poll() is None:
        proc.kill()
    proc.communicate()


def wait_listening(node, server, port=IPERF_PORT):
    """Poll until a TCP listener on `port` exists in `node`."""
    deadline = time.monotonic() + SERVER_WAIT
    while server.poll() is None and time.monotonic() < deadline:
        if run_tool(node, ['ss', '-H', '-ltn', 'sport = :%d' % port]).strip():
            return True
        time.sleep(0.05)
    return False


def iperf_client(src, dst_ip, cfg, udp):
    cmd = ['iperf3', '-c', dst_ip, '-J', '-t', '%g' % cfg.iperf_time,
           '--connect-timeout', '%d' % (CONNECT_TIMEOUT * 1000)]
    if udp:
        cmd += ['-u', '-b', cfg.udp_bandwidth]
    proc = src.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     universal_newlines=True)
    limit = cfg.iperf_time + CONNECT_TIMEOUT + IPERF_MARGIN
    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        stop_process(proc)
        return {'error': 'iperf3 client timed out after %g s' % limit}
    failure = err.strip() or 'iperf3 exit code %d' % proc.returncode
    try:
        data = json.loads(out)
    except ValueError:
        return {'error': failure}
    if proc.returncode != 0 and not data.get('error'):
        data['error'] = failure
    return data


def run_iperf(src, dst, dst_ip, cfg, udp):
    """Run one iperf3 test and return the JSON result or an error object."""
    server = dst.popen(['iperf3', '-s', '-1'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    try:
        if not wait_listening(dst, server):
            return {'error': 'iperf3 server did not start'}
        return iperf_client(src, dst_ip, cfg, udp)
    finally:
        stop_process(server)


def routing_snapshot(net, info, src_name, dst_name, label):
    """Return (raw text, hop count) of the route from src to dst."""
    stamp = '=== %s %.3f ===\n' % (label, time.time())
    src = net.get(src_name)
    if info.routing == 'batman_adv':
        mac = net.get(dst_name).intf(info.interfaces[dst_name]).mac
        originators = run_tool(src, ['batctl', 'o'])
        trace = run_tool(src, ['batctl', 'tr', mac])
        return '%s$ batctl o\n%s\n$ batctl tr %s\n%s' % (
            stamp, originators, mac, trace), parse_traceroute(trace)
    if info.tech == 'mesh':
        text, hops = mesh_snapshot(net, info, src_name, dst_name)
        return stamp + text, hops
    return stamp, None


def mesh_snapshot(net, info, src_name, dst_name):
    src, dst = net.get(src_name), net.get(dst_name)
    intf = info.interfaces[src_name]
    dump = run_tool(src, ['iw', 'dev', intf, 'mpath', 'dump'])
    mac = dst.intf(info.interfaces[dst_name]).mac.lower()
    hops = next((p['hops'] for p in parse_mpath(dump) if p['dest'] == mac),
                None)
    return '$ iw dev %s mpath dump\n%s' % (intf, dump), hops


def plot_rtt(path, title, start, replies, lost, samples):
    """Draw RTT over time with lost pings and the distance timeline."""
    from matplotlib import style
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    with style.context('default'):
        fig = Figure(figsize=(8, 4.5))
        FigureCanvasAgg(fig)
        draw_rtt(fig, title, start, replies, lost, samples)
        fig.savefig(path, dpi=100)


def draw_rtt(fig, title, start, replies, lost, samples):
    ax = fig.subplots()
    ax.plot([t - start for t, _, _ in replies], [r for _, _, r in replies],
            'o', color='tab:blue', markersize=4, label='RTT')
    ax.plot([t - start for t in lost], [0] * len(lost), 'x', color='red',
            clip_on=False, label='lost ping')
    ax.set_xlabel('time (s)')
    ax.set_ylabel('RTT (ms)')
    ax.set_ylim(bottom=0)
    ax2 = ax.twinx()
    ax2.plot([t - start for t, _ in samples], [d for _, d in samples],
             '--', color='tab:gray', label='distance')
    ax2.set_ylabel('distance (m)')
    handles, labels = ax.get_legend_handles_labels()
    extra, extra_labels = ax2.get_legend_handles_labels()
    ax.legend(handles + extra, labels + extra_labels, loc='upper center',
              bbox_to_anchor=(0.5, -0.15), ncol=3)
    ax.set_title(title)
    fig.tight_layout()


def write_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def collect_pair(net, cfg, info, src_name, dst_name):
    """Run the measurements of one pair and return the raw results."""
    src, dst = net.get(src_name), net.get(dst_name)
    dst_ip = links.endpoint_ip(dst, cfg)
    sampler = DistanceSampler(src, dst)
    sampler.start()
    try:
        before = routing_snapshot(net, info, src_name, dst_name, 'before')
        ping_start = time.time()
        output = ping_pair(src, dst_ip, cfg)
        ping_end = time.time()
        iperf = {proto: run_iperf(src, dst, dst_ip, cfg, proto == 'udp')
                 for proto in ('tcp', 'udp')}
        after = routing_snapshot(net, info, src_name, dst_name, 'after')
    finally:
        samples = sampler.stop()
    return {'dst_ip': dst_ip, 'ping': output, 'iperf': iperf,
            'ping_window': (ping_start, ping_end), 'samples': samples,
            'before': before, 'after': after}


def write_pair(cfg, info, src_name, dst_name, raw, out_dir, writers):
    """Write the raw files, CSV rows and plot of one pair."""
    label = '%s_%s' % (src_name, dst_name)
    pair = '%s:%s' % (src_name, dst_name)
    with open(os.path.join(out_dir, 'ping_%s.txt' % label), 'w') as f:
        f.write(raw['ping'])
    for proto, data in raw['iperf'].items():
        write_json(os.path.join(out_dir, 'iperf_%s_%s.json' % (proto, label)),
                   data)
    with open(os.path.join(out_dir, 'routing_%s.txt' % label), 'w') as f:
        f.write('%s\n%s' % (raw['before'][0], raw['after'][0]))
    replies = parse_ping_replies(raw['ping'])
    writers['ping'].writerows([pair, '%.6f' % t, seq, rtt]
                              for t, seq, rtt in replies)
    writers['distance'].writerows([pair, '%.3f' % t, '%.3f' % d]
                                  for t, d in raw['samples'])
    start, end = raw['ping_window']
    lost = lost_times(replies, parse_ping(raw['ping'])['sent'],
                      cfg.ping_interval, start)
    plot_rtt(os.path.join(out_dir, 'rtt_%s.png' % label),
             '%s -> %s (%s)' % (src_name, dst_name, info.tech), start,
             replies, lost, [(t, d) for t, d in raw['samples']
                             if start - SAMPLE_PERIOD <= t <= end])


def pair_summary(src_name, dst_name, raw, duration):
    """Reduce the raw results of one pair to its summary.json entry."""
    ping = parse_ping(raw['ping'])
    tcp = parse_iperf(raw['iperf']['tcp'])
    udp = parse_iperf(raw['iperf']['udp'])
    errors = ['no ping reply'] if ping['received'] == 0 else []
    errors += ['%s: %s' % (name, res['error'])
               for name, res in (('tcp', tcp), ('udp', udp))
               if res['error']]
    return {
        'src': src_name, 'dst': dst_name, 'dst_ip': raw['dst_ip'],
        'ping': ping,
        'tcp': {'throughput_mbps': tcp['throughput_mbps'],
                'retransmits': tcp['retransmits'], 'error': tcp['error']},
        'udp': {'throughput_mbps': udp['throughput_mbps'],
                'jitter_ms': udp['jitter_ms'],
                'lost_percent': udp['lost_percent'], 'error': udp['error']},
        'distance_m': distance_stats(raw['samples']),
        'hops': {'before': raw['before'][1], 'after': raw['after'][1]},
        'errors': errors, 'duration_s': round(duration, 2)}


def run_metrics(net, cfg, info, out_dir):
    """Measure every configured pair and write the results to `out_dir`."""
    summary = {'tech': info.tech, 'routing': info.routing, 'pairs': []}
    with open(os.path.join(out_dir, 'ping.csv'), 'w', newline='') as pf, \
            open(os.path.join(out_dir, 'distance.csv'), 'w',
                 newline='') as df:
        writers = {'ping': csv.writer(pf), 'distance': csv.writer(df)}
        writers['ping'].writerow(['pair', 'epoch', 'seq', 'rtt_ms'])
        writers['distance'].writerow(['pair', 'epoch', 'distance_m'])
        for src_name, dst_name in cfg.pairs:
            began = time.monotonic()
            raw = collect_pair(net, cfg, info, src_name, dst_name)
            duration = time.monotonic() - began
            write_pair(cfg, info, src_name, dst_name, raw, out_dir, writers)
            summary['pairs'].append(pair_summary(
                src_name, dst_name, raw, duration))
            pf.flush()
            df.flush()
    write_json(os.path.join(out_dir, 'summary.json'), summary)
    return summary
