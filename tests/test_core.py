import json

import pytest

from netswitch.backends.linux import LinuxBackend, split_terse
from netswitch.backends.windows import WindowsBackend
from netswitch.models import Profile, ValidationError, mask_to_prefix, prefix_to_mask
from netswitch.storage import ProfileStore


def static_profile(**kw):
    defaults = dict(name="Office", adapter="Ethernet 2", ip_mode="static", ip_address="192.168.10.50",
                    prefix_length=24, gateway="192.168.10.1", dns_mode="static",
                    dns_servers=["192.168.10.1", "1.1.1.1"])
    defaults.update(kw)
    return Profile(**defaults)


# ---- model -------------------------------------------------------------------

def test_mask_conversion():
    assert prefix_to_mask(24) == "255.255.255.0"
    assert mask_to_prefix("255.255.252.0") == 22
    assert mask_to_prefix("16") == 16
    with pytest.raises(ValidationError):
        mask_to_prefix("255.0.255.0")


def test_validation():
    static_profile().validate()
    with pytest.raises(ValidationError, match="Gateway"):
        static_profile(gateway="10.0.0.1").validate()
    with pytest.raises(ValidationError, match="IP address"):
        static_profile(ip_address="300.1.1.1").validate()
    with pytest.raises(ValidationError, match="DNS"):
        static_profile(dns_servers=["nope"]).validate()
    Profile(name="Home").validate()  # DHCP defaults are valid


def test_from_dict_ignores_unknown_and_accepts_mask():
    p = Profile.from_dict({"name": "X", "subnet_mask": "255.255.0.0", "future_field": 1})
    assert p.prefix_length == 16


# ---- storage -----------------------------------------------------------------

def test_store_roundtrip(tmp_path):
    store = ProfileStore(tmp_path / "p.json")
    store.upsert(static_profile())
    store.upsert(Profile(name="Home"))
    again = ProfileStore(tmp_path / "p.json")
    assert [p.name for p in again.profiles] == ["Office", "Home"]
    assert again.profiles[0].dns_servers == ["192.168.10.1", "1.1.1.1"]


def test_export_import(tmp_path):
    src = ProfileStore(tmp_path / "a.json")
    src.upsert(static_profile())
    src.upsert(Profile(name="Home"))
    out = tmp_path / "export.json"
    assert src.export_to(out) == 2
    assert json.loads(out.read_text())["app"] == "NetSwitch"

    dst = ProfileStore(tmp_path / "b.json")
    dst.upsert(Profile(name="office"))  # name clash (case-insensitive)
    added, replaced = dst.import_from(out)
    assert (added, replaced) == (2, 0)
    assert [p.name for p in dst.profiles] == ["office", "Office (2)", "Home"]
    assert len({p.id for p in dst.profiles}) == 3

    added, replaced = dst.import_from(out, overwrite=True)
    assert replaced == 2 and added == 0
    assert dst.profiles[0].ip_mode == "static"


def test_import_bare_list_and_single(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps([{"name": "A"}, {"name": "B"}]))
    store = ProfileStore(tmp_path / "s.json")
    assert store.import_from(f) == (2, 0)
    f.write_text(json.dumps({"name": "C"}))
    assert store.import_from(f) == (1, 0)


# ---- Windows commands ------------------------------------------------------------

def test_windows_static_commands():
    cmds = [c.raw for c in WindowsBackend().build_commands(static_profile(), "Ethernet 2")]
    assert cmds == [
        'netsh interface ipv4 set address name="Ethernet 2" source=static address=192.168.10.50 '
        'mask=255.255.255.0 gateway=192.168.10.1 gwmetric=0',
        'netsh interface ipv4 set dnsservers name="Ethernet 2" source=static address=192.168.10.1 '
        'register=primary validate=no',
        'netsh interface ipv4 add dnsservers name="Ethernet 2" address=1.1.1.1 index=2 validate=no',
    ]


def test_windows_dhcp_commands():
    cmds = WindowsBackend().build_commands(Profile(name="Home"), "WLAN")
    assert [c.raw for c in cmds] == [
        'netsh interface ipv4 set address name="WLAN" source=dhcp',
        'netsh interface ipv4 set dnsservers name="WLAN" source=dhcp',
    ]
    assert cmds[0].tolerate_failure and not cmds[1].tolerate_failure


def test_windows_static_without_gateway():
    cmds = WindowsBackend().build_commands(static_profile(gateway="", dns_servers=[]), "LAN")
    assert "gateway=none" in cmds[0].raw and "gwmetric" not in cmds[0].raw
    assert cmds[1].raw.endswith("source=static address=none")


# ---- Linux commands -------------------------------------------------------------

def test_split_terse():
    assert split_terse(r"eth0:ethernet:connected:My\:Conn") == ["eth0", "ethernet", "connected", "My:Conn"]


def test_linux_commands(monkeypatch):
    b = LinuxBackend()
    monkeypatch.setattr(b, "connection_for", lambda dev: "uuid-123")
    mod, up = b.build_commands(static_profile(), "enp3s0")
    assert mod.args[:5] == ["nmcli", "connection", "modify", "uuid", "uuid-123"]
    assert mod.args[5:] == ["ipv4.method", "manual", "ipv4.addresses", "192.168.10.50/24",
                            "ipv4.gateway", "192.168.10.1", "ipv4.dns", "192.168.10.1,1.1.1.1",
                            "ipv4.ignore-auto-dns", "yes"]
    assert up.args == ["nmcli", "connection", "up", "uuid", "uuid-123", "ifname", "enp3s0"]

    mod, _ = b.build_commands(Profile(name="Home"), "enp3s0")
    assert mod.args[5:] == ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "",
                            "ipv4.dns", "", "ipv4.ignore-auto-dns", "no"]
