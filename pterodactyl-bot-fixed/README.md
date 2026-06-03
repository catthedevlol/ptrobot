# 🦕 Pterodactyl Panel Discord Bot

A production-grade Discord bot for Pterodactyl panel management built with **Python 3.12+** and **discord.py 2.x**.

---

## 📋 Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Database Schema](#database-schema)
- [Command Reference](#command-reference)
- [Quick Start (Local)](#quick-start-local)
- [Production Deployment (Ubuntu/Linux VPS)](#production-deployment-ubuntulinux-vps)
- [Panel API Key Permissions](#panel-api-key-permissions)
- [Troubleshooting](#troubleshooting)

---

## Features

| Feature | Description |
|---|---|
| **Live Node Status** | Auto-updating embeds showing RAM, Disk, CPU, and online status |
| **Node Uptime Overview** | Single embed with 🟢/🔴 status for all nodes |
| **Maintenance Alerts** | Auto-detects node outages, mentions admins, logs duration |
| **User Creation** | Creates Pterodactyl accounts and DMs credentials |
| **Server Creation** | 6-step interactive flow with egg/plan/node selection |
| **Admin System** | Main admins + sub-admins with permission tiers |
| **Sync Command** | Hot-reload cogs, re-sync commands, verify panel integrity |
| **Audit Logging** | All actions logged to a configurable channel |

---

## Project Structure

```
pterodactyl-bot/
├── bot/
│   ├── main.py              # Bot entrypoint — loads cogs, inits DB
│   ├── config.py            # Environment variable configuration
│   ├── database.py          # Async SQLite database layer
│   ├── commands/
│   │   ├── admin.py         # /admin-add /admin-remove /admin-list /set-log-channel
│   │   ├── nodes.py         # /node-stat /node-uptime /node-maintenance
│   │   ├── users.py         # /create-user
│   │   ├── servers.py       # /create-server
│   │   └── sync.py          # /sync
│   ├── tasks/
│   │   └── node_monitor.py  # Background loop — polls nodes, updates embeds
│   ├── utils/
│   │   ├── ptero_api.py     # Async Pterodactyl Application API client
│   │   └── helpers.py       # Permission checks, embed builders, formatters
│   ├── views/
│   │   └── persistent.py    # discord.ui.View subclasses (buttons, selects)
│   └── data/                # SQLite database and log files (auto-created)
├── requirements.txt
├── .env.example
├── pterobot.service          # systemd service file
└── README.md
```

---

## Database Schema

```sql
-- Sub-admin Discord IDs
admins (discord_id PK, added_by, added_at)

-- Discord → Pterodactyl user mapping
pterodactyl_users (discord_id PK, ptero_id, username, email, password, created_at, created_by)

-- Pterodactyl servers created via this bot
pterodactyl_servers (id PK, discord_id, ptero_server_id, identifier, name, egg_id, node_id, created_at, created_by)

-- Tracked persistent status embeds
node_status_messages (node_id, channel_id, message_id) -- PK: (node_id, channel_id)

-- Tracked persistent uptime embeds
node_uptime_messages (channel_id PK, message_id)

-- Active maintenance alert embeds
maintenance_messages (node_id PK, channel_id, message_id)

-- Channels that receive maintenance alerts
maintenance_channels (channel_id PK)

-- Downtime event log
node_downtime_log (id PK, node_id, node_name, down_at, up_at, duration_s)

-- Audit log
action_logs (id PK, action, actor_id, target_id, details, ts)

-- Configured log channel
log_channel (id=1, channel_id)
```

---

## Command Reference

### Node Commands
| Command | Permission | Description |
|---|---|---|
| `/node-stat <channel> [node_id]` | Any Admin | Post/update live node status embed(s) |
| `/node-uptime <channel>` | Any Admin | Post/update uptime overview embed |
| `/node-maintenance <channel>` | Any Admin | Register maintenance alert channel |

### User & Server Commands
| Command | Permission | Description |
|---|---|---|
| `/create-user @user` | Any Admin | Create Pterodactyl account, DM credentials |
| `/create-server @user` | Any Admin | 6-step interactive server creation flow |

### Admin Commands
| Command | Permission | Description |
|---|---|---|
| `/admin-add @user` | Main Admin | Grant sub-admin permissions |
| `/admin-remove @user` | Main Admin | Revoke sub-admin permissions |
| `/admin-list` | Any Admin | List all admins |
| `/set-log-channel <channel>` | Main Admin | Set audit log channel |

### System Commands
| Command | Permission | Description |
|---|---|---|
| `/sync` | Main Admin | Reload cogs, re-sync commands, verify integrity |

---

## Quick Start (Local)

### Prerequisites
- Python 3.12+
- A Pterodactyl panel with Application API access
- A Discord bot token with `bot` + `applications.commands` scopes
- Bot must have **Server Members Intent** and **Message Content Intent** enabled

### Steps

```bash
# 1. Clone / copy the project
cd pterodactyl-bot

# 2. Create a virtual environment
python3.12 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
nano .env                       # fill in all values

# 5. Run the bot
cd bot
python main.py
```

The bot will:
1. Create `data/bot.db` (SQLite) on first run
2. Sync slash commands to your guild (appears instantly)
3. Start the node-monitoring background task

---

## Production Deployment (Ubuntu/Linux VPS)

### 1. Create a dedicated user

```bash
sudo adduser --system --no-create-home --group pterobot
sudo mkdir -p /opt/pterobot
sudo chown pterobot:pterobot /opt/pterobot
```

### 2. Install Python 3.12

```bash
sudo apt update && sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt install -y python3.12 python3.12-venv python3.12-dev
```

### 3. Deploy the bot

```bash
sudo cp -r pterodactyl-bot/bot /opt/pterobot/bot
sudo cp pterodactyl-bot/requirements.txt /opt/pterobot/

# Create venv as root, then fix ownership
sudo python3.12 -m venv /opt/pterobot/venv
sudo /opt/pterobot/venv/bin/pip install -r /opt/pterobot/requirements.txt
sudo chown -R pterobot:pterobot /opt/pterobot
```

### 4. Configure environment

```bash
sudo cp pterodactyl-bot/.env.example /opt/pterobot/.env
sudo nano /opt/pterobot/.env          # fill in all values
sudo chmod 600 /opt/pterobot/.env     # restrict read access
sudo chown pterobot:pterobot /opt/pterobot/.env
```

### 5. Create data directory with correct permissions

```bash
sudo mkdir -p /opt/pterobot/bot/data
sudo chown pterobot:pterobot /opt/pterobot/bot/data
```

### 6. Install and enable the systemd service

```bash
sudo cp pterodactyl-bot/pterobot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pterobot
sudo systemctl start pterobot
```

### 7. Check status and logs

```bash
sudo systemctl status pterobot
sudo journalctl -u pterobot -f          # live logs
sudo journalctl -u pterobot --since today
```

### Updating the bot

```bash
sudo systemctl stop pterobot
sudo cp -r pterodactyl-bot/bot/* /opt/pterobot/bot/
sudo systemctl start pterobot
sudo journalctl -u pterobot -f
```

---

## Panel API Key Permissions

Create your key at: **Panel → Admin → Application API → Create API Key**

Required permissions:

| Category | Permission |
|---|---|
| Users | Read + Write |
| Servers | Read + Write |
| Nodes | Read |
| Allocations | Read |
| Nests / Eggs | Read |

---

## Troubleshooting

### Slash commands not appearing
Run `/sync` in your server, or wait up to 1 minute after bot startup.

### Bot won't start — "Required environment variable not set"
Check your `.env` file is complete and in the correct location.

### `PterodactylError [403]`
Your API key doesn't have the required permissions. See above.

### `PterodactylError [404]` on node stats
The node exists but Wings may be offline. The bot will show the node as 🔴 OFFLINE.

### DMs not sent
The target user has DMs disabled. The bot will warn you but still create the account.

### Database locked errors (SQLite)
Only run one instance of the bot. For multiple instances, switch to PostgreSQL.

### Bot left a server it shouldn't have
The bot only operates in guild ID `1262068469164150846`. Update `GUILD_ID` in `.env` if this is wrong.

---

## Architecture Notes

- **Persistent Views**: All button/select components use `timeout=None` and are re-registered on startup, so they survive restarts.
- **Background Task**: The `NodeMonitorTask` cog runs a `@tasks.loop` that polls the panel, edits embeds in-place, and fires alerts. It restarts cleanly on `/sync`.
- **Permission Tiers**: Main admins (hard-coded IDs) > Sub-admins (database) > Everyone else.
- **Error Isolation**: Every cog wraps API calls in try/except. A single failed node poll won't crash the monitor loop.
