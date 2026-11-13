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
    assert s.status() == {"cpu": False, "ram": False, "disk": False, "net": True}
    s.stop()
    assert {tuple(c) for c in r.calls if c[1] == "stop"} == {
        ("systemctl", "stop", "rpidash-stress-cpu"), ("systemctl", "stop", "rpidash-stress-ram"),
        ("systemctl", "stop", "rpidash-stress-disk"), ("systemctl", "stop", "rpidash-stress-net")}



def test_net_load_rotates_mirrors_and_backs_off(monkeypatch):
    from rpidash_agent import stress_load
    fetched, sleeps = [], []

    def fetch(url):
        fetched.append(url)
        if len(fetched) >= 8:
            raise KeyboardInterrupt  # end the endless loop for the test
        if "fail" in url:
            raise OSError("connection refused")

    urls = ["https://a.example/big", "https://fail.example/big", "https://c.example/big"]
    monkeypatch.setattr(stress_load, "MIRROR_URLS", urls)
    monkeypatch.setattr(stress_load, "fetch", fetch)
    monkeypatch.setattr(stress_load.time, "sleep", sleeps.append)
    with pytest.raises(KeyboardInterrupt):
        stress_load.pull_net()
    assert fetched[:4] == urls + [urls[0]]  # round-robin, a failure moves on to the next mirror
    assert sleeps == []  # one failure among working mirrors needs no back-off


def test_net_load_backs_off_when_every_mirror_fails(monkeypatch):
    from rpidash_agent import stress_load
    fetched, sleeps = [], []

    def fetch(url):
        fetched.append(url)
        if len(fetched) >= 7:
            raise KeyboardInterrupt
        raise OSError("offline")

    monkeypatch.setattr(stress_load, "MIRROR_URLS", ["https://a.example/x", "https://b.example/x"])
    monkeypatch.setattr(stress_load, "fetch", fetch)
    monkeypatch.setattr(stress_load.time, "sleep", sleeps.append)
    with pytest.raises(KeyboardInterrupt):
        stress_load.pull_net()
    assert sleeps == [30, 30, 30]  # a full round of failures -> wait before trying again


def test_mirror_urls_are_https_package_mirrors():
    from rpidash_agent.stress_load import MIRROR_URLS
    assert len(MIRROR_URLS) >= 3 and all(u.startswith("https://") for u in MIRROR_URLS)
    assert not any("cloudflare" in u for u in MIRROR_URLS)


def test_stress_units_are_sandboxed():
    r = Runner()
    stress(r).start(cpu=True, ram=True, net=True, disk=True, minutes=1)
    assert len([c for c in r.calls if c[0] == "systemd-run"]) == 4
    for c in (c for c in r.calls if c[0] == "systemd-run"):
        for prop in ("--property=DynamicUser=yes", "--property=ProtectSystem=strict",
                     "--property=ProtectHome=yes", "--property=NoNewPrivileges=yes"):
            assert prop in c, (prop, c)


def test_failed_start_is_reported():
    from rpidash_agent.stress import StressError

    class Failing(Runner):
        def __call__(self, cmd):
            if cmd[0] == "systemd-run" and "--unit=rpidash-stress-cpu" in cmd:
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 1, "", "Failed to find executable rpibench")
            return super().__call__(cmd)

    with pytest.raises(StressError, match="cpu.*rpibench"):
        stress(Failing()).start(cpu=True, ram=True, net=False, minutes=1)


def test_runner_exceptions_become_stress_errors():
    from rpidash_agent.stress import StressError

    def boom(cmd):
        raise subprocess.TimeoutExpired(cmd, 30)

    with pytest.raises(StressError):
        stress(boom).start(cpu=True, ram=False, net=False, minutes=1)
    assert stress(boom).status() == {"cpu": False, "ram": False, "disk": False, "net": False}


def test_disk_unit_gets_a_cache_dir_and_ram_unit_dies_first():
    r = Runner()
    stress(r).start(cpu=False, ram=True, net=False, disk=True, minutes=2)
    runs = [c for c in r.calls if c[0] == "systemd-run"]
    disk = next(c for c in runs if "--unit=rpidash-stress-disk" in c)
    assert "--property=CacheDirectory=rpidash-stress" in disk and disk[-1] == "disk"
    ram = next(c for c in runs if "--unit=rpidash-stress-ram" in c)
    assert "--property=OOMScoreAdjust=1000" in ram
    assert "--property=CacheDirectory=rpidash-stress" not in ram


def test_ram_plan_holds_70_percent_across_workers():
    from rpidash_agent.stress_load import CHUNK, ram_plan
    avail = 4 * 1024 * CHUNK // 16  # 4 GiB
    workers, per = ram_plan(avail, cores=4)
    assert workers == 4
    assert 0.65 < workers * per * CHUNK / avail <= 0.70
    assert ram_plan(avail, cores=1)[0] == 1
    workers, per = ram_plan(10 * CHUNK, cores=4)  # tiny box: never more than 70%, still 2+ blocks to copy between
    assert per >= 2 and workers * per <= 7


def test_sweep_rewrites_every_block():
    from rpidash_agent.stress_load import sweep
    blocks = [bytearray(b"\x01" * 64), bytearray(64), bytearray(64)]
    sweep(blocks, rounds=1)
    assert all(b[1:] == b"\x01" * 63 for b in blocks)  # the data went all the way round
    assert blocks[0][0] == 2  # and changed, so the next pass writes new values


def test_disk_file_size_is_capped():
    from rpidash_agent.stress_load import DISK_FILE_MAX, MiB, disk_file_size
    assert disk_file_size(100 * 1024 * MiB) == DISK_FILE_MAX
    assert disk_file_size(1000 * MiB) == 100 * MiB  # 10% of free space
    with pytest.raises(SystemExit):
        disk_file_size(100 * MiB)


def test_disk_round_writes_and_reads_the_file(tmp_path):
    import random
    from rpidash_agent.stress_load import MiB, disk_round
    path = str(tmp_path / "stress.tmp")
    try:
        disk_round(path, 4 * MiB, random.Random(1), rand_seconds=0.05)
    except OSError as e:  # O_DIRECT isn't supported by every filesystem (tmpfs)
        pytest.skip(f"no O_DIRECT here: {e}")
    assert (tmp_path / "stress.tmp").stat().st_size == 4 * MiB
