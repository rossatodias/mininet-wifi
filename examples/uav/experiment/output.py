"""Output directories and result files."""

import json
import os


def make_output_dir(path):
    """Create `path`; return the topmost directory this call created.

    An already existing `path` (empty, see config) is returned as is.
    """
    top = path
    while not os.path.exists(os.path.dirname(top)):
        top = os.path.dirname(top)
    os.makedirs(path, exist_ok=True)
    return top


def hand_over_output(top):
    """Give the tree under `top` to the user who started sudo."""
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if uid is None or gid is None:
        return
    os.lchown(top, int(uid), int(gid))
    for root, dirs, files in os.walk(top):
        for name in dirs + files:
            os.lchown(os.path.join(root, name), int(uid), int(gid))


def write_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
