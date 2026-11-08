# UAV experiment framework

`uav_experiment.py` runs a configurable Mininet-WiFi network whose stations
(`dr1` ... `drN`) follow drones simulated in CoppeliaSim, and can measure
ping, iperf3 and routing behaviour between them. It is a flag-driven
alternative to `uav.py`: with no flags it builds the same network (3 drones,
ad hoc + batman_adv, logDistance exponent 4.5, wmediumd interference mode).
`run_matrix.py` runs many such experiments and aggregates the results.

The CoppeliaSim link uses the ZMQ remote API directly, so there is no socket
server and no `data/` directory. For installing CoppeliaSim and the
Mininet-WiFi requirements see the [demo README](../README.md).

## Requirements

Mininet-WiFi (`util/install.sh -Wlnfv`, with the `batman-adv` kernel module)
and CoppeliaSim are installed as described in the [demo README](../README.md).
On top of that the framework needs the following, tested on Ubuntu 24.04
with Python 3.12 and CoppeliaSim Edu 4.9.0 rev6:

| Package | Tested | Needed for |
|---|---|---|
| `python3-zmq` (pyzmq) | 24.0.1 | the CoppeliaSim ZMQ remote API client |
| `python3-cbor2` | 5.6.2 | the same client (message encoding) |
| `iperf3` | 3.16 | `--metrics` throughput, server and client run on the drones |
| `batctl` | 2024.0 | `--metrics` originator table and trace with `--routing batman_adv` |
| `iw` | 6.17 | `--metrics` mesh path dump with `--tech mesh`, built by `util/install.sh` |
| `python3-matplotlib` | 3.6.3 | RTT and matrix plots, drawn with the Agg backend |

The ZMQ client is not installed with pip: it is the one bundled with
CoppeliaSim (`<coppelia-dir>/programming/zmqRemoteApi/clients/python/src`),
and the framework adds it to `sys.path` itself. Without `pyzmq` and `cbor2`
it fails with an `ImportError` when CoppeliaSim is used (`--coppelia gui` or
`headless`); `--coppelia off` does not need them. `ping` and `ss` come from
`iputils-ping` and `iproute2`, which are installed by default.

```
sudo apt install python3-zmq python3-cbor2 iperf3 batctl
```

## Usage

Both scripts live in `examples/uav`. The experiment needs root because of
Mininet-WiFi; use `sudo -E` so `DISPLAY` reaches the CoppeliaSim window.
`--help` lists every flag and does not need root.

```
# interactive, CoppeliaSim window, scene layout and motion (like uav.py)
sudo -E python3 examples/uav/uav_experiment.py

# batch, headless CoppeliaSim, ping and iperf3 dr1 -> dr3 after the warmup
sudo -E python3 examples/uav/uav_experiment.py --coppelia headless --no-cli \
    --metrics

# no CoppeliaSim, 5 drones in a line 20 m apart, which do not move
sudo -E python3 examples/uav/uav_experiment.py --coppelia off --no-cli \
    --metrics --drones 5 --formation line --spacing 20 --trajectory static
```

Batch mode (`--no-cli`) waits `--warmup` seconds (default 20), runs the
metrics if `--metrics` is given and then holds until `--duration` seconds
have elapsed since the start of the warmup. Without `--duration` it exits
when the metrics are done. `--telemetry` (position plot) applies to the CLI
only.

`--coppelia` is `gui` (default), `headless` or `off`. `--coppelia-dir`
defaults to `$COPPELIASIM_ROOT`, otherwise `CoppeliaSim_Edu` next to the
script. The scene is `simulation.ttt` unless `--scene` is given; it is never
modified or saved. All flags are validated before anything starts.

Teardown always runs in reverse order: position sync thread, CoppeliaSim
(simulation stop, then the whole process group is terminated), Mininet-WiFi.
SIGTERM and Ctrl-C are handled.

## Drones

`--drones N` (1 to 32, default 3) creates stations `dr1` ... `drN`, with MAC
`00:00:00:00:00:NN` (N in hex) and address `10.0.0.N/8`. The default
`--formation scene` keeps the three drones of `simulation.ttt`; for N above 3
choose another formation.

With CoppeliaSim, `coppelia.ensure_drones(sim, n)` makes the scene hold
exactly `n` quadcopters before the simulation starts and returns their
handles: the scene drones first (scene order), then the copies in creation
order. Index `i` is `dr{i + 1}`.

- `n <= 3`: the unused scene drones are removed with `sim.removeModel`.
- `n > 3`: `sim.copyPasteObjects` pastes whole drone models, alternating
  between drones 2 and 3 (drone 1 starts the legacy remote API in its script
  and must not be copied). Each copy carries its own controller script, which
  resolves its target and base by name inside its own model, so every copy
  follows its own target.

Simulation load grows with the drone count (each drone has propeller scripts
and two cameras). Measured with software rendering (llvmpipe), the
simulation runs at about real time up to 5 drones headless, at 0.8x with 8
and at 0.7x with 10; the GUI is slower (about 0.3x with 5).

## Formations and trajectories

A formation is the initial layout, a trajectory moves it over time. All
formations are deterministic (except `random`, which depends on `--seed`),
lie in the `z = 0` plane and are centred on `(50, 50, 0)`, the middle of the
CoppeliaSim floor. `--spacing` (default 20) is the distance between
neighbours:

| `--formation` | Layout |
| --- | --- |
| `scene` | the positions of `simulation.ttt` (up to 3 drones) |
| `line` | along x |
| `grid` | `ceil(sqrt(n))` columns, filled row by row |
| `circle` | evenly on a circle whose neighbour chord is the spacing |
| `star` | `dr1` in the centre, the others on a circle of radius = spacing |
| `random` | uniform in a square of side `spacing * ceil(sqrt(n))`, at least `spacing / 2` apart; fails with an error when no free spot is found |

Trajectories are functions of `t`, the seconds since the position sync thread
started, and return the formation position at `t = 0`:

| `--trajectory` | Motion |
| --- | --- |
| `scene` | the built-in diamond pattern of `simulation.ttt`; without CoppeliaSim the drones stay static |
| `static` | none |
| `sweep` | the formation breathes about the centre in xy, scale `1 ... 3` (`SWEEP_MAX_SCALE`) and back every 60 s (`SWEEP_PERIOD`) |
| `orbit` | rigid rotation about the centre in xy, one turn per 120 s (`ORBIT_PERIOD`) |

With `--spacing 20` and the default `--exp 4.5` (range about 46 m) `sweep`
makes neighbours leave each other's range and come back, so pings fail while
the formation is expanded and succeed again when it contracts. The sweep
extends the formation up to three times: keep it inside the scene floor.

### Coordinates and motion

The scene and Mininet-WiFi coordinates are related per axis by
`net = 50 - 20 * scene` (same as `setNodePosition.py`);
`coppelia.scene_to_net` and `coppelia.net_to_scene` implement it. Every
~0.1 s the position sync thread

1. computes the targets with `topology.trajectory` and writes them to the
   drone targets in CoppeliaSim (skipped for `--trajectory scene`),
2. reads the drone positions, calls `setPosition` on the stations and appends
   them to `positions.csv`.

Without CoppeliaSim the trajectory is applied to the stations directly. With
it, the drones follow their targets with a lag of about 0.3 s (about 0.3 m).

For any trajectory other than `scene` the diamond pattern is removed from the
controller scripts in memory before the simulation starts, so Python drives
the targets. For any formation other than `scene` the drone models are moved
to the formation positions before the simulation starts. Real-time mode is
enabled so simulated time follows wall-clock time. In a CoppeliaSim run with
`--formation scene` the stations are moved to the positions of the scene
drones as soon as the sync starts; they differ from the `uav.py` coordinates
used without CoppeliaSim.

## Propagation models

`--model` selects the wmediumd propagation model; parameters a model does not
use are ignored, and unset ones keep the Mininet-WiFi defaults:

| `--model` | Parameters used | wmediumd `model_name` |
| --- | --- | --- |
| `friis` | none | `free_space` |
| `logDistance` (default) | `--exp` | `log_distance` |
| `twoRayGround` | none | `two_ray_ground` |
| `logNormalShadowing` | `--exp` | `log_normal_shadowing` |
| `nakagami` | `--exp`, `--nakagami-m` | `nakagami` |
| `ITU` | none | `itu` |

wmediumd stores `--exp` with one decimal and `--nakagami-m` with two (and
requires it to be at least 0.5), so other values are rejected instead of
being rounded silently. `--variance` is rejected with `logNormalShadowing`:
in wmediumd interference mode Mininet-WiFi never sends the shadowing sample
to wmediumd, so it would have no effect.

## Link technologies

`--tech` selects the wireless technology on `--channel` (default 5);
`--routing` defaults to `batman_adv` for `adhoc` and `none` otherwise.

| `--tech` | Mode | Address | Interface |
| --- | --- | --- | --- |
| `adhoc` (default) | IBSS, optional batman_adv | `192.168.123.N` on `bat0` with batman_adv, else `10.0.0.N` | `drX-wlan0` |
| `mesh` | IEEE 802.11s, HWMP path selection | `10.0.0.N` | `drX-mp0` |
| `infra` | managed stations on one access point | `10.0.0.N` | `drX-wlan0` |

`mesh` and `infra` need `--routing none`. `links.endpoint_ip(node, cfg)`
returns the address to ping for every technology.

`infra` runs without a controller: the access point `ap1` (SSID `infraNet`,
mode `g`, same channel) uses `failMode='standalone'` and is started with
`ap.start([])`. It sits at the centroid (x, y; z = 0) of the initial
formation. Its range is the distance to the farthest drone plus 5 m, rounded
up, set with the `range` parameter (Mininet-WiFi turns it into a tx power).
The coverage is computed for the initial formation only: a trajectory that
leaves it can leave the access point's coverage. The access point is the only
node that must exist before `net.configureNodes()` and start after
`net.build()`, so the runner calls `links.add_access_points` and
`links.start` at those points; both do nothing for the other technologies.
After `build()` the stations are disconnected and associated again, since
`build()` tried to connect them before hostapd was up.

### Warmup

`--warmup` is a fixed wait. Time until the first and the last drone can ping
each other, measured from the end of `net.build()` (one run each,
`--formation line --spacing 20`, `ping -O -D -i 0.5` started right after the
network came up):

| Technology | Drones | First successful ping |
| --- | --- | --- |
| adhoc + batman_adv | 5 (2 hops) | about 18 s |
| adhoc, no routing | 3 | first ping |
| mesh | 5 (2 hops) | first ping |
| infra | 3 and 5 | first ping |

Only batman_adv needs a long warmup: the default 20 s leaves little margin
with 5 drones, and the matrix uses 25 s. The other technologies are ready as
soon as `net.build()` returns, since the stations are already associated
(infra) or the path is resolved on the first frame (mesh).

## Metrics and output

With `--metrics` (batch mode) the runner calls `metrics.run_metrics` once,
after the warmup. For each pair in `--pairs` (default `dr1:drN`, none with a
single drone) it runs, one after the other:

1. `ping -D -i <ping-interval> -c <ping-count> -W 1` (defaults 0.5 s, 20)
2. `iperf3` TCP for `--iperf-time` seconds (default 10)
3. `iperf3` UDP at `--udp-bandwidth` (default 1M) for `--iperf-time` seconds

The iperf3 server runs on the destination with `-s -1` and the client is
killed if it outlives `--iperf-time` plus a margin, so an unreachable
destination ends with recorded errors, not a hang. The time per pair is about
`ping_count * ping_interval + 2 * iperf_time` plus a few seconds of overhead.
While a pair is measured, the distance between its two drones is sampled at
about 1 Hz, and the route is recorded before and after:

| Routing | Snapshot | Hops |
| --- | --- | --- |
| `batman_adv` | `batctl o`, `batctl tr <MAC of the destination interface>` | last hop of the traceroute |
| `--tech mesh` | `iw dev <intf> mpath dump` on the source | `HOP_COUNT` of the destination MAC (`null` if no path yet or iw has no such column) |
| otherwise | none | `null` |

Each run writes to `--output-dir` (default `./uav-results/<timestamp>`, which
must be new or empty; owned by the invoking user when started through sudo):

| File | Content |
| --- | --- |
| `config.json` | resolved configuration |
| `positions.csv` | `time,node,x,y,z` sampled every ~0.1 s |
| `coppeliasim.log` | CoppeliaSim output (not with `--coppelia off`) |
| `summary.json` | results per pair, see below (with `--metrics`) |
| `ping.csv` | `pair,epoch,seq,rtt_ms`, one row per reply (`pair` is `drX:drY`) |
| `distance.csv` | `pair,epoch,distance_m`, sampled at about 1 Hz |
| `ping_<src>_<dst>.txt` | raw ping output |
| `iperf_tcp_<src>_<dst>.json`, `iperf_udp_<src>_<dst>.json` | raw iperf3 JSON, or `{"error": ...}` |
| `routing_<src>_<dst>.txt` | raw route snapshots, `before` and `after` |
| `rtt_<src>_<dst>.png` | RTT over time, lost pings in red, distance on the right axis |

`ping.csv` and `distance.csv` use epoch seconds, so they line up. The
`summary.json` keys are stable; a value that could not be measured is `null`:

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

`rtt_ms` is `null` without replies. Throughputs are measured at the receiver
(UDP: the rate that arrived). `duration_s` is the wall-clock time of the
measurements of the pair. `errors` lists `no ping reply` and one `tcp: ...`
or `udp: ...` entry per failed iperf3 test.

## Experiment matrix

`run_matrix.py` runs `uav_experiment.py` once per configuration and seed,
one after the other, and aggregates the results. It needs root like the
experiment itself; `--dry-run`, `--aggregate-only` and `--help` do not, and
do not need Mininet-WiFi either. Every configuration is checked before the
campaign starts, which includes finding CoppeliaSim unless `--coppelia off`.

```
# print the 51 commands of the default matrix (17 configurations x 3 seeds)
python3 examples/uav/run_matrix.py --dry-run

# the default campaign, headless CoppeliaSim
sudo -E python3 examples/uav/run_matrix.py --output-dir uav-matrix/sweep

# quick check without CoppeliaSim and shorter measurements
sudo -E python3 examples/uav/run_matrix.py --coppelia off \
    --only baseline tech-mesh --seeds 1 --ping-count 10 --iperf-time 3

# continue an interrupted campaign, rebuild the tables and plots
sudo -E python3 examples/uav/run_matrix.py --output-dir uav-matrix/sweep \
    --resume
python3 examples/uav/run_matrix.py --aggregate-only uav-matrix/sweep
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

## Code layout

| Module | Role |
| --- | --- |
| `config.py` | `ExperimentConfig`, all flags, validation |
| `runner.py` | build, start CoppeliaSim, position sync thread, CLI or batch, teardown |
| `coppelia.py` | process launch/teardown, ZMQ helpers, scene handles |
| `drones.py` | names, MAC/IP, initial positions |
| `topology.py` | formations, trajectories, propagation model |
| `links.py` | wireless link setup and endpoint addresses |
| `metrics.py` | traffic measurements |
| `output.py` | output directory and JSON helpers |
| `matrix.py` | the experiment matrix (imports no Mininet-WiFi code) |

Unit tests need neither root, Mininet-WiFi nor CoppeliaSim:

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
