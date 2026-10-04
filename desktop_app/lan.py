"""Addresses a host can advertise for an IPv4 LAN relay."""
import ipaddress
import socket

import psutil


def lan_addresses():
    """List active adapters, preferring physical LANs over VPN/virtual adapters.

    A UDP connect asks the OS for its default route without sending a packet.
    Adapter enumeration still works on an offline hotspot with no default route.
    """
    preferred = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            preferred = probe.getsockname()[0]
    except OSError:
        pass
    try:
        interfaces = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except (OSError, psutil.Error):
        interfaces, stats = {}, {}
    candidates = []
    virtual_names = ("virtual", "vmware", "vethernet", "vbox", "docker", "wsl", "vpn",
                     "tailscale", "zerotier", "wireguard", "tunnel", "tun", "tap", "utun", "loopback")
    for name, addresses in interfaces.items():
        if name in stats and not stats[name].isup:
            continue
        virtual = any(part in name.lower() for part in virtual_names)
        for item in addresses:
            if item.family != socket.AF_INET:
                continue
            try:
                address = ipaddress.IPv4Address(item.address)
            except ValueError:
                continue
            if address.is_loopback or address.is_unspecified or address.is_multicast:
                continue
            priority = (virtual, address.is_link_local, item.address != preferred, not address.is_private,
                        name.casefold(), item.address)
            candidates.append((priority, name, item.address))
    candidates.sort()
    result, seen = [], set()
    for _, name, address in candidates:
        if address not in seen:
            result.append((name, address))
            seen.add(address)
    if not result and preferred:
        address = ipaddress.IPv4Address(preferred)
        if not address.is_loopback and not address.is_unspecified:
            result.append(("Default network", preferred))
    return result
