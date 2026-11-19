# ALLINA Project — Operations & Remediation Runbook

**Runbook ID:** `RB-ALLINA-OPS-001`  
**System:** ALLINA A7 Remediation Agent & Microsoft Teams Bot  
**Environment:** Azure VM (`alina-testing`) / Linux (Ubuntu 24.04 LTS)  
**Location:** `/home/azureuser/allina_project`

---

## 1. System Overview

ALLINA is an enterprise IT operations remediation system integrating a Microsoft Teams bot with policy-governed automated infrastructure remediation.

- **Ingress Layer:** Microsoft Teams Bot running in **Socket Mode** (outbound WebSocket via HTTPS port 443; no incoming firewall ports required).
- **Agent Layer:** A7 Remediation Agent that parses approved runbooks, inspects server disks/logs, and drafts cryptographic SHA-256 bound remediation plans.
- **Governance Layer:** Separation of duties (anti-self-approval), Entra ID approver allowlist, dry-run safety gates, and state machine persistence via SQLite.
- **Execution Target:** Azure VM Run Command (`ALLINA_TARGET_MODE=azure`) or AWS Systems Manager (`aws`).
- **Audit Layer:** Tamper-evident SHA-256 hash-chained append-only audit ledger.

---

## 2. Architecture & Networking

```
+------------------------------------+
|        Microsoft Teams             |
|   (Requester / Approver Chat)      |
+-----------------+------------------+
                  |  ^
  Outbound WS/443 |  | Replies & Cards
                  v  |
+-----------------+------------------+
|    ALLINA Bot (main.py)            |
|    - Socket Mode (No open ports)   |
|    - Ingress Guard & Router        |
+-----------------+------------------+
                  |
     +------------+------------+
     v                         v
+----+--------------------+  +-+--------------------+
| Remediation Agent &     |  | SQLite CAS Database  |
| Runbook Policy Engine   |  | (data/allina_poc.db) |
+----+--------------------+  +----------------------+
     v
+----+--------------------+
| Azure VM Run Command /  | ---> Target: /opt/allina/test-logs
| AWS SSM Executor        |
+-------------------------+
```

> [!NOTE]
> **No Inbound Ports Needed:** Because the bot uses **Socket Mode** (`App(socket_mode=True)`), it connects directly out to Microsoft's cloud over standard HTTPS (TCP 443). No inbound port forwarding, public IP, or ngrok tunnel is required.

---

## 3. Quickstart: Running the Bot

### Prerequisites
- Python 3.12+ (installed)
- Virtual environment with dependencies in `allina-teams-bot/.venv`

### Interactive Execution (Console)
```bash
# 1. Navigate to the bot directory
cd ~/allina_project/allina-teams-bot

# 2. Activate virtual environment
source .venv/bin/activate

# 3. Start the bot
python src/main.py
```

### Background Execution (Persistent)
To keep the bot running after disconnecting from your SSH session:

```bash
cd ~/allina_project/allina-teams-bot
source .venv/bin/activate
nohup python src/main.py > bot.log 2>&1 &
```

- Check process: `ps aux | grep main.py`
- View live logs: `tail -f ~/allina_project/allina-teams-bot/bot.log`
- Stop the bot: `pkill -f "python src/main.py"`

---

## 4. Bot Interaction Cheatsheet (Microsoft Teams)

Send messages to your bot in your Teams 1:1 or team channel:

| Command / Message | Purpose | Expected Bot Behavior |
|---|---|---|
| `hello` or `hi` | Health check / ping | Replies with greeting: `"Hello! How can I assist you today?"` |
| `Our server is running out of disk space. Please investigate and clean up eligible old test logs.` | Request Remediation | Evaluates runbook policy against target logs, computes eligible space recovery, generates plan with unique `req-<id>` and SHA-256 hash, and marks status as `AWAITING_APPROVAL`. |
| `STATUS req-<id>` | Check status | Displays current plan status (`AWAITING_APPROVAL`, `APPROVED`, `EXECUTED`, `REJECTED`). |
| `APPROVE req-<id>` | Approve execution | Verifies approver Entra ID against allowlist, enforces separation of duties, and triggers remediation execution. |
| `REJECT req-<id>` | Reject execution | Rejects request and permanently terminates the workflow. |
| `PENDING` | View waiting requests | Lists all remediation requests currently awaiting human review. |

---

## 5. Remediation Policy: Target Boundaries

The A7 Remediation Agent operates under strict, fail-closed boundaries defined in `.env` and `src/allina/knowledge/runbooks/disk_cleanup.md`:

| Policy Parameter | Active Configuration | Purpose |
|---|---|---|
| Target Environment | `ALLINA_TARGET_MODE=azure` | Targets Azure VM via Azure Run Command API |
| Target Log Directory | `/opt/allina/test-logs` | Only files inside this absolute directory are evaluated |
| Allowed Prefix | `allina-poc-` | Files not starting with this prefix are strictly ignored |
| Allowed Extension | `.log` | Non-log files are strictly ignored |
| Minimum File Age | `24` hours | Files modified within the last 24 hours are excluded |
| Safety Limits | Max 50 files / Max 10 GB | Hard cap on single execution cleanup volume |
| Dry Run Switch | `ALLINA_DRY_RUN=false` | Real file unlinking allowed if backup/execution confirmed |
| Demo Self-Approval | `ALLINA_ALLOW_SELF_APPROVAL_FOR_DEMO=true` | Allows requester to also approve for demo/testing |

---

## 6. Testing & Validation

Run the automated test suite at any time:

```bash
cd ~/allina_project/allina-teams-bot
source .venv/bin/activate
pytest
```
*Current test pass rate: 62 of 62 passed (100%).*

---

## 7. Configuration Reference (`.env`)

Configuration is stored in `allina-teams-bot/.env`:

```ini
# Microsoft Teams App Registration
CLIENT_ID=<microsoft-bot-client-id>
CLIENT_SECRET=<microsoft-bot-client-secret>
TENANT_ID=<microsoft-entra-tenant-id>

# ALLINA Remediation Target Settings
ALLINA_TARGET_MODE=azure
ALLINA_ALLOWED_LOG_DIR=/opt/allina/test-logs
ALLINA_ALLOWED_LOG_PREFIX=allina-poc-
ALLINA_ALLOWED_LOG_EXTENSION=.log
ALLINA_MIN_FILE_AGE_HOURS=24
ALLINA_MAX_CLEANUP_FILES=50
ALLINA_MAX_CLEANUP_BYTES=10737418240
ALLINA_DRY_RUN=false
ALLINA_ALLOW_SELF_APPROVAL_FOR_DEMO=true

# Azure VM Target
AZURE_TENANT_ID=<azure-tenant-id>
AZURE_CLIENT_ID=<azure-client-id>
AZURE_CLIENT_SECRET=<azure-client-secret>
AZURE_SUBSCRIPTION_ID=<azure-subscription-id>
AZURE_RESOURCE_GROUP=alina-testing_group
AZURE_VM_NAME=alina-testing

# Persistence
ALLINA_DB_PATH=data/allina_poc.db
```

---

## 8. Troubleshooting Guide

### 1. `ExperimentalWarning: SocketModeAdapter...` on startup
- **Cause:** Microsoft Teams SDK preview feature notice.
- **Action:** Normal behavior; the bot is running properly and connected.

### 2. Bot does not reply to messages
- **Check 1:** Ensure process is running: `ps aux | grep main.py`.
- **Check 2:** Check live log output: `tail -n 50 bot.log`.
- **Check 3:** Verify Azure Bot registration in Azure Portal has **Socket Mode** enabled or proper messaging endpoint credentials matching `CLIENT_ID`, `CLIENT_SECRET`, and `TENANT_ID`.

### 3. Permission denied on `/opt/allina/test-logs`
- If testing locally on the VM, ensure permissions allow read/write:
  ```bash
  sudo chown -R azureuser:azureuser /opt/allina/test-logs
  ```

### 4. Restarting the Bot
```bash
pkill -f "python src/main.py"
cd ~/allina_project/allina-teams-bot
source .venv/bin/activate
nohup python src/main.py > bot.log 2>&1 &
```

