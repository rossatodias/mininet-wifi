"""Experiment matrix: expansion, sequential execution and aggregation."""

import argparse
import contextlib
import csv
import dataclasses
import io
import itertools
import json
import os
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict

from experiment import config, output

SCRIPT = os.path.join(config.UAV_DIR, 'uav_experiment.py')
DEFAULT_SEEDS = (1, 2, 3)
COPPELIA_MODES = ('headless', 'off')

STARTUP_MARGIN = 120.0
PAIR_OVERHEAD = 15.0
TERM_GRACE = 30.0
CLEAN_TIMEOUT = 120.0

BASELINE_FIXED = {
    'spacing': 20, 'warmup': 25, 'ping-count': 120, 'ping-interval': 0.5,
    'iperf-time': 10, 'udp-bandwidth': '1M', 'metrics': True,
}

FACTORS = {
    'drones': {str(n): {'drones': n} for n in (3, 5, 8, 10)},
    'tech': {t: {'tech': t} for t in ('adhoc', 'infra', 'mesh')},
    'model': {
        'logDistance-exp3': {'model': 'logDistance', 'exp': 3},
        'friis': {'model': 'friis'},
        'logDistance-exp4.5': {'model': 'logDistance', 'exp': 4.5},
        'twoRayGround': {'model': 'twoRayGround'},
        'logNormalShadowing': {'model': 'logNormalShadowing', 'exp': 3},
        'nakagami': {'model': 'nakagami', 'exp': 3},
        'ITU': {'model': 'ITU'},
    },
    'formation': {f: {'formation': f}
                  for f in ('line', 'grid', 'circle', 'star')},
    'trajectory': {t: {'trajectory': t}
                   for t in ('sweep', 'static', 'orbit')},
}
BASELINE = {'drones': '5', 'tech': 'adhoc', 'model': 'logDistance-exp3',
            'formation': 'line', 'trajectory': 'sweep'}

OVERRIDE_FLAGS = ('warmup', 'ping-count', 'ping-interval', 'iperf-time',
                  'udp-bandwidth')

RUN_COLUMNS = ('drones', 'tech', 'routing', 'model', 'exp', 'formation',
               'trajectory')
METRICS = ('loss_percent', 'rtt_avg_ms', 'rtt_max_ms', 'tcp_mbps',
           'udp_mbps', 'jitter_ms', 'udp_lost_percent', 'hops_after',
           'distance_mean_m', 'errors', 'duration_s')
RESULT_COLUMNS = (('config', 'factor', 'value', 'seed') + RUN_COLUMNS +
                  METRICS + ('exit_code', 'timed_out', 'wall_s'))
AGGREGATE_COLUMNS = (('config', 'factor', 'value', 'runs', 'failed') +
                     tuple('%s_%s' % (m, s) for m in METRICS
                           for s in ('mean', 'std', 'n')))
TABLE_COLUMNS = (('loss_percent', 'loss %'), ('rtt_avg_ms', 'RTT avg ms'),
                 ('rtt_max_ms', 'RTT max ms'), ('tcp_mbps', 'TCP Mbps'),
                 ('udp_mbps', 'UDP Mbps'), ('jitter_ms', 'jitter ms'),
                 ('udp_lost_percent', 'UDP lost %'),
                 ('hops_after', 'hops'), ('distance_mean_m', 'distance m'))
PANELS = (('loss_percent', 'Ping loss (%)'), ('rtt_avg_ms', 'RTT avg (ms)'),
          ('tcp_mbps', 'TCP (Mbps)'), ('udp_mbps', 'UDP (Mbps)'),
          ('jitter_ms', 'UDP jitter (ms)'),
          ('udp_lost_percent', 'UDP lost (%)'))


@dataclasses.dataclass
class Experiment:
    name: str
    factor: str
    value: str
    levels: Dict[str, str]
    flags: dict


def make_experiment(levels, overrides=None):
    """Build the experiment whose factors take the given levels."""
    flags = {}
    for factor, options in FACTORS.items():
        flags.update(options[levels[factor]])
    flags.update(BASELINE_FIXED)
    flags.update(overrides or {})
    flags['pairs'] = 'dr1:dr%d' % flags['drones']
    changed = [(f, levels[f]) for f in FACTORS if levels[f] != BASELINE[f]]
    return Experiment(
        name='+'.join('%s-%s' % c for c in changed) or 'baseline',
        factor='+'.join(f for f, _ in changed) or 'baseline',
        value='+'.join(v for _, v in changed) or 'baseline',
        levels=dict(levels), flags=flags)


def single_factor(overrides=None):
    """The baseline, then each factor level changed on its own."""
    experiments = [make_experiment(BASELINE, overrides)]
    for factor, options in FACTORS.items():
        for label in options:
            if label != BASELINE[factor]:
                levels = dict(BASELINE, **{factor: label})
                experiments.append(make_experiment(levels, overrides))
    return experiments


def full_product(overrides=None):
    """Every combination of the factor levels."""
    return [make_experiment(dict(zip(FACTORS, combo)), overrides)
            for combo in itertools.product(*FACTORS.values())]


def select(experiments, only=None, exclude=None):
    """Filter by name; unknown names raise ValueError."""
    only, exclude = only or [], exclude or []
    names = {e.name for e in experiments}
    unknown = [n for n in only + exclude if n not in names]
    if unknown:
        raise ValueError('unknown configuration: %s' % ', '.join(unknown))
    return [e for e in experiments
            if (not only or e.name in only) and e.name not in exclude]


def format_value(value):
    return '%g' % value if isinstance(value, float) else str(value)


def experiment_args(exp, seed, out_dir, mode, coppelia_dir):
    """Command line flags of one run, without interpreter and script."""
    args = []
    for key, value in exp.flags.items():
        args.append('--' + key)
        if value is not True:
            args.append(format_value(value))
    return args + ['--seed', str(seed), '--no-cli', '--coppelia', mode,
                   '--coppelia-dir', coppelia_dir, '--output-dir', out_dir]


def build_argv(exp, seed, out_dir, mode, coppelia_dir):
    return [sys.executable, SCRIPT] + experiment_args(
        exp, seed, out_dir, mode, coppelia_dir)


def run_dir(campaign, name, seed):
    return os.path.join(campaign, name, 'seed%d' % seed)


def default_timeout(flags):
    """Warmup, the metrics of every pair and a startup margin."""
    pairs = flags['pairs'].count(',') + 1
    metrics = (flags['ping-count'] * flags['ping-interval'] +
               2 * flags['iperf-time'] + PAIR_OVERHEAD)
    return flags['warmup'] + pairs * metrics + STARTUP_MARGIN


def invalid_reason(args):
    """Return why the flags fail the configuration checks, else None."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            config.parse_args(args)
    except SystemExit:
        lines = err.getvalue().strip().splitlines()
        return lines[-1] if lines else 'invalid configuration'
    return None


def validate(experiments, seed, mode, coppelia_dir, probe_dir):
    """Split the experiments into (valid, [(experiment, reason)])."""
    valid, skipped = [], []
    for exp in experiments:
        reason = invalid_reason(experiment_args(
            exp, seed, probe_dir, mode, coppelia_dir))
        if reason is None:
            valid.append(exp)
        else:
            skipped.append((exp, reason))
    return valid, skipped


def kill_group(pgid, sig=signal.SIGKILL):
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, sig)


def stop_group(proc):
    """Ask the process group to exit, then kill it; return the exit code."""
    kill_group(proc.pid, signal.SIGTERM)
    try:
        return proc.wait(timeout=TERM_GRACE)
    except subprocess.TimeoutExpired:
        kill_group(proc.pid)
        return proc.wait()


def execute(argv, log_path, timeout):
    """Run one experiment in its own session; return (exit code, timed out).

    A timed out run gets SIGTERM so the experiment can tear down, then
    SIGKILL. No process of the group survives this call.
    """
    with open(log_path, 'w') as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            return proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            return stop_group(proc), True
        except BaseException:
            stop_group(proc)
            raise
        finally:
            kill_group(proc.pid)


def clean_network():
    try:
        subprocess.run(['mn', '-c'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=CLEAN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as err:
        print('warning: mn -c failed: %s' % err, file=sys.stderr)


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def read_json(path):
    """Parsed JSON file, or None when it is missing or unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def has_summary(path):
    return os.path.isfile(os.path.join(path, 'summary.json'))


def run_once(exp, seed, campaign, mode, coppelia_dir, timeout):
    """Run one experiment and return its outcome record."""
    out = run_dir(campaign, exp.name, seed)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    argv = build_argv(exp, seed, out, mode, coppelia_dir)
    start = time.monotonic()
    code, timed_out = execute(argv, out + '.log',
                              timeout or default_timeout(exp.flags))
    return dict(blank_outcome(exp, seed, campaign), exit_code=code,
                wall_s=round(time.monotonic() - start, 1),
                timed_out=timed_out)


def succeeded(outcome):
    return outcome['exit_code'] == 0 and not outcome['timed_out']


def blank_outcome(exp, seed, campaign):
    out = run_dir(campaign, exp.name, seed)
    return {'config': exp.name, 'seed': seed,
            'output_dir': os.path.relpath(out, campaign), 'exit_code': None,
            'wall_s': None, 'timed_out': False, 'skipped': False}


def run_all(experiments, seeds, campaign, mode, coppelia_dir, timeout=None,
            resume=False):
    """Run every (seed, experiment) in turn and record campaign.json.

    A failed run never stops the campaign. Returns the outcomes.
    """
    path = os.path.join(campaign, 'campaign.json')
    previous = {(o['config'], o['seed']): o
                for o in (read_json(path) or {}).get('runs', [])}
    record = {'started': now(), 'finished': None, 'coppelia': mode,
              'seeds': list(seeds), 'factors': {
                  f: list(o) for f, o in FACTORS.items()},
              'baseline': BASELINE,
              'matrix': [dataclasses.asdict(e) for e in experiments],
              'runs': []}
    jobs = [(e, s) for s in seeds for e in experiments]
    for index, (exp, seed) in enumerate(jobs, 1):
        label = '[%d/%d] %s seed%d' % (index, len(jobs), exp.name, seed)
        if resume and has_summary(run_dir(campaign, exp.name, seed)):
            outcome = dict(previous.get((exp.name, seed)) or blank_outcome(
                exp, seed, campaign), skipped=True)
            print('%s: skipped, summary.json exists' % label, flush=True)
        else:
            print('%s: running' % label, flush=True)
            outcome = run_once(exp, seed, campaign, mode, coppelia_dir,
                               timeout)
            print('%s: %s in %.1f s (exit %s)' % (
                label, 'ok' if succeeded(outcome) else
                'TIMED OUT' if outcome['timed_out'] else 'FAILED',
                outcome['wall_s'], outcome['exit_code']), flush=True)
            if not succeeded(outcome):
                clean_network()
        record['runs'].append(outcome)
        output.write_json(path, record)
    record['finished'] = now()
    output.write_json(path, record)
    return record['runs']


def dig(obj, *keys):
    """Nested dictionary lookup that yields None for anything missing."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def pair_metrics(summary):
    """The measurements of the first pair of a summary.json."""
    pair = (dig(summary, 'pairs') or [None])[0]
    errors = dig(pair, 'errors')
    return {
        'loss_percent': dig(pair, 'ping', 'loss_percent'),
        'rtt_avg_ms': dig(pair, 'ping', 'rtt_ms', 'avg'),
        'rtt_max_ms': dig(pair, 'ping', 'rtt_ms', 'max'),
        'tcp_mbps': dig(pair, 'tcp', 'throughput_mbps'),
        'udp_mbps': dig(pair, 'udp', 'throughput_mbps'),
        'jitter_ms': dig(pair, 'udp', 'jitter_ms'),
        'udp_lost_percent': dig(pair, 'udp', 'lost_percent'),
        'hops_after': dig(pair, 'hops', 'after'),
        'distance_mean_m': dig(pair, 'distance_m', 'mean'),
        'errors': None if errors is None else len(errors),
        'duration_s': dig(pair, 'duration_s'),
    }


def result_rows(campaign, record):
    """One row per run, from campaign.json and the run directories."""
    matrix = {e['name']: e for e in record['matrix']}
    rows = []
    for outcome in record['runs']:
        exp = matrix[outcome['config']]
        run = os.path.join(campaign, outcome['output_dir'])
        cfg = read_json(os.path.join(run, 'config.json')) or {
            k.replace('-', '_'): v for k, v in exp['flags'].items()}
        row = {'config': exp['name'], 'factor': exp['factor'],
               'value': exp['value'], 'seed': outcome['seed']}
        row.update({k: cfg.get(k) for k in RUN_COLUMNS})
        row.update(pair_metrics(read_json(os.path.join(run, 'summary.json'))))
        row.update({k: outcome[k] for k in ('exit_code', 'timed_out',
                                            'wall_s')})
        rows.append(row)
    return rows


def describe(values):
    """Return (mean, sample std, n) of the non-null values."""
    data = [v for v in values if v is not None]
    if not data:
        return None, None, 0
    std = statistics.stdev(data) if len(data) > 1 else None
    return statistics.fmean(data), std, len(data)


def aggregate_rows(record, rows):
    """One row per configuration with mean, std and n of every metric."""
    out = []
    for exp in record['matrix']:
        runs = [r for r in rows if r['config'] == exp['name']]
        row = {'config': exp['name'], 'factor': exp['factor'],
               'value': exp['value'], 'runs': len(runs),
               'failed': sum(1 for r in runs if r['timed_out'] or
                             r['exit_code'] not in (0, None))}
        for metric in METRICS:
            mean, std, n = describe([r[metric] for r in runs])
            row[metric + '_mean'] = mean
            row[metric + '_std'] = std
            row[metric + '_n'] = n
        out.append(row)
    return out


def write_csv(path, columns, rows):
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: round(v, 4) if isinstance(v, float) else v
                             for k, v in row.items()})


def table_cell(row, metric):
    mean, std = row[metric + '_mean'], row[metric + '_std']
    if mean is None:
        return '-'
    text = '%.2f' % mean
    if std is not None:
        text += ' ± %.2f' % std
    if row[metric + '_n'] < row['runs']:
        text += ' (n=%d)' % row[metric + '_n']
    return text


def write_table(path, record, aggregated):
    """Markdown table of the aggregated results."""
    head = ['config', 'runs', 'failed'] + [t for _, t in TABLE_COLUMNS]
    lines = ['# Experiment matrix results', '',
             'Seeds: %s. Cells are mean ± sample standard deviation over '
             'the runs with a value.' % ', '.join(map(str, record['seeds'])),
             '', '| ' + ' | '.join(head) + ' |',
             '|' + '---|' * len(head)]
    for row in aggregated:
        cells = [row['config'], str(row['runs']), str(row['failed'])]
        cells += [table_cell(row, m) for m, _ in TABLE_COLUMNS]
        lines.append('| ' + ' | '.join(cells) + ' |')
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def factor_bars(record, aggregated, factor):
    """(label, row, is baseline) for the levels of `factor`, in order."""
    by_name = {r['config']: r for r in aggregated}
    bars = []
    for label in record['factors'][factor]:
        base = label == record['baseline'][factor]
        row = by_name.get('baseline' if base else '%s-%s' % (factor, label))
        if row is not None:
            bars.append((label, row, base))
    return bars


def plot_factor(path, factor, bars):
    """Bars with mean and standard deviation of the main metrics."""
    from matplotlib import style
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    colors = ['tab:gray' if base else 'tab:blue' for _, _, base in bars]
    labels = ['%s\n(n=%d)' % (label, row['runs']) for label, row, _ in bars]
    with style.context('default'):
        fig = Figure(figsize=(13, 7))
        FigureCanvasAgg(fig)
        for ax, (metric, title) in zip(fig.subplots(2, 3).flat, PANELS):
            means = [r[metric + '_mean'] for _, r, _ in bars]
            stds = [r[metric + '_std'] or 0 for _, r, _ in bars]
            ax.bar(range(len(bars)), [m or 0 for m in means], yerr=stds,
                   capsize=3, color=colors)
            for i in (i for i, m in enumerate(means) if m is None):
                ax.annotate('n/a', (i, 0), ha='center', va='bottom',
                            fontsize=8)
            ax.set_xticks(range(len(bars)))
            ax.set_xticklabels(labels, rotation=30, ha='right', fontsize=8)
            ax.set_ylim(bottom=0)
            ax.set_title(title, fontsize=10)
        fig.suptitle('%s (gray: baseline, bars: mean ± std)' % factor)
        fig.tight_layout()
        fig.savefig(path, dpi=100)


def aggregate(campaign):
    """Write results.csv, aggregated.csv, summary.md and the factor plots.

    Returns the paths written.
    """
    record = read_json(os.path.join(campaign, 'campaign.json'))
    if record is None:
        raise ValueError('no readable campaign.json in %s' % campaign)
    rows = result_rows(campaign, record)
    aggregated = aggregate_rows(record, rows)
    paths = [os.path.join(campaign, n) for n in
             ('results.csv', 'aggregated.csv', 'summary.md')]
    write_csv(paths[0], RESULT_COLUMNS, rows)
    write_csv(paths[1], AGGREGATE_COLUMNS, aggregated)
    write_table(paths[2], record, aggregated)
    for factor in record['factors']:
        bars = factor_bars(record, aggregated, factor)
        if len(bars) > 1:
            paths.append(os.path.join(campaign, 'factor-%s.png' % factor))
            plot_factor(paths[-1], factor, bars)
    return paths


def positive_float(text):
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return value


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return value


def nonnegative_float(text):
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError('must not be negative')
    return value


def build_parser():
    p = argparse.ArgumentParser(
        prog='run_matrix.py',
        description='Run a matrix of UAV experiments, one factor at a time '
                    'around a baseline, and aggregate the results.')
    g = p.add_argument_group('matrix')
    g.add_argument('--full', action='store_true',
                   help='cartesian product of all factor levels instead of '
                        'one factor at a time')
    g.add_argument('--seeds', type=int, nargs='+', default=DEFAULT_SEEDS,
                   metavar='S', help='repetitions, passed as --seed '
                                     '(default 1 2 3)')
    g.add_argument('--only', nargs='+', metavar='NAME',
                   help='run only these configurations')
    g.add_argument('--exclude', nargs='+', metavar='NAME',
                   help='skip these configurations')
    g = p.add_argument_group('baseline overrides')
    g.add_argument('--warmup', type=nonnegative_float, metavar='S',
                   help='seconds before the metrics (baseline 25)')
    g.add_argument('--ping-count', type=positive_int, metavar='N',
                   help='pings per pair (baseline 120)')
    g.add_argument('--ping-interval', type=positive_float, metavar='S',
                   help='seconds between pings (baseline 0.5)')
    g.add_argument('--iperf-time', type=positive_float, metavar='S',
                   help='iperf test length (baseline 10)')
    g.add_argument('--udp-bandwidth', metavar='B',
                   help='iperf UDP bandwidth (baseline 1M)')
    g = p.add_argument_group('execution')
    g.add_argument('--coppelia', choices=COPPELIA_MODES, default='headless',
                   help='CoppeliaSim mode of every run (default headless)')
    g.add_argument('--coppelia-dir', metavar='DIR',
                   help='CoppeliaSim directory (default $COPPELIASIM_ROOT, '
                        'else CoppeliaSim_Edu next to uav_experiment.py)')
    g.add_argument('--run-timeout', type=positive_float, metavar='S',
                   help='per-run time limit (default: warmup + metrics + '
                        '%d s)' % STARTUP_MARGIN)
    g.add_argument('--output-dir', metavar='DIR',
                   help='campaign directory (default '
                        './uav-matrix/<timestamp>)')
    g.add_argument('--resume', action='store_true',
                   help='skip runs whose summary.json already exists')
    g.add_argument('--dry-run', action='store_true',
                   help='print the commands without running them')
    g.add_argument('--aggregate-only', metavar='CAMPAIGN',
                   help='rebuild the aggregated results of a campaign')
    return p


def overrides_from(args):
    return {key: getattr(args, key.replace('-', '_'))
            for key in OVERRIDE_FLAGS
            if getattr(args, key.replace('-', '_')) is not None}


def hand_over(paths):
    for path in paths:
        output.hand_over_output(path)


def plan(args, parser):
    """Expand, select and validate; return (experiments, skipped)."""
    experiments = (full_product if args.full else single_factor)(
        overrides_from(args))
    try:
        experiments = select(experiments, args.only, args.exclude)
    except ValueError as err:
        parser.error(str(err))
    coppelia_dir = os.path.abspath(
        args.coppelia_dir or os.environ.get('COPPELIASIM_ROOT') or
        os.path.join(config.UAV_DIR, 'CoppeliaSim_Edu'))
    args.coppelia_dir = coppelia_dir
    return validate(experiments, args.seeds[0], args.coppelia, coppelia_dir,
                    os.path.join(args.output_dir, '.probe'))


def print_dry_run(args, experiments):
    jobs = [(e, s) for s in args.seeds for e in experiments]
    for index, (exp, seed) in enumerate(jobs, 1):
        out = run_dir(args.output_dir, exp.name, seed)
        note = ' (skipped, summary.json exists)' if (
            args.resume and has_summary(out)) else ''
        print('run %d/%d %s seed%d timeout %.0f s%s' % (
            index, len(jobs), exp.name, seed,
            args.run_timeout or default_timeout(exp.flags), note))
        print('  ' + shlex.join(build_argv(
            exp, seed, out, args.coppelia, args.coppelia_dir)))
    print('%d configurations x %d seeds = %d runs' % (
        len(experiments), len(args.seeds), len(jobs)))


def _on_sigterm(signum, frame):
    sys.exit(128 + signum)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.aggregate_only:
        try:
            hand_over(aggregate(args.aggregate_only))
        except ValueError as err:
            parser.error(str(err))
        return 0
    if len(set(args.seeds)) != len(args.seeds):
        parser.error('--seeds must not repeat a value')
    args.output_dir = os.path.abspath(args.output_dir or os.path.join(
        'uav-matrix', time.strftime('%Y%m%d-%H%M%S')))
    experiments, skipped = plan(args, parser)
    if args.full:
        total = len(experiments) + len(skipped)
        print('warning: --full builds %d configurations; with %d seeds that '
              'is up to %d runs' % (total, len(args.seeds),
                                    total * len(args.seeds)),
              file=sys.stderr)
    for exp, reason in skipped:
        print('skipping %s: %s' % (exp.name, reason), file=sys.stderr)
    if not experiments:
        parser.error('no valid configuration to run')
    if args.dry_run:
        print_dry_run(args, experiments)
        return 0
    if os.geteuid() != 0:
        sys.exit('run_matrix.py needs root: run it with sudo -E')
    if (os.path.exists(args.output_dir) and os.listdir(args.output_dir)
            and not args.resume):
        parser.error('--output-dir %s is not empty; use --resume to '
                     'continue it' % args.output_dir)
    signal.signal(signal.SIGTERM, _on_sigterm)
    os.environ['MPLBACKEND'] = 'Agg'
    created = output.make_output_dir(args.output_dir)
    try:
        outcomes = run_all(experiments, args.seeds, args.output_dir,
                           args.coppelia, args.coppelia_dir,
                           args.run_timeout, args.resume)
        aggregate(args.output_dir)
    finally:
        output.hand_over_output(created)
    failed = [o for o in outcomes if not o['skipped'] and not succeeded(o)]
    print('%d runs, %d failed; results in %s' % (
        len(outcomes), len(failed), args.output_dir))
    return 1 if failed else 0
