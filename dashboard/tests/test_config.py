import pytest

from rpidash_dashboard.config import load_config


def test_defaults(tmp_path):
    p = tmp_path / "dashboard.toml"
    p.write_text('token="t"\n')
    cfg = load_config(p)
    assert cfg.token == "t"
    assert cfg.subnet == "192.168.0.0/24"
    assert cfg.agent_port == 8765
    assert cfg.registry_path == tmp_path / "pis.json"
    assert (cfg.warn_temp_c, cfg.warn_disk_pct, cfg.warn_mem_pct) == (70, 90, 90)


def test_overrides(tmp_path):
    p = tmp_path / "dashboard.toml"
    p.write_text('token="t"\nsubnet="10.0.0.0/28"\nregistry_path="/x/pis.json"\nwarn_temp_c=65\n')
    cfg = load_config(p)
    assert cfg.subnet == "10.0.0.0/28" and str(cfg.registry_path) == "/x/pis.json" and cfg.warn_temp_c == 65


def test_missing_token(tmp_path):
    p = tmp_path / "dashboard.toml"
    p.write_text("subnet='x'\n")
    with pytest.raises(ValueError):
        load_config(p)
