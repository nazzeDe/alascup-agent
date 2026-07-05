from __future__ import annotations

import psutil

from src.config import ToolServerConfig


def get_network_info(config: ToolServerConfig) -> dict:
    addrs = {}
    for ifname, snic_list in psutil.net_if_addrs().items():
        addrs[ifname] = []
        for snic in snic_list:
            addrs[ifname].append(
                {
                    "family": str(snic.family),
                    "address": snic.address,
                    "netmask": snic.netmask or "",
                    "broadcast": snic.broadcast or "",
                }
            )

    io_counters = {}
    for ifname, snic in psutil.net_io_counters(pernic=True).items():
        io_counters[ifname] = {
            "bytes_sent": snic.bytes_sent,
            "bytes_recv": snic.bytes_recv,
            "packets_sent": snic.packets_sent,
            "packets_recv": snic.packets_recv,
            "errin": snic.errin,
            "errout": snic.errout,
            "dropin": snic.dropin,
            "dropout": snic.dropout,
        }

    interfaces = {}
    for ifname in addrs:
        interfaces[ifname] = {
            "addresses": addrs.get(ifname, []),
            "io_counters": io_counters.get(ifname, {}),
        }

    return {
        "interfaces": interfaces,
        "iface_count": len(interfaces),
    }
