"""Experiment configuration: flags, defaults and validation."""

import argparse
import os
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from experiment import drones, topology

UAV_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORMATIONS = ('scene', 'line', 'grid', 'circle', 'star', 'random')
TRAJECTORIES = ('scene', 'static', 'sweep', 'orbit')
MODELS = ('friis', 'logDistance', 'twoRayGround', 'logNormalShadowing',
          'nakagami', 'ITU')
TECHS = ('infra', 'adhoc', 'mesh')
ROUTINGS = ('batman_adv', 'none')
COPPELIA_MODES = ('gui', 'headless', 'off')
MAX_DRONES = 32

BANDWIDTH_RE = re.compile(r'^\d+(\.\d+)?[KMG]?$')
PAIR_RE = re.compile(r'^dr\d+$')


@dataclass(frozen=True)
class ExperimentConfig:
    drones: int = 3
    formation: str = 'scene'
    spacing: float = 20.0
    trajectory: str = 'scene'
    seed: int = 1
    model: str = 'logDistance'
    exp: float = 4.5
    variance: Optional[float] = None
    nakagami_m: Optional[float] = None
    tech: str = 'adhoc'
    routing: str = 'batman_adv'
    channel: int = 5
    coppelia: str = 'gui'
    coppelia_dir: str = os.path.join(UAV_DIR, 'CoppeliaSim_Edu')
    scene: str = os.path.join(UAV_DIR, 'simulation.ttt')
    duration: Optional[float] = None
    warmup: float = 20.0
    no_cli: bool = False
    telemetry: bool = False
    output_dir: str = 'uav-results'
    metrics: bool = False
    pairs: Tuple[Tuple[str, str], ...] = (('dr1', 'dr3'),)
    ping_count: int = 20
    ping_interval: float = 0.5
    iperf_time: float = 10.0
    udp_bandwidth: str = '1M'


def default_routing(tech):
    return 'batman_adv' if tech == 'adhoc' else 'none'


def build_parser():
    p = argparse.ArgumentParser(
        prog='uav_experiment.py',
        description='Mininet-WiFi UAV experiment driven by CoppeliaSim.')
    g = p.add_argument_group('drones and topology')
    g.add_argument('--drones', type=int, default=3, metavar='N',
                   help='number of drones, 1..%d (default 3)' % MAX_DRONES)
    g.add_argument('--formation', choices=FORMATIONS, default='scene',
                   help='initial layout; scene keeps simulation.ttt '
                        '(default scene)')
    g.add_argument('--spacing', type=float, default=20.0, metavar='M',
                   help='formation spacing in meters (default 20)')
    g.add_argument('--trajectory', choices=TRAJECTORIES, default='scene',
                   help='drone motion; scene keeps the built-in pattern of '
                        'simulation.ttt (default scene)')
    g.add_argument('--seed', type=int, default=1,
                   help='seed for random layouts (default 1)')
    g = p.add_argument_group('propagation model')
    g.add_argument('--model', choices=MODELS, default='logDistance',
                   help='propagation model (default logDistance)')
    g.add_argument('--exp', type=float, default=4.5,
                   help='path loss exponent (default 4.5)')
    g.add_argument('--variance', type=float, default=None,
                   help='shadowing variance in dB')
    g.add_argument('--nakagami-m', type=float, default=None, metavar='M',
                   help='Nakagami shape parameter')
    g = p.add_argument_group('link technology')
    g.add_argument('--tech', choices=TECHS, default='adhoc',
                   help='wireless technology (default adhoc)')
    g.add_argument('--routing', choices=ROUTINGS, default=None,
                   help='routing protocol (default: batman_adv for adhoc, '
                        'none otherwise)')
    g.add_argument('--channel', type=int, default=5,
                   help='wireless channel (default 5)')
    g = p.add_argument_group('CoppeliaSim')
    g.add_argument('--coppelia', choices=COPPELIA_MODES, default='gui',
                   help='gui window, headless, or off (default gui)')
    g.add_argument('--coppelia-dir', default=None, metavar='DIR',
                   help='CoppeliaSim directory (default $COPPELIASIM_ROOT, '
                        'else CoppeliaSim_Edu next to this script)')
    g.add_argument('--scene', default=None, metavar='PATH',
                   help='scene file (default simulation.ttt next to this '
                        'script)')
    g = p.add_argument_group('run control')
    g.add_argument('--duration', type=float, default=None, metavar='S',
                   help='with --no-cli, total run time in seconds '
                        '(default: stop once metrics are done)')
    g.add_argument('--warmup', type=float, default=20.0, metavar='S',
                   help='with --no-cli, seconds to wait before metrics; '
                        'batman_adv needs about 18 with 5 drones '
                        '(default 20)')
    g.add_argument('--no-cli', action='store_true',
                   help='batch mode: no CLI, no telemetry plot')
    g.add_argument('--telemetry', action='store_true',
                   help='show the position plot (CLI mode only)')
    g.add_argument('--output-dir', default=None, metavar='DIR',
                   help='results directory (default ./uav-results/<time>)')
    g = p.add_argument_group('metrics')
    g.add_argument('--metrics', action='store_true',
                   help='run the traffic metrics in batch mode')
    g.add_argument('--pairs', default=None, metavar='A:B,...',
                   help='source:destination pairs (default dr1:drN)')
    g.add_argument('--ping-count', type=int, default=20, metavar='N',
                   help='pings per pair (default 20)')
    g.add_argument('--ping-interval', type=float, default=0.5, metavar='S',
                   help='seconds between pings (default 0.5)')
    g.add_argument('--iperf-time', type=float, default=10.0, metavar='S',
                   help='iperf test length in seconds (default 10)')
    g.add_argument('--udp-bandwidth', default='1M', metavar='B',
                   help='iperf UDP bandwidth, e.g. 1M (default 1M)')
    return p


def parse_pairs(parser, text, n):
    pairs = []
    for item in text.split(','):
        names = item.split(':')
        if len(names) != 2 or not all(PAIR_RE.match(x) for x in names):
            parser.error('invalid pair %r, expected drX:drY' % item)
        if names[0] == names[1]:
            parser.error('pair %r has identical endpoints' % item)
        for name in names:
            if name not in drones.drone_names(n):
                parser.error('pair %r: %s is not one of the %d drones'
                             % (item, name, n))
        pairs.append(tuple(names))
    return tuple(pairs)


def check_range(parser, ok, message):
    if not ok:
        parser.error(message)


def parse_args(argv=None):
    parser = build_parser()
    a = parser.parse_args(argv)
    n = a.drones
    check_range(parser, 1 <= n <= MAX_DRONES,
                '--drones must be between 1 and %d' % MAX_DRONES)
    check_range(parser, a.spacing > 0, '--spacing must be positive')
    check_range(parser, a.exp > 0, '--exp must be positive')
    check_range(parser, a.variance is None or a.variance >= 0,
                '--variance must not be negative')
    check_range(parser, a.nakagami_m is None or a.nakagami_m > 0,
                '--nakagami-m must be positive')
    error = topology.propagation_error(a.model, a.exp, a.variance,
                                       a.nakagami_m)
    if error:
        parser.error(error)
    check_range(parser, a.channel >= 1, '--channel must be positive')
    check_range(parser, a.duration is None or a.duration > 0,
                '--duration must be positive')
    check_range(parser, a.warmup >= 0, '--warmup must not be negative')
    check_range(parser, a.ping_count >= 1, '--ping-count must be positive')
    check_range(parser, a.ping_interval > 0,
                '--ping-interval must be positive')
    check_range(parser, a.iperf_time > 0, '--iperf-time must be positive')
    check_range(parser, BANDWIDTH_RE.match(a.udp_bandwidth),
                '--udp-bandwidth must look like 500K, 1M or 2.5M')
    if a.formation == 'scene' and n > 3:
        parser.error('--formation scene supports at most 3 drones; '
                     'choose another formation')
    routing = a.routing or default_routing(a.tech)
    if a.tech == 'mesh' and routing == 'batman_adv':
        parser.error('--tech mesh cannot be combined with --routing '
                     'batman_adv')
    if a.tech == 'infra' and routing != 'none':
        parser.error('--tech infra requires --routing none')
    if a.pairs is not None:
        pairs = parse_pairs(parser, a.pairs, n)
    elif n > 1:
        pairs = (('dr1', 'dr%d' % n),)
    else:
        pairs = ()
    coppelia_dir = (a.coppelia_dir or os.environ.get('COPPELIASIM_ROOT')
                    or os.path.join(UAV_DIR, 'CoppeliaSim_Edu'))
    scene = a.scene or os.path.join(UAV_DIR, 'simulation.ttt')
    if a.coppelia != 'off':
        if not os.path.isfile(os.path.join(coppelia_dir, 'coppeliaSim.sh')):
            parser.error('coppeliaSim.sh not found in %s' % coppelia_dir)
        if not os.path.isfile(scene):
            parser.error('scene file not found: %s' % scene)
    output_dir = os.path.abspath(a.output_dir or os.path.join(
        'uav-results', time.strftime('%Y%m%d-%H%M%S')))
    if os.path.exists(output_dir) and (not os.path.isdir(output_dir)
                                       or os.listdir(output_dir)):
        parser.error('--output-dir %s exists and is not an empty '
                     'directory' % output_dir)
    return ExperimentConfig(
        drones=n, formation=a.formation, spacing=a.spacing,
        trajectory=a.trajectory, seed=a.seed, model=a.model, exp=a.exp,
        variance=a.variance, nakagami_m=a.nakagami_m, tech=a.tech,
        routing=routing, channel=a.channel, coppelia=a.coppelia,
        coppelia_dir=os.path.abspath(coppelia_dir),
        scene=os.path.abspath(scene), duration=a.duration, warmup=a.warmup,
        no_cli=a.no_cli, telemetry=a.telemetry,
        output_dir=output_dir, metrics=a.metrics,
        pairs=pairs, ping_count=a.ping_count,
        ping_interval=a.ping_interval, iperf_time=a.iperf_time,
        udp_bandwidth=a.udp_bandwidth)
