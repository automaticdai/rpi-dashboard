"""Minimal HTTP server speaking the rpidash agent protocol (only /health so far)."""
import asyncio
import json
import machine

VERSION = "pico-0.1.0"
PORT = 8765
DEVICE_ID = machine.unique_id().hex()
_REASON = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}


def make_handler(hostname):
    async def handle(reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            while True:  # skip headers
                h = await asyncio.wait_for(reader.readline(), 5)
                if not h or h == b"\r\n":
                    break
            parts = line.decode().split()
            if len(parts) < 2:
                code, body = 400, {"error": "bad request"}
            elif parts[1].split("?")[0] != "/health":
                code, body = 404, {"error": "not found"}
            elif parts[0] != "GET":
                code, body = 405, {"error": "method not allowed"}
            else:
                code, body = 200, {"app": "rpidash-agent", "id": DEVICE_ID, "hostname": hostname,
                                   "version": VERSION, "kind": "mcu"}
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
    return handle


async def serve(hostname):
    return await asyncio.start_server(make_handler(hostname), "0.0.0.0", PORT)
