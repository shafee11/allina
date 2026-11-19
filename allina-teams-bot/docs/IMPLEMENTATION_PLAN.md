# ALLINA A7 EC2 Remediation POC — Implementation Plan

**Project:** ALLINA A7 Remediation — EC2 Disk-Space / Test-Log Cleanup  
**Current Phase:** Stage 1 — Scaffold Inspection, SDK Recording & Baseline Architecture  
**Target Project Directory:** `D:\allina_project\allina-teams-bot`  
**Execution Environment:** Windows PowerShell, Python 3.13.14, Microsoft Teams SDK (`microsoft-teams-apps 2.2.1`), Socket Mode  

---

## 1. Existing Scaffold Summary

The project was bootstrapped using the Microsoft Teams CLI Python Echo Bot template:
- **`src/main.py`**: Minimal entry point that creates `App(socket_mode=True)` and defines message handlers:
  - `@app.on_message_pattern(re.compile(r"hello|hi|greetings"))`: Responds to greeting patterns.
  - `@app.on_message`: Echoes the user's message via `ctx.reply(TypingActivityInput())` and `ctx.send(...)`.
  - `asyncio.run(app.start())`: Starts the bot in Socket Mode (no public HTTP webhook or dev tunnels required).
- **`pyproject.toml`**: Configured with Hatchling build backend, Python `>=3.12`.
- **Status of Working Bot:** App provisioned and installed in personal Teams chat; successfully receiving and replying to messages.

---

## 2. Teams SDK 2.2.1 API Discovery & Verification

Live inspection of `microsoft-teams-apps 2.2.1`, `microsoft-teams-api 2.2.1`, and dependencies confirmed the exact signatures and attributes:

### A. Message Activity & Identity Extraction
- **Activity Context:** Passed to message handlers as `ctx: ActivityContext[MessageActivity]`.
- **Message Activity:** `ctx.activity` of type `MessageActivity`.
  - `ctx.activity.text`: The message text string.
  - `ctx.activity.from_`: Account object containing authenticated sender details:
    - `ctx.activity.from_.aad_object_id`: **Entra / Azure AD Object ID** (trusted identity used for authorization allowlists).
    - `ctx.activity.from_.id`: Teams User ID.
    - `ctx.activity.from_.name`: User display name (for friendly UI rendering only; never trusted for authorization).
    - `ctx.activity.from_.tenant_id`: Microsoft 365 Tenant ID.
  - `ctx.activity.conversation`: ConversationAccount object:
    - `ctx.activity.conversation.id`: Unique conversation thread ID.
    - `ctx.activity.conversation.tenant_id`: Tenant ID of the conversation.
    - `ctx.activity.conversation.conversation_type`: e.g., `"personal"`.
  - `ctx.activity.service_url`: Service URL for Teams outbound message delivery.

### B. Replying & Message Handling
- Within handler:
  - `await ctx.send(text_or_activity)`: Sends a message to the current conversation.
  - `await ctx.reply(text_or_activity)`: Sends a reply in context.
- Pattern matching:
  - `@app.on_message_pattern(regex_pattern)`: Intercepts messages matching regex.
  - `@app.on_message`: Catch-all message handler.

### C. Proactive Notification vs. Conversational Pull
- `App.send(conversation_id, activity, *, service_url=None)` is supported by the SDK.
- **Proactive Notification Strategy:**
  1. Capture and store `conversation_id` and `service_url` when any authorized user (requester or approver) interacts with the bot.
  2. When a plan is created, attempt proactive delivery to the designated approver's stored conversation.
  3. **Pull-Mode Fallback:** If proactive delivery is unsupported in the current Socket Mode session or the approver has not yet messaged the bot, the approver can open the bot chat and send `PENDING` to list actionable requests.

---

## 3. Proposed Module Architecture (`src/allina/`)

The architecture strictly decouples intake, planning, policy, approval, execution, and audit:

```
src/allina/
├── config.py                    # Validated Pydantic BaseSettings (fail-closed defaults)
├── schemas/
│   ├── requests.py              # IngressRequest, RequestStatus, RequestRecord
│   ├── plans.py                 # FileMetadata, RemediationPlan, DiskMetrics
│   ├── approvals.py             # ApprovalDecision, ApprovalRecord
│   └── execution.py             # SSMExecutionResult, VerificationResult
├── ingress/
│   ├── guard.py                 # Tenant validation, size limits, input sanitization
│   └── router.py                # Routes commands (PENDING, APPROVE, REJECT, STATUS, REMEDIATE, ECHO)
├── core/
│   └── orchestrator.py          # State transitions, workflow coordination, notification dispatch
├── agents/
│   └── remediation_agent.py     # Preflight check, evidence gathering, plan synthesis
├── knowledge/
│   └── runbooks/
│       └── disk_cleanup.md      # Approved constraints (prefix, age, directory, exclusions)
├── governance/
│   ├── policy.py                # POC policy evaluation, risk classification
│   └── approvals.py             # Approver allowlist check, self-approval prevention, plan hash check
├── execution/
│   ├── ssm_client.py            # Boto3 SSM wrapper (read-only and execution commands)
│   ├── commands.py              # Fixed, allowlisted shell commands / scripts (no arbitrary shell)
│   └── verification.py          # Before/after disk metric delta verification
├── persistence/
│   ├── database.py              # SQLite connection, schema migrations, WAL mode
│   └── repositories.py          # Atomic CRUD for requests, plans, approvals
├── audit/
│   └── logger.py                # Append-only hash-chained audit logger
└── teams/
    ├── message_handlers.py      # Translates Teams activities to/from ALLINA domain
    └── proactive_messaging.py   # Proactive notification adapter with pull fallback
```

---

## 4. Safety & Governance Architecture

1. **Deterministic Execution (No LLM Shell Access):**
   - The model never generates shell scripts or commands.
   - All SSM executions use static, reviewed shell commands with parameters bound to strict allowlists.
2. **Strict Filesystem Boundaries:**
   - Single target instance: `ALLINA_EC2_INSTANCE_ID`.
   - Single target directory: `ALLINA_ALLOWED_LOG_DIR` (e.g. `/opt/allina/test-logs`).
   - Strict file naming: `ALLINA_ALLOWED_LOG_PREFIX` (default `allina-poc-*.log`).
   - Rejection of symlinks, hardlinks, directory traversal (`../`), active open files, and files newer than `ALLINA_MIN_FILE_AGE_HOURS`.
3. **Fail-Closed Operational Controls:**
   - Defaults: `ALLINA_DRY_RUN=true`, `ALLINA_ENABLE_REAL_EXECUTION=false`, `ALLINA_BACKUP_CONFIRMED=false`.
   - In dry-run mode, planning and approvals succeed normally, but SSM execution is simulated.
   - Destructive cleanup requires explicit operator enablement in `.env`.
4. **Approval Integrity & Anti-Replay Protection:**
   - Separate approver required (`requester_aad_id != approver_aad_id`).
   - Approver must be in `ALLINA_APPROVER_AAD_IDS`.
   - Approval is cryptographically bound to `plan_hash = SHA256(canonical_json(plan))`.
   - Atomic database state transition prevents duplicate or replayed execution.
5. **Tamper-Evident Audit Ledger:**
   - SQLite append-only table `audit_events` with SHA-256 hash chaining:
     `event_hash = SHA256(prev_hash + actor_id + event_type + timestamp + payload_json)`

---

## 5. Risk Analysis & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Accidental deletion of production logs | Severe data loss | Multi-layered path containment: fixed directory allowlist, prefix filter, symlink rejection, and fail-closed flags (`ALLINA_DRY_RUN=true`). |
| Command injection via Teams message | Remote code execution | Ingress Guard treats all user input as untrusted. Parameters never pass into shell commands; shell scripts are static. |
| Requester approves own request | Bypassed authorization | Server-side validation of authenticated Entra Object ID rejects any request where requester equals approver. |
| Replayed or duplicate approval messages | Double execution | SQLite atomic compare-and-swap (`UPDATE ... WHERE status = 'AWAITING_APPROVAL'`) ensures one-time execution. |
| Proactive messaging failure in Socket Mode | Approver unnotified | Conversational pull command (`PENDING`) provides a complete, independent fallback. |
| False success reported | Untracked failure | Verification phase measures disk utilization delta and checks file deletion before declaring success. |

---

## 6. Staged Implementation Roadmap

- **Stage 1 (Current):** Scaffold inspection, Teams SDK API recording, baseline project layout, `.env.example`, `.gitignore`, baseline tests.
- **Stage 2:** Pydantic schemas, SQLite persistence layer with atomic transitions, Ingress Guard, Teams router.
- **Stage 3:** Approved runbook (`disk_cleanup.md`), A7 Remediation Agent, read-only SSM inspection, deterministic plan synthesis and SHA-256 hashing.
- **Stage 4:** Governance policy, approver allowlist, conversational approval commands (`PENDING`, `APPROVE <id>`, `REJECT <id>`).
- **Stage 5:** Boto3 SSM client, preflight instance validation, dry-run simulation mode.
- **Stage 6:** Post-execution verification, hash-chained audit logger, full test suite with mocked SSM fixtures.
- **Stage 7:** Gated live execution adapter, AWS prerequisite guide, demo script, and final status documentation.

