"""Traffic measurements between drone pairs."""

import json
import os
import re
import subprocess

from experiment import links

LOSS_RE = re.compile(r'(\d+) packets transmitted, (\d+) (?:packets )?'
                     r'received.*?([\d.]+)% packet loss')
RTT_RE = re.compile(r'rtt min/avg/max/mdev = '
                    r'([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms')


def parse_ping(text):
    """Extract loss and round-trip times from ping output."""
    result = {'transmitted': 0, 'received': 0, 'loss_percent': 100.0,
              'rtt_ms': None}
    loss = LOSS_RE.search(text)
    if loss:
        result['transmitted'] = int(loss.group(1))
        result['received'] = int(loss.group(2))
        result['loss_percent'] = float(loss.group(3))
    rtt = RTT_RE.search(text)
    if rtt:
        result['rtt_ms'] = dict(zip(('min', 'avg', 'max', 'mdev'),
                                    map(float, rtt.groups())))
    return result


def ping_pair(src, dst_ip, cfg):
    cmd = ['ping', '-D', '-c', str(cfg.ping_count), '-i',
           str(cfg.ping_interval), '-W', '1', dst_ip]
    proc = src.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     universal_newlines=True)
    output, _ = proc.communicate()
    return output


def run_metrics(net, cfg, info, out_dir):
    """Ping every configured pair and write summary.json to `out_dir`."""
    summary = {'tech': info.tech, 'routing': info.routing, 'pings': []}
    for src_name, dst_name in cfg.pairs:
        src, dst = net.get(src_name), net.get(dst_name)
        dst_ip = links.endpoint_ip(dst, cfg)
        output = ping_pair(src, dst_ip, cfg)
        with open(os.path.join(
                out_dir, 'ping_%s_%s.txt' % (src_name, dst_name)), 'w') as f:
            f.write(output)
        entry = {'src': src_name, 'dst': dst_name, 'dst_ip': dst_ip}
        entry.update(parse_ping(output))
        summary['pings'].append(entry)
    with open(os.path.join(out_dir, 'summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)
    return summary
