"""Find agents on the LAN (TCP scan; WSL2 can't see mDNS) and follow DHCP address changes."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import time

from .client import AgentClient, AgentError
from .registry import Registry

log = logging.getLogger(__name__)
SCAN_TIMEOUT = 1.0


async def scan(subnet: str, client: AgentClient, concurrency: int = 64) -> list[tuple[str, dict]]:
    sem = asyncio.Semaphore(concurrency)

    async def probe(ip: str):
        async with sem:
            try:
                return ip, await client.health(ip, timeout=SCAN_TIMEOUT)
            except (AgentError, PermissionError):
                return None

    hosts = [str(h) for h in ipaddress.ip_network(subnet, strict=False).hosts()]
    results = await asyncio.gather(*(probe(h) for h in hosts))
    return [r for r in results if r]


class Rediscovery:
    def __init__(self, registry: Registry, client: AgentClient, subnet: str, min_interval: float = 300.0,
                 clock=time.monotonic) -> None:
        self.registry, self.client, self.subnet = registry, client, subnet
        self.min_interval, self.clock = min_interval, clock
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def maybe_run(self, unreachable_ids: set[str]) -> dict[str, str]:
        if not unreachable_ids or self._lock.locked():
            return {}
        now = self.clock()
        if self._last is not None and now - self._last < self.min_interval:
            return {}
        async with self._lock:
            self._last = now
            found = await scan(self.subnet, self.client)
            moved = {}
            for ip, health in found:
                pi = self.registry.get(health["id"])
                if pi and pi.id in unreachable_ids and pi.host != ip:
                    pi.host = ip
                    moved[pi.id] = ip
            if moved:
                self.registry.save()
                log.info("rediscovered: %s", moved)
            return moved
