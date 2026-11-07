import subprocess

import pytest

from rpidash_agent.stress import Stress


class Runner:
    def __init__(self, active=()):
        self.calls, self.active = [], set(active)

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[:2] == ["systemctl", "is-active"]:
            st = "active" if cmd[2] in self.active else "inactive"
            return subprocess.CompletedProcess(cmd, 0 if st == "active" else 3, st + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def stress(runner):
    return Stress(binary="/opt/rpidash-agent/bin/rpibench", python="/opt/rpidash-agent/venv/bin/python",
                  cores=4, runner=runner)


def test_start_cpu_and_ram_for_5_minutes():
    r = Runner()
    stress(r).start(cpu=True, ram=True, net=False, minutes=5)
    runs = [c for c in r.calls if c[0] == "systemd-run"]
    assert len(runs) == 2
    cpu = next(c for c in runs if "--unit=rpidash-stress-cpu" in c)
    assert "--property=RuntimeMaxSec=300" in cpu and "--nice=19" in cpu
    assert cpu[-3:] == ["/opt/rpidash-agent/bin/rpibench", "burn", "4"]
    ram = next(c for c in runs if "--unit=rpidash-stress-ram" in c)
    assert ram[-3:] == ["-m", "rpidash_agent.stress_load", "ram"]
    assert ram[ram.index("/opt/rpidash-agent/venv/bin/python") - 1] == "--"


def test_restart_stops_previous_run_first():
    r = Runner()
    stress(r).start(cpu=True, ram=False, net=False, minutes=1)
    stop_i = r.calls.index(["systemctl", "stop", "rpidash-stress-cpu"])
    run_i = next(i for i, c in enumerate(r.calls) if c[0] == "systemd-run")
    assert stop_i < run_i


@pytest.mark.parametrize("kw", [dict(cpu=False, ram=False, net=False, minutes=5),
                                dict(cpu=True, ram=False, net=False, minutes=0),
                                dict(cpu=True, ram=False, net=False, minutes=31)])
def test_invalid_requests(kw):
    r = Runner()
    with pytest.raises(ValueError):
        stress(r).start(**kw)
    assert r.calls == []


def test_stop_and_status():
    r = Runner(active={"rpidash-stress-net"})
    s = stress(r)
    assert s.status() == {"cpu": False, "ram": False, "net": True}
    s.stop()
    assert {tuple(c) for c in r.calls if c[1] == "stop"} == {
        ("systemctl", "stop", "rpidash-stress-cpu"), ("systemctl", "stop", "rpidash-stress-ram"),
        ("systemctl", "stop", "rpidash-stress-net")}
