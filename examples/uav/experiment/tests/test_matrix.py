import contextlib
import csv
import io
import json
import os
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

from experiment import matrix

EXPECTED_NAMES = [
    'baseline', 'drones-3', 'drones-8', 'drones-10', 'tech-infra',
    'tech-mesh', 'model-friis', 'model-logDistance-exp4.5',
    'model-twoRayGround', 'model-logNormalShadowing', 'model-nakagami',
    'model-ITU', 'formation-grid', 'formation-circle', 'formation-star',
    'trajectory-static', 'trajectory-orbit']


def by_name(experiments):
    return {e.name: e for e in experiments}


def summary(loss=0.0, avg=2.0, tcp=5.0, udp=1.0, errors=()):
    return {'tech': 'adhoc', 'routing': 'batman_adv', 'pairs': [{
        'src': 'dr1', 'dst': 'dr5', 'dst_ip': '192.168.123.5',
        'ping': {'sent': 120, 'received': 100, 'loss_percent': loss,
                 'rtt_ms': None if avg is None else
                 {'min': 1.0, 'avg': avg, 'max': avg * 3, 'mdev': 0.5}},
        'tcp': {'throughput_mbps': tcp, 'retransmits': 3, 'error': None},
        'udp': {'throughput_mbps': udp, 'jitter_ms': 0.4,
                'lost_percent': 1.5, 'error': None},
        'distance_m': {'min': 80.0, 'mean': 120.0, 'max': 160.0},
        'hops': {'before': 1, 'after': 2},
        'errors': list(errors), 'duration_s': 110.0}]}


def write_run(campaign, config, seed, data=None, cfg=None):
    path = os.path.join(campaign, config, 'seed%d' % seed)
    os.makedirs(path)
    if cfg is not None:
        with open(os.path.join(path, 'config.json'), 'w') as f:
            json.dump(cfg, f)
    if data is not None:
        with open(os.path.join(path, 'summary.json'), 'w') as f:
            json.dump(data, f)
    return path


def outcome(config, seed, code=0, timed_out=False):
    return {'config': config, 'seed': seed,
            'output_dir': '%s/seed%d' % (config, seed), 'exit_code': code,
            'wall_s': 10.0, 'timed_out': timed_out, 'skipped': False}


def campaign_record(names, seeds):
    experiments = [e for e in matrix.single_factor() if e.name in names]
    return {'seeds': list(seeds),
            'factors': {f: list(o) for f, o in matrix.FACTORS.items()},
            'baseline': matrix.BASELINE,
            'matrix': [matrix.dataclasses.asdict(e) for e in experiments],
            'runs': []}


class ExpansionTest(unittest.TestCase):
    def test_single_factor_has_17_named_configurations(self):
        names = [e.name for e in matrix.single_factor()]
        self.assertEqual(names, EXPECTED_NAMES)
        self.assertEqual(len(set(names)), 17)

    def test_baseline_flags(self):
        flags = matrix.single_factor()[0].flags
        expected = {
            'drones': 5, 'tech': 'adhoc', 'model': 'logDistance', 'exp': 3,
            'formation': 'line', 'trajectory': 'sweep', 'spacing': 20,
            'warmup': 25, 'ping-count': 120, 'ping-interval': 0.5,
            'iperf-time': 10, 'udp-bandwidth': '1M', 'metrics': True,
            'pairs': 'dr1:dr5'}
        self.assertEqual(flags, expected)

    def test_each_experiment_changes_one_factor(self):
        experiments = matrix.single_factor()
        base = experiments[0].levels
        for exp in experiments[1:]:
            changed = [f for f in base if exp.levels[f] != base[f]]
            self.assertEqual(changed, [exp.factor])
            self.assertEqual(exp.name, '%s-%s' % (exp.factor, exp.value))

    def test_pairs_follow_the_drone_count(self):
        experiments = by_name(matrix.single_factor())
        self.assertEqual(experiments['drones-3'].flags['pairs'], 'dr1:dr3')
        self.assertEqual(experiments['drones-10'].flags['pairs'],
                         'dr1:dr10')

    def test_model_flags(self):
        experiments = by_name(matrix.single_factor())
        self.assertEqual(experiments['model-friis'].flags['model'], 'friis')
        self.assertNotIn('exp', experiments['model-friis'].flags)
        self.assertEqual(
            experiments['model-logDistance-exp4.5'].flags['exp'], 4.5)
        for name in ('model-logNormalShadowing', 'model-nakagami'):
            self.assertEqual(experiments[name].flags['exp'], 3)
        for exp in experiments.values():
            self.assertNotIn('variance', exp.flags)

    def test_tech_leaves_routing_to_the_default(self):
        for exp in matrix.single_factor():
            self.assertNotIn('routing', exp.flags)

    def test_overrides_apply_to_every_experiment(self):
        overrides = {'ping-count': 10, 'iperf-time': 3.0}
        for exp in matrix.single_factor(overrides):
            self.assertEqual(exp.flags['ping-count'], 10)
            self.assertEqual(exp.flags['iperf-time'], 3.0)

    def test_full_product_size_and_unique_names(self):
        experiments = matrix.full_product()
        self.assertEqual(len(experiments), 4 * 3 * 7 * 4 * 3)
        self.assertEqual(len({e.name for e in experiments}),
                         len(experiments))
        named = by_name(experiments)
        self.assertEqual(named['tech-mesh+trajectory-orbit'].factor,
                         'tech+trajectory')
        self.assertIn('baseline', named)

    def test_full_contains_every_single_factor_configuration(self):
        full = {e.name for e in matrix.full_product()}
        self.assertLessEqual({e.name for e in matrix.single_factor()}, full)

    def test_select_only_and_exclude(self):
        experiments = matrix.single_factor()
        only = matrix.select(experiments, only=['tech-mesh', 'baseline'])
        self.assertEqual([e.name for e in only], ['baseline', 'tech-mesh'])
        rest = matrix.select(experiments, exclude=['baseline'])
        self.assertEqual(len(rest), 16)

    def test_select_unknown_name(self):
        with self.assertRaises(ValueError) as ctx:
            matrix.select(matrix.single_factor(), only=['nope'])
        self.assertIn('nope', str(ctx.exception))


class ValidationTest(unittest.TestCase):
    def test_default_matrix_is_valid(self):
        valid, skipped = matrix.validate(
            matrix.single_factor(), 1, 'off', '/unused', '/nonexistent/p')
        self.assertEqual(len(valid), 17)
        self.assertEqual(skipped, [])

    def test_full_matrix_count_without_invalid_combinations(self):
        valid, skipped = matrix.validate(
            matrix.full_product(), 1, 'off', '/unused', '/nonexistent/p')
        self.assertEqual(len(valid) + len(skipped), 1008)
        self.assertEqual(skipped, [])

    def test_invalid_combinations_are_skipped_with_a_reason(self):
        factors = {f: dict(o) for f, o in matrix.FACTORS.items()}
        factors['drones']['40'] = {'drones': 40}
        factors['tech']['bad'] = {'tech': 'infra', 'routing': 'batman_adv'}
        with mock.patch.object(matrix, 'FACTORS', factors):
            experiments = matrix.full_product()
            valid, skipped = matrix.validate(
                experiments, 1, 'off', '/unused', '/nonexistent/p')
        self.assertEqual(len(experiments), 5 * 4 * 7 * 4 * 3)
        self.assertEqual(len(valid) + len(skipped), len(experiments))
        self.assertTrue(skipped)
        reasons = {r for _, r in skipped}
        self.assertTrue(any('--drones' in r for r in reasons))
        self.assertTrue(any('infra' in r for r in reasons))
        self.assertTrue(all('drones-40' not in e.name and
                            'tech-bad' not in e.name for e in valid))

    def test_invalid_reason_is_the_last_error_line(self):
        reason = matrix.invalid_reason(['--ping-count', '0'])
        self.assertIn('--ping-count must be positive', reason)
        self.assertIsNone(matrix.invalid_reason(['--coppelia', 'off']))


class CommandTest(unittest.TestCase):
    def test_build_argv(self):
        exp = by_name(matrix.single_factor())['tech-mesh']
        argv = matrix.build_argv(exp, 2, '/out/tech-mesh/seed2', 'off',
                                 '/cop')
        self.assertEqual(argv[:2], [matrix.sys.executable, matrix.SCRIPT])
        self.assertTrue(matrix.SCRIPT.endswith('uav_experiment.py'))
        args = argv[2:]
        self.assertEqual(args[args.index('--tech') + 1], 'mesh')
        self.assertEqual(args[args.index('--exp') + 1], '3')
        self.assertEqual(args[args.index('--ping-interval') + 1], '0.5')
        self.assertEqual(args[args.index('--pairs') + 1], 'dr1:dr5')
        self.assertEqual(args[args.index('--seed') + 1], '2')
        self.assertEqual(args[args.index('--coppelia') + 1], 'off')
        self.assertEqual(args[args.index('--coppelia-dir') + 1], '/cop')
        self.assertEqual(args[args.index('--output-dir') + 1],
                         '/out/tech-mesh/seed2')
        self.assertIn('--metrics', args)
        self.assertIn('--no-cli', args)
        self.assertNotIn('True', args)
        self.assertNotIn('--variance', args)
        self.assertNotIn('--routing', args)

    def test_float_overrides_are_formatted_compactly(self):
        exp = matrix.single_factor({'iperf-time': 3.0, 'warmup': 5.0})[0]
        args = matrix.experiment_args(exp, 1, '/o', 'off', '/c')
        self.assertEqual(args[args.index('--iperf-time') + 1], '3')
        self.assertEqual(args[args.index('--warmup') + 1], '5')

    def test_run_dir_naming(self):
        self.assertEqual(
            matrix.run_dir('/c', 'model-logDistance-exp4.5', 3),
            '/c/model-logDistance-exp4.5/seed3')

    def test_default_timeout(self):
        flags = matrix.single_factor()[0].flags
        expected = 25 + (120 * 0.5 + 2 * 10 + matrix.PAIR_OVERHEAD) + \
            matrix.STARTUP_MARGIN
        self.assertEqual(matrix.default_timeout(flags), expected)
        flags = dict(flags, pairs='dr1:dr5,dr2:dr4')
        self.assertGreater(matrix.default_timeout(flags), expected + 90)


class ExecuteTest(unittest.TestCase):
    def popen(self, wait_effects):
        proc = mock.Mock(pid=4242)
        proc.wait.side_effect = wait_effects
        return proc

    def run_execute(self, proc, timeout=5):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch('subprocess.Popen', return_value=proc) as popen, \
                    mock.patch('os.killpg') as killpg:
                result = matrix.execute(['x'], os.path.join(d, 'l.log'),
                                        timeout)
        return result, popen, killpg

    def test_success_runs_in_its_own_session(self):
        result, popen, killpg = self.run_execute(self.popen([0]))
        self.assertEqual(result, (0, False))
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.STDOUT)
        killpg.assert_called_once_with(4242, signal.SIGKILL)

    def test_failure_exit_code_is_kept(self):
        result, _, _ = self.run_execute(self.popen([3]))
        self.assertEqual(result, (3, False))

    def test_timeout_terminates_the_group(self):
        timeout = subprocess.TimeoutExpired('x', 5)
        proc = self.popen([timeout, -15])
        result, _, killpg = self.run_execute(proc)
        self.assertEqual(result, (-15, True))
        self.assertEqual([c.args for c in killpg.call_args_list],
                         [(4242, signal.SIGTERM), (4242, signal.SIGKILL)])

    def test_timeout_escalates_to_kill(self):
        timeout = subprocess.TimeoutExpired('x', 5)
        proc = self.popen([timeout, timeout, -9])
        result, _, killpg = self.run_execute(proc)
        self.assertEqual(result, (-9, True))
        self.assertEqual([c.args[1] for c in killpg.call_args_list],
                         [signal.SIGTERM, signal.SIGKILL, signal.SIGKILL])

    def test_gone_process_group_is_not_an_error(self):
        proc = self.popen([0])
        with tempfile.TemporaryDirectory() as d:
            with mock.patch('subprocess.Popen', return_value=proc), \
                    mock.patch('os.killpg', side_effect=ProcessLookupError):
                result = matrix.execute(['x'], os.path.join(d, 'l.log'), 5)
        self.assertEqual(result, (0, False))

    def test_interrupt_stops_the_group_and_propagates(self):
        proc = self.popen([KeyboardInterrupt(), -15])
        with tempfile.TemporaryDirectory() as d:
            with mock.patch('subprocess.Popen', return_value=proc), \
                    mock.patch('os.killpg') as killpg:
                with self.assertRaises(KeyboardInterrupt):
                    matrix.execute(['x'], os.path.join(d, 'l.log'), 5)
        self.assertEqual(killpg.call_args_list[0].args,
                         (4242, signal.SIGTERM))

    def test_clean_network_uses_an_argument_list(self):
        with mock.patch('subprocess.run') as run:
            matrix.clean_network()
        self.assertEqual(run.call_args.args[0], ['mn', '-c'])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_clean_network_survives_a_missing_command(self):
        with mock.patch('subprocess.run', side_effect=FileNotFoundError):
            with contextlib.redirect_stderr(io.StringIO()):
                matrix.clean_network()


class RunAllTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.campaign = self.tmp.name
        self.experiments = matrix.select(
            matrix.single_factor(), only=['baseline', 'tech-mesh'])

    def run_all(self, results, **kwargs):
        """Run with `execute` replaced by a queue of (code, timed_out)."""
        def fake(argv, log_path, timeout):
            self.timeouts.append(timeout)
            self.argvs.append(argv)
            return results.pop(0)

        self.timeouts, self.argvs = [], []
        with mock.patch.object(matrix, 'execute', side_effect=fake), \
                mock.patch.object(matrix, 'clean_network') as clean, \
                contextlib.redirect_stdout(io.StringIO()):
            outcomes = matrix.run_all(
                self.experiments, (1, 2), self.campaign, 'off', '/cop',
                **kwargs)
        return outcomes, clean

    def test_failures_do_not_abort_and_trigger_cleanup(self):
        results = [(0, False), (1, False), (-15, True), (0, False)]
        outcomes, clean = self.run_all(results)
        self.assertEqual(len(outcomes), 4)
        self.assertEqual(
            [(o['config'], o['seed']) for o in outcomes],
            [('baseline', 1), ('tech-mesh', 1), ('baseline', 2),
             ('tech-mesh', 2)])
        self.assertEqual([matrix.succeeded(o) for o in outcomes],
                         [True, False, False, True])
        self.assertTrue(outcomes[2]['timed_out'])
        self.assertEqual(clean.call_count, 2)

    def test_log_and_output_paths(self):
        outcomes, _ = self.run_all([(0, False)] * 4)
        self.assertEqual(outcomes[1]['output_dir'], 'tech-mesh/seed1')
        out = self.argvs[1][self.argvs[1].index('--output-dir') + 1]
        self.assertEqual(out, os.path.join(self.campaign, 'tech-mesh',
                                           'seed1'))

    def test_timeout_override(self):
        self.run_all([(0, False)] * 4, timeout=20)
        self.assertEqual(set(self.timeouts), {20})
        self.run_all([(0, False)] * 4)
        self.assertEqual(set(self.timeouts), {
            matrix.default_timeout(self.experiments[0].flags)})

    def test_campaign_json(self):
        self.run_all([(0, False), (1, False), (0, False), (0, False)])
        with open(os.path.join(self.campaign, 'campaign.json')) as f:
            record = json.load(f)
        self.assertEqual(record['seeds'], [1, 2])
        self.assertEqual(record['coppelia'], 'off')
        self.assertIsNotNone(record['started'])
        self.assertIsNotNone(record['finished'])
        self.assertEqual([m['name'] for m in record['matrix']],
                         ['baseline', 'tech-mesh'])
        self.assertEqual(record['runs'][1]['exit_code'], 1)
        self.assertEqual(set(record['runs'][0]),
                         {'config', 'seed', 'output_dir', 'exit_code',
                          'wall_s', 'timed_out', 'skipped'})

    def test_stale_output_of_a_failed_run_is_removed(self):
        stale = write_run(self.campaign, 'baseline', 1)
        open(os.path.join(stale, 'positions.csv'), 'w').close()
        self.run_all([(0, False)] * 4)
        self.assertFalse(os.path.exists(os.path.join(stale, 'positions.csv')))

    def test_resume_skips_runs_with_a_summary(self):
        write_run(self.campaign, 'baseline', 1, data=summary())
        write_run(self.campaign, 'tech-mesh', 1)
        outcomes, clean = self.run_all([(0, False)] * 3, resume=True)
        self.assertEqual([o['skipped'] for o in outcomes],
                         [True, False, False, False])
        self.assertEqual(len(self.argvs), 3)
        clean.assert_not_called()

    def test_resume_keeps_the_previous_outcome(self):
        write_run(self.campaign, 'baseline', 1, data=summary())
        previous = {'runs': [outcome('baseline', 1, code=0)]}
        previous['runs'][0]['wall_s'] = 77.0
        with open(os.path.join(self.campaign, 'campaign.json'), 'w') as f:
            json.dump(previous, f)
        outcomes, _ = self.run_all([(0, False)] * 3, resume=True)
        self.assertEqual(outcomes[0]['wall_s'], 77.0)
        self.assertTrue(outcomes[0]['skipped'])

    def test_without_resume_existing_summaries_are_rerun(self):
        write_run(self.campaign, 'baseline', 1, data=summary())
        outcomes, _ = self.run_all([(0, False)] * 4)
        self.assertFalse(any(o['skipped'] for o in outcomes))
        self.assertEqual(len(self.argvs), 4)


class ResultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.campaign = self.tmp.name
        self.cfg = {'drones': 5, 'tech': 'adhoc', 'routing': 'batman_adv',
                    'model': 'logDistance', 'exp': 3.0,
                    'formation': 'line', 'trajectory': 'sweep'}

    def record(self, names=('baseline',), seeds=(1,)):
        return campaign_record(names, seeds)

    def test_pair_metrics(self):
        m = matrix.pair_metrics(summary(loss=16.7, errors=['no ping reply']))
        self.assertEqual(m['loss_percent'], 16.7)
        self.assertEqual(m['rtt_avg_ms'], 2.0)
        self.assertEqual(m['rtt_max_ms'], 6.0)
        self.assertEqual(m['tcp_mbps'], 5.0)
        self.assertEqual(m['udp_mbps'], 1.0)
        self.assertEqual(m['jitter_ms'], 0.4)
        self.assertEqual(m['udp_lost_percent'], 1.5)
        self.assertEqual(m['hops_after'], 2)
        self.assertEqual(m['distance_mean_m'], 120.0)
        self.assertEqual(m['errors'], 1)
        self.assertEqual(m['duration_s'], 110.0)

    def test_pair_metrics_with_nulls_and_missing_summary(self):
        data = summary(avg=None)
        data['pairs'][0]['distance_m'] = None
        data['pairs'][0]['hops'] = {'before': None, 'after': None}
        m = matrix.pair_metrics(data)
        self.assertIsNone(m['rtt_avg_ms'])
        self.assertIsNone(m['distance_mean_m'])
        self.assertIsNone(m['hops_after'])
        self.assertEqual(m['errors'], 0)
        self.assertEqual(set(matrix.pair_metrics(None).values()), {None})
        self.assertEqual(set(matrix.pair_metrics({'pairs': []}).values()),
                         {None})

    def test_read_json_tolerates_missing_and_broken_files(self):
        path = os.path.join(self.campaign, 'bad.json')
        with open(path, 'w') as f:
            f.write('{')
        self.assertIsNone(matrix.read_json(path))
        self.assertIsNone(matrix.read_json(path + '.missing'))

    def test_result_rows_read_config_and_summary(self):
        write_run(self.campaign, 'baseline', 1, summary(), self.cfg)
        write_run(self.campaign, 'baseline', 2)
        record = self.record(seeds=(1, 2))
        record['runs'] = [outcome('baseline', 1),
                          outcome('baseline', 2, code=1)]
        rows = matrix.result_rows(self.campaign, record)
        self.assertEqual(rows[0]['routing'], 'batman_adv')
        self.assertEqual(rows[0]['loss_percent'], 0.0)
        self.assertEqual(rows[0]['exit_code'], 0)
        self.assertEqual(rows[0]['factor'], 'baseline')
        self.assertEqual(rows[1]['exit_code'], 1)
        self.assertIsNone(rows[1]['loss_percent'])
        self.assertEqual(rows[1]['drones'], 5)
        self.assertEqual(rows[1]['model'], 'logDistance')

    def test_describe(self):
        mean, std, n = matrix.describe([1.0, None, 3.0, 5.0])
        self.assertEqual((mean, n), (3.0, 3))
        self.assertAlmostEqual(std, 2.0)
        self.assertEqual(matrix.describe([4.0]), (4.0, None, 1))
        self.assertEqual(matrix.describe([None, None]), (None, None, 0))

    def test_aggregate_rows(self):
        write_run(self.campaign, 'baseline', 1, summary(loss=10, tcp=4.0),
                  self.cfg)
        write_run(self.campaign, 'baseline', 2, summary(loss=30, tcp=6.0),
                  self.cfg)
        write_run(self.campaign, 'baseline', 3,
                  summary(loss=50, avg=None, tcp=8.0), self.cfg)
        write_run(self.campaign, 'baseline', 4)
        record = self.record(seeds=(1, 2, 3, 4))
        record['runs'] = [outcome('baseline', s) for s in (1, 2, 3)] + [
            outcome('baseline', 4, code=None, timed_out=True)]
        rows = matrix.result_rows(self.campaign, record)
        agg = matrix.aggregate_rows(record, rows)[0]
        self.assertEqual(agg['runs'], 4)
        self.assertEqual(agg['failed'], 1)
        self.assertEqual(agg['loss_percent_n'], 3)
        self.assertAlmostEqual(agg['loss_percent_mean'], 30.0)
        self.assertAlmostEqual(agg['loss_percent_std'], 20.0)
        self.assertEqual(agg['rtt_avg_ms_n'], 2)
        self.assertAlmostEqual(agg['rtt_avg_ms_mean'], 2.0)
        self.assertAlmostEqual(agg['tcp_mbps_mean'], 6.0)
        self.assertAlmostEqual(agg['tcp_mbps_std'], 2.0)

    def test_table_cell(self):
        row = {'runs': 3, 'x_mean': 1.234, 'x_std': 0.5, 'x_n': 3}
        self.assertEqual(matrix.table_cell(row, 'x'), '1.23 ± 0.50')
        row.update(x_n=2, x_std=None)
        self.assertEqual(matrix.table_cell(row, 'x'), '1.23 (n=2)')
        row.update(x_mean=None, x_n=0)
        self.assertEqual(matrix.table_cell(row, 'x'), '-')

    def test_aggregate_writes_csv_markdown_and_plots(self):
        names = ('baseline', 'drones-3', 'drones-8', 'tech-mesh')
        record = self.record(names, seeds=(1, 2))
        for name in names:
            for seed, loss in ((1, 10.0), (2, 20.0)):
                write_run(self.campaign, name, seed,
                          summary(loss=loss), self.cfg)
                record['runs'].append(outcome(name, seed))
        matrix.write_json(os.path.join(self.campaign, 'campaign.json'),
                          record)
        paths = matrix.aggregate(self.campaign)
        files = sorted(os.path.basename(p) for p in paths)
        self.assertEqual(files, [
            'aggregated.csv', 'factor-drones.png', 'factor-tech.png',
            'results.csv', 'summary.md'])
        for path in paths:
            self.assertTrue(os.path.getsize(path) > 0, path)
        with open(os.path.join(self.campaign, 'results.csv')) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 8)
        self.assertEqual(tuple(rows[0]), matrix.RESULT_COLUMNS)
        with open(os.path.join(self.campaign, 'aggregated.csv')) as f:
            agg = list(csv.DictReader(f))
        self.assertEqual([r['config'] for r in agg], list(names))
        self.assertEqual(agg[0]['loss_percent_mean'], '15.0')
        self.assertEqual(agg[0]['loss_percent_n'], '2')
        with open(os.path.join(self.campaign, 'summary.md')) as f:
            text = f.read()
        self.assertIn('| baseline | 2 | 0 | 15.00 ± 7.07 |', text)
        with open(os.path.join(self.campaign, 'factor-tech.png'), 'rb') as f:
            self.assertEqual(f.read(8), b'\x89PNG\r\n\x1a\n')

    def test_aggregate_without_campaign_json(self):
        with self.assertRaises(ValueError):
            matrix.aggregate(self.campaign)

    def test_factor_bars_order_and_baseline(self):
        record = self.record(('baseline', 'drones-8', 'drones-3'))
        agg = [{'config': e['name']} for e in record['matrix']]
        bars = matrix.factor_bars(record, agg, 'drones')
        self.assertEqual([(b[0], b[2]) for b in bars],
                         [('3', False), ('5', True), ('8', False)])


class MainTest(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = matrix.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_lists_51_runs_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, 'campaign')
            code, out, _ = self.run_main(
                ['--dry-run', '--coppelia', 'off', '--output-dir', target])
            self.assertFalse(os.path.exists(target))
        self.assertEqual(code, 0)
        self.assertEqual(sum(1 for line in out.splitlines()
                             if line.startswith('run ')), 51)
        self.assertIn('17 configurations x 3 seeds = 51 runs', out)
        self.assertIn('baseline seed1', out)
        self.assertIn('tech-mesh seed3', out)

    def test_dry_run_with_selection_and_overrides(self):
        code, out, _ = self.run_main(
            ['--dry-run', '--coppelia', 'off', '--only', 'baseline',
             'tech-mesh', '--seeds', '1', '--ping-count', '10',
             '--iperf-time', '3'])
        self.assertEqual(code, 0)
        self.assertIn('2 configurations x 1 seeds = 2 runs', out)
        self.assertIn('--ping-count 10 ', out)
        self.assertIn('--iperf-time 3 ', out)

    def test_dry_run_full_warns_about_the_size(self):
        code, out, err = self.run_main(
            ['--dry-run', '--coppelia', 'off', '--full', '--seeds', '1'])
        self.assertIn('warning: --full builds 1008 configurations', err)
        self.assertIn('1008 runs', out)

    def test_dry_run_marks_resumed_runs(self):
        with tempfile.TemporaryDirectory() as d:
            write_run(d, 'baseline', 1, data=summary())
            _, out, _ = self.run_main(
                ['--dry-run', '--coppelia', 'off', '--only', 'baseline',
                 '--seeds', '1', '--resume', '--output-dir', d])
        self.assertIn('skipped, summary.json exists', out)

    def test_invalid_selection_and_seeds_are_usage_errors(self):
        for argv in (['--only', 'nope'], ['--seeds', '1', '1'],
                     ['--run-timeout', '0'], ['--ping-count', '0'],
                     ['--coppelia', 'gui']):
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        matrix.main(['--dry-run'] + argv)
                self.assertEqual(ctx.exception.code, 2)

    def test_all_configurations_invalid_is_an_error(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                matrix.main(['--dry-run', '--coppelia', 'off',
                             '--udp-bandwidth', 'fast'])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn('skipping baseline', err.getvalue())

    def test_running_needs_root(self):
        with mock.patch('os.geteuid', return_value=1000), \
                mock.patch.object(matrix, 'run_all') as run_all:
            with self.assertRaises(SystemExit) as ctx:
                self.run_main(['--coppelia', 'off', '--only', 'baseline'])
        self.assertIn('needs root', str(ctx.exception))
        run_all.assert_not_called()

    def test_non_empty_campaign_requires_resume(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'x'), 'w').close()
            with mock.patch('os.geteuid', return_value=0), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    matrix.main(['--coppelia', 'off', '--output-dir', d])
        self.assertEqual(ctx.exception.code, 2)

    def test_campaign_runs_aggregates_and_hands_over(self):
        def fake(argv, log_path, timeout):
            out = argv[argv.index('--output-dir') + 1]
            os.makedirs(out)
            with open(os.path.join(out, 'summary.json'), 'w') as f:
                json.dump(summary(), f)
            return 0, False

        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, 'campaign')
            with mock.patch('os.geteuid', return_value=0), \
                    mock.patch.object(matrix, 'execute', side_effect=fake), \
                    mock.patch.object(matrix.runner,
                                      'hand_over_output') as hand_over:
                code, out, _ = self.run_main(
                    ['--coppelia', 'off', '--only', 'baseline', 'drones-3',
                     'drones-8', '--seeds', '1', '--output-dir', target])
            self.assertEqual(code, 0)
            self.assertIn('3 runs, 0 failed', out)
            hand_over.assert_called_once_with(target)
            self.assertTrue(os.path.isfile(os.path.join(target,
                                                        'aggregated.csv')))
            self.assertTrue(os.path.isfile(os.path.join(
                target, 'factor-drones.png')))

    def test_failed_run_gives_a_non_zero_exit_status(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, 'campaign')
            with mock.patch('os.geteuid', return_value=0), \
                    mock.patch.object(matrix, 'execute',
                                      return_value=(-15, True)), \
                    mock.patch.object(matrix, 'clean_network') as clean, \
                    mock.patch.object(matrix.runner, 'hand_over_output'):
                code, out, _ = self.run_main(
                    ['--coppelia', 'off', '--only', 'baseline', '--seeds',
                     '1', '--output-dir', target, '--run-timeout', '20'])
        self.assertEqual(code, 1)
        self.assertIn('1 runs, 1 failed', out)
        clean.assert_called_once_with()

    def test_aggregate_only(self):
        with tempfile.TemporaryDirectory() as d:
            record = campaign_record(('baseline',), (1,))
            write_run(d, 'baseline', 1, summary(), None)
            record['runs'] = [outcome('baseline', 1)]
            matrix.write_json(os.path.join(d, 'campaign.json'), record)
            with mock.patch.object(matrix.runner,
                                   'hand_over_output') as hand_over:
                code, _, _ = self.run_main(['--aggregate-only', d])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(os.path.join(d, 'summary.md')))
            self.assertEqual(hand_over.call_count, 3)

    def test_aggregate_only_of_a_missing_campaign(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                matrix.main(['--aggregate-only', '/nonexistent/campaign'])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
