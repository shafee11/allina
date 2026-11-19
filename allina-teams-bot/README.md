# ALLINA A7 Remediation POC — Teams Bot

An enterprise AI-assisted IT operations bot integrating Microsoft Teams with AWS Systems Manager (SSM) for controlled, policy-governed remediation.

## Current Stage: Stage 7 Complete — ALLINA A7 POC Ready

- **Microsoft Teams Integration:** Native Microsoft Teams bot running in Socket Mode via `src/main.py`. Echo Bot preserved as fallback.
- **Ingress Guard:** Validates authenticated Entra Object IDs (`from_.aad_object_id`), checks message size, and enforces Microsoft 365 tenant boundary.
- **Command Routing:** Differentiates conversational commands (`PENDING`, `APPROVE <id>`, `REJECT <id>`, `STATUS <id>`), remediation requests, and greetings.
- **A7 Remediation Agent:** Reads approved Markdown runbook (`src/allina/knowledge/runbooks/disk_cleanup.md`), collects read-only evidence, enforces multi-layered filesystem safeguards (prefix, extension, age, path traversal and symlink rejection), and synthesizes deterministic plans.
- **Governance Engine:** Enforces separation of duties (blocks self-approval), checks Entra ID approver allowlist, rechecks cryptographic SHA-256 plan hash, and prevents duplicate/replayed approvals via atomic SQLite CAS transitions.
- **AWS Systems Manager Execution:** Boto3 SSM adapter with preflight online instance check, allowlisted static scripts with parameter sanitization, and fail-closed defaults.
- **Dry-Run Simulation:** Simulates remediation safely without modifying EC2 files by default.
- **Gated Live Execution:** Destructive deletion guarded behind three explicit switches (`ALLINA_DRY_RUN=false`, `ALLINA_ENABLE_REAL_EXECUTION=true`, and `ALLINA_BACKUP_CONFIRMED=true`).
- **Post-Execution Verification:** Independently verifies file unlinking, available space delta, and blocks same-volume move fraud.
- **Tamper-Evident Audit Ledger:** Cryptographic SHA-256 hash-chained append-only event log with automatic secret masking and integrity validation (`verify_chain_integrity()`).
- **Resilience:** Durable SQLite persistence across process restarts; fail-safe error boundaries prevent raw stack traces or credential leaks in Teams.
- **Documentation:** Full setup guide in `docs/AWS_SSM_PREREQUISITES.md`, step-by-step interactive demo script in `docs/DEMO_SCRIPT.md`, and architectural roadmap in `docs/IMPLEMENTATION_PLAN.md`.
- **Test Coverage:** All 55 automated tests passing with 100% success rate.

## Project Structure

- `src/main.py`: Entry point and Microsoft Teams app bootstrap (Socket Mode).
- `src/allina/`: ALLINA remediation agent modules (schemas, ingress, core, agents, governance, execution, persistence, audit, teams).
- `docs/IMPLEMENTATION_PLAN.md`: Full architectural specification and roadmap.
- `tests/`: Automated test suite.
- `.env.example`: Configuration template with safe placeholders.
- `pyproject.toml`: Dependencies and project metadata.

## Setup & Running (Windows PowerShell)

1. Activate virtual environment:
   ```powershell
   .venv\Scripts\Activate.ps1
   ```
2. Install dependencies:
   ```powershell
   pip install -e .
   ```
3. Run tests:
   ```powershell
   pytest
   ```
4. Run the Teams bot:
   ```powershell
   python src/main.py
   ```
