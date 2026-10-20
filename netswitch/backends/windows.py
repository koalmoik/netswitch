"""Windows backend.

Reads use PowerShell (structured JSON output, independent of the UI language);
writes use ``netsh`` whose keywords are not localised.
"""

from __future__ import annotations

import ctypes
import json

from ..models import Profile
from .base import Adapter, Backend, BackendError, Command, run

PS_PREFIX = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;$ErrorActionPreference='Stop';"


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def netsh_quote(value: str) -> str:
    if '"' in value:
        raise BackendError(f"Adapter name contains an unsupported character: {value}")
    return f'"{value}"'


def powershell_json(script: str):
    res = run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", PS_PREFIX + script]
    )
    if res.returncode != 0:
        raise BackendError(f"PowerShell failed:\n{res.stderr.strip() or res.stdout.strip()}")
    out = res.stdout.strip()
    return json.loads(out) if out else None


class WindowsBackend(Backend):
    name = "windows"
    output_encoding = "oem"  # netsh prints in the console code page

    def needs_elevation(self) -> bool:
        try:
            return not ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return True

    def list_adapters(self) -> list[Adapter]:
        data = powershell_json(
            "ConvertTo-Json -Compress -InputObject @(Get-NetAdapter | "
            "Sort-Object Name | Select-Object Name,InterfaceDescription,Status,MacAddress)"
        ) or []
        return [
            Adapter(
                name=d["Name"],
                description=d.get("InterfaceDescription") or "",
                status=str(d.get("Status") or ""),
                mac=d.get("MacAddress") or "",
            )
            for d in data
        ]

    def read_config(self, adapter: str) -> Profile:
        a = ps_quote(adapter)
        script = f"""
$a = {a}
$na = Get-NetAdapter -Name $a
$ipi = Get-NetIPInterface -InterfaceAlias $a -AddressFamily IPv4
$ip = @(Get-NetIPAddress -InterfaceAlias $a -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {{ $_.PrefixOrigin -ne 'WellKnown' }})[0]
$gw = @(Get-NetRoute -InterfaceAlias $a -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric)[0]
$dns = (Get-DnsClientServerAddress -InterfaceAlias $a -AddressFamily IPv4).ServerAddresses
$reg = Get-ItemProperty -Path ("HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\" + $na.InterfaceGuid) -ErrorAction SilentlyContinue
[pscustomobject]@{{
  Dhcp      = [string]$ipi.Dhcp
  IP        = $ip.IPAddress
  Prefix    = $ip.PrefixLength
  Gateway   = $gw.NextHop
  Dns       = @($dns)
  StaticDns = [string]$reg.NameServer
}} | ConvertTo-Json -Compress
"""
        d = powershell_json(script)
        if not d:
            raise BackendError(f"Could not read configuration of '{adapter}'")
        gateway = d.get("Gateway") or ""
        if gateway == "0.0.0.0":
            gateway = ""
        static_dns = (d.get("StaticDns") or "").replace(",", " ").split()
        return Profile(
            name=f"{adapter} (current)",
            adapter=adapter,
            ip_mode="dhcp" if d.get("Dhcp") == "Enabled" else "static",
            ip_address=d.get("IP") or "",
            prefix_length=int(d.get("Prefix") or 24),
            gateway=gateway,
            dns_mode="static" if static_dns else "dhcp",
            dns_servers=static_dns or [s for s in d.get("Dns") or [] if ":" not in s],
        )

    def build_commands(self, profile: Profile, adapter: str) -> list[Command]:
        name = f"name={netsh_quote(adapter)}"
        base = "netsh interface ipv4"
        cmds: list[str] = []
        tolerant: set[int] = set()

        if profile.ip_mode == "dhcp":
            # netsh exits non-zero if DHCP is already enabled; that is harmless.
            tolerant.add(len(cmds))
            cmds.append(f"{base} set address {name} source=dhcp")
        else:
            gw = profile.gateway or "none"
            gw_part = f"gateway={gw}" + (" gwmetric=0" if profile.gateway else "")
            cmds.append(
                f"{base} set address {name} source=static "
                f"address={profile.ip_address} mask={profile.subnet_mask} {gw_part}"
            )

        if profile.dns_mode == "dhcp":
            cmds.append(f"{base} set dnsservers {name} source=dhcp")
        elif not profile.dns_servers:
            cmds.append(f"{base} set dnsservers {name} source=static address=none")
        else:
            first, *rest = profile.dns_servers
            cmds.append(f"{base} set dnsservers {name} source=static address={first} register=primary validate=no")
            for i, server in enumerate(rest, start=2):
                cmds.append(f"{base} add dnsservers {name} address={server} index={i} validate=no")

        return [
            Command(args=c.split(), raw=c, tolerate_failure=i in tolerant)
            for i, c in enumerate(cmds)
        ]

    def apply(self, profile: Profile, adapter: str | None = None) -> list[str]:
        if self.needs_elevation():
            raise BackendError("Administrator rights are required to change network settings.")
        log = super().apply(profile, adapter)
        run(["ipconfig", "/flushdns"], encoding=self.output_encoding)
        return log
