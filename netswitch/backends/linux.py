"""Linux backend using NetworkManager's ``nmcli``."""

from __future__ import annotations

import re

from ..models import Profile
from .base import Adapter, Backend, BackendError, Command, run

SKIP_TYPES = {"loopback", "bridge", "tun", "wireguard", "dummy"}


def split_terse(line: str) -> list[str]:
    """Split an ``nmcli -t`` line on unescaped colons."""
    parts = re.split(r"(?<!\\):", line)
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in parts]


def split_values(value: str) -> list[str]:
    return [v.strip() for v in re.split(r"[,|]", value) if v.strip()]


def nmcli(*args: str) -> str:
    res = run(["nmcli", *args])
    if res.returncode != 0:
        raise BackendError(f"nmcli {' '.join(args)} failed:\n{res.stderr.strip()}")
    return res.stdout


class LinuxBackend(Backend):
    name = "linux"

    def list_adapters(self) -> list[Adapter]:
        adapters = []
        for line in nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status").splitlines():
            if not line.strip():
                continue
            device, typ, state, connection = (split_terse(line) + [""] * 4)[:4]
            if typ in SKIP_TYPES or device.startswith("p2p-"):
                continue
            desc = typ + (f" ({connection})" if connection else "")
            adapters.append(Adapter(name=device, description=desc, status=state))
        return adapters

    def connection_for(self, device: str) -> str:
        """UUID of the connection profile active on ``device``, or of one bound to it."""
        uuid = nmcli("-g", "GENERAL.CON-UUID", "device", "show", device).strip()
        if uuid:
            return uuid
        for line in nmcli("-t", "-f", "UUID,TYPE", "connection", "show").splitlines():
            con_uuid = split_terse(line)[0]
            iface = nmcli("-g", "connection.interface-name", "connection", "show", "uuid", con_uuid).strip()
            if iface == device:
                return con_uuid
        raise BackendError(
            f"No NetworkManager connection found for '{device}'. "
            "Connect the device once so NetworkManager creates a connection profile."
        )

    def read_config(self, adapter: str) -> Profile:
        uuid = self.connection_for(adapter)
        fields = nmcli(
            "-g", "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns,ipv4.ignore-auto-dns",
            "connection", "show", "uuid", uuid,
        ).splitlines()
        method, addresses, gateway, dns, ignore_auto = (fields + [""] * 5)[:5]
        if method == "manual":
            addr = (split_values(addresses) or ["/24"])[0]
        else:
            live = nmcli("-g", "IP4.ADDRESS", "device", "show", adapter)
            addr = (split_values(live) or ["/24"])[0]
            gateway = nmcli("-g", "IP4.GATEWAY", "device", "show", adapter).strip()
        ip, _, prefix = addr.partition("/")
        static_dns = split_values(dns)
        if not static_dns:
            static_dns = split_values(nmcli("-g", "IP4.DNS", "device", "show", adapter))
        return Profile(
            name=f"{adapter} (current)",
            adapter=adapter,
            ip_mode="static" if method == "manual" else "dhcp",
            ip_address=ip,
            prefix_length=int(prefix or 24),
            gateway=gateway.strip(),
            dns_mode="static" if (dns.strip() and ignore_auto == "yes") else "dhcp",
            dns_servers=static_dns,
        )

    def build_commands(self, profile: Profile, adapter: str) -> list[Command]:
        uuid = self.connection_for(adapter)
        mod = ["nmcli", "connection", "modify", "uuid", uuid]
        if profile.ip_mode == "static":
            mod += [
                "ipv4.method", "manual",
                "ipv4.addresses", f"{profile.ip_address}/{profile.prefix_length}",
                "ipv4.gateway", profile.gateway,
            ]
        else:
            mod += ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""]
        if profile.dns_mode == "static":
            mod += ["ipv4.dns", ",".join(profile.dns_servers), "ipv4.ignore-auto-dns", "yes"]
        else:
            mod += ["ipv4.dns", "", "ipv4.ignore-auto-dns", "no"]
        return [
            Command(mod),
            Command(["nmcli", "connection", "up", "uuid", uuid, "ifname", adapter]),
        ]
