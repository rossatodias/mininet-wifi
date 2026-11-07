import contextlib
import csv
import os
import tempfile
import unittest
from unittest import mock

from experiment import config, coppelia, runner


def station(name):
    node = mock.Mock()
    node.name = name
    return node


class OutputTest(unittest.TestCase):
    def test_make_output_dir_returns_topmost_new_directory(self):
        with tempfile.TemporaryDirectory() as d:
            top = runner.make_output_dir(os.path.join(d, 'a', 'b', 'c'))
            self.assertEqual(top, os.path.join(d, 'a'))
            self.assertTrue(os.path.isdir(os.path.join(d, 'a', 'b', 'c')))

    def test_make_output_dir_single_new_level(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'out')
            self.assertEqual(runner.make_output_dir(path), path)

    def test_make_output_dir_existing_returns_itself(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(runner.make_output_dir(d), d)

    def test_hand_over_output_without_sudo_is_noop(self):
        with mock.patch.dict(os.environ, clear=True):
            with mock.patch('os.lchown') as lchown:
                runner.hand_over_output('/nonexistent')
        lchown.assert_not_called()

    def test_hand_over_output_touches_only_the_subtree(self):
        env = {'SUDO_UID': '1000', 'SUDO_GID': '1001'}
        with tempfile.TemporaryDirectory() as d:
            top = os.path.join(d, 'a')
            os.makedirs(os.path.join(top, 'sub'))
            open(os.path.join(top, 'sub', 'f'), 'w').close()
            os.symlink(d, os.path.join(top, 'link'))
            with mock.patch.dict(os.environ, env):
                with mock.patch('os.lchown') as lchown, \
                        mock.patch('os.chown') as chown:
                    runner.hand_over_output(top)
            touched = {c.args[0] for c in lchown.call_args_list}
        self.assertEqual(touched, {top,
                                   os.path.join(top, 'sub'),
                                   os.path.join(top, 'sub', 'f'),
                                   os.path.join(top, 'link')})
        for call in lchown.call_args_list:
            self.assertEqual(call.args[1:], (1000, 1001))
        chown.assert_not_called()

    def test_write_config(self):
        with tempfile.TemporaryDirectory() as d:
            runner.write_config(config.ExperimentConfig(), d)
            self.assertTrue(os.path.getsize(os.path.join(d, 'config.json')))


class TwoTicks:
    """Stand-in for the stop event: the loop runs two iterations."""

    def __init__(self):
        self.ticks = 2

    def is_set(self):
        return self.ticks <= 0

    def wait(self, timeout):
        self.ticks -= 1


@contextlib.contextmanager
def make_sync(cfg, handles=None):
    stations = [station('dr1'), station('dr2')]
    bases = [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'positions.csv')
        sync = runner.PositionSync(stations, bases, cfg, path, handles)
        sync.stop_event = TwoTicks()
        yield sync, stations, path


def read_rows(path):
    with open(path) as f:
        return list(csv.reader(f))


class PositionSyncTest(unittest.TestCase):
    def test_without_coppelia_applies_trajectory(self):
        cfg = config.ExperimentConfig(coppelia='off', trajectory='static')
        with make_sync(cfg) as (sync, stations, path):
            sync.run()
            rows = read_rows(path)
        self.assertIsNone(sync.error)
        stations[1].setPosition.assert_called_with('4.000,5.000,6.000')
        self.assertEqual(stations[1].setPosition.call_count, 2)
        self.assertEqual(rows[0], ['time', 'node', 'x', 'y', 'z'])
        self.assertEqual(len(rows), 1 + 2 * 2)
        self.assertEqual(rows[1][1:], ['dr1', '1.000', '2.000', '3.000'])

    def test_with_coppelia_sets_targets_and_copies_positions(self):
        cfg = config.ExperimentConfig(trajectory='static')
        handles = [coppelia.DroneHandles(1, 2, 3),
                   coppelia.DroneHandles(4, 5, 6)]
        read = [(10.0, 11.0, 12.0), (13.0, 14.0, 15.0)]
        with make_sync(cfg, handles) as (sync, stations, path):
            with mock.patch.object(coppelia, 'new_client',
                                   return_value=('client', 'sim')), \
                    mock.patch.object(coppelia, 'close_client') as close, \
                    mock.patch.object(coppelia, 'set_targets') as targets, \
                    mock.patch.object(coppelia, 'read_positions',
                                      return_value=read):
                sync.run()
        close.assert_called_once_with('client')
        targets.assert_called_with('sim', handles,
                                   [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
        stations[0].setPosition.assert_called_with('10.000,11.000,12.000')

    def test_scene_trajectory_leaves_targets_alone(self):
        cfg = config.ExperimentConfig(trajectory='scene')
        handles = [coppelia.DroneHandles(1, 2, 3)] * 2
        with make_sync(cfg, handles) as (sync, stations, path):
            with mock.patch.object(coppelia, 'new_client',
                                   return_value=('client', 'sim')), \
                    mock.patch.object(coppelia, 'close_client'), \
                    mock.patch.object(coppelia, 'set_targets') as targets, \
                    mock.patch.object(coppelia, 'read_positions',
                                      return_value=[(0, 0, 0)] * 2):
                sync.run()
        targets.assert_not_called()

    def test_failure_is_recorded(self):
        cfg = config.ExperimentConfig(coppelia='off', trajectory='static')
        with make_sync(cfg) as (sync, stations, path):
            with mock.patch.object(runner.topology, 'trajectory',
                                   side_effect=RuntimeError('boom')):
                sync.run()
        self.assertIsInstance(sync.error, RuntimeError)

    def test_stop_ends_thread(self):
        cfg = config.ExperimentConfig(coppelia='off', trajectory='static')
        with tempfile.TemporaryDirectory() as d:
            sync = runner.PositionSync([station('dr1')], [(0, 0, 0)], cfg,
                                       os.path.join(d, 'p.csv'))
            sync.start()
            sync.stop()
            self.assertFalse(sync.is_alive())


class RunTest(unittest.TestCase):
    def test_network_is_stopped_when_setup_fails(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.ExperimentConfig(
                coppelia='off', output_dir=os.path.join(d, 'out'))
            with mock.patch.object(runner, 'Mininet_wifi') as wifi, \
                    mock.patch.object(runner.topology, 'propagation',
                                      side_effect=RuntimeError('boom')), \
                    mock.patch.object(runner, 'run_cli') as cli, \
                    mock.patch('os.geteuid', return_value=0), \
                    mock.patch('signal.signal'):
                with self.assertRaises(RuntimeError):
                    runner.run(cfg)
        wifi.return_value.stop.assert_called_once()
        cli.assert_not_called()

    def test_stop_failure_is_only_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.ExperimentConfig(
                coppelia='off', output_dir=os.path.join(d, 'out'))
            with mock.patch.object(runner, 'Mininet_wifi') as wifi, \
                    mock.patch.object(runner.topology, 'propagation',
                                      side_effect=ValueError('boom')), \
                    mock.patch.object(runner, 'run_cli'), \
                    mock.patch('os.geteuid', return_value=0), \
                    mock.patch('signal.signal'):
                wifi.return_value.stop.side_effect = RuntimeError('half up')
                with self.assertRaises(ValueError):
                    runner.run(cfg)


class BuildNetworkTest(unittest.TestCase):
    def test_link_hooks_wrap_the_build(self):
        cfg = config.ExperimentConfig(coppelia='off')
        order = mock.Mock()
        order.build_links.return_value = 'info'
        order.add_stations.return_value = ['sta']
        order.propagation.return_value = ('friis', {})
        with mock.patch.object(runner.drones, 'add_stations',
                               order.add_stations), \
                mock.patch.object(runner.topology, 'propagation',
                                  order.propagation), \
                mock.patch.object(runner.links, 'add_access_points',
                                  order.add_access_points), \
                mock.patch.object(runner.links, 'build_links',
                                  order.build_links), \
                mock.patch.object(runner.links, 'start', order.start):
            stations, link_info = runner.build_network(order.net, cfg)
        self.assertEqual(stations, ['sta'])
        self.assertEqual(link_info, 'info')
        self.assertEqual(order.mock_calls, [
            mock.call.add_stations(order.net, cfg),
            mock.call.add_access_points(order.net, ['sta'], cfg),
            mock.call.propagation(cfg),
            mock.call.net.setPropagationModel(model='friis'),
            mock.call.net.configureNodes(),
            mock.call.build_links(order.net, ['sta'], cfg),
            mock.call.net.build(),
            mock.call.start(order.net, 'info')])


class BatchTest(unittest.TestCase):
    def test_runs_metrics_after_warmup(self):
        cfg = config.ExperimentConfig(warmup=0.0, metrics=True,
                                      duration=0.3)
        with mock.patch.object(runner.metrics, 'run_metrics') as run:
            with mock.patch.object(runner.time, 'sleep') as sleep:
                runner.run_batch('net', cfg, 'info', '/out')
        run.assert_called_once_with('net', cfg, 'info', '/out')
        self.assertEqual(sleep.call_count, 2)

    def test_no_metrics_no_duration(self):
        cfg = config.ExperimentConfig(warmup=0.0)
        with mock.patch.object(runner.metrics, 'run_metrics') as run:
            runner.run_batch('net', cfg, 'info', '/out')
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
