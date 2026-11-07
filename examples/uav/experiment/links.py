"""Wireless link setup per technology."""

import math
import re
from dataclasses import dataclass
from typing import Dict, Optional

from mn_wifi.link import adhoc, mesh

from experiment import drones

SSID = 'adhocNet'
MESH_SSID = 'meshNet'
INFRA_SSID = 'infraNet'
BATMAN_IFACE = 'bat0'
AP_NAME = 'ap1'
AP_RANGE_MARGIN = 5.0


@dataclass(frozen=True)
class LinkInfo:
    tech: str
    routing: str
    ssid: str
    channel: int
    interfaces: Dict[str, str]
    routing_iface: Optional[str] = None


def centroid(positions):
    """Mean x and y of `positions`; z is 0."""
    n = len(positions)
    return (sum(p[0] for p in positions) / n,
            sum(p[1] for p in positions) / n, 0.0)


def coverage(positions):
    """Return (AP position, range) covering every position."""
    center = centroid(positions)
    farthest = max(math.dist(center[:2], p[:2]) for p in positions)
    return center, math.ceil(farthest + AP_RANGE_MARGIN)


def add_access_points(net, stations, cfg):
    """Add the access point of an infrastructure network.

    Must run before net.configureNodes(); does nothing for other
    technologies. Coverage is computed for the initial formation only.
    """
    if cfg.tech != 'infra':
        return
    center, reach = coverage(drones.initial_positions(cfg))
    net.addAccessPoint(
        AP_NAME, ssid=INFRA_SSID, mode='g', channel=cfg.channel,
        failMode='standalone', position='%.3f,%.3f,%.3f' % center,
        range=reach)


def build_links(net, stations, cfg):
    """Create the wireless links of `stations`; must run before net.build()."""
    if cfg.tech == 'infra':
        return LinkInfo(
            tech=cfg.tech, routing=cfg.routing, ssid=INFRA_SSID,
            channel=cfg.channel,
            interfaces={sta.name: '%s-wlan0' % sta.name for sta in stations})
    if cfg.tech == 'mesh':
        return build_mesh(net, stations, cfg)
    return build_adhoc(net, stations, cfg)


def build_adhoc(net, stations, cfg):
    kwargs = {}
    if cfg.routing == 'batman_adv':
        kwargs['proto'] = 'batman_adv'
    interfaces = {}
    for sta in stations:
        interfaces[sta.name] = '%s-wlan0' % sta.name
        net.addLink(sta, cls=adhoc, intf=interfaces[sta.name], ssid=SSID,
                    mode='g', channel=cfg.channel, ht_cap='HT40+', **kwargs)
    return LinkInfo(
        tech=cfg.tech, routing=cfg.routing, ssid=SSID, channel=cfg.channel,
        interfaces=interfaces,
        routing_iface=BATMAN_IFACE if kwargs else None)


def build_mesh(net, stations, cfg):
    interfaces = {}
    for sta in stations:
        net.addLink(sta, cls=mesh, intf='%s-wlan0' % sta.name,
                    ssid=MESH_SSID, channel=cfg.channel, ht_cap='HT40+')
        interfaces[sta.name] = '%s-mp0' % sta.name
    return LinkInfo(
        tech=cfg.tech, routing=cfg.routing, ssid=MESH_SSID,
        channel=cfg.channel, interfaces=interfaces)


def start(net, info):
    """Start what must run after net.build(); does nothing except for infra.

    The access point is started without a controller. The stations are
    disconnected and associated again: build() tried to connect them before
    hostapd was up and left the attempt pending.
    """
    if info.tech != 'infra':
        return
    ap = net.get(AP_NAME)
    ap.start([])
    for sta in net.stations:
        sta.wintfs[0].disconnect(ap.wintfs[0])
        sta.setAssociation(ap)


def endpoint_ip(node, cfg):
    """Address other drones use to reach `node`."""
    index = int(re.search(r'\d+', node.name).group())
    if cfg.routing == 'batman_adv':
        return '192.168.123.%d' % index
    return '10.0.0.%d' % index
