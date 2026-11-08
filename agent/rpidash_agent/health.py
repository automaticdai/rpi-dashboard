"""Network link, storage health and attached hardware, read from /proc and /sys (plus a couple
of read-only tools). Everything here only looks; nothing changes the Pi."""
from __future__ import annotations

import ctypes
import fcntl
import os
import re
import subprocess
from pathlib import Path

ROOT = Path("/")
SENSOR_RE = re.compile(r"^(imx\d+|ov\d+|ar\d+|gc\d+)", re.I)  # camera sensor chips on CSI


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


# ---------- network link ----------
def _default_iface(root: Path) -> str | None:
    text = _read(root / "proc/net/route") or ""
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) >= 2 and f[1] == "00000000":
            return f[0]
    return None


def network_link(root: Path = ROOT) -> dict:
    """The interface carrying the default route, and how good its link is."""
    iface = _default_iface(root)
    out = {"interface": iface, "kind": None, "signal_dbm": None, "quality_pct": None, "speed_mbps": None}
    if iface is None:
        return out
    for line in (_read(root / "proc/net/wireless") or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        if name.strip() == iface:
            f = rest.split()
            out.update(kind="Wi-Fi", quality_pct=round(float(f[1]) / 70 * 100), signal_dbm=int(float(f[2])))
            return out
    out["kind"] = "Wi-Fi" if iface.startswith("wl") else "Ethernet"
    speed = _read(root / "sys/class/net" / iface / "speed")
    if speed and speed.lstrip("-").isdigit() and int(speed) > 0:
        out["speed_mbps"] = int(speed)
    return out


def wifi_dbm(root: Path = ROOT) -> int | None:
    return network_link(root)["signal_dbm"]


# ---------- storage health ----------
NVME_ADMIN_CMD = 0xC0484E41  # _IOWR('N', 0x41, struct nvme_admin_cmd)


class _NvmeAdminCmd(ctypes.Structure):
    _fields_ = [("opcode", ctypes.c_uint8), ("flags", ctypes.c_uint8), ("rsvd1", ctypes.c_uint16),
                ("nsid", ctypes.c_uint32), ("cdw2", ctypes.c_uint32), ("cdw3", ctypes.c_uint32),
                ("metadata", ctypes.c_uint64), ("addr", ctypes.c_uint64), ("metadata_len", ctypes.c_uint32),
                ("data_len", ctypes.c_uint32), ("cdw10", ctypes.c_uint32), ("cdw11", ctypes.c_uint32),
                ("cdw12", ctypes.c_uint32), ("cdw13", ctypes.c_uint32), ("cdw14", ctypes.c_uint32),
                ("cdw15", ctypes.c_uint32), ("timeout_ms", ctypes.c_uint32), ("result", ctypes.c_uint32)]


def parse_nvme_smart(log: bytes) -> dict:
    """The NVMe 'SMART / Health Information' log page (512 bytes)."""
    u = lambda a, b: int.from_bytes(log[a:b], "little")
    return {
        "percent_used": log[5],
        "critical_warning": log[0],
        "temperature_c": u(1, 3) - 273,
        "available_spare": log[3],
        "media_errors": u(160, 176),
        "power_on_hours": u(128, 144),
        "written_gb": u(48, 64) * 512_000 / 1e9,  # data units are 1000 x 512 bytes
    }


def read_nvme_smart(dev: str) -> dict | None:
    """Read the SMART log with the NVMe admin 'Get Log Page' command (root only, read-only)."""
    buf = ctypes.create_string_buffer(512)
    cmd = _NvmeAdminCmd(opcode=0x02, nsid=0xFFFFFFFF, addr=ctypes.addressof(buf), data_len=512,
                        cdw10=(127 << 16) | 0x02)
    try:
        fd = os.open(dev, os.O_RDONLY)
        try:
            fcntl.ioctl(fd, NVME_ADMIN_CMD, cmd)
        finally:
            os.close(fd)
    except OSError:
        return None
    return parse_nvme_smart(buf.raw)


ERROR_RE = re.compile(r"I/O error|Buffer I/O|EXT4-fs error|blk_update_request|mmc\d+: .*(error|timeout)", re.I)


def kernel_storage_errors() -> int | None:
    """Storage-looking errors in this boot's kernel log."""
    try:
        r = subprocess.run(["journalctl", "-k", "-b", "--no-pager", "-q", "-o", "cat"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return sum(1 for line in r.stdout.splitlines() if ERROR_RE.search(line))


_EOL = {"0x01": "normal", "0x02": "warning", "0x03": "urgent"}


def storage_health(root_device: str | None, root: Path = ROOT, kernel_errors=kernel_storage_errors,
                   nvme_smart=read_nvme_smart) -> dict:
    name = Path(root_device or "").name
    out: dict = {"device": root_device, "read_only": None, "io_errors": kernel_errors(),
                 "emmc_life_used_pct": None, "emmc_pre_eol": None, "nvme": None}
    for line in (_read(root / "proc/mounts") or "").splitlines():
        f = line.split()
        if len(f) >= 4 and f[1] == "/":
            out["read_only"] = "ro" in f[3].split(",")
    m = re.match(r"^(mmcblk\d+)", name)
    if m:
        life = _read(root / "sys/block" / m.group(1) / "device/life_time")
        if life:
            n = max(int(x, 16) for x in life.split())  # type A / type B; 0x01 = 0-10 %, 0x0B = past its life
            out["emmc_life_used_pct"] = ((n - 1) * 10, n * 10)
        out["emmc_pre_eol"] = _EOL.get(_read(root / "sys/block" / m.group(1) / "device/pre_eol_info") or "")
    m = re.match(r"^(nvme\d+)", name)
    if m:
        out["nvme"] = nvme_smart(f"/dev/{m.group(1)}")
    return out


def storage_warnings(h: dict) -> list[str]:
    out = []
    if h.get("read_only"):
        out.append("storage_readonly")
    if h.get("io_errors"):
        out.append("storage_errors")
    life, nvme = h.get("emmc_life_used_pct"), h.get("nvme") or {}
    if (life and life[1] >= 80) or h.get("emmc_pre_eol") in ("warning", "urgent") \
            or nvme.get("percent_used", 0) >= 80 or nvme.get("critical_warning"):
        out.append("storage_worn")
    return out


# ---------- peripherals ----------
def list_csi_cameras() -> list[str] | None:
    """CSI cameras as libcamera sees them; None if rpicam-hello isn't available."""
    try:
        r = subprocess.run(["rpicam-hello", "--list-cameras"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return [m.group(1) for m in re.finditer(r"(?m)^\s*\d+ : (.+)$", r.stdout)]


def peripherals(root: Path = ROOT, list_cameras=list_csi_cameras) -> dict:
    displays = []
    for status in sorted((root / "sys/class/drm").glob("card*-*/status")):
        conn = status.parent.name.split("-", 1)[1]
        if conn.startswith("Writeback"):
            continue
        displays.append({"name": conn, "connected": _read(status) == "connected"})

    i2c = []
    for dev in sorted((root / "sys/bus/i2c/devices").glob("*-00*")):
        if (dev / "driver").exists():  # bound to a driver: actually present
            i2c.append({"address": dev.name, "name": _read(dev / "name")})

    usb = []
    for dev in sorted((root / "sys/bus/usb/devices").iterdir()) if (root / "sys/bus/usb/devices").exists() else []:
        product = _read(dev / "product")
        if product and _read(dev / "bDeviceClass") != "09":  # skip hubs
            usb.append({"product": product, "manufacturer": _read(dev / "manufacturer")})

    csi = list_cameras()
    if csi is None:  # no libcamera tools: fall back to sensor chips the kernel bound
        cameras = [f"{d['name']} (CSI)" for d in i2c if SENSOR_RE.match(d["name"] or "")]
    else:
        cameras = [re.sub(r"\s*\(/.*\)\s*$", "", c) for c in csi]
    for v in sorted((root / "sys/class/video4linux").glob("video*")):
        try:
            real = v.resolve()
        except OSError:
            continue
        if "/usb" not in str(real):
            continue
        # Name it after the USB device (its "product"), not the driver ("gspca main driver").
        product = next((_read(d / "product") for d in real.parents if (d / "product").exists()), None)
        name = (product or _read(v / "name") or "").rstrip(":")
        if name and f"{name} (USB)" not in cameras:
            cameras.append(f"{name} (USB)")
    return {"displays": displays, "cameras": cameras, "i2c": i2c, "usb": usb}
