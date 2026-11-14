"""HTTP server speaking the rpidash agent protocol, as far as a microcontroller can."""
import asyncio
import json
import time

import machine

PORT = 8765
DEVICE_ID = machine.unique_id().hex()
_REASON = {200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
           405: "Method Not Allowed", 409: "Conflict", 503: "Service Unavailable"}
# Things the Linux agent does that a Pico can't: answered with a clear message, not a bare 404.
_UNSUPPORTED = ("/shutdown", "/agent/restart", "/bench", "/stress", "/stress/stop", "/storage",
                "/peripherals")
_RANGES = ("1h", "6h", "24h", "7d")


def _query(target):
    path, _, qs = target.partition("?")
    params = {}
    for pair in qs.split("&"):
        k, _, v = pair.partition("=")
        if k:
            params[k] = v
    return path, params


class Server:
    def __init__(self, agent, token):
        """agent provides: hostname, version, history, latest, fresh_sample(), spec(), uptime(),
        and runner (the core-1 app runner, or None)."""
        self.agent, self.token = agent, token.strip()

    def _route(self, method, path, params, authed):
        a = self.agent
        if path == "/health":
            if method != "GET":
                return 405, {"error": "method not allowed"}
            return 200, {"app": "rpidash-agent", "id": DEVICE_ID, "hostname": a.hostname,
                         "version": a.version, "kind": "mcu"}
        known = path in ("/spec", "/live", "/now", "/history", "/reboot", "/app/restart") or path in _UNSUPPORTED \
            or path.startswith("/services/")
        if not known:
            return 404, {"error": "not found"}
        if not authed:
            return 401, {"error": "token required"}
        if path in _UNSUPPORTED or path.startswith("/services/"):
            return 400, {"error": "not supported on a Pico (microcontroller)"}
        if path == "/reboot":
            if method != "POST":
                return 405, {"error": "method not allowed"}
            asyncio.create_task(_reset_soon())
            return 202, {"ok": True}
        if path == "/app/restart":
            if method != "POST":
                return 405, {"error": "method not allowed"}
            if a.runner is None:
                return 400, {"error": "no application is running on core 1"}
            if not a.runner.request_restart():
                return 409, {"error": "the application is stuck in setup() or step() and can't be restarted; reboot the Pico"}
            return 202, {"ok": True}
        if method != "GET":
            return 405, {"error": "method not allowed"}
        if path == "/spec":
            return 200, a.spec()
        if path == "/now":
            return 200, {"id": DEVICE_ID, "sample": a.fresh_sample()}
        if path == "/live":
            up = a.uptime()
            return 200, {"id": DEVICE_ID, "version": a.version, "sample": a.latest, "top": [],
                         "services": [], "uptime_s": up, "agent_uptime_s": up, "stress": None,
                         "link": {"interface": "wlan0", "kind": "Wi-Fi",
                                  "signal_dbm": (a.latest or {}).get("wifi_dbm")},
                         "storage_warnings": [], "app": a.runner.status() if a.runner else None}
        rng = params.get("range", "1h")
        if rng not in _RANGES:
            return 400, {"error": "range must be one of %s" % sorted(_RANGES)}
        return 200, a.history.query(rng, time.time())

    async def handle(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            authed, length = False, 0
            while True:
                h = await asyncio.wait_for(reader.readline(), 5)
                if not h or h == b"\r\n":
                    break
                name, _, value = h.decode().partition(":")
                name, value = name.strip().lower(), value.strip()
                if name == "authorization":
                    authed = bool(self.token) and value == "Bearer " + self.token
                elif name == "content-length":
                    length = int(value) if value.isdigit() else 0
            if 0 < length <= 2048:  # bodies (e.g. JSON for /stress) aren't used; just drain them
                await asyncio.wait_for(reader.readexactly(length), 5)
            parts = line.decode().split()
            if len(parts) < 2:
                code, body = 400, {"error": "bad request"}
            else:
                path, params = _query(parts[1])
                code, body = self._route(parts[0], path, params, authed)
            data = json.dumps(body).encode()
            writer.write(b"HTTP/1.0 %d %s\r\nContent-Type: application/json\r\n"
                         b"Content-Length: %d\r\nConnection: close\r\n\r\n"
                         % (code, _REASON[code].encode(), len(data)))
            writer.write(data)
            await writer.drain()
        except Exception as e:  # a bad client must never take the server down
            print("http:", repr(e))
        finally:
            writer.close()
            await writer.wait_closed()

    async def serve(self):
        return await asyncio.start_server(self.handle, "0.0.0.0", PORT)


async def _reset_soon():
    await asyncio.sleep_ms(500)  # let the reply go out first
    machine.reset()
