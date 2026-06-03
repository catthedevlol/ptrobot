"""
utils/node_checker.py — Production-grade node health checking

Implements real Wings daemon probing with:
- HTTP /api/system checks (primary)
- TCP connection fallback
- Exponential backoff retry logic
- Real online/offline determination
- Detailed logging for diagnostics
"""

from __future__ import annotations

import logging
import asyncio
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp

log = logging.getLogger("bot.node_checker")


@dataclass
class NodeCheckResult:
    """Result of a single node health check."""
    node_id: int
    node_name: str
    online: bool
    checked_at: str  # ISO-8601 UTC
    method: str  # "tcp" or "http"
    response_time_ms: Optional[int]
    error_msg: Optional[str] = None


class NodeChecker:
    """Production-grade async node health checker."""

    def __init__(self) -> None:
        self._retry_delays = [1, 2, 4, 8]  # exponential backoff in seconds
        self._connect_timeout = 5.0
        self._request_timeout = 8.0

    async def check_node(self, node_attrs: dict) -> NodeCheckResult:
        """
        Check if a node's Wings daemon is reachable.
        
        Attempts in order:
        1. HTTP GET /api/system on Wings (preferred, gives status)
        2. TCP connection to Wings port (fallback)
        
        Returns NodeCheckResult with real online status.
        Never returns fake online status.
        """
        node_id = node_attrs["id"]
        node_name = node_attrs["name"]
        fqdn = node_attrs.get("fqdn", "")
        scheme = "https" if node_attrs.get("scheme", "https") == "https" else "http"
        wings_port = node_attrs.get("daemon_listen", 8080)

        if not fqdn:
            log.warning("[NodeCheck] %s: No FQDN configured", node_name)
            return NodeCheckResult(
                node_id=node_id,
                node_name=node_name,
                online=False,
                checked_at=self._now_utc(),
                method="none",
                response_time_ms=None,
                error_msg="No FQDN configured",
            )

        # Try HTTP first (more informative)
        result = await self._check_http(node_id, node_name, fqdn, scheme, wings_port)
        if result is not None:
            return result

        # Fall back to TCP
        result = await self._check_tcp(node_id, node_name, fqdn, wings_port)
        if result is not None:
            return result

        # Both failed - node is OFFLINE
        log.warning("[NodeCheck] %s: All checks failed - marking OFFLINE", node_name)
        return NodeCheckResult(
            node_id=node_id,
            node_name=node_name,
            online=False,
            checked_at=self._now_utc(),
            method="tcp",
            response_time_ms=None,
            error_msg="All checks failed",
        )

    async def _check_http(
        self,
        node_id: int,
        node_name: str,
        fqdn: str,
        scheme: str,
        port: int,
    ) -> Optional[NodeCheckResult]:
        """Try HTTP /api/system check with retry logic."""
        url = f"{scheme}://{fqdn}:{port}/api/system"

        for attempt in range(len(self._retry_delays) + 1):
            try:
                start = datetime.now(timezone.utc)
                timeout = aiohttp.ClientTimeout(
                    total=self._request_timeout,
                    connect=self._connect_timeout,
                )
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, ssl=False) as resp:
                        elapsed = int(
                            (datetime.now(timezone.utc) - start).total_seconds() * 1000
                        )
                        # Any response < 600 means Wings is running
                        if resp.status < 600:
                            log.info(
                                "[NodeCheck] %s HTTP check OK (status=%d, %dms)",
                                node_name,
                                resp.status,
                                elapsed,
                            )
                            return NodeCheckResult(
                                node_id=node_id,
                                node_name=node_name,
                                online=True,
                                checked_at=self._now_utc(),
                                method="http",
                                response_time_ms=elapsed,
                            )
            except asyncio.TimeoutError:
                if attempt < len(self._retry_delays):
                    delay = self._retry_delays[attempt]
                    log.debug(
                        "[NodeCheck] %s HTTP timeout (attempt %d/%d), retrying in %ds",
                        node_name,
                        attempt + 1,
                        len(self._retry_delays) + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                log.warning(
                    "[NodeCheck] %s HTTP timeout after %d attempts - offline",
                    node_name,
                    len(self._retry_delays) + 1,
                )
                return None
            except Exception as exc:
                log.debug(
                    "[NodeCheck] %s HTTP check failed (attempt %d): %s",
                    node_name,
                    attempt + 1,
                    exc,
                )
                if attempt < len(self._retry_delays):
                    await asyncio.sleep(self._retry_delays[attempt])
                    continue
                return None
        return None

    async def _check_tcp(
        self,
        node_id: int,
        node_name: str,
        fqdn: str,
        port: int,
    ) -> Optional[NodeCheckResult]:
        """Try TCP connection check with retry logic."""
        for attempt in range(len(self._retry_delays) + 1):
            try:
                start = datetime.now(timezone.utc)
                
                # Resolve hostname
                try:
                    ip = socket.gethostbyname(fqdn)
                except socket.gaierror as exc:
                    log.warning(
                        "[NodeCheck] %s DNS resolution failed: %s",
                        node_name,
                        exc,
                    )
                    return None

                # Try to connect
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port),
                    timeout=self._connect_timeout,
                )
                writer.close()
                await writer.wait_closed()
                
                elapsed = int(
                    (datetime.now(timezone.utc) - start).total_seconds() * 1000
                )
                log.info(
                    "[NodeCheck] %s TCP check OK (%dms)",
                    node_name,
                    elapsed,
                )
                return NodeCheckResult(
                    node_id=node_id,
                    node_name=node_name,
                    online=True,
                    checked_at=self._now_utc(),
                    method="tcp",
                    response_time_ms=elapsed,
                )
            except asyncio.TimeoutError:
                if attempt < len(self._retry_delays):
                    delay = self._retry_delays[attempt]
                    log.debug(
                        "[NodeCheck] %s TCP timeout (attempt %d/%d), retrying in %ds",
                        node_name,
                        attempt + 1,
                        len(self._retry_delays) + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                log.warning(
                    "[NodeCheck] %s TCP timeout after %d attempts - offline",
                    node_name,
                    len(self._retry_delays) + 1,
                )
                return None
            except Exception as exc:
                log.debug(
                    "[NodeCheck] %s TCP check failed (attempt %d): %s",
                    node_name,
                    attempt + 1,
                    exc,
                )
                if attempt < len(self._retry_delays):
                    await asyncio.sleep(self._retry_delays[attempt])
                    continue
                return None
        return None

    @staticmethod
    def _now_utc() -> str:
        """Get current UTC time as ISO-8601 string."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
