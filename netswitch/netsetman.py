"""Reading NetSetMan XML exports (File → Export in NetSetMan 5)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import Profile, ValidationError, mask_to_prefix


def looks_like_xml(raw: bytes) -> bool:
    return raw.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def parse_netsetman(raw: bytes) -> tuple[list[Profile], list[str]]:
    """Return the importable profiles and a reason for each one that was skipped."""
    try:
        root = ET.fromstring(raw.decode("utf-8-sig"))
    except (ET.ParseError, UnicodeDecodeError) as e:
        raise ValidationError(f"Not a valid XML file: {e}") from None
    if root.tag != "NetSetMan":
        raise ValidationError("This XML file is not a NetSetMan export")
    profiles, skipped = [], []
    for el in root.iter("Profile"):
        name = (el.get("name") or "").strip() or "Unnamed"
        try:
            profiles.append(_profile(el, name))
        except ValidationError as e:
            skipped.append(f"{name}: {e}")
    return profiles, skipped


def _profile(el: ET.Element, name: str) -> Profile:
    nic = el.find("Settings/Nic")
    settings = {s.get("name"): s for s in el.findall("Settings/Setting")}
    p = Profile(name=name, adapter=(nic.get("name") or "").strip() if nic is not None else "")
    notes = ["Imported from NetSetMan"]

    ip = settings.get("IP")
    if ip is None or ip.get("set") != "True":
        raise ValidationError("does not change the IP settings")
    if ip.get("dhcp") == "False":
        item = ip.find("Item")
        if item is None or not (item.text or "").strip():
            raise ValidationError("static IP without an address")
        p.ip_mode = "static"
        p.ip_address = item.text.strip()
        p.prefix_length = mask_to_prefix(item.get("prefix") or item.get("mask") or "24")
        # A static IP gets no DNS from DHCP. Where NetSetMan leaves DNS alone this means no servers.
        p.dns_mode = "static"

    gw = settings.get("Gateway")
    gw_item = gw.find("Item") if gw is not None else None
    if gw_item is not None and (gw_item.text or "").strip():
        if gw.get("set") == "True":
            p.gateway = gw_item.text.strip()
        else:
            notes.append(f"Gateway {gw_item.text.strip()} was saved but disabled in NetSetMan")

    dns = settings.get("DNS")
    if dns is not None and dns.get("set") == "True" and dns.get("dhcp") == "False":
        p.dns_mode = "static"
        p.dns_servers = [i.text.strip() for i in dns.findall("Item") if (i.text or "").strip()]

    p.notes = "\n".join(notes)
    p.validate()
    return p
