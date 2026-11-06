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
from .history import RANGES, History
from .services import Services

ENTRY_RE = re.compile(r"^(user:)?[A-Za-z0-9@._-]+$")


def make_server(cfg: AgentConfig, history: History, services: Services, *, host: str = "0.0.0.0",
                port: int | None = None, spec_fn=read_spec, id_fn=machine_id, top_fn=top_processes,
                reboot_delay: float = 1.0, request_timeout: float = 10.0) -> ThreadingHTTPServer:
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
            if url.path not in ("/spec", "/live", "/history"):
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return
            if url.path == "/spec":
                return self._send(200, spec_fn())
            if url.path == "/live":
                latest = history.latest()
                return self._send(200, {
                    "id": id_fn(),
                    "sample": asdict(latest) if latest else None,
                    "top": top_fn(),
                    "services": services.states(),
                    "uptime_s": time.time() - psutil.boot_time(),
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
