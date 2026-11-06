import subprocess

import pytest

from rpidash_agent.services import Services


class FakeRunner:
    def __init__(self, rc=0, out="active\n", exc=None):
        self.calls, self.rc, self.out, self.exc = [], rc, out, exc

    def __call__(self, cmd):
        self.calls.append(cmd)
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.rc, self.out, "")


def svc(runner=None):
    return Services(["cpu-performance", "user:oled-stats"], user="yfrl", runner=runner or FakeRunner())


def test_system_command():
    assert svc().command("cpu-performance", "restart") == ["systemctl", "restart", "cpu-performance"]


def test_user_command():
    assert svc().command("user:oled-stats", "stop") == ["systemctl", "--user", "-M", "yfrl@", "stop", "oled-stats"]


@pytest.mark.parametrize("entry", ["ssh;rm -rf /", "../reboot", "sshd", "user:sshd", "oled-stats"])
def test_rejects_not_allowlisted(entry):
    r = FakeRunner()
    with pytest.raises(PermissionError):
        svc(r).run(entry, "restart")
    assert r.calls == []


@pytest.mark.parametrize("action", ["enable", "mask", "kill", "restart; reboot"])
def test_rejects_bad_action(action):
    r = FakeRunner()
    with pytest.raises(PermissionError):
        svc(r).run("cpu-performance", action)
    assert r.calls == []


def test_run_ok():
    r = FakeRunner(rc=0, out="")
    assert svc(r).run("cpu-performance", "restart") == (True, "")
    assert r.calls == [["systemctl", "restart", "cpu-performance"]]


def test_states():
    r = FakeRunner(rc=3, out="inactive\n")
    assert svc(r).states() == [{"name": "cpu-performance", "state": "inactive"},
                               {"name": "user:oled-stats", "state": "inactive"}]


def test_states_unknown_on_error():
    r = FakeRunner(exc=subprocess.TimeoutExpired("systemctl", 30))
    assert [x["state"] for x in svc(r).states()] == ["unknown", "unknown"]


def test_reboot():
    r = FakeRunner()
    svc(r).reboot()
    assert r.calls == [["systemctl", "reboot"]]


def test_poweroff():
    r = FakeRunner()
    svc(r).poweroff()
    assert r.calls == [["systemctl", "poweroff"]]
