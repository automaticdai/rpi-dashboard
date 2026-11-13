import subprocess
from pathlib import Path
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


def test_read_disk_bytes_counts_physical_disks_only(tmp_path):
    from rpidash_agent.collect import read_disk_bytes
    stat = "100 0 {r} 0 50 0 {w} 0 0 0 0\n"
    for name, r, w in [("mmcblk0", 10, 20), ("nvme0n1", 1, 2), ("loop0", 999, 999), ("zram0", 999, 999)]:
        d = tmp_path / "sys/block" / name
        d.mkdir(parents=True)
        (d / "stat").write_text(stat.format(r=r, w=w))
    assert read_disk_bytes(tmp_path) == (11 * 512, 22 * 512)


def test_disk_rates(monkeypatch):
    counters = [(0, 0)]
    monkeypatch.setattr(collect, "read_disk_bytes", lambda: counters[-1])
    c = Collector()
    assert c.sample(100.0).disk_read_bps == 0.0
    counters.append((5000, 20_000))
    s = c.sample(110.0)
    assert (s.disk_read_bps, s.disk_write_bps) == (500.0, 2000.0)


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


def test_top_processes_tolerates_concurrent_cache_removal(monkeypatch):
    class Racy(dict):
        def __iter__(self):  # another thread already removed pid 999999
            return iter(list(super().keys()) + [999999])

    monkeypatch.setattr(collect, "_proc_cache", Racy())
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([]))
    assert collect.top_processes() == []


def test_read_fan_rpm(tmp_path):
    from rpidash_agent.collect import read_fan_rpm
    assert read_fan_rpm(root=tmp_path) is None
    d = tmp_path / "sys/class/hwmon/hwmon3"
    d.mkdir(parents=True)
    (d / "fan1_input").write_text("1585\n")
    assert read_fan_rpm(root=tmp_path) == 1585


def test_sample_has_fan_field():
    assert "fan_rpm" in collect.FIELDS


CT = namedtuple("CT", "user nice system idle iowait irq softirq steal guest guest_nice")


def test_cpu_percent_from_own_deltas_not_psutil_thread_state(monkeypatch):
    import threading
    seq = iter([CT(100, 0, 100, 800, 0, 0, 0, 0, 0, 0),    # since boot: 20% busy
                CT(150, 0, 150, 900, 0, 0, 0, 0, 0, 0)])   # +100 busy, +100 idle -> 50%
    monkeypatch.setattr(psutil, "cpu_times", lambda: next(seq))
    monkeypatch.setattr(psutil, "cpu_percent", lambda *a, **k: (_ for _ in ()).throw(AssertionError("not used")))
    c = Collector()
    out = {}
    t = threading.Thread(target=lambda: out.update(a=c.cpu_pct()))
    t.start(); t.join()
    assert out["a"] == pytest.approx(50.0)  # a brand-new thread still sees the real delta


@pytest.mark.parametrize("device,setup,expected", [
    ("/dev/mmcblk0p2", {"mmcblk0/device/type": "SD"}, "SD"),
    ("/dev/mmcblk0p2", {"mmcblk0/device/type": "MMC"}, "eMMC"),
    ("/dev/nvme0n1p2", {}, "NVMe"),
    ("/dev/sda2", {"sda/link": "../devices/platform/usb1/1-1/host0/block/sda"}, "USB"),
    ("/dev/sda2", {"sda/link": "../devices/platform/ahci/ata1/host0/block/sda"}, "SATA"),
    ("/dev/root", {}, None),
    (None, {}, None),
])
def test_storage_type(tmp_path, device, setup, expected):
    from rpidash_agent.collect import storage_type
    blk = tmp_path / "sys/block"
    for rel, value in setup.items():
        if rel.endswith("/link"):
            name = rel.split("/")[0]
            target = tmp_path / "sys" / value.replace("../", "")
            target.mkdir(parents=True)
            blk.mkdir(parents=True, exist_ok=True)
            (blk / name).symlink_to(target)
        else:
            f = blk / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(value + "\n")
    assert storage_type(device, root=tmp_path) == expected


def test_spec_has_storage_type():
    assert "storage" in read_spec()


PMIC = (Path(__file__).parent / "data" / "pmic_read_adc_pi5.txt").read_text()


def test_parse_pmic_sums_rail_power():
    from rpidash_agent.collect import parse_pmic
    w = parse_pmic(PMIC)
    assert 0.5 < w < 5.0          # a real Pi 5 at idle: ~1.5 W across its rails
    text = "  VDD_CORE_A current(7)=2.00000000A\n  VDD_CORE_V volt(15)=0.80000000V\n" \
           "  3V3_SYS_A current(4)=0.10000000A\n  3V3_SYS_V volt(12)=3.30000000V\n" \
           "  EXT5V_V volt(24)=5.10000000V\n"   # EXT5V has a voltage but no current: not a rail
    assert parse_pmic(text) == pytest.approx(2.0 * 0.8 + 0.1 * 3.3)
    assert parse_pmic("Can't open device file: /dev/vcio") is None
    assert parse_pmic("") is None


def test_read_power_gives_up_on_boards_without_a_pmic(monkeypatch):
    from rpidash_agent import collect
    calls = []
    monkeypatch.setattr(collect, "_vcgencmd", lambda *a: calls.append(a) or "error=1 error_msg=\"Command not registered\"")
    c = Collector()
    assert c.read_power() is None and c.read_power() is None
    assert len(calls) == 1   # not asked again every sample


def test_sample_has_power_field():
    assert "power_w" in collect.FIELDS


def test_sample_has_wifi_field():
    assert "wifi_dbm" in collect.FIELDS
