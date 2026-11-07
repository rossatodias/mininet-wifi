import os
import subprocess
import unittest
from unittest import mock

from experiment import config, coppelia

CONTROLLER = """local x = 1
    if n_val < 500 then
        targetPosA[2]=targetPosA[2]-0.002
        targetPosA[1]=targetPosA[1]+0.005
        n_val = n_val + 1
    end
        --targetPosA[2]=targetPosA[2]-0.002
    itemData={pos[1],pos[2],0.002,0,0,1,0.2*s}
    sim.setObjectPosition(targetObj,-1,targetPosA)
"""


class FakeSim:
    handle_world = -1
    scripttype_childscript = 1
    scriptstringparam_text = 2

    def __init__(self):
        self.pos = {1: [0.0, 0.0, 0.5], 2: [0.0, 0.0, 0.51],
                    3: [0.0, 0.0, 0.5]}
        self.text = CONTROLLER

    def getObjectPosition(self, handle, rel):
        return list(self.pos[handle])

    def setObjectPosition(self, handle, pos, rel):
        self.pos[handle] = list(pos)

    def getScript(self, kind, handle):
        return 99

    def getScriptStringParam(self, script, param):
        return self.text

    def setScriptStringParam(self, script, param, text):
        self.text = text


class ConversionTest(unittest.TestCase):
    def test_scene_to_net_matches_original_mapping(self):
        self.assertEqual(coppelia.scene_to_net((0, 0, 0)), (50, 50, 50))
        self.assertEqual(coppelia.scene_to_net((1.0, -0.5, 2.0)),
                         (30.0, 60.0, 10.0))

    def test_inverse(self):
        p = (12.5, 47.0, 3.0)
        back = coppelia.scene_to_net(coppelia.net_to_scene(p))
        for a, b in zip(p, back):
            self.assertAlmostEqual(a, b)

    def test_custom_origin_and_scale(self):
        self.assertEqual(coppelia.scene_to_net((1,), origin=10, scale=2),
                         (8,))
        self.assertEqual(coppelia.net_to_scene((8,), origin=10, scale=2),
                         (1,))


class LaunchArgvTest(unittest.TestCase):
    def test_headless(self):
        argv = coppelia.launch_argv('/cs', '/s.ttt', True, 23001)
        self.assertEqual(argv, ['/cs/coppeliaSim.sh', '-h',
                                '-GzmqRemoteApi.rpcPort=23001', '/s.ttt'])

    def test_gui(self):
        argv = coppelia.launch_argv('/cs', '/s.ttt', False)
        self.assertIn('-gGUIITEMS_2', argv)
        self.assertNotIn('-h', argv)
        self.assertIn('-GzmqRemoteApi.rpcPort=23000', argv)
        self.assertNotIn('-s', argv)
        self.assertEqual(argv[-1], '/s.ttt')

    def test_launch_uses_own_session_and_open_stdin(self):
        cfg = config.ExperimentConfig(coppelia='headless')
        with mock.patch('subprocess.Popen') as popen:
            coppelia.launch(cfg, '/dev/null')
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stdin'], subprocess.PIPE)
        self.assertIsInstance(popen.call_args.args[0], list)


class SceneTest(unittest.TestCase):
    def test_disable_builtin_motion(self):
        sim = FakeSim()
        coppelia.disable_builtin_motion(
            sim, [coppelia.DroneHandles(1, 2, 3)])
        self.assertIn('targetPosA[2]=targetPosA[2]\n', sim.text)
        self.assertIn('targetPosA[1]=targetPosA[1]\n', sim.text)
        self.assertNotIn('targetPosA[2]=targetPosA[2]-', sim.text.replace(
            '--targetPosA[2]=targetPosA[2]-0.002', ''))
        self.assertIn('0.002,0,0,1', sim.text)
        self.assertIn('n_val = n_val + 1', sim.text)

    def test_disable_builtin_motion_unknown_controller(self):
        sim = FakeSim()
        sim.text = 'function sysCall_init() end'
        with self.assertRaises(RuntimeError):
            coppelia.disable_builtin_motion(
                sim, [coppelia.DroneHandles(1, 2, 3)])

    def test_place_drones_moves_model_with_offset(self):
        sim = FakeSim()
        coppelia.place_drones(sim, [coppelia.DroneHandles(1, 2, 3)],
                              [(30.0, 50.0, 50.0)])
        self.assertEqual(sim.pos[3], [1.0, 0.0, 0.0])
        self.assertAlmostEqual(sim.pos[2][2], 0.01)

    def test_targets_and_positions_use_net_coordinates(self):
        sim = FakeSim()
        drones = [coppelia.DroneHandles(1, 2, 3)]
        coppelia.set_targets(sim, drones, [(30.0, 60.0, 10.0)])
        self.assertEqual(sim.pos[3], [1.0, -0.5, 2.0])
        sim.pos[1] = [1.0, -0.5, 2.0]
        self.assertEqual(coppelia.read_positions(sim, drones),
                         [(30.0, 60.0, 10.0)])


class FakeScene:
    """Scene with three drone models whose copies get new handles."""
    handle_all = -2
    TREE = 4

    def __init__(self, tree_after_copy=None):
        self.models = [100, 200, 300]
        self.removed = []
        self.copies = []
        self.copied_from = []
        self.next_handle = 1000
        self.tree_after_copy = tree_after_copy or self.TREE

    def base_of(self, model):
        return model + 1

    def getObject(self, path, options):
        if path == '/Quadricopter_base':
            return self.base_of(self.models[options['index']])
        return self.base_of(options['proxy'])

    def getObjectParent(self, handle):
        return handle - 1

    def getObjectChild(self, handle, index):
        return handle + 1

    def copyPasteObjects(self, handles, options):
        self.copied_from.append((handles, options))
        self.next_handle += 10
        self.copies.append(self.next_handle)
        return [self.next_handle]

    def getObjectsInTree(self, handle, kind, options):
        if handle in self.copies:
            return list(range(self.tree_after_copy))
        return list(range(self.TREE))

    def removeModel(self, handle):
        self.removed.append(handle)


class EnsureDronesTest(unittest.TestCase):
    def test_fewer_drones_remove_the_unused_models(self):
        sim = FakeScene()
        handles = coppelia.ensure_drones(sim, 1)
        self.assertEqual(handles, [coppelia.DroneHandles(101, 100, 102)])
        self.assertEqual(sim.removed, [200, 300])
        self.assertEqual(sim.copies, [])

    def test_three_drones_keep_the_scene(self):
        sim = FakeScene()
        handles = coppelia.ensure_drones(sim, 3)
        self.assertEqual([d.model for d in handles], [100, 200, 300])
        self.assertEqual(sim.removed, [])
        self.assertEqual(sim.copies, [])

    def test_more_drones_copy_drones_2_and_3_in_order(self):
        sim = FakeScene()
        handles = coppelia.ensure_drones(sim, 8)
        self.assertEqual([d.model for d in handles[:3]], [100, 200, 300])
        self.assertEqual(
            sim.copied_from,
            [([200], 1), ([300], 1), ([200], 1), ([300], 1), ([200], 1)])
        self.assertEqual([d.model for d in handles[3:]], sim.copies)
        self.assertEqual(len({d.base for d in handles}), 8)
        self.assertEqual(sim.removed, [])

    def test_copy_handles_follow_the_copied_model(self):
        sim = FakeScene()
        handles = coppelia.ensure_drones(sim, 4)
        copy = handles[3]
        self.assertEqual(copy.model, sim.copies[0])
        self.assertEqual(copy.base, copy.model + 1)
        self.assertEqual(copy.target, copy.base + 1)

    def test_incomplete_copy_is_an_error(self):
        sim = FakeScene(tree_after_copy=1)
        with self.assertRaises(RuntimeError):
            coppelia.ensure_drones(sim, 4)


class StopTest(unittest.TestCase):
    def test_stop_simulation_waits(self):
        sim = mock.Mock(simulation_stopped=0)
        sim.getSimulationState.side_effect = [17, 17, 0]
        coppelia.stop_simulation(sim)
        sim.stopSimulation.assert_called_once()
        self.assertEqual(sim.getSimulationState.call_count, 3)

    def test_stop_simulation_timeout(self):
        sim = mock.Mock(simulation_stopped=0)
        sim.getSimulationState.return_value = 17
        with self.assertRaises(RuntimeError):
            coppelia.stop_simulation(sim, timeout=0.2)

    def test_terminate_kills_process_group(self):
        proc = subprocess.Popen(['sleep', '30'], start_new_session=True,
                                stdin=subprocess.PIPE)
        coppelia.terminate(proc)
        self.assertIsNotNone(proc.returncode)
        with self.assertRaises(ProcessLookupError):
            os.killpg(proc.pid, 0)


if __name__ == '__main__':
    unittest.main()
