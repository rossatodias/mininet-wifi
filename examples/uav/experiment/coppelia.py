"""CoppeliaSim process control and ZMQ remote API helpers."""

import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass

from mininet.log import info

HOST = '127.0.0.1'
ZMQ_PORT = 23000
RPC_TIMEOUT_MS = 15000
SCENE_ORIGIN = 50.0
SCENE_SCALE = 20.0
MOTION_LINE = re.compile(
    r'(?m)^(\s*targetPosA\[\d\]=targetPosA\[\d\])[-+][0-9.]+[ \t]*$')


@dataclass(frozen=True)
class DroneHandles:
    base: int
    model: int
    target: int


def scene_to_net(p, origin=SCENE_ORIGIN, scale=SCENE_SCALE):
    """Convert a CoppeliaSim position to Mininet-WiFi coordinates."""
    return tuple(origin - scale * v for v in p)


def net_to_scene(p, origin=SCENE_ORIGIN, scale=SCENE_SCALE):
    """Convert a Mininet-WiFi position to CoppeliaSim coordinates."""
    return tuple((origin - v) / scale for v in p)


def launch_argv(coppelia_dir, scene, headless, port=ZMQ_PORT):
    """Command line that opens `scene` with the ZMQ API on `port`."""
    argv = [os.path.join(coppelia_dir, 'coppeliaSim.sh')]
    argv.append('-h' if headless else '-gGUIITEMS_2')
    argv.append('-GzmqRemoteApi.rpcPort=%d' % port)
    argv.append(scene)
    return argv


def launch(cfg, log_path, port=ZMQ_PORT):
    """Start CoppeliaSim in its own session; output goes to `log_path`.

    Headless mode exits when stdin reaches EOF, so stdin stays open.
    """
    argv = launch_argv(cfg.coppelia_dir, cfg.scene,
                       cfg.coppelia == 'headless', port)
    with open(log_path, 'wb') as log:
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=log,
                                stderr=subprocess.STDOUT,
                                start_new_session=True)


def _remote_api_client(coppelia_dir):
    path = os.path.join(coppelia_dir, 'programming', 'zmqRemoteApi',
                        'clients', 'python', 'src')
    if path not in sys.path:
        sys.path.insert(0, path)
    from coppeliasim_zmqremoteapi_client import RemoteAPIClient
    return RemoteAPIClient


def new_client(coppelia_dir, port=ZMQ_PORT):
    """Connect to a running CoppeliaSim; return (client, sim).

    The client is not thread-safe: use one per thread.
    """
    client = _remote_api_client(coppelia_dir)(host=HOST, port=port)
    sim = client.require('sim')
    client.socket.RCVTIMEO = RPC_TIMEOUT_MS
    return client, sim


def close_client(client):
    client.socket.close(linger=0)
    client.context.term()


def connect(cfg, proc, port=ZMQ_PORT, timeout=60.0):
    """Wait for the API of `proc` to answer; return (client, sim)."""
    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            raise RuntimeError('CoppeliaSim exited with status %s'
                               % proc.returncode)
        try:
            socket.create_connection((HOST, port), 0.5).close()
            return new_client(cfg.coppelia_dir, port)
        except Exception as err:
            if time.monotonic() > deadline:
                raise RuntimeError('CoppeliaSim API not reachable on '
                                   'port %d: %s' % (port, err))
            time.sleep(0.2)


def wait_for_scene(sim, timeout=60.0):
    """Wait until the first drone of the scene exists."""
    deadline = time.monotonic() + timeout
    while sim.getObject('/Quadricopter_base',
                        {'index': 0, 'noError': True}) == -1:
        if time.monotonic() > deadline:
            raise RuntimeError('scene did not load')
        time.sleep(0.2)


# --- ensure_drones: owned by the drone-count work ---------------------------

def ensure_drones(sim, n):
    """Handles of `n` drones; drone i corresponds to dr{i + 1}."""
    if n > 3:
        raise NotImplementedError('more than 3 drones in the scene')
    handles = []
    for i in range(n):
        base = sim.getObject('/Quadricopter_base', {'index': i})
        handles.append(DroneHandles(base=base,
                                    model=sim.getObjectParent(base),
                                    target=sim.getObjectChild(base, 0)))
    return handles

# --- end of ensure_drones ---------------------------------------------------


def disable_builtin_motion(sim, drones):
    """Remove the diamond target pattern from the controllers in memory."""
    for d in drones:
        script = sim.getScript(sim.scripttype_childscript, d.model)
        text = sim.getScriptStringParam(script, sim.scriptstringparam_text)
        text, count = MOTION_LINE.subn(r'\1', text)
        if count == 0:
            raise RuntimeError('built-in motion not found in controller '
                               'of object %d' % d.model)
        sim.setScriptStringParam(script, sim.scriptstringparam_text, text)


def place_drones(sim, drones, net_positions):
    """Move each drone (model, base and target) to a Mininet-WiFi position.

    Only valid before the simulation starts.
    """
    for d, pos in zip(drones, net_positions):
        pos = list(net_to_scene(pos))
        base = sim.getObjectPosition(d.base, sim.handle_world)
        model = sim.getObjectPosition(d.model, sim.handle_world)
        sim.setObjectPosition(
            d.model, [p + m - b for p, m, b in zip(pos, model, base)],
            sim.handle_world)
        sim.setObjectPosition(d.target, pos, sim.handle_world)


def set_targets(sim, drones, net_positions):
    """Set the position each drone controller flies to."""
    for d, pos in zip(drones, net_positions):
        sim.setObjectPosition(d.target, list(net_to_scene(pos)),
                              sim.handle_world)


def read_positions(sim, drones):
    """Current drone positions in Mininet-WiFi coordinates."""
    return [scene_to_net(sim.getObjectPosition(d.base, sim.handle_world))
            for d in drones]


def start_simulation(sim):
    sim.setBoolParam(sim.boolparam_realtime_simulation, True)
    sim.startSimulation()


def stop_simulation(sim, timeout=10.0):
    sim.stopSimulation()
    deadline = time.monotonic() + timeout
    while sim.getSimulationState() != sim.simulation_stopped:
        if time.monotonic() > deadline:
            raise RuntimeError('simulation did not stop')
        time.sleep(0.1)


def _kill_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    return True


def terminate(proc, timeout=10.0):
    """Stop CoppeliaSim and every process it started."""
    if proc.stdin:
        proc.stdin.close()
    if _kill_group(proc.pid, signal.SIGTERM):
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            info('*** CoppeliaSim ignored SIGTERM, killing it\n')
    _kill_group(proc.pid, signal.SIGKILL)
    proc.wait()
