"""
utils/wings_probe.py — Real Wings daemon health probing.

Performs genuine TCP + HTTP health checks against Wings daemon.
No fake status, no caching, no assumptions — real-time probing only.

Features:
- TCP connection verification
- HTTP /api/system endpoint validation
- Configurable timeout and retry logic
- Detailed error attribution
- Response time measurement
- Status code tracking
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp

log = logging.getLogger("bot.wings_probe")


@dataclass
class ProbeResult:
    """Result of a single probe attempt."""
    timestamp: str  # ISO-8601 UTC
    probe_type: str  # "tcp" or "http"
    success: bool
    response_time_ms: float
    status_code: Optional[int] = None
    error_reason: Optional[str] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"


@dataclass
class NodeProbeResult:
    """Aggregated probe result for a node."""
    node_id: int
    node_name: str
    timestamp: str  # ISO-8601 UTC
    is_online: bool  # True only if TCP AND HTTP succeed
    tcp_result: Optional[ProbeResult]
    http_result: Optional[ProbeResult]
    probe_duration_ms: float
    failure_reason: Optional[str] = None  # Human-readable reason if offline


class WingsProbe:
    """Probes real Wings daemon health without caching or assumptions."""

    def __init__(
        self,
        tcp_timeout: float = 5.0,
        http_timeout: float = 10.0,
        max_retries: int = 1,
    ):
        """
        Initialize Wings probe.

        Args:
            tcp_timeout: TCP connection timeout in seconds
            http_timeout: HTTP request timeout in seconds
            max_retries: Number of retries (0 = single attempt, 1 = retry once)
        """
        self.tcp_timeout = tcp_timeout
        self.http_timeout = http_timeout
        self.max_retries = max_retries

    async def probe_tcp(
        self,
        host: str,
        port: int,
    ) -> ProbeResult:
        """
        Probe TCP connectivity to Wings port.

        Real test: can we open a socket to host:port?
        """
        start = datetime.now(timezone.utc)
        last_error = None

        for attempt in range(1 + self.max_retries):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=self.tcp_timeout,
                )
                writer.close()
                await writer.wait_closed()

                elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                return ProbeResult(
                    timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
                    probe_type="tcp",
                    success=True,
                    response_time_ms=elapsed_ms,
                )

            except asyncio.TimeoutError:
                last_error = f"TCP timeout ({self.tcp_timeout}s exceeded)"
            except OSError as e:
                last_error = f"Connection failed: {type(e).__name__}"
            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"

            if attempt < self.max_retries:
                await asyncio.sleep(0.2)  # Brief delay before retry

        elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        return ProbeResult(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
            probe_type="tcp",
            success=False,
            response_time_ms=elapsed_ms,
            error_reason=last_error,
        )

    async def probe_http_health(
        self,
        url: str,
        session: aiohttp.ClientSession,
    ) -> ProbeResult:
        """
        Probe HTTP health endpoint (/api/system).

        Real test: does Wings respond to HTTP with 2xx status?
        """
        start = datetime.now(timezone.utc)
        last_error = None

        for attempt in range(1 + self.max_retries):
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.http_timeout),
                    ssl=False,  # Pterodactyl often uses self-signed certs
                ) as resp:
                    elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                    success = 200 <= resp.status < 300

                    return ProbeResult(
                        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
                        probe_type="http",
                        success=success,
                        response_time_ms=elapsed_ms,
                        status_code=resp.status,
                        error_reason=None if success else f"HTTP {resp.status}",
                    )

            except asyncio.TimeoutError:
                last_error = f"HTTP timeout ({self.http_timeout}s exceeded)"
            except aiohttp.ClientConnectorError as e:
                last_error = f"Connection error: {str(e)}"
            except aiohttp.ClientSSLError:
                last_error = "SSL certificate verification failed"
            except aiohttp.ClientError as e:
                last_error = f"HTTP error: {type(e).__name__}"
            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"

            if attempt < self.max_retries:
                await asyncio.sleep(0.2)

        elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        return ProbeResult(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
            probe_type="http",
            success=False,
            response_time_ms=elapsed_ms,
            error_reason=last_error,
        )

    async def probe_node(
        self,
        node_id: int,
        node_name: str,
        fqdn: str,
        port: int,
        session: aiohttp.ClientSession,
    ) -> NodeProbeResult:
        """
        Perform complete node health probe (TCP + HTTP in parallel).

        A node is ONLINE only if BOTH TCP and HTTP succeed.
        Any failure = OFFLINE.

        Args:
            node_id: Pterodactyl node ID
            node_name: Node display name
            fqdn: Hostname/IP of Wings daemon
            port: Wings port (typically 8080)
            session: aiohttp ClientSession

        Returns:
            NodeProbeResult with aggregated probe outcome
        """
        start = datetime.now(timezone.utc)
        health_url = f"https://{fqdn}:{port}/api/system"

        # Run both probes in parallel
        tcp_result, http_result = await asyncio.gather(
            self.probe_tcp(fqdn, port),
            self.probe_http_health(health_url, session),
            return_exceptions=False,
        )

        # Node is online only if BOTH probes succeed
        is_online = tcp_result.success and http_result.success

        # Determine reason for offline
        failure_reason = None
        if not is_online:
            if not tcp_result.success and not http_result.success:
                failure_reason = "Both TCP and HTTP checks failed"
            elif not tcp_result.success:
                failure_reason = f"TCP unreachable: {tcp_result.error_reason}"
            else:
                failure_reason = f"HTTP unhealthy: {http_result.error_reason}"

        elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        return NodeProbeResult(
            node_id=node_id,
            node_name=node_name,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
            is_online=is_online,
            tcp_result=tcp_result,
            http_result=http_result,
            probe_duration_ms=elapsed_ms,
            failure_reason=failure_reason,
        )
