"""Metric and hardware-spec collection for the agent."""
from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
from dataclasses import dataclass, fields
from pathlib import Path

import psutil

from . import __version__

ROOT = Path("/")


@dataclass(frozen=True)
class Sample:
    ts: float
    cpu_pct: float
    load1: float
    mem_used: int
    mem_total: int
    swap_used: int
    disk_pct: float
    net_rx_bps: float
    net_tx_bps: float
    temp_c: float | None
    throttled: int | None
    fan_rpm: int | None = None
    power_w: float | None = None  # Pi 5 / CM5 only: sum of the PMIC's measured supply rails
    wifi_dbm: float | None = None  # signal of the Wi-Fi link carrying the default route
    disk_read_bps: float = 0.0  # bytes/s read from / written to whole block devices (SD, eMMC, NVMe, USB)
    disk_write_bps: float = 0.0


FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Sample))


def _vcgencmd(*args: str) -> str | None:
    try:
        r = subprocess.run(["vcgencmd", *args], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def read_temperature(root: Path = ROOT) -> float | None:
    temps = []
    for f in sorted((root / "sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            temps.append(int(f.read_text().strip()) / 1000)
        except (OSError, ValueError):
            continue
    if temps:
        return max(temps)
    out = _vcgencmd("measure_temp")
    m = re.search(r"temp=([\d.]+)", out or "")
    return float(m.group(1)) if m else None


def read_fan_rpm(root: Path = ROOT) -> int | None:
    """Fastest fan reported by hwmon (e.g. the CM5/Pi 5 `pwmfan`); None if there's no fan sensor."""
    speeds = []
    for f in (root / "sys/class/hwmon").glob("hwmon*/fan*_input"):
        try:
            speeds.append(int(f.read_text().strip()))
        except (OSError, ValueError):
            continue
    return max(speeds) if speeds else None


def _wifi_dbm() -> float | None:
    from .health import wifi_dbm  # local import: health imports nothing from here
    try:
        return wifi_dbm()
    except (OSError, ValueError, IndexError):
        return None


def parse_pmic(text: str) -> float | None:
    """Total power (W) of the rails `vcgencmd pmic_read_adc` reports both a current and a voltage
    for (Pi 5 / CM5). Loads fed straight from the 5 V input (USB, NVMe, fan) aren't included."""
    amps = {m[1]: float(m[2]) for m in re.finditer(r"(?m)^\s*(\S+)_A current\(\d+\)=([\d.]+)A", text or "")}
    volts = {m[1]: float(m[2]) for m in re.finditer(r"(?m)^\s*(\S+)_V volt\(\d+\)=([\d.]+)V", text or "")}
    rails = [amps[k] * volts[k] for k in amps if k in volts]
    return sum(rails) if rails else None


def parse_throttled(text: str) -> int | None:
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", text)
    return int(m.group(1), 16) if m else None


def read_throttled() -> int | None:
    out = _vcgencmd("get_throttled")
    return parse_throttled(out) if out else None


def machine_id(root: Path = ROOT) -> str:
    return (root / "etc/machine-id").read_text().strip()


def _read(path: Path) -> str | None:
    try:
        return path.read_text().replace("\x00", "").strip()
    except OSError:
        return None


def _os_name(root: Path) -> str:
    text = _read(root / "etc/os-release") or ""
    m = re.search(r'^PRETTY_NAME="?([^"\n]*)"?', text, re.M)
    return m.group(1) if m else platform.system()


def _cpu_name(root: Path) -> str:
    text = _read(root / "proc/cpuinfo") or ""
    for key in ("model name", "Model", "Hardware"):
        m = re.search(rf"^{key}\s*:\s*(.+)$", text, re.M)
        if m:
            return m.group(1).strip()
    return platform.processor() or platform.machine()


def _serial(root: Path) -> str | None:
    s = _read(root / "proc/device-tree/serial-number")
    if s:
        return s
    m = re.search(r"^Serial\s*:\s*(\S+)", _read(root / "proc/cpuinfo") or "", re.M)
    return m.group(1) if m else None


def _interfaces() -> list[dict]:
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        if name == "lo":
            continue
        ipv4 = next((a.address for a in addrs if a.family == socket.AF_INET), None)
        mac = next((a.address for a in addrs if a.family == psutil.AF_LINK), None)
        if ipv4 or mac:
            out.append({"name": name, "ipv4": ipv4, "mac": mac})
    return out


def storage_type(device: str | None, root: Path = ROOT) -> str | None:
    """What the root filesystem lives on: "SD", "eMMC", "NVMe", "USB" or "SATA" (None if unknown)."""
    if not device:
        return None
    name = Path(device).name                                   # mmcblk0p2, nvme0n1p2, sda2
    m = re.match(r"^(mmcblk\d+|nvme\d+n\d+|sd[a-z]+)", name)
    if not m:
        return None
    disk = m.group(1)
    if disk.startswith("nvme"):
        return "NVMe"
    if disk.startswith("mmcblk"):  # SD cards and eMMC both show up as mmcblk; the kernel knows which
        kind = _read(root / "sys/block" / disk / "device/type")
        return {"SD": "SD", "MMC": "eMMC"}.get(kind or "", "SD/eMMC")
    try:
        path = str((root / "sys/block" / disk).resolve())
    except OSError:
        return None
    return "USB" if "/usb" in path else "SATA"


def read_spec(root: Path = ROOT) -> dict:
    root_part = next((p for p in psutil.disk_partitions(all=False) if p.mountpoint == "/"), None)
    return {
        "model": _read(root / "proc/device-tree/model") or platform.machine(),
        "serial": _serial(root),
        "cpu": _cpu_name(root),
        "cores": psutil.cpu_count() or 1,
        "ram_total": psutil.virtual_memory().total,
        "root_device": root_part.device if root_part else None,
        "storage": storage_type(root_part.device if root_part else None, root),
        "root_size": psutil.disk_usage("/").total,
        "os": _os_name(root),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "interfaces": _interfaces(),
        "agent_version": __version__,
        "boot_time": psutil.boot_time(),
    }


_proc_cache: dict[int, psutil.Process] = {}


def top_processes(n: int = 10) -> list[dict]:
    """Top processes by CPU since the previous call (cached Process objects)."""
    seen, rows = set(), []
    for p in psutil.process_iter(["pid", "name"]):
        pid = p.info["pid"]
        seen.add(pid)
        proc = _proc_cache.setdefault(pid, p)
        try:
            rows.append({
                "pid": pid,
                "name": p.info["name"],
                "cpu_pct": proc.cpu_percent(None),
                "mem_pct": round(proc.memory_percent(), 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for pid in list(_proc_cache):
        if pid not in seen:
            _proc_cache.pop(pid, None)  # another request thread may have removed it already
    rows.sort(key=lambda r: r["cpu_pct"], reverse=True)
    return rows[:n]


_VIRTUAL_DISK = re.compile(r"^(loop|ram|zram|dm-|md|sr|fd)")


def read_disk_bytes(root: Path = ROOT) -> tuple[int, int]:
    """Total bytes read and written by the physical disks since boot (/sys/block/*/stat counts
    512-byte sectors; partitions aren't listed there, so nothing is counted twice)."""
    rd = wr = 0
    for stat in (root / "sys/block").glob("*/stat"):
        if _VIRTUAL_DISK.match(stat.parent.name):
            continue
        try:
            f = stat.read_text().split()
            rd += int(f[2]) * 512
            wr += int(f[6]) * 512
        except (OSError, ValueError, IndexError):
            continue
    return rd, wr


class Collector:
    def __init__(self) -> None:
        self._prev_net: tuple[float, int, int] | None = None
        self._prev_disk: tuple[float, int, int] | None = None
        self._has_pmic: bool | None = None  # learned on the first read
        # CPU % is worked out here from cpu_times() deltas. psutil.cpu_percent(None) keeps its
        # baseline per thread, and the HTTP server answers each request on a new thread.
        self._prev_cpu = self._cpu_counters()

    @staticmethod
    def _cpu_counters() -> tuple[float, float]:
        t = psutil.cpu_times()
        idle = t.idle + getattr(t, "iowait", 0.0)
        return sum(t), idle

    def read_power(self) -> float | None:
        if self._has_pmic is False:
            return None  # this board has no PMIC readout: don't spawn vcgencmd every sample
        w = parse_pmic(_vcgencmd("pmic_read_adc") or "")
        if self._has_pmic is None:
            self._has_pmic = w is not None
        return w

    def cpu_pct(self) -> float:
        total, idle = self._cpu_counters()
        prev_total, prev_idle = self._prev_cpu
        self._prev_cpu = (total, idle)
        dt = total - prev_total
        if dt <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1 - (idle - prev_idle) / dt)))

    def _net(self, now: float) -> tuple[float, float]:
        rx = tx = 0
        for name, c in psutil.net_io_counters(pernic=True).items():
            if name == "lo":
                continue
            rx += c.bytes_recv
            tx += c.bytes_sent
        prev, self._prev_net = self._prev_net, (now, rx, tx)
        if prev is None or now <= prev[0]:
            return 0.0, 0.0
        dt = now - prev[0]
        return max(0.0, (rx - prev[1]) / dt), max(0.0, (tx - prev[2]) / dt)

    def _disk(self, now: float) -> tuple[float, float]:
        rd, wr = read_disk_bytes()
        prev, self._prev_disk = self._prev_disk, (now, rd, wr)
        if prev is None or now <= prev[0]:
            return 0.0, 0.0
        dt = now - prev[0]
        return max(0.0, (rd - prev[1]) / dt), max(0.0, (wr - prev[2]) / dt)

    def sample(self, now: float) -> Sample:
        vm = psutil.virtual_memory()
        rx, tx = self._net(now)
        drd, dwr = self._disk(now)
        return Sample(
            ts=now,
            cpu_pct=self.cpu_pct(),
            load1=os.getloadavg()[0],
            mem_used=vm.total - vm.available,
            mem_total=vm.total,
            swap_used=psutil.swap_memory().used,
            disk_pct=psutil.disk_usage("/").percent,
            net_rx_bps=rx,
            net_tx_bps=tx,
            temp_c=read_temperature(),
            throttled=read_throttled(),
            fan_rpm=read_fan_rpm(),
            power_w=self.read_power(),
            wifi_dbm=_wifi_dbm(),
            disk_read_bps=drd,
            disk_write_bps=dwr,
        )
