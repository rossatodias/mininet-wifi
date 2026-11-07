"""Wireless link setup per technology."""

import re
from dataclasses import dataclass
from typing import Dict, Optional

from mn_wifi.link import adhoc

SSID = 'adhocNet'
BATMAN_IFACE = 'bat0'


@dataclass(frozen=True)
class LinkInfo:
    tech: str
    routing: str
    ssid: str
    channel: int
    interfaces: Dict[str, str]
    routing_iface: Optional[str] = None


def build_links(net, stations, cfg):
    """Create the wireless links of `stations`; must run before net.build()."""
    if cfg.tech != 'adhoc':
        raise NotImplementedError('link technology %s' % cfg.tech)
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


def endpoint_ip(node, cfg):
    """Address other drones use to reach `node`."""
    index = int(re.search(r'\d+', node.name).group())
    if cfg.routing == 'batman_adv':
        return '192.168.123.%d' % index
    return '10.0.0.%d' % index
