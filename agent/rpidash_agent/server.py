"""HTTP API for the agent (stdlib only)."""
from __future__ import annotations

import hmac
import json
import re
import socket
import sys
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import psutil

from . import __version__
from .collect import machine_id, read_spec, top_processes
from .config import AgentConfig
from .bench import BenchError
from .stress import StressError
from .history import RANGES, History
from .services import Services

ENTRY_RE = re.compile(r"^(user:)?[A-Za-z0-9@._-]+$")


def make_server(cfg: AgentConfig, history: History, services: Services, *, host: str = "0.0.0.0",
                port: int | None = None, spec_fn=read_spec, id_fn=machine_id, top_fn=top_processes,
                reboot_delay: float = 1.0, request_timeout: float = 10.0, jobs=None,
                stress=None, now_fn=None) -> ThreadingHTTPServer:
    token = cfg.token.encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"rpidash-agent/{__version__}"
        timeout = request_timeout  # drop idle or half-sent connections so they can't pile up threads

        def log_request(self, code="-", size="-"):
            # Only failures are logged: every line lands in journald, which may be on the SD card.
            if isinstance(code, int) and code < 400:
                return
            super().log_request(code, size)

        def log_message(self, fmt, *args):  # stderr only (journald), never a file
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json_body(self):
            """Parse a small JSON object body; send 400 and return None if it isn't one."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) if 0 < n <= 4096 else b"{}")
                if isinstance(body, dict):
                    return body
            except ValueError:
                pass
            self._send(400, {"error": "body must be a JSON object"})
            return None

        def _authed(self) -> bool:
            h = self.headers.get("Authorization", "")
            given = h[7:].encode() if h.startswith("Bearer ") else b""
            if hmac.compare_digest(given, token):
                return True
            self._send(401, {"error": "unauthorized"})
            return False

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/health":
                return self._send(200, {"app": "rpidash-agent", "id": id_fn(),
                                        "hostname": socket.gethostname(), "version": __version__})
            if url.path not in ("/spec", "/live", "/history", "/bench", "/now"):
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return
            if url.path == "/spec":
                return self._send(200, spec_fn())
            if url.path == "/now":
                if now_fn is None:
                    return self._send(503, {"error": "live readings unavailable"})
                return self._send(200, {"id": id_fn(), "sample": now_fn()})
            if url.path == "/bench":
                if not jobs:
                    return self._send(503, {"error": "benchmarks unavailable"})
                return self._send(200, {**jobs.status(), "id": id_fn()})
            if url.path == "/live":
                latest = history.latest()
                return self._send(200, {
                    "id": id_fn(),
                    "version": __version__,
                    "sample": asdict(latest) if latest else None,
                    "top": top_fn(),
                    "services": services.states(),
                    "uptime_s": time.time() - psutil.boot_time(),
                    "stress": stress.status() if stress else None,
                })
            rng = parse_qs(url.query).get("range", ["1h"])[0]
            if rng not in RANGES:
                return self._send(400, {"error": f"range must be one of {sorted(RANGES)}"})
            return self._send(200, history.query(RANGES[rng], time.time()))

        def do_POST(self):
            parts = urlsplit(self.path).path.split("/")
            power = {"reboot": services.reboot, "shutdown": services.poweroff}
            if len(parts) == 2 and parts[0] == "" and parts[1] in power:
                if not self._authed():
                    return
                self._send(202, {"ok": True})
                threading.Timer(reboot_delay, power[parts[1]]).start()
                return
            if parts[1:] in (["bench"], ["stress"], ["stress", "stop"]):
                if not self._authed():
                    return
                tool = jobs if parts[1] == "bench" else stress
                if tool is None:
                    return self._send(503, {"error": f"{parts[1]} unavailable"})
                if parts[1:] == ["stress", "stop"]:
                    return self._send(200, stress.stop())
                body = self._json_body()
                if body is None:
                    return
                try:
                    if parts[1] == "bench":
                        tests = body.get("tests")
                        if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
                            raise ValueError("tests must be a list of names")
                        if stress is not None and any(stress.status().values()):
                            return self._send(409, {"error": "stress is running on this Pi; stop it before benchmarking"})
                        return self._send(202, {"job": jobs.start(tests)})
                    minutes = body.get("minutes", 5)
                    return self._send(200, stress.start(cpu=bool(body.get("cpu")), ram=bool(body.get("ram")),
                                                        net=bool(body.get("net")), minutes=minutes))
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                except (BenchError, StressError) as e:
                    return self._send(409, {"error": str(e)})
            if len(parts) != 4 or parts[1] != "services":
                return self._send(404, {"error": "not found"})
            entry, action = unquote(parts[2]), parts[3]
            if not ENTRY_RE.match(entry):
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return
            try:
                ok, output = services.run(entry, action)
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            return self._send(200, {"ok": ok, "output": output})

    return ThreadingHTTPServer((host, cfg.port if port is None else port), Handler)
