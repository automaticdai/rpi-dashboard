"""deploy.sh, run against stand-in ssh/rsync/scp commands that log what they were asked to do."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from rpidash_agent import __version__

REPO = Path(__file__).resolve().parents[1]

STUB = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
host="$1"; [[ $host == -* ]] && host="$2"
case "$(basename "$0")" in
  ssh)
    for a in "$@"; do last="$a"; done
    [[ " $STUB_DOWN " == *" ${@: -2:1} "* || " $STUB_DOWN " == *" $host "* ]] && exit 255
    if [[ $last == *"test -f /etc/rpidash-agent.toml"* ]]; then [[ " $STUB_FRESH " == *" ${@: -2:1} "* ]] && exit 1; exit 0; fi
    if [[ $last == *"install.sh"* ]]; then [[ " $STUB_INSTALL_FAILS " == *" ${@: -2:1} "* ]] && exit 1; exit 0; fi
    if [[ $last == *"8765/health"* ]]; then echo "${STUB_VERSION:-@AGENT_VERSION@}"; exit 0; fi
    ;;
esac
exit 0
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ssh", "rsync", "scp"):
        f = bin_dir / name
        f.write_text(STUB.replace("@AGENT_VERSION@", __version__))
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    token = tmp_path / "token"
    token.write_text("t" * 32)
    reg = tmp_path / "pis.json"
    reg.write_text(json.dumps({"pis": [{"hostname": "rpi-a"}, {"hostname": "rpi-b"}]}))
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(tmp_path / "log"),
            "RPIDASH_TOKEN_FILE": str(token), "RPIDASH_REGISTRY": str(reg)}


def deploy(env, *args, **extra):
    r = subprocess.run([str(REPO / "deploy.sh"), *args], env={**env, **extra}, capture_output=True, text=True,
                       timeout=30)
    log = Path(env["STUB_LOG"]).read_text().splitlines() if Path(env["STUB_LOG"]).exists() else []
    return r, log


def test_upgrade_every_pi_in_pis_json(env):
    r, log = deploy(env)
    assert r.returncode == 0, r.stderr
    assert [l.split()[0] for l in log if l.startswith("rsync")] == ["rsync", "rsync"]
    assert any("rpi-a:~/rpidash-agent/" in l for l in log) and any("rpi-b:~/rpidash-agent/" in l for l in log)
    assert not [l for l in log if l.startswith("scp")]  # configs exist: no token sent
    assert sum("sudo" in l and "install.sh" in l for l in log) == 2
    assert "rpi-a" in r.stdout and "ok" in r.stdout and __version__ in r.stdout


def test_named_hosts_only(env):
    r, log = deploy(env, "rpi-b")
    assert r.returncode == 0
    assert not any("rpi-a" in l for l in log)


def test_first_install_sends_token_and_services(env):
    r, log = deploy(env, "--services", "cpu-performance,user:oled-stats", "rpi-a", STUB_FRESH="rpi-a")
    assert r.returncode == 0, r.stderr
    scp = [l for l in log if l.startswith("scp")]
    assert len(scp) == 1 and "rpi-a:~/rpidash-agent/token" in scp[0]
    install = next(l for l in log if "install.sh" in l)
    assert "RPIDASH_SERVICES='cpu-performance,user:oled-stats'" in install
    assert ("t" * 32) not in "\n".join(log)  # the token never appears on a command line


def test_one_failure_does_not_stop_the_rest(env):
    r, log = deploy(env, STUB_INSTALL_FAILS="rpi-a")
    assert r.returncode == 1
    assert any("rpi-b" in l and "install.sh" in l for l in log)
    assert "rpi-a" in r.stdout and "FAILED" in r.stdout


def test_unreachable_pi_reported(env):
    r, _ = deploy(env, "rpi-b", STUB_DOWN="rpi-b")
    assert r.returncode == 1 and "FAILED" in r.stdout


def test_wrong_version_after_install_is_a_failure(env):
    r, _ = deploy(env, "rpi-a", STUB_VERSION="0.0.9")
    assert r.returncode == 1 and "0.0.9" in r.stdout


def test_bad_service_list_rejected_before_touching_any_pi(env):
    r, log = deploy(env, "--services", "evil';reboot", "rpi-a")
    assert r.returncode == 2 and log == []
