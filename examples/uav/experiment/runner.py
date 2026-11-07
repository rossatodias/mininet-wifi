"""Run one experiment: network, CoppeliaSim, position sync, CLI or batch."""

import csv
import dataclasses
import json
import os
import signal
import sys
import threading
import time

from mininet.log import setLogLevel, info, warn
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

from experiment import coppelia, drones, links, metrics, topology

TICK = 0.1


class PositionSync(threading.Thread):
    """Keep station positions in step with the drones.

    With CoppeliaSim, drive the drone targets from the trajectory and copy
    the drone positions to the stations. Without it, apply the trajectory
    to the stations directly.
    """

    def __init__(self, stations, bases, cfg, csv_path, handles=None):
        super().__init__(daemon=True, name='position-sync')
        self.stations = stations
        self.bases = bases
        self.cfg = cfg
        self.csv_path = csv_path
        self.handles = handles
        self.stop_event = threading.Event()
        self.error = None

    def stop(self):
        self.stop_event.set()
        self.join()

    def run(self):
        client = None
        try:
            sim = None
            if self.handles:
                client, sim = coppelia.new_client(self.cfg.coppelia_dir)
            with open(self.csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['time', 'node', 'x', 'y', 'z'])
                self.loop(sim, writer, f)
        except Exception as err:
            self.error = err
            warn('*** position sync stopped: %s\n' % err)
        finally:
            if client is not None:
                coppelia.close_client(client)

    def loop(self, sim, writer, out):
        start = time.monotonic()
        while not self.stop_event.is_set():
            t = time.monotonic() - start
            targets = [topology.trajectory(self.cfg.trajectory, b, t)
                       for b in self.bases]
            if sim is None:
                positions = targets
            else:
                if self.cfg.trajectory != 'scene':
                    coppelia.set_targets(sim, self.handles, targets)
                positions = coppelia.read_positions(sim, self.handles)
            for sta, pos in zip(self.stations, positions):
                sta.setPosition('%.3f,%.3f,%.3f' % tuple(pos))
                writer.writerow(['%.3f' % t, sta.name] +
                                ['%.3f' % v for v in pos])
            out.flush()
            self.stop_event.wait(TICK)


def build_network(net, cfg):
    info("*** Creating nodes\n")
    stations = drones.add_stations(net, cfg)
    model, kwargs = topology.propagation(cfg)
    net.setPropagationModel(model=model, **kwargs)
    info("*** Configuring nodes\n")
    net.configureNodes()
    info("*** Creating links\n")
    link_info = links.build_links(net, stations, cfg)
    info("*** Starting network\n")
    net.build()
    return stations, link_info


def start_coppelia(cfg, out_dir, bases):
    """Launch CoppeliaSim, prepare the scene and start the simulation.

    Returns (process, client, drone handles, base positions).
    """
    info("*** Starting CoppeliaSim (%s)\n" % cfg.coppelia)
    proc = coppelia.launch(cfg, os.path.join(out_dir, 'coppeliasim.log'))
    client = None
    try:
        client, sim = coppelia.connect(cfg, proc)
        coppelia.wait_for_scene(sim)
        handles = coppelia.ensure_drones(sim, cfg.drones)
        if cfg.trajectory != 'scene':
            coppelia.disable_builtin_motion(sim, handles)
        if cfg.formation != 'scene':
            coppelia.place_drones(sim, handles, bases)
        else:
            bases = coppelia.read_positions(sim, handles)
        coppelia.start_simulation(sim)
    except BaseException:
        if client is not None:
            coppelia.close_client(client)
        coppelia.terminate(proc)
        raise
    return proc, client, sim, handles, bases


def stop_coppelia(proc, client, sim):
    try:
        coppelia.stop_simulation(sim)
    except Exception as err:
        warn('*** could not stop the simulation: %s\n' % err)
    coppelia.close_client(client)
    coppelia.terminate(proc)


def run_batch(net, cfg, link_info, out_dir):
    start = time.monotonic()
    info("*** Warming up for %g s\n" % cfg.warmup)
    time.sleep(cfg.warmup)
    if cfg.metrics:
        info("*** Running metrics\n")
        metrics.run_metrics(net, cfg, link_info, out_dir)
    if cfg.duration is not None:
        remaining = cfg.duration - (time.monotonic() - start)
        if remaining > 0:
            info("*** Holding for %.1f s\n" % remaining)
            time.sleep(remaining)


def run_cli(net, stations, cfg):
    if cfg.telemetry:
        from mn_wifi.telemetry import telemetry
        telemetry(nodes=stations, single=True, data_type='position')
    info("*** Running CLI\n")
    CLI(net)


def write_config(cfg, out_dir):
    with open(os.path.join(out_dir, 'config.json'), 'w') as f:
        json.dump(dataclasses.asdict(cfg), f, indent=2)


def make_output_dir(path):
    """Create `path`; return the topmost directory this call created.

    An already existing `path` (empty, see config) is returned as is.
    """
    top = path
    while not os.path.exists(os.path.dirname(top)):
        top = os.path.dirname(top)
    os.makedirs(path, exist_ok=True)
    return top


def hand_over_output(top):
    """Give the tree under `top` to the user who started sudo."""
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if uid is None or gid is None:
        return
    os.lchown(top, int(uid), int(gid))
    for root, dirs, files in os.walk(top):
        for name in dirs + files:
            os.lchown(os.path.join(root, name), int(uid), int(gid))


def _on_sigterm(signum, frame):
    sys.exit(128 + signum)


def run(cfg):
    setLogLevel('info')
    if os.geteuid() != 0:
        sys.exit('uav_experiment.py needs root: run it with sudo -E')
    if cfg.no_cli:
        os.environ['MPLBACKEND'] = 'Agg'
    signal.signal(signal.SIGTERM, _on_sigterm)
    out_dir = cfg.output_dir
    created = make_output_dir(out_dir)
    write_config(cfg, out_dir)
    if cfg.coppelia == 'off' and cfg.trajectory == 'scene':
        info("*** No CoppeliaSim: the scene trajectory keeps the drones "
             "static\n")
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)
    sync = proc = None
    try:
        stations, link_info = build_network(net, cfg)
        bases = drones.initial_positions(cfg)
        handles = client = sim = None
        if cfg.coppelia != 'off':
            proc, client, sim, handles, bases = start_coppelia(
                cfg, out_dir, bases)
        sync = PositionSync(stations, bases, cfg,
                            os.path.join(out_dir, 'positions.csv'), handles)
        sync.start()
        if cfg.no_cli:
            run_batch(net, cfg, link_info, out_dir)
            if sync.error:
                sys.exit('position sync failed: %s' % sync.error)
        else:
            run_cli(net, stations, cfg)
    finally:
        info("*** Stopping\n")
        if sync is not None:
            sync.stop()
        if proc is not None:
            stop_coppelia(proc, client, sim)
        try:
            net.stop()
        except Exception as err:
            warn('*** could not stop the network cleanly: %s\n' % err)
        hand_over_output(created)
