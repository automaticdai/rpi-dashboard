from rpidash_dashboard.config import DashboardConfig
from rpidash_dashboard.status import health_warnings

CFG = DashboardConfig(token="t", registry_path="pis.json")


def s(**kw):
    base = {"temp_c": 40.0, "disk_pct": 10.0, "mem_used": 1, "mem_total": 10, "throttled": 0}
    base.update(kw)
    return base


def test_status_warnings():
    assert health_warnings(s(temp_c=70), CFG) == ["hot"]
    assert health_warnings(s(disk_pct=89.9), CFG) == []
    assert health_warnings(s(disk_pct=90), CFG) == ["disk"]
    assert health_warnings(s(mem_used=9, mem_total=10), CFG) == ["mem"]
    assert health_warnings(s(throttled=None, temp_c=None), CFG) == []
    assert health_warnings(s(throttled=0x4), CFG) == ["throttled"]
    assert health_warnings(None, CFG) == []
    assert health_warnings(s(mem_total=0), CFG) == []
