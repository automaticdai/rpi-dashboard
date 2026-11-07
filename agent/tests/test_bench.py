import json
import os
import subprocess
import threading
import time

import pytest

from rpidash_agent.bench import BenchError, BenchJobs, Benchmarks, net_bench


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        test, threads = cmd[-3], int(cmd[-2])
        score = {"int": 100.0, "fp": 50.0}[test] * threads
        out = json.dumps({"test": test, "threads": threads, "seconds": 5.0, "score": score, "unit": "x"})
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")


def make(tmp_path, **kw):
    binary = tmp_path / "rpibench"
    binary.write_text("")
    (tmp_path / "rpibench.compiler").write_text("cc (Debian 14.2.0-19) 14.2.0\n")
    return Benchmarks(binary=binary, state_dir=tmp_path, run=kw.pop("run", FakeRun()), cores=4,
                      cpu_prefix=["systemd-run", "--wait"], **kw)


def test_cpu_bench_runs_int_and_fp_on_one_and_all_cores(tmp_path):
    run = FakeRun()
    r = make(tmp_path, run=run).cpu(seconds=5)
    assert r == {"int_1": 100.0, "int_all": 400.0, "fp_1": 50.0, "fp_all": 200.0, "cores": 4,
                 "compiler": "cc (Debian 14.2.0-19) 14.2.0"}
    assert [c[-3:] for c in run.calls] == [["int", "1", "5"], ["int", "4", "5"], ["fp", "1", "5"], ["fp", "4", "5"]]
    assert all(c[:2] == ["systemd-run", "--wait"] for c in run.calls)


def test_cpu_bench_without_binary(tmp_path):
    b = Benchmarks(binary=tmp_path / "missing", state_dir=tmp_path, run=FakeRun(), cores=4)
    with pytest.raises(BenchError, match="rpibench not built"):
        b.cpu()


def test_disk_bench_measures_and_cleans_up(tmp_path):
    r = make(tmp_path).disk(size_mb=4, secs=0.2)
    assert set(r) == {"seq_write_MBps", "seq_read_MBps", "rand4k_read_iops", "rand4k_write_iops", "size_mb"}
    assert all(v > 0 for v in r.values())
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".disk_bench")]


def test_disk_bench_needs_free_space(tmp_path, monkeypatch):
    class St:
        f_bavail, f_frsize = 10, 4096
    monkeypatch.setattr(os, "statvfs", lambda p: St)
    with pytest.raises(BenchError, match="free space"):
        make(tmp_path).disk(size_mb=256)


class FakeNet:
    def latency_ms(self):
        return [12.0, 10.0, 11.0, 30.0, 10.5]

    def download(self, nbytes):
        return nbytes / 12_500_000  # 100 Mbit/s

    def upload(self, nbytes):
        return nbytes / 2_500_000  # 20 Mbit/s


def test_net_bench_computes_mbps_and_median_latency():
    r = net_bench(FakeNet(), seconds=1.0)
    assert r["latency_ms"] == 11.0
    assert r["download_Mbps"] == pytest.approx(100.0)
    assert r["upload_Mbps"] == pytest.approx(20.0)
    assert r["server"] == "speed.cloudflare.com"


class SlowBench:
    def __init__(self, gate=None, fail=()):
        self.gate, self.fail = gate, fail

    def _go(self, name):
        if self.gate:
            self.gate.wait(2)
        if name in self.fail:
            raise BenchError(f"{name} broke")
        return {"value": name}

    def cpu(self):
        return self._go("cpu")

    def disk(self):
        return self._go("disk")

    def net(self):
        return self._go("net")


def wait_done(jobs):
    for _ in range(200):
        if jobs.status()["state"] != "running":
            return jobs.status()
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_job_runs_tests_and_reports_results():
    jobs = BenchJobs(SlowBench())
    job = jobs.start(["cpu", "net"])
    st = wait_done(jobs)
    assert st["state"] == "done" and st["job"] == job
    assert st["results"] == {"cpu": {"value": "cpu"}, "net": {"value": "net"}}
    assert st["errors"] == {} and st["finished"] >= st["started"]


def test_job_error_in_one_test_keeps_others():
    jobs = BenchJobs(SlowBench(fail=("disk",)))
    jobs.start(["cpu", "disk"])
    st = wait_done(jobs)
    assert st["results"] == {"cpu": {"value": "cpu"}}
    assert st["errors"] == {"disk": "disk broke"}


def test_only_one_job_at_a_time():
    gate = threading.Event()
    jobs = BenchJobs(SlowBench(gate=gate))
    jobs.start(["cpu"])
    assert jobs.status()["state"] == "running" and jobs.status()["current"] == "cpu"
    with pytest.raises(BenchError, match="already running"):
        jobs.start(["disk"])
    gate.set()
    wait_done(jobs)


def test_unknown_test_rejected():
    with pytest.raises(ValueError):
        BenchJobs(SlowBench()).start(["gpu"])
    with pytest.raises(ValueError):
        BenchJobs(SlowBench()).start([])


def test_idle_status():
    assert BenchJobs(SlowBench()).status()["state"] == "idle"


class RecordingNet(FakeNet):
    def __init__(self):
        self.sizes = []

    def download(self, nbytes):
        self.sizes.append(nbytes)
        return nbytes / 125_000_000  # 1 Gbit/s, so chunk sizes grow to the cap

    def upload(self, nbytes):
        self.sizes.append(nbytes)
        return nbytes / 125_000_000


def test_net_bench_never_asks_for_more_than_cloudflare_allows():
    from rpidash_agent.bench import NET_MAX_BYTES
    net = RecordingNet()
    net_bench(net, seconds=2.0)
    assert max(net.sizes) == NET_MAX_BYTES
    assert NET_MAX_BYTES <= 25_000_000  # Cloudflare answers 403 above ~50 MB


def test_cloudflare_error_status_raises(monkeypatch):
    import http.client
    from rpidash_agent.bench import CloudflareNet

    class Resp:
        status = 403
        def read(self, *a):
            return b""

    class Conn:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): pass
        def getresponse(self): return Resp()
        def close(self): pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Conn)
    for op in (lambda n: n.download(1000), lambda n: n.upload(1000), lambda n: n.latency_ms()):
        with pytest.raises(BenchError, match="403"):
            op(CloudflareNet())


class PickyNet(RecordingNet):
    """Refuses some sizes, like Cloudflare does."""
    def __init__(self, refused):
        super().__init__()
        self.refused = refused

    def download(self, nbytes):
        if nbytes in self.refused:
            raise BenchError("speed.cloudflare.com answered HTTP 403")
        return super().download(nbytes)


def test_net_bench_uses_known_good_sizes_and_skips_refused_ones():
    from rpidash_agent.bench import NET_SIZES
    net = PickyNet(refused={NET_SIZES[-1]})
    r = net_bench(net, seconds=1.0)
    assert r["download_Mbps"] == pytest.approx(1000.0)
    assert set(net.sizes) <= set(NET_SIZES)


def test_net_bench_fails_when_every_size_is_refused():
    from rpidash_agent.bench import NET_SIZES
    with pytest.raises(BenchError):
        net_bench(PickyNet(refused=set(NET_SIZES)), seconds=1.0)


def test_rate_limit_or_timeout_is_not_treated_as_refused_size():
    class Limited(RecordingNet):
        def download(self, n):
            raise BenchError("speed.cloudflare.com is rate-limiting this network (HTTP 429)")
    with pytest.raises(BenchError, match="429"):
        net_bench(Limited(), seconds=1.0)


def test_network_errors_become_bench_errors(monkeypatch):
    import http.client
    from rpidash_agent.bench import CloudflareNet

    class Conn:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): raise TimeoutError("The read operation timed out")
        def close(self): pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Conn)
    with pytest.raises(BenchError, match="network error"):
        CloudflareNet().download(1000)


def test_429_message_says_rate_limited(monkeypatch):
    import http.client
    from rpidash_agent.bench import CloudflareNet

    class Resp:
        status = 429
        def read(self, *a): return b""

    class Conn:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): pass
        def getresponse(self): return Resp()
        def close(self): pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Conn)
    with pytest.raises(BenchError, match="rate-limiting"):
        CloudflareNet().download(1000)
