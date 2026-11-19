"""Approval management service enforcing separation of duties and atomic transitions."""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel
from src.allina.config import settings
from src.allina.governance.policy import GovernancePolicyEngine
from src.allina.persistence.repositories import (
    ApprovalRepository,
    PlanRepository,
    RequestRepository,
)
from src.allina.schemas.approvals import ApprovalDecisionType, ApprovalRecord
from src.allina.schemas.plans import compute_plan_hash
from src.allina.schemas.requests import RequestStatus


class ApprovalServiceResult(BaseModel):
    """Result of an approval/rejection command evaluation."""

    success: bool
    request_id: str
    decision: Optional[ApprovalDecisionType] = None
    message: str
    plan_hash: Optional[str] = None


class ApprovalService:
    """Orchestrates human approvals, security validations, and atomic transitions."""

    def __init__(
        self,
        request_repo: RequestRepository,
        plan_repo: PlanRepository,
        approval_repo: ApprovalRepository,
        approver_allowlist: Optional[List[str]] = None,
    ):
        self.request_repo = request_repo
        self.plan_repo = plan_repo
        self.approval_repo = approval_repo
        self.approver_allowlist = approver_allowlist

    def process_decision(
        self,
        request_id: str,
        approver_aad_id: str,
        approver_name: str,
        decision_type: ApprovalDecisionType,
        reason: Optional[str] = None,
    ) -> ApprovalServiceResult:
        """
        Validate and apply an approval or rejection command.
        Enforces separation of duties, expiry, plan-hash binding, and atomic CAS transitions.
        """
        # 1. Fetch request
        req = self.request_repo.get_request(request_id)
        if not req:
            return ApprovalServiceResult(
                success=False,
                request_id=request_id,
                message=f"❌ Request `{request_id}` not found.",
            )

        # 2. Anti-Replay / State Check
        if req.status != RequestStatus.AWAITING_APPROVAL:
            return ApprovalServiceResult(
                success=False,
                request_id=request_id,
                message=(
                    f"⚠️ Approval Rejected: Request `{request_id}` is not awaiting approval "
                    f"(current status: `{req.status.value}`). Duplicate or replayed commands are prevented."
                ),
            )

        # 3. Fetch plan
        plan = self.plan_repo.get_plan(request_id)
        if not plan:
            return ApprovalServiceResult(
                success=False,
                request_id=request_id,
                message=f"❌ No remediation plan found for request `{request_id}`.",
            )

        # 4. Expiry Check
        now = datetime.now(timezone.utc)
        if now > plan.expires_at:
            self.request_repo.update_status(request_id, RequestStatus.EXPIRED)
            return ApprovalServiceResult(
                success=False,
                request_id=request_id,
                message=(
                    f"⏳ Approval Rejected: Remediation plan for `{request_id}` expired at "
                    f"{plan.expires_at.strftime('%Y-%m-%d %H:%M UTC')}. Request transitioned to EXPIRED."
                ),
            )

        # 5. Anti-Self-Approval
        is_self_approval = approver_aad_id == req.requester_aad_id
        if is_self_approval and not settings.allina_allow_self_approval_for_demo:
            return ApprovalServiceResult(
                success=False,
                request_id=request_id,
                message=(
                    f"⛔ Governance Violation: Requester ({approver_name}) cannot approve their own "
                    f"remediation request. An independent authorized approver is required."
                ),
            )

        # 6. Approver Allowlist Check
        allowlist = (
            self.approver_allowlist
            if self.approver_allowlist is not None
            else settings.allina_approver_aad_ids
        )
        if allowlist:
            # If an explicit allowlist is configured, verify approver authorization.
            # In demo mode, if the requester is self-approving, allow them even if not in allowlist.
            if is_self_approval and settings.allina_allow_self_approval_for_demo:
                pass
            elif not GovernancePolicyEngine.is_approver_authorized(approver_aad_id, allowlist):
                return ApprovalServiceResult(
                    success=False,
                    request_id=request_id,
                    message=(
                        f"⛔ Unauthorized Approver: User '{approver_name}' (`{approver_aad_id}`) is not in "
                        f"the authorized approver allowlist."
                    ),
                )
        else:
            # Allowlist is empty / not configured
            # If demo mode is active, allow any authenticated tenant user (requester or colleague) to approve.
            # In strict production mode, fail closed.
            if not settings.allina_allow_self_approval_for_demo:
                return ApprovalServiceResult(
                    success=False,
                    request_id=request_id,
                    message=(
                        f"⛔ Unauthorized Approver: User '{approver_name}' (`{approver_aad_id}`) is not in "
                        f"the authorized approver allowlist."
                    ),
                )

        # 7. Plan Hash Integrity Verification
        computed_hash = compute_plan_hash(plan.model_dump())
        if computed_hash != plan.plan_hash:
            return ApprovalServiceResult(
                success=False,
                request_id=request_id,
                message=(
                    f"🚨 Plan Integrity Violation: Computed plan hash does not match stored canonical hash. "
                    f"The plan may have been tampered with."
                ),
            )

        # 8. Atomic State Transition (Compare-And-Swap)
        new_status = (
            RequestStatus.APPROVED
            if decision_type == ApprovalDecisionType.APPROVED
            else RequestStatus.REJECTED
        )
        transition_succeeded = self.request_repo.update_status(
            request_id=request_id,
            new_status=new_status,
            expected_status=RequestStatus.AWAITING_APPROVAL,
        )

        if not transition_succeeded:
            return ApprovalServiceResult(
                success=False,
                request_id=request_id,
                message=(
                    f"⚠️ Concurrency Conflict: Request `{request_id}` status changed concurrently. "
                    f"Approval transition aborted."
                ),
            )

        # 9. Record durable approval decision
        record = ApprovalRecord(
            request_id=request_id,
            approver_aad_id=approver_aad_id,
            approver_name=approver_name,
            decision=decision_type,
            plan_hash=plan.plan_hash,
            reason=reason or f"Conversational {decision_type.value} command via Teams",
            decided_at=now,
        )
        self.approval_repo.record_decision(record)

        if decision_type == ApprovalDecisionType.APPROVED:
            return ApprovalServiceResult(
                success=True,
                request_id=request_id,
                decision=decision_type,
                plan_hash=plan.plan_hash,
                message=(
                    f"✅ **Request `{request_id}` Approved** by {approver_name}.\n"
                    f"- Plan Hash Verified: `{plan.plan_hash[:16]}...`\n"
                    f"- Status: `{RequestStatus.APPROVED.value}`\n"
                    f"- Target: `{plan.target_instance_id}` ({len(plan.eligible_files)} files)\n"
                    f"Ready for controlled SSM execution."
                ),
            )
        else:
            return ApprovalServiceResult(
                success=True,
                request_id=request_id,
                decision=decision_type,
                plan_hash=plan.plan_hash,
                message=(
                    f"🛑 **Request `{request_id}` Rejected** by {approver_name}.\n"
                    f"- Status: `{RequestStatus.REJECTED.value}`\n"
                    f"Remediation workflow terminated. No modifications will be made."
                ),
            )

