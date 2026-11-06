import subprocess
from collections import namedtuple

import psutil
import pytest

from rpidash_agent import collect
from rpidash_agent.collect import (
    Collector, machine_id, parse_throttled, read_spec, read_temperature, read_throttled,
)

Nic = namedtuple("Nic", "bytes_sent bytes_recv")


def no_vcgencmd(*a, **k):
    raise FileNotFoundError("vcgencmd")


def test_parse_throttled():
    assert parse_throttled("throttled=0x50005\n") == 0x50005
    assert parse_throttled("throttled=0x0") == 0
    assert parse_throttled("garbage") is None


def test_read_temperature_sysfs(tmp_path):
    for i, v in enumerate(["48000", "51500"]):
        d = tmp_path / f"sys/class/thermal/thermal_zone{i}"
        d.mkdir(parents=True)
        (d / "temp").write_text(v + "\n")
    assert read_temperature(root=tmp_path) == 51.5


def test_read_temperature_none(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", no_vcgencmd)
    assert read_temperature(root=tmp_path) is None


def test_read_throttled_missing_vcgencmd(monkeypatch):
    monkeypatch.setattr(subprocess, "run", no_vcgencmd)
    assert read_throttled() is None


def test_net_rates(monkeypatch):
    state = {"lo": Nic(0, 0), "eth0": Nic(0, 50_000)}
    monkeypatch.setattr(psutil, "net_io_counters", lambda pernic=False: dict(state))
    c = Collector()
    assert c.sample(100.0).net_rx_bps == 0.0
    state["eth0"] = Nic(0, 60_000)
    state["lo"] = Nic(999_999, 999_999)
    s = c.sample(110.0)
    assert s.net_rx_bps == 1000.0
    assert s.net_tx_bps == 0.0
    state["eth0"] = Nic(0, 10)  # counter reset
    assert c.sample(120.0).net_rx_bps == 0.0


def test_machine_id(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/machine-id").write_text("abc\n")
    assert machine_id(root=tmp_path) == "abc"


def test_read_spec_keys():
    spec = read_spec()
    for k in ["model", "serial", "cpu", "cores", "ram_total", "root_device", "root_size", "os",
              "kernel", "arch", "hostname", "interfaces", "agent_version", "boot_time"]:
        assert k in spec, k
    assert spec["cores"] >= 1
