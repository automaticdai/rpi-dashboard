import pytest

from rpidash_agent.config import load_config


def write(tmp_path, text):
    p = tmp_path / "agent.toml"
    p.write_text(text)
    return p


def test_load_full(tmp_path):
    cfg = load_config(write(tmp_path, 'token="t"\nport=9000\ninterval=5\nservices_allowlist=["cpu-performance","user:oled-stats"]\n'))
    assert cfg.token == "t"
    assert cfg.port == 9000
    assert cfg.interval == 5
    assert cfg.services_allowlist == ["cpu-performance", "user:oled-stats"]
    assert cfg.service_user == "yfrl"


def test_defaults(tmp_path):
    cfg = load_config(write(tmp_path, 'token="t"\n'))
    assert cfg.port == 8765
    assert cfg.interval == 10.0
    assert cfg.services_allowlist == []


def test_missing_token_raises(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, "port=1\n"))


def test_empty_token_raises(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, 'token=""\n'))
