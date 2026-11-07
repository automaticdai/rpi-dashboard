import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "rpibench" / "rpibench.c"
pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("bin") / "rpibench"
    subprocess.run(["cc", "-O2", "-pthread", "-Wall", "-Werror", "-o", str(out), str(SRC), "-lm"], check=True)
    return out


def run(binary, *args):
    r = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("test,unit", [("int", "Mops/s"), ("fp", "MFLOPS")])
def test_single_thread_reports_positive_score(binary, test, unit):
    d = run(binary, test, "1", "0.5")
    assert d["test"] == test and d["threads"] == 1 and d["unit"] == unit
    assert d["score"] > 0 and 0.4 < d["seconds"] < 2


def test_more_threads_scores_higher(binary):
    import os
    if (os.cpu_count() or 1) < 2:
        pytest.skip("single core")
    one = run(binary, "int", "1", "0.5")["score"]
    two = run(binary, "int", "2", "0.5")["score"]
    assert two > one * 1.4


def test_bad_args_fail(binary):
    assert subprocess.run([str(binary), "nope"], capture_output=True).returncode != 0
    assert subprocess.run([str(binary), "int", "0", "1"], capture_output=True).returncode != 0


def test_burn_runs_until_killed(binary):
    p = subprocess.Popen([str(binary), "burn", "1"])
    time.sleep(0.3)
    assert p.poll() is None
    p.terminate()
    assert p.wait(timeout=3) is not None
