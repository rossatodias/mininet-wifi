"""Formations, trajectories and propagation models."""


def _unsupported(what):
    raise NotImplementedError(what)


def formation(kind, n, spacing, seed):
    """Mininet-WiFi positions of `n` drones for the formation `kind`."""
    if kind == 'line':
        return [(10.0 + i * spacing, 50.0, 0.0) for i in range(n)]
    return _unsupported('formation %s' % kind)


def trajectory(kind, base, t):
    """Position at `t` seconds of a drone whose formation position is `base`.
    """
    if kind in ('static', 'scene'):
        return tuple(base)
    return _unsupported('trajectory %s' % kind)


def propagation(cfg):
    """Return (model_name, kwargs) for net.setPropagationModel."""
    if cfg.model == 'logDistance':
        return 'logDistance', {'exp': cfg.exp}
    if cfg.model == 'friis':
        return 'friis', {}
    return _unsupported('propagation model %s' % cfg.model)
