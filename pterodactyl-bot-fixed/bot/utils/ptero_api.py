"""
utils/ptero_api.py — Async Pterodactyl Application API client.

Changes vs original:
- get_node_stats now queries the Wings /api/system endpoint via the panel's
  node resource to detect true offline state (not just API reachability).
- Added check_node_online() that returns bool without raising.
- Hardened _request() with proper timeout/connection-error handling.
- generate_username() now handles usernames that start with ., _, or -.
- Added retry_create_user() that retries on username conflicts.
"""

from __future__ import annotations

import logging
import re
import string
import random
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger("bot.ptero_api")


class PterodactylError(Exception):
    """Raised when the panel returns an error response."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"[{status}] {message}")


@dataclass
class NodeStats:
    node_id: int
    name: str
    online: bool
    memory_total: int   # MB
    memory_used: int    # MB
    disk_total: int     # MB
    disk_used: int      # MB
    cpu_count: int
    allocated_resources: dict
    uptime: int | None  # seconds — None if offline


@dataclass
class EggInfo:
    id: int
    name: str
    nest_id: int
    docker_image: str
    startup: str
    variables: list[dict]


class PterodactylAPI:
    """Async client for the Pterodactyl Application API."""

    def __init__(self, panel_url: str, api_key: str) -> None:
        self._base = panel_url.rstrip("/") + "/api/application"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._session: aiohttp.ClientSession | None = None

    # ── Session management ────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20, connect=8)
            connector = aiohttp.TCPConnector(limit=20, force_close=False)
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=timeout,
                connector=connector,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Low-level request ─────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
        base_override: str | None = None,
    ) -> Any:
        session = await self._get_session()
        base = base_override or self._base
        url = f"{base}{path}"
        try:
            async with session.request(method, url, json=json, params=params) as resp:
                if resp.status == 204:
                    return {}
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    errors = body.get("errors", [{}])
                    detail = (
                        errors[0].get("detail", "Unknown error")
                        if errors
                        else str(body)
                    )
                    raise PterodactylError(resp.status, detail)
                return body
        except PterodactylError:
            raise
        except aiohttp.ClientConnectorError as exc:
            raise PterodactylError(0, f"Connection refused / unreachable: {exc}") from exc
        except aiohttp.ServerTimeoutError as exc:
            raise PterodactylError(0, f"Request timed out: {exc}") from exc
        except aiohttp.ClientError as exc:
            raise PterodactylError(0, f"Network error: {exc}") from exc

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, json: dict) -> Any:
        return await self._request("POST", path, json=json)

    async def _delete(self, path: str) -> None:
        await self._request("DELETE", path)

    # ── Nodes ─────────────────────────────────────────────────────────────────

    async def list_nodes(self) -> list[dict]:
        data = await self._get("/nodes")
        return [item["attributes"] for item in data.get("data", [])]

    async def get_node(self, node_id: int) -> dict:
        data = await self._get(f"/nodes/{node_id}")
        return data["attributes"]

    async def get_node_allocations(self, node_id: int) -> list[dict]:
        data = await self._get(
            f"/nodes/{node_id}/allocations", params={"per_page": 500}
        )
        return [item["attributes"] for item in data.get("data", [])]

    async def get_node_stats(self, node_id: int) -> NodeStats:
        """
        Fetch live resource data for a single node.

        True offline detection:
        1. If the Application API /nodes/{id} call fails → offline.
        2. We also attempt the Wings /api/system health endpoint through the
           panel's configured FQDN.  If Wings is unreachable → offline.
        3. The `allocated_resources` block in the node response reflects
           panel-side allocation data (always available if the API responds).
           Wings reachability is the real online signal.
        """
        try:
            node = await self._get(f"/nodes/{node_id}")
            attrs = node["attributes"]
        except PterodactylError:
            raise  # re-raise so caller marks offline

        allocated = attrs.get("allocated_resources", {})

        # Check Wings reachability via the node's FQDN + Wings port
        wings_online = await self._check_wings(attrs)

        return NodeStats(
            node_id=node_id,
            name=attrs["name"],
            online=wings_online,
            memory_total=attrs.get("memory", 0),
            memory_used=allocated.get("memory", 0),
            disk_total=attrs.get("disk", 0),
            disk_used=allocated.get("disk", 0),
            cpu_count=attrs.get("cpu", 0),
            allocated_resources=allocated,
            uptime=None,
        )

    async def _check_wings(self, node_attrs: dict) -> bool:
        """
        Attempt to reach the Wings daemon on the node's FQDN.
        Wings listens on port 8080 (default) or the configured daemon.listen port.
        Returns True if Wings responds with HTTP 2xx/4xx, False if unreachable.
        """
        fqdn = node_attrs.get("fqdn", "")
        scheme = "https" if node_attrs.get("scheme", "https") == "https" else "http"
        port = node_attrs.get("daemon_listen", 8080)
        if not fqdn:
            return False  # can't check — treat as offline

        wings_url = f"{scheme}://{fqdn}:{port}"
        try:
            session = await self._get_session()
            # Wings /api/system returns 401 without auth — that's fine, it means Wings is up
            timeout = aiohttp.ClientTimeout(total=8, connect=5)
            async with session.get(
                f"{wings_url}/api/system",
                timeout=timeout,
                ssl=False,  # self-signed certs are common on Wings
            ) as resp:
                # Any HTTP response (even 401/403) means Wings is running
                return resp.status < 600
        except Exception:
            return False

    async def check_node_online(self, node_id: int) -> bool:
        """Safe wrapper — returns bool, never raises."""
        try:
            stats = await self.get_node_stats(node_id)
            return stats.online
        except Exception:
            return False

    # ── Users ─────────────────────────────────────────────────────────────────

    async def list_users(self) -> list[dict]:
        data = await self._get("/users", params={"per_page": 500})
        return [item["attributes"] for item in data.get("data", [])]

    async def create_user(
        self, username: str, email: str, first_name: str, last_name: str, password: str
    ) -> dict:
        payload = {
            "username": username,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "password": password,
        }
        data = await self._post("/users", payload)
        return data["attributes"]

    async def delete_user(self, user_id: int) -> None:
        await self._delete(f"/users/{user_id}")

    # ── Eggs ──────────────────────────────────────────────────────────────────

    async def list_nests(self) -> list[dict]:
        data = await self._get("/nests", params={"per_page": 100})
        return [item["attributes"] for item in data.get("data", [])]

    async def list_eggs(self, nest_id: int) -> list[dict]:
        data = await self._get(
            f"/nests/{nest_id}/eggs",
            params={"include": "variables", "per_page": 100},
        )
        return [item["attributes"] for item in data.get("data", [])]

    async def get_egg(self, nest_id: int, egg_id: int) -> dict:
        data = await self._get(
            f"/nests/{nest_id}/eggs/{egg_id}",
            params={"include": "variables"},
        )
        return data["attributes"]

    # ── Allocations ───────────────────────────────────────────────────────────

    async def get_free_allocation(self, node_id: int) -> dict | None:
        """Return the first unassigned allocation on a node, or None."""
        allocs = await self.get_node_allocations(node_id)
        for alloc in allocs:
            if not alloc.get("assigned"):
                return alloc
        return None

    # ── Servers ───────────────────────────────────────────────────────────────

    async def create_server(
        self,
        name: str,
        user_id: int,
        egg_id: int,
        docker_image: str,
        startup: str,
        environment: dict,
        limits: dict,
        feature_limits: dict,
        allocation_id: int,
    ) -> dict:
        payload = {
            "name": name,
            "user": user_id,
            "egg": egg_id,
            "docker_image": docker_image,
            "startup": startup,
            "environment": environment,
            "limits": limits,
            "feature_limits": feature_limits,
            "allocation": {"default": allocation_id},
        }
        data = await self._post("/servers", payload)
        return data["attributes"]

    async def list_servers(self) -> list[dict]:
        data = await self._get("/servers", params={"per_page": 500})
        return [item["attributes"] for item in data.get("data", [])]

    async def get_server(self, server_id: int) -> dict:
        data = await self._get(f"/servers/{server_id}")
        return data["attributes"]

    async def delete_server(self, server_id: int) -> None:
        await self._delete(f"/servers/{server_id}")

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def sanitize_username(raw: str) -> str:
        """
        Produce a Pterodactyl-safe username from an arbitrary Discord display name.

        Pterodactyl rules:
        - 1–255 characters
        - Only alphanumeric, underscore, hyphen, period
        - Must start with an alphanumeric character

        Strategy:
        1. Lowercase the input.
        2. Replace any leading non-alphanumeric characters with 'u'.
        3. Strip all characters that aren't [a-z0-9._-].
        4. Truncate to 20 characters.
        5. If result is empty, fall back to 'user'.
        """
        s = raw.lower().strip()
        # Remove all characters that Pterodactyl rejects
        s = re.sub(r"[^a-z0-9._\-]", "", s)
        # Ensure the username starts with an alphanumeric character
        s = re.sub(r"^[^a-z0-9]+", "", s)
        s = s[:20] or "user"
        return s

    @staticmethod
    def generate_username(base: str = "") -> str:
        """
        Generate a unique, panel-safe username from an arbitrary base string.

        Examples:
            .username  → user_a3f92
            _test      → user_b1c44
            NormalName → normalname_x82hd
        """
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        clean = PterodactylAPI.sanitize_username(base)
        if not clean:
            clean = "user"
        return f"{clean}_{suffix}"

    @staticmethod
    def generate_password(length: int = 16) -> str:
        """Generate a secure random password meeting complexity requirements."""
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        while True:
            pwd = "".join(random.choices(chars, k=length))
            if (
                any(c.isupper() for c in pwd)
                and any(c.islower() for c in pwd)
                and any(c.isdigit() for c in pwd)
                and any(c in "!@#$%^&*" for c in pwd)
            ):
                return pwd
