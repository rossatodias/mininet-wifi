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

## Formations, trajectories and propagation

All formations are deterministic, lie in the `z = 0` plane and are centred
on `(50, 50, 0)`, the middle of the CoppeliaSim floor (2.5 m altitude).
`--spacing` is the distance between neighbours:

| Formation | Layout |
| --- | --- |
| `line` | along x |
| `grid` | `ceil(sqrt(n))` columns, filled row by row |
| `circle` | evenly on a circle whose neighbour chord is the spacing |
| `star` | `dr1` in the centre, the others on a circle of radius = spacing |
| `random` | uniform in a square of side `spacing * ceil(sqrt(n))`, at least `spacing / 2` apart, reproducible with `--seed` (fails with an error when no free spot is found) |

Trajectories are functions of `t`, the seconds since the sync thread
started, and return the formation position at `t = 0`:

| Trajectory | Motion |
| --- | --- |
| `static` | none |
| `scene` | the built-in pattern of `simulation.ttt` |
| `sweep` | the formation breathes about the centre in xy, scale `1 ... 3` (`SWEEP_MAX_SCALE`) and back every 60 s (`SWEEP_PERIOD`) |
| `orbit` | rigid rotation about the centre in xy, one turn per 120 s (`ORBIT_PERIOD`) |

With `--spacing 20` and the default `--exp 4.5` (range about 46 m) `sweep`
makes neighbours leave each other's range and come back, so pings fail while
the formation is expanded and succeed again when it contracts. With the
default 20 s warmup the metrics start while the formation is already
expanding. The sweep extends the formation up to three times, keep it inside
the scene floor. With CoppeliaSim the drones follow the targets with a lag of
about 0.3 s (about 0.3 m).

`--model` selects the wmediumd propagation model; the parameters a model does
not use are ignored:

| `--model` | Parameters used | wmediumd `model_name` |
| --- | --- | --- |
| `friis` | none | `free_space` |
| `logDistance` | `--exp` | `log_distance` |
| `twoRayGround` | none | `two_ray_ground` |
| `logNormalShadowing` | `--exp` | `log_normal_shadowing` |
| `nakagami` | `--exp`, `--nakagami-m` | `nakagami` |
| `ITU` | none | `itu` |

Unset parameters keep the Mininet-WiFi defaults. wmediumd stores `--exp` with
one decimal and `--nakagami-m` with two (and requires it to be at least 0.5),
so other values are rejected instead of being rounded silently.
`--variance` is rejected with `logNormalShadowing`: in wmediumd interference
mode Mininet-WiFi never sends the shadowing sample to wmediumd, so it would
have no effect.

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

## Link technologies

`--tech` selects the wireless technology; `--routing` defaults to
`batman_adv` for `adhoc` and `none` otherwise.

| `--tech` | Mode | Address | Interface in `LinkInfo.interfaces` |
| --- | --- | --- | --- |
| `adhoc` | IBSS, optional batman_adv | `192.168.123.N` on `bat0` with batman_adv, else `10.0.0.N` | `drX-wlan0` |
| `mesh` | IEEE 802.11s, HWMP path selection | `10.0.0.N` | `drX-mp0` |
| `infra` | managed stations on one access point | `10.0.0.N` | `drX-wlan0` |

`mesh` needs `--routing none` and `infra` runs without a controller: the
access point `ap1` uses `failMode='standalone'` and is started with
`ap.start([])`. `links.endpoint_ip(node, cfg)` returns the address to ping for
every technology.

The access point is the only node that must exist before
`net.configureNodes()` and start after `net.build()`, so the runner calls
`links.add_access_points(net, stations, cfg)` right after the stations are
created and `links.start(net, info)` right after `net.build()`. Both do
nothing for `adhoc` and `mesh`. After `build()` the stations are
disconnected and associated again, since `build()` tried to connect them
before hostapd was up.

For `infra`, `ap1` sits at the centroid (x, y; z = 0) of the initial
formation, on the same channel, mode `g`, with SSID `infraNet`. Its range is
the distance to the farthest drone plus 5 m, rounded up, set with the
`range` parameter (Mininet-WiFi turns it into a tx power). The coverage is
computed for the initial formation only: a trajectory that leaves it can
leave the access point's coverage.

`--warmup` stays a fixed wait. Time until the first and the last drone can
ping each other, measured from the end of `net.build()` (one run each,
`--formation line --spacing 20`, `ping -O -D -i 0.5` started right after the
network came up):

| Technology | Drones | First successful ping |
| --- | --- | --- |
| adhoc + batman_adv | 5 (2 hops) | about 18 s |
| adhoc, no routing | 3 | first ping |
| mesh | 5 (2 hops) | first ping |
| infra | 3 and 5 | first ping |

Only batman_adv needs a long warmup: keep the default 20 s with a margin
(25 s is safer for more drones). The other technologies are ready as soon as
`net.build()` returns, since the stations are already associated (infra) or
the path is resolved on the first frame (mesh).

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

## Metrics

With `--metrics` the runner calls `metrics.run_metrics(net, cfg, info,
out_dir)` once, after the warmup. For each pair in `--pairs` it runs, one
after the other:

1. `ping -D -i <ping-interval> -c <ping-count> -W 1`
2. `iperf3` TCP for `--iperf-time` seconds
3. `iperf3` UDP at `--udp-bandwidth` for `--iperf-time` seconds

The iperf3 server runs on the destination with `-s -1` and the client is
killed if it outlives `--iperf-time` plus a margin, so an unreachable
destination ends with recorded errors, not a hang. The time per pair is
about `ping_count * ping_interval + 2 * iperf_time` plus a few seconds of
overhead. While a pair is measured, the distance between its two drones is
sampled at about 1 Hz, and the route is recorded before and after:

| Routing | Snapshot | Hops |
| --- | --- | --- |
| `batman_adv` | `batctl o`, `batctl tr <MAC of the destination interface>` | last hop of the traceroute |
| `--tech mesh` | `iw dev <intf> mpath dump` on the source | `HOP_COUNT` of the destination MAC (`null` if no path yet or iw has no such column) |
| otherwise | none | `null` |

Files written to the output directory, in addition to `config.json` and
`positions.csv`:

| File | Content |
| --- | --- |
| `ping.csv` | `pair,epoch,seq,rtt_ms`, one row per reply (`pair` is `drX:drY`) |
| `ping_<src>_<dst>.txt` | raw ping output |
| `iperf_tcp_<src>_<dst>.json`, `iperf_udp_<src>_<dst>.json` | raw iperf3 JSON, or `{"error": ...}` |
| `distance.csv` | `pair,epoch,distance_m`, sampled at about 1 Hz |
| `routing_<src>_<dst>.txt` | raw route snapshots, `before` and `after` |
| `rtt_<src>_<dst>.png` | RTT over time, lost pings in red, distance on the right axis |
| `summary.json` | results per pair, see below |

All timestamps are epoch seconds, so `ping.csv` and `distance.csv` line up.
`summary.json` keys are stable; a value that could not be measured is
`null`:

```
{
  "tech": "adhoc", "routing": "batman_adv",
  "pairs": [{
    "src": "dr1", "dst": "dr3", "dst_ip": "192.168.123.3",
    "ping": {"sent": 20, "received": 20, "loss_percent": 0.0,
             "rtt_ms": {"min": 0.8, "avg": 2.1, "max": 5.0, "mdev": 0.9}},
    "tcp": {"throughput_mbps": 6.7, "retransmits": 109, "error": null},
    "udp": {"throughput_mbps": 1.0, "jitter_ms": 0.4,
            "lost_percent": 0.0, "error": null},
    "distance_m": {"min": 40.0, "mean": 40.0, "max": 40.0},
    "hops": {"before": 2, "after": 2},
    "errors": [],
    "duration_s": 31.1
  }]
}
```

`rtt_ms` is `null` without replies. Throughputs are measured at the
receiver (UDP: the rate that arrived). `duration_s` is the wall-clock time of
the measurements of the pair. `errors` lists `no ping reply` and
one `tcp: ...` or `udp: ...` entry per failed iperf3 test.

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

## Experiment matrix

`run_matrix.py` runs `uav_experiment.py` once per configuration and seed,
one after the other, and aggregates the results. It needs root like the
experiment itself (`--dry-run` and `--aggregate-only` do not).

```
# print the 51 commands of the default matrix (17 configurations x 3 seeds)
python3 run_matrix.py --dry-run

# the default campaign, headless CoppeliaSim
sudo -E python3 run_matrix.py --output-dir uav-matrix/sweep

# quick check without CoppeliaSim and shorter measurements
sudo -E python3 run_matrix.py --coppelia off --only baseline tech-mesh \
    --seeds 1 --ping-count 10 --iperf-time 3

# continue an interrupted campaign, rebuild the tables and plots
sudo -E python3 run_matrix.py --output-dir uav-matrix/sweep --resume
python3 run_matrix.py --aggregate-only uav-matrix/sweep
```

The baseline is 5 drones in a `line` 20 m apart, `sweep` trajectory,
`adhoc` with batman_adv, `logDistance` with exponent 3, warmup 25 s, 120
pings every 0.5 s (one 60 s sweep period), 10 s iperf3 tests, UDP at 1M,
metrics for `dr1:drN`. Each factor is changed on its own:

| Factor | Levels (configuration names) |
| --- | --- |
| baseline | `baseline` |
| drones | `drones-3`, `drones-8`, `drones-10` |
| tech | `tech-infra`, `tech-mesh` |
| model | `model-friis`, `model-logDistance-exp4.5`, `model-twoRayGround`, `model-logNormalShadowing`, `model-nakagami`, `model-ITU` |
| formation | `formation-grid`, `formation-circle`, `formation-star` |
| trajectory | `trajectory-static`, `trajectory-orbit` |

That is 17 configurations. `logNormalShadowing` and `nakagami` use exponent
3 and `--variance` is never passed. `--seeds` (default `1 2 3`) repeats
every configuration; the seed only changes `random` formations, so here it
labels a repetition. `--only` and `--exclude` take configuration names.
`--full` runs the cartesian product of all levels (1008 configurations,
named like `tech-mesh+trajectory-orbit`) and warns about the size;
combinations that fail the configuration checks are skipped with the reason
printed.

`--warmup`, `--ping-count`, `--ping-interval`, `--iperf-time` and
`--udp-bandwidth` replace the baseline values in every configuration.
`--coppelia` is `headless` (default) or `off`.

Runs are sequential, seed by seed, and each has a time limit: warmup, the
metrics of the pairs and 120 s of startup margin (240 s for the baseline),
or `--run-timeout`. A run that fails or times out is recorded, `mn -c` runs
before the next one and the campaign goes on. On a timeout the run gets
SIGTERM so it can tear down, then SIGKILL for its whole process group. The
exit status is 1 if any run failed. `--resume` skips runs that already have
a `summary.json`.

```
<campaign>/
    campaign.json          matrix, seeds, times, outcome of every run
    results.csv            one row per run
    aggregated.csv         mean, std and n of each metric per configuration
    summary.md             aggregated.csv as a markdown table
    factor-<factor>.png    one comparison figure per factor
    <config>/seed<S>.log   output of the run
    <config>/seed<S>/      the output directory of that run
```

`results.csv` has the configuration, factor, level, seed, the main flags
and, for the first pair, loss, RTT average and maximum, TCP and UDP
throughput, jitter, UDP loss, hops after, mean distance, error count,
measurement duration, exit code, timeout flag and wall time. `aggregated.csv`
ignores missing values and reports how many runs had one (`_n`); the
standard deviation is the sample one and is empty for a single value. Each
figure shows loss, RTT, TCP, UDP, jitter and UDP loss as bars with mean and
standard deviation, the baseline in gray.

A baseline run takes about 135 s with headless CoppeliaSim (25 s warmup,
80 s of metrics, about 25 s to start and stop the network and CoppeliaSim)
and about 125 s with `--coppelia off`. The default 51 runs take about two
hours; the larger drone counts start more slowly.

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
