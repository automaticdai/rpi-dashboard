"""Wi-Fi signal, storage health and peripherals, read from a fake /sys and /proc tree."""
import struct

import pytest

from rpidash_agent import health


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   38.  -72.  -256        0      0      0     36      0        0
"""


def test_wifi_signal_from_proc(tmp_path):
    write(tmp_path, "proc/net/wireless", WIRELESS)
    write(tmp_path, "proc/net/route", "Iface\tDestination\tGateway\nwlan0\t00000000\t0100A8C0\n")
    w = health.network_link(root=tmp_path)
    assert w == {"interface": "wlan0", "kind": "Wi-Fi", "signal_dbm": -72, "quality_pct": 54, "speed_mbps": None}


def test_wired_link_speed(tmp_path):
    write(tmp_path, "proc/net/route", "Iface\tDestination\tGateway\neth0\t00000000\t0100A8C0\n")
    write(tmp_path, "sys/class/net/eth0/speed", "1000\n")
    assert health.network_link(root=tmp_path) == {"interface": "eth0", "kind": "Ethernet", "signal_dbm": None,
                                                  "quality_pct": None, "speed_mbps": 1000}


def test_no_default_route(tmp_path):
    assert health.network_link(root=tmp_path)["interface"] is None


def smart_log(pct_used=3, crit=0, temp_k=310, media_errors=0, poh=1234, written_units=1_000_000):
    b = bytearray(512)
    b[0] = crit
    b[1:3] = struct.pack("<H", temp_k)
    b[3] = 100           # available spare
    b[5] = pct_used
    b[48:64] = written_units.to_bytes(16, "little")
    b[128:144] = poh.to_bytes(16, "little")
    b[160:176] = media_errors.to_bytes(16, "little")
    return bytes(b)


def test_parse_nvme_smart():
    s = health.parse_nvme_smart(smart_log())
    assert s == {"percent_used": 3, "critical_warning": 0, "temperature_c": 37, "available_spare": 100,
                 "media_errors": 0, "power_on_hours": 1234, "written_gb": pytest.approx(512.0)}


def test_storage_health_emmc_and_errors(tmp_path):
    write(tmp_path, "sys/block/mmcblk0/device/life_time", "0x02 0x01\n")
    write(tmp_path, "sys/block/mmcblk0/device/pre_eol_info", "0x01\n")
    write(tmp_path, "proc/mounts", "/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n")
    h = health.storage_health("/dev/mmcblk0p2", root=tmp_path, kernel_errors=lambda: 2, nvme_smart=lambda dev: None)
    assert h["emmc_life_used_pct"] == (10, 20) and h["emmc_pre_eol"] == "normal"
    assert h["read_only"] is False and h["io_errors"] == 2
    assert health.storage_warnings(h) == ["storage_errors"]


def test_storage_health_read_only_and_worn_nvme(tmp_path):
    write(tmp_path, "proc/mounts", "/dev/nvme0n1p2 / ext4 ro,relatime 0 0\n")
    h = health.storage_health("/dev/nvme0n1p2", root=tmp_path, kernel_errors=lambda: 0,
                              nvme_smart=lambda dev: health.parse_nvme_smart(smart_log(pct_used=85)))
    assert h["read_only"] is True and h["nvme"]["percent_used"] == 85
    assert sorted(health.storage_warnings(h)) == ["storage_readonly", "storage_worn"]


def test_peripherals(tmp_path):
    write(tmp_path, "sys/class/drm/card1-HDMI-A-1/status", "connected\n")
    write(tmp_path, "sys/class/drm/card1-HDMI-A-2/status", "disconnected\n")
    write(tmp_path, "sys/class/drm/card1-Writeback-1/status", "unknown\n")
    write(tmp_path, "sys/bus/i2c/devices/10-0010/name", "imx219\n")
    (tmp_path / "sys/bus/i2c/devices/10-0010/driver").mkdir()                 # bound: really there
    write(tmp_path, "sys/bus/i2c/devices/10-0045/name", "ov5647\n")             # no driver: not present
    write(tmp_path, "sys/bus/i2c/devices/i2c-1/name", "bcm2835 (i2c@7e804000)\n")  # a bus, not a device
    write(tmp_path, "sys/bus/usb/devices/1-1/product", "USB Camera-B4.09.24.1\n")
    write(tmp_path, "sys/bus/usb/devices/1-1/bDeviceClass", "00\n")
    write(tmp_path, "sys/bus/usb/devices/1-1/manufacturer", "OmniVision\n")
    write(tmp_path, "sys/bus/usb/devices/usb1/product", "xHCI Host Controller\n")
    write(tmp_path, "sys/bus/usb/devices/usb1/bDeviceClass", "09\n")             # hub: skipped
    p = health.peripherals(root=tmp_path, list_cameras=lambda: ["imx219 [3280x2464] (/base/soc/i2c0mux/i2c@1/imx219@10)"])
    assert p["displays"] == [{"name": "HDMI-A-1", "connected": True}, {"name": "HDMI-A-2", "connected": False}]
    assert p["i2c"] == [{"address": "10-0010", "name": "imx219"}]
    assert p["usb"] == [{"product": "USB Camera-B4.09.24.1", "manufacturer": "OmniVision"}]
    assert p["cameras"] == ["imx219 [3280x2464]"]


def test_peripherals_cameras_fallback_to_kernel_sensors(tmp_path):
    write(tmp_path, "sys/bus/i2c/devices/10-0010/name", "imx219\n")
    (tmp_path / "sys/bus/i2c/devices/10-0010/driver").mkdir()
    p = health.peripherals(root=tmp_path, list_cameras=lambda: None)
    assert p["cameras"] == ["imx219 (CSI)"]


def test_usb_camera_named_by_its_usb_device_not_its_driver(tmp_path):
    usbdev = tmp_path / "sys/devices/platform/usb3/3-1"
    write(tmp_path, "sys/devices/platform/usb3/3-1/product", "USB Camera-B4.09.24.1\n")
    vdir = usbdev / "3-1:1.0/video4linux/video0"
    write(tmp_path, "sys/devices/platform/usb3/3-1/3-1:1.0/video4linux/video0/name", "gspca main driver\n")
    (tmp_path / "sys/class/video4linux").mkdir(parents=True)
    (tmp_path / "sys/class/video4linux/video0").symlink_to(vdir)
    p = health.peripherals(root=tmp_path, list_cameras=lambda: [])
    assert p["cameras"] == ["USB Camera-B4.09.24.1 (USB)"]


def test_read_only_uses_the_real_system_view_not_the_agents_sandbox(tmp_path):
    # The agent runs with ProtectSystem=strict, so its own /proc/mounts shows / as read-only.
    write(tmp_path, "proc/mounts", "/dev/mmcblk0p2 / ext4 ro,nosuid,noatime 0 0\n")      # sandbox view
    write(tmp_path, "proc/1/mounts", "/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n")           # the real system
    h = health.storage_health("/dev/mmcblk0p2", root=tmp_path, kernel_errors=lambda: 0, nvme_smart=lambda d: None)
    assert h["read_only"] is False
    write(tmp_path, "proc/1/mounts", "/dev/mmcblk0p2 / ext4 ro,noatime 0 0\n")           # really read-only
    h = health.storage_health("/dev/mmcblk0p2", root=tmp_path, kernel_errors=lambda: 0, nvme_smart=lambda d: None)
    assert h["read_only"] is True
