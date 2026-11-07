# Example of Drones integration using CoppeliaSim

Three drones (`dr1`, `dr2`, `dr3`) are flown in a CoppeliaSim scene
(`simulation.ttt`). Their positions are read through the CoppeliaSim legacy
remote API (TCP port 19999) and applied to the Mininet-WiFi stations, which
talk over an ad hoc network running `batman_adv`.

Tested on Ubuntu 24.04 (WSL2) with CoppeliaSim Edu 4.9.0 rev6 and Python 3.12.

## Requirements

* Mininet-WiFi installed (`util/install.sh -Wlnfv`), including the
  `batman-adv` kernel module and the `batctl` tool.
* A graphical display (X11 or Wayland/WSLg) for CoppeliaSim. `sudo` must keep
  the `DISPLAY` variable, which is the default on Ubuntu.

## Installing CoppeliaSim

Run the installer as your normal user, from the root of the repository, so
that the files are not owned by root:

```
./examples/uav/install.sh
```

It downloads CoppeliaSim Edu for your Ubuntu release and extracts it to
`examples/uav/CoppeliaSim_Edu`. Do not commit that directory.

The example uses the legacy remote API client shipped in this directory
(`sim.py`, `simConst.py` and `remoteApi.so`). CoppeliaSim 4.9 still ships the
matching server plugin, which the scene enables when the simulation starts.

## Running the network topology

Start the ad hoc scenario with:

```
sudo python3 examples/uav/uav.py
```

The script starts Mininet-WiFi and the simulation in CoppeliaSim. In parallel,
`simpleTest.py` captures the drones' positions through the remote API and
writes them to `examples/uav/data/<node>.txt` (the directory is created at
runtime), and `setNodePosition.py` sends them to the Mininet-WiFi socket
server. The Mininet-WiFi graph is used to observe the nodes' position as well
as the signal range.

When the CLI shows up, try:

```
mininet-wifi> py dr1.position
mininet-wifi> dr1 ifconfig
mininet-wifi> dr1 batctl n
mininet-wifi> dr1 ping -c 5 dr2
```

With `batman_adv` the routable address of each drone is on `bat0`. The scene
makes the drones fly a diamond pattern, so reachability changes over time as
they move away from and towards each other.

---
**NOTE**

If CoppeliaSim, `simpleTest.py` or `setNodePosition.py` keep running after
finishing the Mininet-WiFi execution, stop only those processes:

```
sudo pkill -f coppeliaSim
sudo pkill -f simpleTest.py
sudo pkill -f setNodePosition.py
```

If there is an error to connect to the Mininet-WiFi socket (port 12345), look
for what is holding the port and stop that process only:

```
sudo ss -ltnp 'sport = :12345'
```

Then clean up leftovers of a previous run with `sudo mn -c`.

CoppeliaSim runs as root because the script is started with `sudo`. On the
first run it writes its settings to `/root/.CoppeliaSim/usrset.txt` and asks
which scripting language you prefer (Lua or Python); pick either one, or set
`preferredSandboxLang` in that file. The `XDG_RUNTIME_DIR not set` warning it
prints is harmless.

---

By [Fabricio Rodriguez](https://github.com/ecwolf)
