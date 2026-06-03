# Pterodactyl Bot — Bug Fix & Quality Update

## Changed Files

| File | Status |
|---|---|
| `bot/database.py` | Rewritten |
| `bot/config.py` | Updated |
| `bot/utils/ptero_api.py` | Rewritten |
| `bot/utils/helpers.py` | Updated |
| `bot/tasks/node_monitor.py` | Rewritten |
| `bot/commands/nodes.py` | Updated |
| `bot/commands/users.py` | Rewritten |
| `bot/commands/servers.py` | Rewritten |
| `bot/commands/admin.py` | Patched |
| `.env.example` | Updated |

---

## Bug Fixes & Changes

### 1. Node Status Bug (FIXED)

**Root cause:** `get_node_stats()` in `ptero_api.py` only called the Pterodactyl Application API
(`/api/application/nodes/{id}`). That endpoint returns node *configuration* (RAM, disk limits),
not live Wings daemon status. The Application API responds normally even when Wings is completely
down — so `online` was always set to `True` whenever the API responded.

**Fix:** `get_node_stats()` now also checks the Wings health endpoint
(`{scheme}://{fqdn}:{wings_port}/api/system`) via a direct HTTP request.
- Any HTTP response (even 401 Unauthorized) = Wings is running = **ONLINE**
- Connection refused / timeout / no response = Wings unreachable = **OFFLINE**
- Application API failure = **OFFLINE**

All status embeds now include a "Last checked: HH:MM:SS UTC" timestamp in the footer.

---

### 2. Advanced Uptime System (NEW)

**New table: `node_uptime`**

Stores per-node state that survives bot restarts:
- `online_since` — UTC timestamp when the node last came online
- `offline_since` — UTC timestamp when the node last went offline
- `last_online_session_s` — duration of the last online session in seconds
- `last_downtime_s` — duration of the most recent downtime in seconds
- `total_downtime_s` — cumulative total downtime in seconds (all time)
- `month_start` / `month_downtime_s` — rolling monthly downtime for uptime %

**New table: `node_events`** — ordered timeline of every online/offline transition.

**New table: `node_history`** — monthly uptime snapshots (for future reporting).

**Uptime embed** now shows:

```
🟢 Germany-1
  ↳ Online For: 4d 12h 22m
  ↳ Monthly Uptime: 99.98%
  ↳ Total Downtime: 12m

🔴 Singapore-1
  ↳ Offline Since: 18m ago
  ↳ Last Online Session: 2d 8h
  ↳ Last Downtime: 4m
  ↳ Monthly Uptime: 97.21%
```

**State persistence across restarts:** On the first poll cycle after startup,
`_load_state_from_db()` reads `node_uptime` rows to initialise `_node_was_online`.
This prevents false "node came online" / "node went offline" transitions being
fired for every node on every restart.

---

### 3. Username Validation Bug (FIXED)

**Root cause:** `generate_username()` used `c.isalnum()` to filter characters,
which silently drops leading `.`, `_`, `-` but leaves an empty `clean` string
(after stripping), resulting in usernames like `12345` that either collide or
don't make sense. Pterodactyl also requires usernames to **start with an
alphanumeric character**, so even if we kept the dots/hyphens, leading ones
would cause 422 errors.

**Fix in `ptero_api.py`:**

```python
@staticmethod
def sanitize_username(raw: str) -> str:
    s = raw.lower().strip()
    # Strip chars outside [a-z0-9._-]
    s = re.sub(r"[^a-z0-9._\-]", "", s)
    # Strip leading non-alphanumeric chars (Pterodactyl requirement)
    s = re.sub(r"^[^a-z0-9]+", "", s)
    return s[:20] or "user"

@staticmethod
def generate_username(base: str = "") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    clean = PterodactylAPI.sanitize_username(base)
    return f"{clean}_{suffix}"
```

Examples:
- `.username` → `username_a3f92`
- `_test` → `test_b1c44`
- `-hello` → `hello_x82hd`
- `John Doe` → `johndoe_p9m3k`

**Collision retry in `commands/users.py`:** The `create_user` command now
retries up to 5 times with a freshly-generated username when the panel returns
HTTP 422 or 409 (username/email already taken). After all retries are exhausted,
a clean error embed is shown.

---

### 4. Server Creation Quality (IMPROVED)

**Success embed** now includes:
- Owner mention
- Server Name
- Node
- Egg + Software (if Minecraft)
- Server ID
- Resources (CPU / RAM / Disk)
- Connection address:port

**Automatic DM to user** (`commands/servers.py`):

```
🎮 Your Server Is Ready!
  Server Name
  Node
  Egg / Software
  Panel Username
  Address:Port
  Panel link
```

DM includes **Open Panel** and **Support** (if `SUPPORT_URL` is set) buttons.

**New `SUPPORT_URL` config variable** — optional, shown as a button in server DMs.

**Extended `pterodactyl_servers` table** stores: `node_name`, `egg_name`,
`software`, `plan_label`, `cpu`, `ram`, `disk`, `allocation_ip`,
`allocation_port`.

---

### 5. Error Handling (HARDENED)

Every `PterodactylError` is now caught and shown as a clean embed.

A `_api_error_embed()` helper maps HTTP status codes to user-friendly hints:

| Status | Hint shown |
|---|---|
| 0 | Panel unreachable — check network/URL |
| 403 | API key lacks permission |
| 404 | Resource not found |
| 409 | Conflict — resource may already exist |
| 422 | Invalid data — check egg variables/allocation |
| 500 | Panel internal server error |
| 503 | Panel temporarily unavailable |

`aiohttp` network errors (connection refused, timeout, generic `ClientError`)
are caught in `_request()` and wrapped as `PterodactylError(status=0, ...)`.

---

### 6. Logging Improvements

All log embeds use `log_embed()` (purple, with actor avatar).

Events logged:
- `User Created` — panel ID, username, DM status
- `Failed User Creation` — error detail, retry count
- `Server Created` — owner, server name, node, egg, plan, DM status
- `Failed Server Creation` — owner, node, egg, error detail
- `Node Offline` — node name and ID
- `Node Online` — node name, ID, downtime duration
- `/sync executed` — full sync report

---

### 7. Database Improvements

**New tables:**
- `node_uptime` — persistent per-node uptime statistics
- `node_events` — ordered online/offline event log
- `node_history` — monthly uptime snapshots
- `server_logs` — per-server audit events

**All timestamps stored as UTC ISO-8601** (`YYYY-MM-DDTHH:MM:SSZ`).

**`PRAGMA journal_mode=WAL`** — enables concurrent reads while writing and
improves crash recovery.

**Write serialisation via `asyncio.Lock`** — prevents SQLite "database is
locked" errors when multiple coroutines write simultaneously (the original code
had no write serialisation).

**`_run_migrations()`** — idempotently adds new columns to existing databases
via `ALTER TABLE ... ADD COLUMN`. Existing data is preserved on upgrade.

---

### 8. Code Quality

| Issue | Fix |
|---|---|
| Race condition: multiple coroutines writing to SQLite concurrently | `asyncio.Lock` on all `execute()` calls |
| `datetime.utcnow()` (deprecated, naïve) in `close_downtime()` | Replaced with `datetime.now(timezone.utc)` everywhere |
| `_node_was_online` state lost on restart → spurious offline/online events | State loaded from `node_uptime` DB table on startup |
| `aiohttp` session not re-used across API calls in commands (creates/closes a new session per command) | Documented limitation; session is properly closed in `finally` blocks |
| `permission check` in `require_*` decorators only sent response via `send_message` — failed if interaction was already deferred | Added `is_done()` check, falls back to `followup.send()` |
| `added_at` timestamp parsed without timezone → `ValueError` on Python 3.11+ with `Z` suffix | `.replace("Z", "+00:00")` applied before `fromisoformat()` |
| `fmt_duration()` didn't handle days | Added days branch: `Xd Yh Zm` |
| No `fmt_ago()` helper for "X ago" display | Added `fmt_ago()` to `helpers.py` |

