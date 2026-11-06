"""Async HTTP client for rpidash agents."""
from __future__ import annotations

from urllib.parse import quote

import httpx


class AgentError(Exception):
    """Agent unreachable, timed out, or returned an unexpected response."""


class AgentAuthError(AgentError):
    """Agent rejected the token (HTTP 401)."""


class AgentClient:
    def __init__(self, token: str, port: int = 8765, timeout: float = 3.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.port = port
        self.timeout = timeout
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout,
                                       headers={"Authorization": f"Bearer {token}"})

    async def _req(self, method: str, host: str, path: str, *, params=None, timeout=None) -> dict:
        url = f"http://{host}:{self.port}{path}"
        try:
            r = await self._http.request(method, url, params=params,
                                         timeout=self.timeout if timeout is None else timeout)
        except httpx.HTTPError as e:
            raise AgentError(f"{host}: {type(e).__name__}: {e}") from e
        if r.status_code == 401:
            raise AgentAuthError(f"{host}: token rejected")
        if r.status_code == 403:
            raise PermissionError(r.json().get("error", "forbidden") if _is_json(r) else "forbidden")
        if not r.is_success:
            raise AgentError(f"{host}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise AgentError(f"{host}: invalid JSON") from e

    async def health(self, host: str, timeout: float | None = None) -> dict:
        d = await self._req("GET", host, "/health", timeout=timeout)
        if not isinstance(d, dict) or d.get("app") != "rpidash-agent" or not d.get("id"):
            raise AgentError(f"{host}: not an rpidash agent")
        return d

    async def spec(self, host: str) -> dict:
        return await self._req("GET", host, "/spec")

    async def live(self, host: str) -> dict:
        return await self._req("GET", host, "/live")

    async def history(self, host: str, range_: str) -> dict:
        return await self._req("GET", host, "/history", params={"range": range_})

    async def reboot(self, host: str) -> dict:
        return await self._req("POST", host, "/reboot")

    async def service_action(self, host: str, entry: str, action: str) -> dict:
        return await self._req("POST", host, f"/services/{quote(entry, safe='')}/{quote(action, safe='')}")

    async def aclose(self) -> None:
        await self._http.aclose()


def _is_json(r: httpx.Response) -> bool:
    return r.headers.get("content-type", "").startswith("application/json")
