"""Teams activity handlers and ALLINA routing integration."""

import asyncio
import logging
from typing import Optional
from microsoft_teams.api import MessageActivity, TypingActivityInput
from microsoft_teams.apps import ActivityContext
from src.allina.agents.remediation_agent import A7RemediationAgent
from src.allina.audit.logger import AuditLogger
from src.allina.config import settings
from src.allina.execution.executor import RemediationExecutor
from src.allina.governance.approvals import ApprovalService
from src.allina.ingress.guard import IngressGuard
from src.allina.ingress.router import CommandRouter
from src.allina.persistence.database import DatabaseManager
from src.allina.persistence.repositories import (
    ApprovalRepository,
    ConversationRepository,
    ExecutionRepository,
    PlanRepository,
    RequestRepository,
)
from src.allina.schemas.approvals import ApprovalDecisionType
from src.allina.schemas.requests import RemediationRequest, RequestStatus

logger = logging.getLogger(__name__)


def _format_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable string."""
    if num_bytes >= 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.2f} KB"
    return f"{num_bytes} B"


class AllinaTeamsDispatcher:
    """Dispatches Teams messages to Ingress Guard, Command Router, and ALLINA workflows."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        remediation_agent: Optional[A7RemediationAgent] = None,
        approval_service: Optional[ApprovalService] = None,
        executor: Optional[RemediationExecutor] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.db = db_manager or DatabaseManager(settings.allina_db_path)
        self.ingress_guard = IngressGuard()
        self.request_repo = RequestRepository(self.db)
        self.plan_repo = PlanRepository(self.db)
        self.approval_repo = ApprovalRepository(self.db)
        self.execution_repo = ExecutionRepository(self.db)
        self.conv_repo = ConversationRepository(self.db)
        self.audit_logger = audit_logger or AuditLogger(self.db)
        self.remediation_agent = remediation_agent or A7RemediationAgent(
            request_repo=self.request_repo, plan_repo=self.plan_repo
        )
        self.approval_service = approval_service or ApprovalService(
            request_repo=self.request_repo,
            plan_repo=self.plan_repo,
            approval_repo=self.approval_repo,
        )
        self.executor = executor or RemediationExecutor(
            request_repo=self.request_repo,
            plan_repo=self.plan_repo,
            execution_repo=self.execution_repo,
            audit_logger=self.audit_logger,
        )

    async def dispatch(self, ctx: ActivityContext[MessageActivity]) -> None:
        """Process incoming Teams message activity."""
        try:
            # 1. Send typing indicator for responsiveness
            try:
                await ctx.reply(TypingActivityInput())
            except Exception:
                pass

            # 2. Ingress Guard validation
            is_valid, reason, incoming = self.ingress_guard.validate_activity(ctx.activity)
            if not is_valid or not incoming:
                await ctx.send(f"⚠️ [ALLINA Ingress Guard] Message rejected: {reason}")
                return

            # 3. Cache conversation context for potential proactive messaging
            self.conv_repo.save_conversation(
                user_aad_id=incoming.sender_aad_id,
                conversation_id=incoming.conversation_id,
                service_url=incoming.service_url,
                tenant_id=incoming.tenant_id,
            )

            # 4. Route command
            command = CommandRouter.parse(incoming.raw_text)

            if command.command_type == "GREETING":
                await ctx.send("Hello! I am the ALLINA IT Operations Assistant. How can I assist you today?")
                return

            if command.command_type == "REMEDIATE":
                # A. Create persistent request
                req = RemediationRequest(
                    tenant_id=incoming.tenant_id,
                    requester_aad_id=incoming.sender_aad_id,
                    requester_name=incoming.sender_name,
                    original_message=incoming.raw_text,
                    status=RequestStatus.RECEIVED,
                )
                self.request_repo.create_request(req)
                self.audit_logger.record_event(
                    request_id=req.request_id,
                    actor_aad_id=incoming.sender_aad_id,
                    event_type="REQUEST_INTAKE",
                    payload={"requester_name": incoming.sender_name, "message": incoming.raw_text},
                )

                # Send immediate progress update so the user gets instant feedback in Teams
                vm_name = settings.azure_vm_name if settings.allina_target_mode == "azure" else settings.allina_ec2_instance_id
                target_label = f"Azure VM `{vm_name}`" if settings.allina_target_mode == "azure" else f"Instance `{vm_name}`"
                await ctx.send(
                    f"🔍 **Investigating Request:** Connecting to {target_label} to inspect disk capacity and test logs...\n\n"
                    "*(Cloud verification in progress, plan generation takes ~25-35 seconds)*"
                )

                # B. Execute A7 read-only investigation and plan generation offloaded to thread
                plan = await asyncio.to_thread(self.remediation_agent.investigate_and_plan, req)
                self.audit_logger.record_event(
                    request_id=req.request_id,
                    actor_aad_id="SYSTEM_A7",
                    event_type="PLAN_GENERATED",
                    payload={
                        "plan_hash": plan.plan_hash,
                        "file_count": len(plan.eligible_files),
                        "estimated_recovery_bytes": plan.estimated_recovery_bytes,
                    },
                )

                # C. Format rich plan summary for Teams
                files_summary = "\n".join(
                    [
                        f"  - `{f.basename}` ({_format_bytes(f.size_bytes)}, age: {f.age_hours}h)"
                        for f in plan.eligible_files
                    ]
                ) or "  - *(No eligible old test log files found matching criteria)*"

                ack_msg = (
                    f"🛡️ **ALLINA A7 Remediation Plan Generated**\n\n"
                    f"- **Request ID:** `{req.request_id}`\n"
                    f"- **Target Instance:** `{plan.target_instance_id}`\n"
                    f"- **Target Directory:** `{plan.target_dir}`\n"
                    f"- **Eligible Files ({len(plan.eligible_files)}):**\n{files_summary}\n"
                    f"- **Estimated Space Recovery:** `{_format_bytes(plan.estimated_recovery_bytes)}`\n"
                    f"- **Plan SHA-256 Hash:** `{plan.plan_hash[:16]}...`\n"
                    f"- **Expires At:** `{plan.expires_at.strftime('%Y-%m-%d %H:%M UTC')}`\n"
                    f"- **Current Status:** `{RequestStatus.AWAITING_APPROVAL.value}`\n\n"
                    f"⚠️ **Approval Required:** Destructive operations require authorization from an authorized approver.\n"
                    f"Approvers can reply: `APPROVE {req.request_id}` or `REJECT {req.request_id}` (or `PENDING` to review)."
                )
                await ctx.send(ack_msg)
                return

            if command.command_type == "PENDING":
                pending = self.request_repo.list_pending_requests()
                if not pending:
                    await ctx.send("ℹ️ No remediation requests are currently awaiting approval.")
                else:
                    lines = ["📋 **Pending Remediation Approvals:**"]
                    for p in pending:
                        plan = self.plan_repo.get_plan(p.request_id)
                        recovery = _format_bytes(plan.estimated_recovery_bytes) if plan else "unknown"
                        lines.append(
                            f"- `{p.request_id}` | Requester: {p.requester_name} | Est. Recovery: {recovery}"
                        )
                    lines.append("\nRespond with `APPROVE <request_id>` or `REJECT <request_id>`.")
                    await ctx.send("\n".join(lines))
                return

            if command.command_type == "STATUS":
                if not command.request_id:
                    await ctx.send("⚠️ Usage: `STATUS <request_id>`")
                    return
                req = self.request_repo.get_request(command.request_id)
                if not req:
                    await ctx.send(f"❌ Request `{command.request_id}` not found.")
                else:
                    plan = self.plan_repo.get_plan(command.request_id)
                    plan_info = ""
                    if plan:
                        plan_info = (
                            f"\n- Target: `{plan.target_instance_id}` ({plan.target_dir})"
                            f"\n- Eligible Files: {len(plan.eligible_files)}"
                            f"\n- Estimated Recovery: {_format_bytes(plan.estimated_recovery_bytes)}"
                            f"\n- Plan Hash: `{plan.plan_hash[:16]}...`"
                        )
                    audit_ok, _ = self.audit_logger.verify_chain_integrity()
                    audit_status = "Verified Intact (SHA-256)" if audit_ok else "Integrity Compromised"
                    await ctx.send(
                        f"📊 **Request Status for `{req.request_id}`:**\n"
                        f"- Status: `{req.status.value}`\n"
                        f"- Requester: {req.requester_name}\n"
                        f"- Created: {req.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
                        f"- Last Updated: {req.updated_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
                        f"- Audit Ledger: `{audit_status}`"
                        f"{plan_info}"
                    )
                return

            if command.command_type in ("APPROVE", "REJECT"):
                if not command.request_id:
                    await ctx.send(f"⚠️ Usage: `{command.command_type} <request_id>`")
                    return

                decision_type = (
                    ApprovalDecisionType.APPROVED
                    if command.command_type == "APPROVE"
                    else ApprovalDecisionType.REJECTED
                )

                result = self.approval_service.process_decision(
                    request_id=command.request_id,
                    approver_aad_id=incoming.sender_aad_id,
                    approver_name=incoming.sender_name,
                    decision_type=decision_type,
                )
                self.audit_logger.record_event(
                    request_id=command.request_id,
                    actor_aad_id=incoming.sender_aad_id,
                    event_type=f"APPROVAL_{decision_type.value}",
                    payload={
                        "decision": decision_type.value,
                        "approver_name": incoming.sender_name,
                        "success": result.success,
                    },
                )
                await ctx.send(result.message)

                # If approved, immediately run execution (dry-run simulation by default)
                if result.success and result.decision == ApprovalDecisionType.APPROVED:
                    vm_name = settings.azure_vm_name if settings.allina_target_mode == "azure" else settings.allina_ec2_instance_id
                    target_label = f"Azure VM `{vm_name}`" if settings.allina_target_mode == "azure" else f"Instance `{vm_name}`"
                    await ctx.send(f"⚡ **Executing Remediation:** Running authorized cleanup workflow on {target_label}...")
                    exec_outcome = await asyncio.to_thread(self.executor.execute, command.request_id)
                    await ctx.send(exec_outcome.message)
                return

            # 5. Fallback: preserve existing Echo Bot behavior
            await ctx.send(f"You said '{incoming.raw_text}'")

        except Exception as e:
            logger.exception("Unexpected error in Teams dispatcher: %s", e)
            await ctx.send(
                "⚠️ [ALLINA Error] An unexpected error occurred while processing your request. "
                "The incident has been logged. Please contact support."
            )

