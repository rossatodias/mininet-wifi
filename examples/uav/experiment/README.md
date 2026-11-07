# UAV experiment framework

`uav_experiment.py` runs a configurable Mininet-WiFi network whose stations
(`dr1` ... `drN`) follow drones simulated in CoppeliaSim. It is a flag-driven
alternative to `uav.py`: with no flags it builds the same network (3 drones,
ad hoc + batman_adv, logDistance exponent 4.5, wmediumd interference mode).
The CoppeliaSim link uses the ZMQ remote API directly, so there is no socket
server and no `data/` directory.

## Usage

Everything needs root because of Mininet-WiFi; use `sudo -E` so `DISPLAY`
reaches the CoppeliaSim window. Run `python3 uav_experiment.py --help` for the
full flag list.

```
# interactive, CoppeliaSim window, scene layout and motion (like uav.py)
sudo -E python3 uav_experiment.py

# batch, headless, 40 s, ping dr1 -> dr3 after the warmup
sudo -E python3 uav_experiment.py --coppelia headless --no-cli \
    --duration 40 --metrics

# no CoppeliaSim, drones in a line 20 m apart, drones do not move
sudo -E python3 uav_experiment.py --coppelia off --no-cli --metrics \
    --formation line --spacing 20 --duration 60
```

Batch mode (`--no-cli`) waits `--warmup` seconds (default 20, batman_adv needs
about that long to learn routes), runs the metrics if `--metrics` is given and
then holds until `--duration` has elapsed. Without `--duration` it exits when
the metrics are done. `--telemetry` (position plot) applies to the CLI only.

`--coppelia-dir` defaults to `$COPPELIASIM_ROOT`, otherwise
`CoppeliaSim_Edu` next to the script. The scene is `simulation.ttt` unless
`--scene` is given; it is never modified or saved.

## Drone count

`--drones N` (1 to 32) creates stations `dr1` ... `drN`, with MAC
`00:00:00:00:00:NN` (N in hex) and address `10.0.0.N/8`. For N above 3 an
explicit `--formation` is required (only `line` is implemented).

`coppelia.ensure_drones(sim, n)` makes the scene hold exactly `n`
quadcopters before the simulation starts, and returns their handles in a
fixed order: the scene drones first (scene order), then the copies in
creation order. Index `i` is `dr{i + 1}`.

- `n <= 3`: the unused scene drones are removed with `sim.removeModel`.
- `n > 3`: `sim.copyPasteObjects([model], 1)` pastes whole drone models,
  alternating between drones 2 and 3 (drone 1 starts the legacy remote API
  in its script and must not be copied). The base is found relative to the
  copy (`./Quadricopter_base`) and the target is its child, so no scene-wide
  index lookup is involved.

Each copy carries its own controller script. The script resolves its target
and base by name inside its own model, so every copy follows its own target
and `disable_builtin_motion` works on copies exactly as on the scene drones.

```
sudo -E python3 uav_experiment.py --no-cli --drones 8 --formation line \
    --spacing 10 --trajectory static --duration 40
```

Simulation load grows with the drone count (each drone has propeller
scripts and two cameras). Measured with software rendering (llvmpipe), the
simulation runs at about real time up to 5 drones headless, at 0.8x with 8
and at 0.7x with 10; the GUI is slower (about 0.3x with 5).

## Output

Each run writes to `--output-dir` (default `./uav-results/<timestamp>`, owned
by the invoking user when started through sudo):

| File | Content |
| --- | --- |
| `config.json` | resolved configuration |
| `positions.csv` | `time,node,x,y,z` sampled every ~0.1 s |
| `summary.json` | metrics results (with `--metrics`) |
| `ping_<src>_<dst>.txt` | raw ping output per pair |
| `coppeliasim.log` | CoppeliaSim output |

## Layout

| Module | Role |
| --- | --- |
| `config.py` | `ExperimentConfig`, all flags, validation |
| `runner.py` | build, start CoppeliaSim, position sync thread, CLI or batch, teardown |
| `coppelia.py` | process launch/teardown, ZMQ helpers, scene handles |
| `drones.py` | names, MAC/IP, initial positions |
| `topology.py` | formations, trajectories, propagation model |
| `links.py` | wireless link setup and endpoint addresses |
| `metrics.py` | traffic measurements |

Teardown always runs in reverse order: sync thread, CoppeliaSim (simulation
stop, then the whole process group is terminated), Mininet-WiFi. SIGTERM and
Ctrl-C are handled.

## Coordinates and motion

The scene and Mininet-WiFi coordinates are related per axis by
`net = 50 - 20 * scene` (same as `setNodePosition.py`);
`coppelia.scene_to_net` and `coppelia.net_to_scene` implement it. Every
~0.1 s the sync thread

1. computes the targets with `topology.trajectory` and writes them to the
   drone targets in CoppeliaSim (skipped for `--trajectory scene`),
2. reads the drone positions and calls `setPosition` on the stations.

With `--coppelia off` the trajectory is applied to the stations directly.

The scene controllers move their target along a built-in diamond pattern.
For any trajectory other than `scene` the pattern is removed from the
controller scripts in memory before the simulation starts, so Python drives
the targets. For any formation other than `scene` the drone models are moved
to the formation positions before the simulation starts. Real-time mode is
enabled so simulated time follows wall-clock time.

## Extension points

Each follow-up area is one module with a frozen interface. Choices that are
not implemented yet raise `NotImplementedError` and are accepted by the
config validation:

| Area | Interface | Implemented | Not implemented |
| --- | --- | --- | --- |
| Drone count | `drones.drone_names(n)`, `drones.initial_positions(cfg)`, `coppelia.ensure_drones(sim, n)` | up to 3 drones in the scene | `ensure_drones` for n > 3 |
| Topology | `topology.formation(kind, n, spacing, seed)`, `topology.trajectory(kind, base, t)`, `topology.propagation(cfg)` | `line`; `static`, `scene`; `logDistance`, `friis` | other formations, trajectories and models |
| Links | `links.build_links(net, stations, cfg) -> LinkInfo`, `links.endpoint_ip(node, cfg)` | `adhoc` with batman_adv or no routing | `infra`, `mesh` |
| Metrics | `metrics.run_metrics(net, cfg, info, out_dir)` | ping loss and RTT per pair | iperf, CSV, plots |

## Tests

Unit tests need neither root nor CoppeliaSim:

```
python3 -m unittest discover -s examples/uav/experiment/tests -t examples/uav
```

## Notes

- With batman_adv the routable address is on `bat0` (`192.168.123.N`), not
  `10.0.0.N`.
- Headless CoppeliaSim exits when its stdin closes, so the process is started
  with an open stdin pipe.
- CoppeliaSim starts helper processes (`python/pythonLauncher.py`); they
  belong to its process group and are terminated with it.
- The scene's first controller also opens the legacy remote API on port
  19999 once the simulation runs; the ZMQ API listens on port 23000 on all
  interfaces of the host.
