"""Drone naming, addressing and initial placement."""

from experiment import topology

SCENE_POSITIONS = [(30.0, 60.0, 0.0), (70.0, 30.0, 0.0), (10.0, 20.0, 0.0)]


def drone_names(n):
    return ['dr%d' % i for i in range(1, n + 1)]


def drone_mac(index):
    """MAC of drone number `index` (1-based)."""
    return '00:00:00:00:00:%02x' % index


def drone_ip(index):
    return '10.0.0.%d/8' % index


def initial_positions(cfg):
    """Initial Mininet-WiFi positions, one per drone."""
    if cfg.formation == 'scene' and cfg.drones <= len(SCENE_POSITIONS):
        return list(SCENE_POSITIONS[:cfg.drones])
    return topology.formation(cfg.formation, cfg.drones, cfg.spacing,
                              cfg.seed)


def add_stations(net, cfg):
    """Add one station per drone to `net` and return them."""
    stations = []
    for i, (name, pos) in enumerate(
            zip(drone_names(cfg.drones), initial_positions(cfg)), start=1):
        stations.append(net.addStation(
            name, mac=drone_mac(i), ip=drone_ip(i),
            position='%s,%s,%s' % pos))
    return stations
