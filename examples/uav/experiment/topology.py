"""Formations, trajectories and propagation models."""

import math
import random

CENTER = (50.0, 50.0, 0.0)
SWEEP_MAX_SCALE = 3.0
SWEEP_PERIOD = 60.0
ORBIT_PERIOD = 120.0
RANDOM_ATTEMPTS = 1000
MIN_NAKAGAMI_M = 0.5


def _centered(points):
    """Translate `points` so that their centroid is CENTER."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return [(CENTER[0] + x - cx, CENTER[1] + y - cy, CENTER[2])
            for x, y in points]


def _on_circle(count, radius):
    """`count` points evenly spaced on a circle around CENTER."""
    return [(CENTER[0] + radius * math.cos(2 * math.pi * i / count),
             CENTER[1] + radius * math.sin(2 * math.pi * i / count),
             CENTER[2]) for i in range(count)]


def _line(n, spacing):
    return _centered([(i * spacing, 0.0) for i in range(n)])


def _grid(n, spacing):
    cols = math.ceil(math.sqrt(n))
    return _centered([((i % cols) * spacing, (i // cols) * spacing)
                      for i in range(n)])


def _circle(n, spacing):
    if n == 1:
        return [CENTER]
    return _on_circle(n, spacing / (2 * math.sin(math.pi / n)))


def _star(n, spacing):
    return [CENTER] + _on_circle(n - 1, spacing)


def _random(n, spacing, seed):
    rng = random.Random(seed)
    half = spacing * math.ceil(math.sqrt(n)) / 2
    points = []
    for _ in range(n):
        for _ in range(RANDOM_ATTEMPTS):
            p = (CENTER[0] + rng.uniform(-half, half),
                 CENTER[1] + rng.uniform(-half, half), CENTER[2])
            if all(math.dist(p, q) >= spacing / 2 for q in points):
                points.append(p)
                break
        else:
            raise ValueError(
                'random formation: no free spot for drone %d after %d '
                'attempts; increase --spacing or reduce --drones'
                % (len(points) + 1, RANDOM_ATTEMPTS))
    return points


def formation(kind, n, spacing, seed):
    """Mininet-WiFi positions of `n` drones for the formation `kind`."""
    if kind == 'line':
        return _line(n, spacing)
    if kind == 'grid':
        return _grid(n, spacing)
    if kind == 'circle':
        return _circle(n, spacing)
    if kind == 'star':
        return _star(n, spacing)
    if kind == 'random':
        return _random(n, spacing, seed)
    raise NotImplementedError('formation %s' % kind)


def _sweep(base, t):
    """Scale the formation about CENTER between 1 and SWEEP_MAX_SCALE."""
    phase = 2 * math.pi * t / SWEEP_PERIOD
    grow = (SWEEP_MAX_SCALE - 1) * (1 - math.cos(phase)) / 2
    return (base[0] + (base[0] - CENTER[0]) * grow,
            base[1] + (base[1] - CENTER[1]) * grow, base[2])


def _orbit(base, t):
    """Rotate the formation about CENTER."""
    angle = 2 * math.pi * t / ORBIT_PERIOD
    dx, dy = base[0] - CENTER[0], base[1] - CENTER[1]
    return (base[0] + dx * (math.cos(angle) - 1) - dy * math.sin(angle),
            base[1] + dx * math.sin(angle) + dy * (math.cos(angle) - 1),
            base[2])


def trajectory(kind, base, t):
    """Position at `t` seconds of a drone whose formation position is `base`.
    """
    if kind in ('static', 'scene'):
        return tuple(base)
    if kind == 'sweep':
        return _sweep(base, t)
    if kind == 'orbit':
        return _orbit(base, t)
    raise NotImplementedError('trajectory %s' % kind)


def _is_multiple(value, step):
    ratio = value / step
    return abs(ratio - round(ratio)) <= 1e-9


def propagation_error(model, exp, variance, nakagami_m):
    """Why the propagation flags cannot be honoured, or None.

    wmediumd stores the exponent with one decimal and the Nakagami shape
    with two, and Mininet-WiFi never sends the shadowing variance to it, so
    such values are rejected instead of being dropped or rounded silently.
    Flags the model does not use are ignored.
    """
    if model in ('logDistance', 'nakagami', 'logNormalShadowing'):
        if not _is_multiple(exp, 0.1):
            return ('--exp %g is not a multiple of 0.1, the resolution of '
                    'the wmediumd configuration' % exp)
    if model == 'logNormalShadowing' and variance is not None:
        return ('--variance has no effect with wmediumd interference mode: '
                'the shadowing sample is never sent to wmediumd')
    if model == 'nakagami' and nakagami_m is not None:
        if nakagami_m < MIN_NAKAGAMI_M:
            return '--nakagami-m must be at least %g' % MIN_NAKAGAMI_M
        if not _is_multiple(nakagami_m, 0.01):
            return ('--nakagami-m %g is not a multiple of 0.01, the '
                    'resolution of the wmediumd configuration' % nakagami_m)
    return None


def propagation(cfg):
    """Return (model_name, kwargs) for net.setPropagationModel.

    The flags must have passed `propagation_error`.
    """
    if cfg.model in ('logDistance', 'logNormalShadowing'):
        return cfg.model, {'exp': cfg.exp}
    if cfg.model == 'nakagami':
        kwargs = {'exp': cfg.exp}
        if cfg.nakagami_m is not None:
            kwargs['m'] = cfg.nakagami_m
        return 'nakagami', kwargs
    if cfg.model in ('friis', 'twoRayGround', 'ITU'):
        return cfg.model, {}
    raise ValueError('unknown propagation model %s' % cfg.model)
