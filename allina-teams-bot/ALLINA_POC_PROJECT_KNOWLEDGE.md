# ALLINA POC — Project Knowledge & Build Specification

**Purpose:** This file is the project context and source of truth for the first ALLINA proof of concept (POC). Keep it in the project root and read it before making architectural or code changes.

**POC name:** ALLINA A7 Remediation — EC2 Disk-Space / Test-Log Cleanup  
**Status:** Teams bot foundation is working; POC application workflow is the next build stage.  
**Current developer environment:** Windows PowerShell, Python 3.13.14, Node.js 24.14.0, npm 11.9.0, Microsoft Teams CLI 3.1.0.  
**Current project folder:** `D:\allina_project\allina-teams-bot`  
**Current Teams transport:** Socket Mode for local development.

---

## 1. What ALLINA is

ALLINA is an AI-assisted enterprise IT operations platform. It receives requests and system events, identifies the right workflow, retrieves trusted operational context, applies governance and approval rules, executes approved operations through controlled tools, verifies the result, and records what happened.

The central architecture principle is:

> **AI plans; policy authorizes; people approve when required; tested automation executes; everything is recorded.**

AI reasoning must remain separate from privileged execution. An LLM must never receive unrestricted shell, AWS, or infrastructure access. All changes must go through explicit, typed, validated tools and policy checks.

The source architecture describes seven numbered layers, with an Ingress Guard between L1 and L2. Governance (L5) applies across the workflow, not just at one step.

## 2. The first POC — one complete use case

Build only **A7 — Remediation Agent** for one narrowly scoped scenario:

> An employee messages the ALLINA bot in Microsoft Teams: “Our EC2 test server is running out of disk space. Please investigate and safely clean up eligible old test log files.”

The POC should demonstrate the complete path from Teams request intake to a verified AWS-side result and audit record.

### POC target

Use an unused, explicitly allowlisted EC2 instance rather than a fake server. However, the first implementation must be safe by default:

- Read-only inspection is the first live AWS capability.
- Dry-run is the default execution mode.
- Only a dedicated ALLINA POC test-log directory may be inspected for cleanup.
- Do not scan or clean arbitrary system locations such as `/var/log`.
- Only specifically named/generated POC test files (for example, `allina-poc-*.log`) are eligible.
- Never accept an arbitrary filesystem path or shell command from a user or model.
- Real deletion stays disabled until the operator deliberately enables it, the AWS target is allowlisted, the approver is configured, the backup/snapshot safeguard is confirmed, and all tests pass.
- Prefer archiving eligible test files to a separate approved storage location before deletion. If archive is not configured, keep the operation in dry-run; do not quietly treat a same-volume move as freed disk space.

The POC must not modify production systems or real application/system logs.

## 3. User experience: Teams bot is the interface

The user starts a normal **personal chat** with the ALLINA Teams bot and types a request in natural language. No initial Adaptive Card and no Power Automate flow are required for this POC.

The bot should:

1. Receive the user's message through the existing Microsoft Teams Python SDK app.
2. Identify the authenticated Teams sender from the SDK activity/context (do not trust a name, email, or user ID typed in message text).
3. Send the request to the ALLINA workflow/core.
4. Reply with progress and the investigation/plan summary.
5. If approval is required, notify the configured approver and wait for a response.
6. Report the final result to the requester and approver.

### Conversational approval

For the first POC, use plain-text commands in the ALLINA bot conversation:

- `PENDING` — list approvals available to the current authorized approver.
- `APPROVE <request_id>` — approve the exact pending plan.
- `REJECT <request_id>` — reject the exact pending plan.
- Optional later: `STATUS <request_id>` — show request status to an authorized participant.

These commands are **not sufficient by themselves** to authorize an operation. The backend must also validate:

- The message comes from a trusted Teams activity and tenant.
- The sender's Entra/Azure AD object ID is in the configured approver allowlist.
- The approver is not the requester (no self-approval).
- The request exists, is pending, and has not expired.
- The approval is bound to the exact plan hash, target instance, file list, action, and parameters.
- The approval is single-use and cannot be replayed.

Do not implement “anyone who types APPROVE can execute”. Never treat an unverified display name or email as proof of identity.

### Separate requester and approver

The architecture prohibits self-approval. The POC should configure a distinct approver identity using an Entra object ID. Ideally, test with a second Microsoft 365/Teams user who has installed and opened the bot.

The bot may need to send a proactive personal message to that approver. Store the conversation reference/context required by the installed Teams SDK after the approver starts a personal chat with the bot. Inspect the actual SDK APIs in this project before implementing proactive messaging.

If proactive delivery is not supported by the current Socket Mode setup or installed SDK version, implement a safe pull-based fallback: the approver opens the bot chat, sends `PENDING`, and then responds with `APPROVE <request_id>` or `REJECT <request_id>`. Do not silently weaken the authorization rule to make the demo work.

**Adaptive Cards are optional, not the first approval mechanism.** They can later replace the typed commands while calling the same backend approval service. The Teams bot remains the interface in either case.

## 4. End-to-end workflow

1. **Request intake:** Employee sends a natural-language request to the ALLINA personal Teams bot.
2. **Ingress guard:** Validate message size/schema, tenant and authenticated sender; reject malformed or unsafe input; treat user text as untrusted data; apply basic rate limiting.
3. **Request classification:** Determine that the request is an EC2 disk-space investigation/remediation request and route to A7. Unknown or unsupported requests must be declined or handed to a human; do not guess.
4. **Investigation:** Use AWS Systems Manager (SSM) to inspect only the configured EC2 instance and approved POC log directory. Collect disk usage, eligible file metadata and relevant evidence. Initial commands are read-only.
5. **Knowledge retrieval:** Read the local approved Markdown runbook for this use case. The runbook defines permitted directory, filename prefix, minimum age, exclusions, archive/delete sequence, prechecks and postchecks.
6. **Plan creation:** Produce a typed, deterministic plan containing request ID, tenant, requester, approver requirement, EC2 instance ID, region, canonical target directory, exact eligible file paths, file sizes, timestamps, estimated space recovery, operation, preconditions, expiry and risk/approval requirement.
7. **Policy decision:** Default-deny unknown operations. Read-only inspection can be allowed. Any destructive cleanup requires explicit human approval. Do not invent official T0–T3 definitions; the source architecture says these thresholds still need to be defined. Use a clearly labelled POC policy until the formal risk matrix exists.
8. **Approval:** Persist the plan and its SHA-256 hash. Notify the separate configured approver (proactive message if supported; otherwise pull-based `PENDING`). Await a valid approval/rejection command.
9. **Pre-execution re-check:** Revalidate sender/approver, token or approval record, request state, plan hash, target instance, tenant, file list, expiry, allowlist, maintenance/safety settings and execution flag. Re-inspect the exact files and ensure they have not changed since planning.
10. **Execution:** Invoke a fixed, allowlisted SSM operation using Boto3. Never concatenate arbitrary user/model text into a shell command. Execute only against the configured EC2 instance and exact planned files. Require explicit configuration switches for live changes.
11. **Verification:** Re-read disk usage and file state. Confirm the expected files were archived/deleted and actual free space changed as expected. A same-volume move is not proof of recovered disk space.
12. **Rollback/escalation:** If a precheck fails, stop without changes. If execution fails or verification fails, follow the defined restore procedure where available and escalate. Do not claim rollback succeeded unless it is independently verified.
13. **Audit and closeout:** Record request, identities, evidence, plan hash, policy decision, approval, SSM command ID, execution output summary, before/after measurements, verification result and final status. Notify requester and approver in Teams.

## 5. Architecture mapping for this POC

| ALLINA element | POC implementation |
|---|---|
| L1 — Entry point | Existing Microsoft Teams personal bot chat |
| Ingress Guard | Python validation, sender/tenant checks, safe input handling |
| L2 — ALLINA Core | Lightweight request/workflow coordinator; use existing FastAPI/SDK app structure |
| L3 — Specialist | A7 Remediation Agent only |
| L4 — Knowledge | Local Markdown runbook + structured EC2 inspection results |
| L5 — Governance | Python policy service, approver allowlist, plan hash, one-time approval state, audit |
| L6 — Execution | Fixed Boto3/SSM adapter and controlled runbook implementation |
| L7 — Managed system | One explicitly allowlisted unused EC2 instance |
| Closeout | Verification, SQLite audit, Teams response |

The POC can simulate the responsibilities of enterprise-scale components, but must clearly label them as POC implementations. Do not claim SQLite/hash chaining is WORM storage or that a local Python policy module is equivalent to a production OPA deployment.

## 6. Technology choices

### Use now

- Python (the current local environment is Python 3.13.14; check package compatibility before changing versions).
- Existing Microsoft Teams SDK Python scaffold generated by Teams CLI.
- Teams Socket Mode for local development.
- Existing `src/main.py` entry point and current working bot configuration.
- FastAPI/Uvicorn only as already used by the scaffold; inspect before restructuring.
- Pydantic for typed request, plan, approval and result contracts.
- SQLite for durable POC request/approval/workflow/audit state.
- Boto3 for AWS API calls and SSM Run Command.
- LangGraph only if it can be added without destabilizing the working bot; a deterministic, explicit workflow is acceptable for the first vertical slice. Keep LLM reasoning optional and behind an interface.
- Python logging with request/correlation IDs.
- pytest for tests.

### Do not add yet unless genuinely required

- Power Automate.
- A separate Teams bot framework or second bot registration.
- Temporal Server, OPA, MCP Gateway, PostgreSQL, Redis, LiteLLM Proxy, Langfuse, Ragas or enterprise-wide connectors.
- A custom web approval portal.

Keep modules structured so these can be added later without rewriting the POC.

## 7. AWS connectivity and safety prerequisites

Use AWS Systems Manager Run Command, not an inbound SSH port, for the POC.

Before live integration, document and validate:

- The EC2 instance ID and AWS region are explicit configuration values and allowlisted.
- The EC2 instance is managed/online in Systems Manager; SSM Agent is installed and running.
- The instance has an appropriate IAM instance profile/permissions for Systems Manager.
- The local developer's AWS identity/profile can describe the target and send/read only the required SSM commands.
- The instance can reach Systems Manager endpoints (direct network or configured VPC endpoints).
- The target directory exists and is dedicated to ALLINA POC test logs.
- A backup/snapshot/archive safeguard is documented and confirmed before enabling destructive cleanup.

Use the AWS credential provider chain / named AWS profile or AWS IAM Identity Center. Do not place AWS access keys in source code, Teams messages, committed files or logs. Do not print secret values.

### Required configuration (names may be refined, retain equivalent controls)

- `AWS_PROFILE`
- `AWS_REGION`
- `ALLINA_EC2_INSTANCE_ID`
- `ALLINA_ALLOWED_LOG_DIR`
- `ALLINA_ALLOWED_LOG_PREFIX` (default `allina-poc-`)
- `ALLINA_MIN_FILE_AGE_HOURS`
- `ALLINA_APPROVER_AAD_IDS` (one or more trusted Entra object IDs)
- `ALLINA_TENANT_ID`
- `ALLINA_DRY_RUN` (default `true`)
- `ALLINA_ENABLE_REAL_EXECUTION` (default `false`)
- `ALLINA_BACKUP_CONFIRMED` (default `false`; require a recorded backup/snapshot reference before destructive execution)
- `ALLINA_DB_PATH`

Use `.env.example` with placeholders. Keep `.env` ignored by Git. Validate configuration at startup and fail closed if required values are missing.

## 8. Safety and security requirements (non-negotiable)

1. Default deny for unknown actions.
2. Default dry-run; no destructive operation on first launch.
3. One allowlisted EC2 instance only.
4. One dedicated test directory only; no unrestricted `/var/log` or root filesystem cleanup.
5. Restrict file names by configured POC prefix and approved extension; exclude symlinks, active files, and files that fail age/ownership/size checks.
6. The model never generates executable shell commands. Use fixed scripts/commands and validated parameters.
7. Separate read-only inspection from execute/delete permission.
8. Require a separate authenticated approver; reject self-approval.
9. Bind approval to immutable canonical plan hash and expiry; single-use; prevent replay.
10. Re-run policy and all prechecks immediately before execution.
11. Use least-privilege AWS permissions and SSM; never expose AWS credentials to the model or Teams chat.
12. Add a global execution kill switch.
13. Keep audit records append-only at the application level with hash chaining if practical; clearly state this is tamper-evident POC logging, not certified WORM storage.
14. Do not expose internal exception traces, credentials, environment variables or sensitive infrastructure details in Teams replies.
15. Never report success until post-execution verification passes.

## 9. Suggested project structure

Adapt the existing scaffold rather than replacing it. A target structure could be:

```text
allina-teams-bot/
├── src/
│   ├── main.py                      # Preserve existing Teams SDK bootstrap
│   └── allina/
│       ├── config.py
│       ├── schemas/
│       │   ├── requests.py
│       │   ├── plans.py
│       │   └── approvals.py
│       ├── ingress/
│       │   └── guard.py
│       ├── core/
│       │   └── orchestrator.py
│       ├── agents/
│       │   └── remediation_agent.py
│       ├── knowledge/
│       │   └── runbooks/disk_cleanup.md
│       ├── governance/
│       │   ├── policy.py
│       │   └── approvals.py
│       ├── execution/
│       │   ├── ssm_client.py
│       │   ├── commands.py
│       │   └── verification.py
│       ├── persistence/
│       │   ├── database.py
│       │   └── repositories.py
│       ├── audit/
│       │   └── logger.py
│       └── teams/
│           ├── message_handlers.py
│           └── proactive_messaging.py
├── tests/
├── docs/
├── .env.example
├── .gitignore
├── README.md
└── requirements / pyproject configuration (preserve current scaffold convention)
```

Do not create duplicate `main.py` files or replace the working app entry point without inspecting the scaffold.

## 10. Data and state to persist

At minimum persist:

- **Request:** request ID, tenant ID, requester Entra object ID, original message, created time, status.
- **Investigation:** target instance, measurements, evidence references, timestamp.
- **Plan:** canonical plan JSON, plan hash, exact target and files, action, estimated recovery, preconditions, expiry.
- **Approval:** approver Entra object ID, decision, timestamp, plan hash, expiry, one-time state.
- **Execution:** SSM command ID, execution timestamps, bounded output summary, before/after metrics, status.
- **Audit event:** actor, event type, request ID, timestamp, details with secrets removed, previous hash, current event hash.

Suggested statuses: `RECEIVED`, `INVESTIGATING`, `PLAN_READY`, `AWAITING_APPROVAL`, `APPROVED`, `REJECTED`, `EXECUTING`, `VERIFYING`, `SUCCEEDED`, `FAILED`, `ROLLED_BACK`, `ESCALATED`, `EXPIRED`.

Use transactions/atomic state transitions so two approval messages cannot both trigger execution.

## 11. Acceptance criteria

The POC is complete only when these tests pass:

### Teams and intake
- A user can message the installed ALLINA personal bot.
- The bot identifies the sender from trusted Teams activity context.
- A normal request creates a persisted request ID and receives a useful acknowledgement.
- Unrecognized or malformed requests are safely rejected or escalated.

### Investigation and planning
- A7 performs a read-only SSM preflight on the single allowlisted EC2 instance.
- The approved local runbook is retrieved.
- The agent returns a structured plan showing exact eligible test files, file sizes, ages and estimated disk recovery.
- No arbitrary file paths or commands are accepted from the model/user.

### Governance and approval
- Cleanup cannot execute without approval.
- The requester cannot approve their own request.
- An unauthorized Teams user cannot approve.
- Wrong, expired, replayed or plan-mismatched approvals are rejected.
- A rejection stops the workflow and is audited.
- The approver can retrieve pending requests (for example, `PENDING`).

### Execution and verification
- Dry-run works without changing any files.
- Real execution is disabled by default.
- Live execution requires all configured safety switches and an approved plan.
- Only the exact allowlisted POC files on the allowlisted instance can be processed.
- SSM result is polled to a terminal state with timeout handling.
- Verification measures actual disk space before/after and checks file outcomes.
- Failure does not produce a false success response; rollback/restore or escalation is recorded.

### Audit and resilience
- All important transitions are recorded.
- Audit hash-chain validation test detects modified records.
- Restarting the bot does not lose pending request/approval state.
- Repeated or duplicate approval messages do not execute twice.
- Tests run without AWS credentials using mocked SSM responses.

## 12. Implementation order

Build incrementally in this order:

1. Inspect the existing Teams scaffold and run its current Echo Bot test unchanged.
2. Document the existing Teams SDK version, Socket Mode bootstrap, handler API, activity identity fields and proactive messaging API.
3. Preserve the working Teams message response while adding a clean ALLINA message-handler/router.
4. Add Pydantic schemas, configuration validation, SQLite persistence and request IDs.
5. Add deterministic ingress guard and A7 workflow skeleton.
6. Add runbook retrieval and mocked/read-only EC2 investigation.
7. Add plan creation, policy checks, plan hash and persistent approvals.
8. Add trusted-approver conversational flow. Support proactive notification only if the installed SDK supports it; otherwise use the safe `PENDING` pull flow.
9. Add Boto3 SSM read-only adapter, preflight and dry-run.
10. Add tightly gated destructive execution only after all previous tests and prerequisites pass.
11. Add post-checks, rollback/escalation, audit and Teams closeout.
12. Write setup/run instructions, test instructions, AWS prerequisite guide and a short demo script.

At each stage, run tests and keep the existing Echo Bot working.

## 13. Open decisions — do not invent values

These must remain configuration items or be clearly called out for the developer:

- AWS account, region, EC2 instance ID and SSM online status.
- Actual dedicated POC directory on EC2 and test-file naming convention.
- Backup/snapshot/archive approach and reference.
- Authorized approver Entra object ID(s) and tenant ID.
- Whether the SDK's proactive personal messaging works in current Socket Mode setup.
- Formal T0–T3 risk matrix (not defined in the current architecture source).
- Whether/which LLM provider should be used. The POC must work safely without an LLM key; do not make an LLM a prerequisite for basic tests.

Do not fabricate AWS IDs, credentials, Entra IDs, tenant details, app secrets, URLs or production policies.

## 14. Current known-good development state

- Teams CLI scaffold already exists in `D:\allina_project\allina-teams-bot`.
- The user successfully provisioned the Teams app and installed it in personal Teams chat.
- The Echo Bot successfully received and responded to a Teams message.
- Local runtime uses Socket Mode and currently launches from `python src/main.py`.
- `App(socket_mode=True)` was added to the app setup based on the generated scaffold; preserve the working implementation and verify actual installed SDK APIs before changing it.
- Preview warnings such as `ExperimentalTeamsSocketMode` and `ExperimentalTeamsHtmlWidget` appeared, but the bot still worked. Treat them as warnings unless a real exception occurs.
- Do not share `.env` or its secrets. Do not rewrite Teams registration or switch to HTTP mode for this local POC.

## 15. Source alignment

This POC is a deliberately narrow implementation of the user's ALLINA workflow and architecture documents:

- `final_workflow.pdf` — “ALLINA production workflow”.
- `technical_imple_final.pdf` — “Project ALLINA: Architecture and Technical Approach — Detailed Seven-Layer Design”.

The POC implements one A7 use case and simulates some enterprise-scale components. The source documents describe broader architecture and explicitly leave hosting, exact use-case acceptance criteria, provider inventory, approver authority matrix, risk tiers, runner rollout, audit retention and SLOs to be confirmed. Do not present POC shortcuts as production-complete controls.
