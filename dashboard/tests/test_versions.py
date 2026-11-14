from rpidash_agent import __version__ as agent_version

from rpidash_dashboard import EXPECTED_AGENT_VERSION, __version__ as dashboard_version
from rpidash_dashboard.config import DashboardConfig
from rpidash_dashboard.status import health_warnings


def test_versions_move_together():
    assert agent_version == dashboard_version == EXPECTED_AGENT_VERSION
    assert agent_version != "0.1.0"


def test_outdated_agent_is_a_warning():
    cfg = DashboardConfig(token="t", registry_path="pis.json")
    sample = {"temp_c": 40.0, "disk_pct": 10.0, "mem_used": 1, "mem_total": 10, "throttled": 0}
    assert health_warnings(sample, cfg, agent_version=EXPECTED_AGENT_VERSION) == []
    assert health_warnings(sample, cfg, agent_version="0.1.0") == ["agent_outdated"]
    assert health_warnings(sample, cfg, agent_version=None) == ["agent_outdated"]  # too old to say


def test_pico_agent_is_checked_against_its_own_version():
    import re
    from pathlib import Path
    from rpidash_dashboard import EXPECTED_PICO_VERSION
    cfg = DashboardConfig(token="t", registry_path="pis.json")
    sample = {"temp_c": 30.0, "disk_pct": 1.0, "mem_used": 1, "mem_total": 10, "throttled": None}
    assert health_warnings(sample, cfg, agent_version=EXPECTED_PICO_VERSION) == []
    assert health_warnings(sample, cfg, agent_version="pico-0.0.1") == ["agent_outdated"]
    # the Pico's own code reports the version the dashboard expects
    main = (Path(__file__).parents[2] / "pico" / "main.py").read_text()
    assert re.search(r'^VERSION = "([^"]+)"', main, re.M).group(1) == EXPECTED_PICO_VERSION
