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


def read_spec(root: Path = ROOT) -> dict:
    root_part = next((p for p in psutil.disk_partitions(all=False) if p.mountpoint == "/"), None)
    return {
        "model": _read(root / "proc/device-tree/model") or platform.machine(),
        "serial": _serial(root),
        "cpu": _cpu_name(root),
        "cores": psutil.cpu_count() or 1,
        "ram_total": psutil.virtual_memory().total,
        "root_device": root_part.device if root_part else None,
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


class Collector:
    def __init__(self) -> None:
        self._prev_net: tuple[float, int, int] | None = None
        psutil.cpu_percent(None)  # prime the CPU counter

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

    def sample(self, now: float) -> Sample:
        vm = psutil.virtual_memory()
        rx, tx = self._net(now)
        return Sample(
            ts=now,
            cpu_pct=psutil.cpu_percent(None),
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
        )
