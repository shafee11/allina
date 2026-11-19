# ALLINA A7 Remediation POC — Interactive Demo Script

**Scenario:** EC2 Disk-Space / Dedicated Test-Log Remediation  
**Target:** Microsoft Teams Personal Bot Chat  
**Actors:**
- **Alice (Requester):** Cloud Ops Engineer (`user-alice`)
- **Bob (Approver):** Authorized IT Ops Lead (`approver-bob`, listed in `ALLINA_APPROVER_AAD_IDS`)

---

## Act 1: Request Intake & A7 Investigation

### 1.1 Alice sends natural language request in Teams
In the 1:1 personal chat with the ALLINA bot, Alice types:

> **Alice:** `Our EC2 test server is running out of disk space. Please investigate and safely clean up eligible old test log files.`

### 1.2 Bot acknowledges, investigates, and produces plan
The bot identifies Alice's Entra ID from the Teams activity context, validates tenant boundary, evaluates the approved runbook (`RB-A7-EC2-DISK-CLEANUP-001`), and replies in Teams:

> **ALLINA Bot:**
> ```text
> 🛡️ ALLINA A7 Remediation Plan Generated
> 
> - Request ID: req-4c7b8219de01
> - Target Instance: i-0123456789abcdef0
> - Target Directory: /opt/allina/test-logs
> - Eligible Files (3):
>   - allina-poc-service1.log (450.00 MB, age: 72.0h)
>   - allina-poc-batch-worker.log (820.00 MB, age: 48.0h)
>   - allina-poc-stress-eval.log (1.17 GB, age: 36.0h)
> - Estimated Space Recovery: 2.41 GB
> - Plan SHA-256 Hash: a891f7c32e04bf91...
> - Expires At: 2026-11-13 13:45 UTC
> - Current Status: AWAITING_APPROVAL
> 
> ⚠️ Approval Required: Destructive operations require authorization from an authorized approver.
> Approvers can reply: APPROVE req-4c7b8219de01 or REJECT req-4c7b8219de01 (or PENDING to review).
> ```

---

## Act 2: Governance Enforcement (Anti-Self-Approval)

### 2.1 Alice attempts to approve her own request
Alice tries to approve:

> **Alice:** `APPROVE req-4c7b8219de01`

### 2.2 Bot blocks self-approval
> **ALLINA Bot:**
> ```text
> ⛔ Governance Violation: Requester (Alice) cannot approve their own remediation request.
> An independent authorized approver is required.
> ```

---

## Act 3: Independent Approver Review

### 3.1 Bob checks pending approvals
Bob (authorized approver) opens his personal chat with the ALLINA bot:

> **Bob:** `PENDING`

### 3.2 Bot returns pending queue
> **ALLINA Bot:**
> ```text
> 📋 Pending Remediation Approvals:
> - req-4c7b8219de01 | Requester: Alice | Est. Recovery: 2.41 GB
> 
> Respond with APPROVE <request_id> or REJECT <request_id>.
> ```

### 3.3 Bob approves the request
> **Bob:** `APPROVE req-4c7b8219de01`

### 3.4 Bot validates approver, verifies plan hash, and simulates execution
The bot validates Bob's Entra ID against `ALLINA_APPROVER_AAD_IDS`, rechecks the plan hash, commits atomic approval, and runs the dry-run simulation:

> **ALLINA Bot (Message 1 — Approval):**
> ```text
> ✅ Request req-4c7b8219de01 Approved by Bob.
> - Plan Hash Verified: a891f7c32e04bf91...
> - Status: APPROVED
> - Target: i-0123456789abcdef0 (3 files)
> Ready for controlled SSM execution.
> ```

> **ALLINA Bot (Message 2 — Execution Outcome):**
> ```text
> 🔬 [DRY-RUN COMPLETE] Remediation Simulated & Verified
> 
> - Request ID: req-4c7b8219de01
> - Target Instance: i-0123456789abcdef0
> - Execution Mode: DRY-RUN (Simulation)
> - Simulated Cleanup: 3 files (2.41 GB)
> - Verification: PASSED (Dry-run simulation verified. 3 files (2.41 GB) identified for safe cleanup.)
> - Final Status: SUCCEEDED
> - Audit Chain Hash: f54a93c72b1008d1...
> 
> ℹ️ Note: Live deletion is disabled (ALLINA_DRY_RUN=true). Target files remain untouched on the EC2 instance.
> ```

---

## Act 4: Audit & Status Inspection

### 4.1 Alice inspects the final status
Alice checks the completed request:

> **Alice:** `STATUS req-4c7b8219de01`

### 4.2 Bot returns verified status and cryptographic audit confirmation
> **ALLINA Bot:**
> ```text
> 📊 Request Status for req-4c7b8219de01:
> - Status: SUCCEEDED
> - Requester: Alice
> - Created: 2026-11-13 13:15 UTC
> - Last Updated: 2026-11-13 13:20 UTC
> - Audit Ledger: Verified Intact (SHA-256)
> - Target: i-0123456789abcdef0 (/opt/allina/test-logs)
> - Eligible Files: 3
> - Estimated Recovery: 2.41 GB
> - Plan Hash: a891f7c32e04bf91...
> ```

---

## Act 5: Anti-Replay Protection Demo

### 5.1 Bob accidentally re-sends approval
> **Bob:** `APPROVE req-4c7b8219de01`

### 5.2 Bot denies duplicate execution
> **ALLINA Bot:**
> ```text
> ⚠️ Approval Rejected: Request req-4c7b8219de01 is not awaiting approval (current status: SUCCEEDED).
> Duplicate or replayed commands are prevented.
> ```

