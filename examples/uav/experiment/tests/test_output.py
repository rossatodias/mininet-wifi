import json
import os
import tempfile
import unittest
from unittest import mock

from experiment import output


class OutputTest(unittest.TestCase):
    def test_make_output_dir_returns_topmost_new_directory(self):
        with tempfile.TemporaryDirectory() as d:
            top = output.make_output_dir(os.path.join(d, 'a', 'b', 'c'))
            self.assertEqual(top, os.path.join(d, 'a'))
            self.assertTrue(os.path.isdir(os.path.join(d, 'a', 'b', 'c')))

    def test_make_output_dir_single_new_level(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'out')
            self.assertEqual(output.make_output_dir(path), path)

    def test_make_output_dir_existing_returns_itself(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(output.make_output_dir(d), d)

    def test_hand_over_output_without_sudo_is_noop(self):
        with mock.patch.dict(os.environ, clear=True):
            with mock.patch('os.lchown') as lchown:
                output.hand_over_output('/nonexistent')
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
                    output.hand_over_output(top)
            touched = {c.args[0] for c in lchown.call_args_list}
        self.assertEqual(touched, {top,
                                   os.path.join(top, 'sub'),
                                   os.path.join(top, 'sub', 'f'),
                                   os.path.join(top, 'link')})
        for call in lchown.call_args_list:
            self.assertEqual(call.args[1:], (1000, 1001))
        chown.assert_not_called()

    def test_write_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'x.json')
            output.write_json(path, {'a': [1, None]})
            with open(path) as f:
                self.assertEqual(json.load(f), {'a': [1, None]})


if __name__ == '__main__':
    unittest.main()
