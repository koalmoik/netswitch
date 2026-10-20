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
    added, replaced, _ = dst.import_from(out)
    assert (added, replaced) == (2, 0)
    assert [p.name for p in dst.profiles] == ["office", "Office (2)", "Home"]
    assert len({p.id for p in dst.profiles}) == 3

    added, replaced, _ = dst.import_from(out, overwrite=True)
    assert replaced == 2 and added == 0
    assert dst.profiles[0].ip_mode == "static"


def test_import_bare_list_and_single(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps([{"name": "A"}, {"name": "B"}]))
    store = ProfileStore(tmp_path / "s.json")
    assert store.import_from(f) == (2, 0, [])
    f.write_text(json.dumps({"name": "C"}))
    assert store.import_from(f) == (1, 0, [])


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


# ---- PowerShell input ------------------------------------------------------------

def test_windows_read_config_passes_adapter_via_env(monkeypatch):
    import subprocess

    from netswitch.backends import windows

    seen = {}

    def fake_run(cmd, encoding="utf-8", timeout=60, env=None):
        seen["script"], seen["env"] = cmd[-1], env
        out = json.dumps({"Dhcp": "Enabled", "IP": "10.0.0.5", "Prefix": 24, "Gateway": "10.0.0.1",
                          "Dns": ["10.0.0.1"], "StaticDns": ""})
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(windows, "run", fake_run)
    evil = "Eth\u2019; Remove-Item C:\\x; \u2018"  # curly quotes count as quotes in PowerShell
    p = WindowsBackend().read_config(evil)
    assert evil not in seen["script"]
    assert seen["env"]["NETSWITCH_ADAPTER"] == evil
    assert p.adapter == evil and p.gateway == "10.0.0.1"


# ---- active profile detection ----------------------------------------------------

def test_matches():
    current = static_profile(name="Ethernet 2 (current)")
    assert static_profile().matches(current)
    assert not static_profile(ip_address="192.168.10.51").matches(current)
    assert not static_profile(dns_servers=["1.1.1.1"]).matches(current)
    assert not Profile(name="Home").matches(current)
    # DHCP: the live address doesn't matter, only the modes and static DNS
    live = Profile(name="x", ip_address="10.1.2.3", gateway="10.1.2.1", dns_servers=["10.1.2.1"])
    assert Profile(name="Home").matches(live)
    assert not Profile(name="Home", dns_mode="static", dns_servers=["1.1.1.1"]).matches(live)


# ---- connection check ------------------------------------------------------------

def test_check_gateway(monkeypatch):
    b = LinuxBackend()
    pinged = []
    monkeypatch.setattr(b, "ping", lambda host: pinged.append(host) or host == "192.168.10.1")
    assert "reachable" in b.check_gateway(static_profile(), "enp3s0", wait=0)
    assert "does not answer" in b.check_gateway(static_profile(gateway="192.168.10.254"), "enp3s0", wait=0)
    assert "skipped" in b.check_gateway(static_profile(gateway=""), "enp3s0", wait=0)

    monkeypatch.setattr(b, "read_config", lambda dev: Profile(name="x", gateway="192.168.10.1"))
    assert "reachable" in b.check_gateway(Profile(name="Home"), "enp3s0", wait=0)
    monkeypatch.setattr(b, "read_config", lambda dev: Profile(name="x"))
    assert "No gateway" in b.check_gateway(Profile(name="Home"), "enp3s0", wait=0)


def test_ping_only_accepts_addresses(monkeypatch):
    from netswitch.backends import base

    monkeypatch.setattr(base, "run", lambda *a, **k: pytest.fail("ping must not run"))
    assert not LinuxBackend().ping("-f")


# ---- CLI ---------------------------------------------------------------------------

def test_cli_apply_with_adapter(tmp_path, monkeypatch, capsys):
    import netswitch.backends
    from netswitch.__main__ import main

    applied = []

    class FakeBackend:
        def apply(self, profile, adapter=None):
            applied.append((profile.name, adapter))
            return []

    monkeypatch.setattr(netswitch.backends, "get_backend", FakeBackend)
    path = tmp_path / "p.json"
    store = ProfileStore(path)
    store.upsert(Profile(name="Home"))  # no adapter saved

    assert main(["--profiles", str(path), "--apply", "home"]) == 2
    assert "--adapter" in capsys.readouterr().err
    assert main(["--profiles", str(path), "--apply", "home", "--adapter", "wlan0"]) == 0
    assert applied == [("Home", "wlan0")]


# ---- NetSetMan import --------------------------------------------------------------

NETSETMAN_XML = """﻿<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<NetSetMan version="5.0.6">
  <Profiles>
    <Group name="Gruppe">
      <Profile name="Automatische IP">
        <Settings>
          <Nic name="Ethernet">{DCF018F7}</Nic>
          <Setting name="IP" id="11" set="True" dhcp="True"/>
        </Settings>
      </Profile>
      <Profile name="MRBTS">
        <Settings>
          <Nic name="Ethernet">{DCF018F7}</Nic>
          <Setting name="IP" id="11" set="True" dhcp="False"><Item prefix="25">192.168.255.130</Item></Setting>
          <Setting name="Gateway" id="12" set="False"><Item>192.168.255.129</Item></Setting>
        </Settings>
      </Profile>
      <Profile name="Office">
        <Settings>
          <Nic name="LAN">{58368E84}</Nic>
          <Setting name="IP" id="11" set="True" dhcp="False"><Item prefix="24">10.0.0.5</Item></Setting>
          <Setting name="Gateway" id="12" set="True"><Item>10.0.0.1</Item></Setting>
          <Setting name="DNS" id="13" set="True" dhcp="False"><Item>10.0.0.1</Item><Item>1.1.1.1</Item></Setting>
        </Settings>
      </Profile>
      <Profile name="SRAN FPRx">
        <Settings>
          <Nic name="Ethernet">{58368E84}</Nic>
          <Setting name="IP" id="11" set="True" dhcp="False"/>
        </Settings>
      </Profile>
      <Profile name="Only DNS">
        <Settings>
          <Setting name="DNS" id="13" set="True" dhcp="True"/>
        </Settings>
      </Profile>
    </Group>
  </Profiles>
</NetSetMan>
"""


def test_import_netsetman(tmp_path):
    f = tmp_path / "netsetman.xml"
    f.write_text(NETSETMAN_XML, encoding="utf-8")
    store = ProfileStore(tmp_path / "s.json")
    added, replaced, skipped = store.import_from(f)
    assert (added, replaced) == (3, 0)
    assert skipped == ["SRAN FPRx: static IP without an address", "Only DNS: does not change the IP settings"]

    auto, mrbts, office = store.profiles
    assert (auto.adapter, auto.ip_mode, auto.dns_mode) == ("Ethernet", "dhcp", "dhcp")
    assert (mrbts.ip_address, mrbts.prefix_length, mrbts.gateway) == ("192.168.255.130", 25, "")
    assert (mrbts.dns_mode, mrbts.dns_servers) == ("static", [])
    assert "192.168.255.129" in mrbts.notes  # the disabled gateway is kept for reference
    assert (office.adapter, office.gateway, office.dns_servers) == ("LAN", "10.0.0.1", ["10.0.0.1", "1.1.1.1"])


def test_import_rejects_other_xml(tmp_path):
    f = tmp_path / "other.xml"
    f.write_text("<Something/>", encoding="utf-8")
    with pytest.raises(ValidationError, match="not a NetSetMan export"):
        ProfileStore(tmp_path / "s.json").import_from(f)
