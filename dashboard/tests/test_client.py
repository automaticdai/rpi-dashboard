import httpx
import pytest

from rpidash_dashboard.client import AgentAuthError, AgentClient, AgentError


def client(handler, **kw):
    return AgentClient("tok", transport=httpx.MockTransport(handler), **kw)


async def test_auth_header_sent():
    seen = {}

    def h(req):
        seen["auth"] = req.headers.get("authorization")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"model": "Pi"})

    assert await client(h).spec("192.168.0.5") == {"model": "Pi"}
    assert seen["auth"] == "Bearer tok"
    assert seen["url"] == "http://192.168.0.5:8765/spec"


async def test_401_raises_auth_error():
    with pytest.raises(AgentAuthError):
        await client(lambda r: httpx.Response(401, json={})).live("h")


async def test_timeout_raises_agent_error():
    def h(req):
        raise httpx.ConnectTimeout("boom")

    with pytest.raises(AgentError) as e:
        await client(h).live("h")
    assert not isinstance(e.value, AgentAuthError)


async def test_500_raises_agent_error():
    with pytest.raises(AgentError):
        await client(lambda r: httpx.Response(500)).live("h")


async def test_health_rejects_non_agent():
    with pytest.raises(AgentError):
        await client(lambda r: httpx.Response(200, json={"app": "other"})).health("h")
    with pytest.raises(AgentError):
        await client(lambda r: httpx.Response(200, text="<html>")).health("h")


async def test_service_action_quotes():
    seen = {}

    def h(req):
        seen["path"] = req.url.raw_path.decode()
        seen["method"] = req.method
        return httpx.Response(200, json={"ok": True, "output": ""})

    await client(h).service_action("h", "user:oled-stats", "restart")
    assert seen == {"path": "/services/user%3Aoled-stats/restart", "method": "POST"}


async def test_history_range_param():
    seen = {}

    def h(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"ts": []})

    await client(h).history("h", "6h")
    assert seen["url"].endswith("/history?range=6h")


async def test_health_sends_no_token():
    seen = {}

    def h(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"app": "rpidash-agent", "id": "x", "hostname": "h", "version": "0"})

    await client(h).health("192.168.0.99")
    assert seen["auth"] is None


async def test_restart_agent_posts():
    seen = {}

    def h(req):
        seen["m"], seen["p"] = req.method, req.url.path
        return httpx.Response(202, json={"ok": True})

    assert await client(h).restart_agent("h") == {"ok": True}
    assert seen == {"m": "POST", "p": "/agent/restart"}
