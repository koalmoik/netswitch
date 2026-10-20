"""Profile data model and validation."""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = 1


class ValidationError(ValueError):
    pass


@dataclass
class Profile:
    name: str
    adapter: str = ""  # interface alias (Windows) / device name (Linux); "" = choose at apply time
    ip_mode: str = "dhcp"  # "dhcp" | "static"
    ip_address: str = ""
    prefix_length: int = 24
    gateway: str = ""
    dns_mode: str = "dhcp"  # "dhcp" | "static"
    dns_servers: list[str] = field(default_factory=list)
    notes: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # ---- serialisation -------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        if "name" not in clean or not str(clean["name"]).strip():
            raise ValidationError("Profile is missing a name")
        # Accept a dotted subnet mask under the legacy key "subnet_mask".
        if "prefix_length" not in clean and data.get("subnet_mask"):
            clean["prefix_length"] = mask_to_prefix(data["subnet_mask"])
        clean["prefix_length"] = int(clean.get("prefix_length", 24))
        clean["dns_servers"] = [str(s).strip() for s in clean.get("dns_servers") or [] if str(s).strip()]
        return cls(**clean)

    # ---- helpers -------------------------------------------------------

    @property
    def subnet_mask(self) -> str:
        return prefix_to_mask(self.prefix_length)

    def summary(self) -> str:
        if self.ip_mode == "dhcp":
            ip = "DHCP"
        else:
            ip = f"{self.ip_address}/{self.prefix_length}"
            if self.gateway:
                ip += f" via {self.gateway}"
        dns = "auto" if self.dns_mode == "dhcp" else (", ".join(self.dns_servers) or "none")
        return f"{ip} | DNS: {dns}"

    def validate(self) -> None:
        if not self.name.strip():
            raise ValidationError("Name must not be empty")
        if self.ip_mode not in ("dhcp", "static"):
            raise ValidationError(f"Invalid IP mode: {self.ip_mode}")
        if self.dns_mode not in ("dhcp", "static"):
            raise ValidationError(f"Invalid DNS mode: {self.dns_mode}")
        if self.ip_mode == "static":
            ip = _parse_ipv4(self.ip_address, "IP address")
            if not 0 < self.prefix_length <= 32:
                raise ValidationError("Prefix length must be between 1 and 32")
            if self.gateway:
                gw = _parse_ipv4(self.gateway, "Gateway")
                net = ipaddress.IPv4Network(f"{ip}/{self.prefix_length}", strict=False)
                if gw not in net:
                    raise ValidationError(f"Gateway {gw} is not inside the network {net}")
        for server in self.dns_servers:
            try:
                ipaddress.ip_address(server)
            except ValueError:
                raise ValidationError(f"Invalid DNS server: {server}") from None


def _parse_ipv4(value: str, label: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(value.strip())
    except ValueError:
        raise ValidationError(f"{label} is not a valid IPv4 address: {value!r}") from None


def prefix_to_mask(prefix: int) -> str:
    return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)


def mask_to_prefix(mask: str) -> int:
    mask = str(mask).strip()
    if mask.isdigit():
        return int(mask)
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        raise ValidationError(f"Invalid subnet mask: {mask}") from None
