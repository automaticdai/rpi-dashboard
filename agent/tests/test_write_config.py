import stat
import tomllib

import pytest

from rpidash_agent.config import load_config
from rpidash_agent.write_config import main

TOKEN = "Abc_def-123456789012345678901234567890xyz"


def run(tmp_path, token=TOKEN, services="cpu-performance, user:oled-stats", user="yfrl"):
    tf = tmp_path / "token"
    tf.write_text(token + "\n")
    out = tmp_path / "agent.toml"
    main(["--token-file", str(tf), "--services", services, "--user", user, "--out", str(out)])
    return out


def test_writes_loadable_private_config(tmp_path):
    out = run(tmp_path)
    cfg = load_config(out)
    assert cfg.token == TOKEN
    assert cfg.services_allowlist == ["cpu-performance", "user:oled-stats"]
    assert cfg.service_user == "yfrl"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_empty_services(tmp_path):
    assert tomllib.loads(run(tmp_path, services="").read_text())["services_allowlist"] == []


@pytest.mark.parametrize("bad", ['short', 'has"quote' + "x" * 20, "has space" + "x" * 20, "x" * 20 + "\\"])
def test_rejects_bad_token(tmp_path, bad):
    with pytest.raises(SystemExit):
        run(tmp_path, token=bad)
    assert not (tmp_path / "agent.toml").exists()


@pytest.mark.parametrize("bad", ['evil"]\ntoken="x', "a b", "../x", "user:"])
def test_rejects_bad_service(tmp_path, bad):
    with pytest.raises(SystemExit):
        run(tmp_path, services=bad)


def test_rejects_bad_user(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, user='yfrl"\nx')


def test_refuses_to_overwrite(tmp_path):
    out = run(tmp_path)
    with pytest.raises(SystemExit):
        main(["--token-file", str(tmp_path / "token"), "--services", "", "--user", "yfrl", "--out", str(out)])
