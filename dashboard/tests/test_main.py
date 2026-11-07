import platform

import rpidash_dashboard.__main__ as entry


def test_is_wsl_detects_wsl2_kernel(monkeypatch):
    monkeypatch.setattr(platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    assert entry.is_wsl() is True


def test_is_wsl_false_on_plain_linux(monkeypatch):
    monkeypatch.setattr(platform, "release", lambda: "6.12.25+rpt-rpi-2712")
    assert entry.is_wsl() is False


def test_resolve_host_widens_loopback_only_on_wsl(monkeypatch):
    monkeypatch.setattr(entry, "is_wsl", lambda: True)
    assert entry.resolve_host("127.0.0.1") == "0.0.0.0"
    assert entry.resolve_host("192.168.0.5") == "192.168.0.5"


def test_resolve_host_keeps_loopback_when_not_wsl(monkeypatch):
    monkeypatch.setattr(entry, "is_wsl", lambda: False)
    assert entry.resolve_host("127.0.0.1") == "127.0.0.1"


def test_main_passes_resolved_host_to_uvicorn(monkeypatch, tmp_path):
    cfg = tmp_path / "dashboard.toml"
    cfg.write_text('token="t"\n')
    seen = {}
    monkeypatch.setattr(entry, "is_wsl", lambda: True)
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, host, port: seen.update(host=host, port=port))
    entry.main(["--config", str(cfg), "--port", "4000"])
    assert seen == {"host": "0.0.0.0", "port": 4000}
