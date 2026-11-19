from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from src.allina.config import settings
from src.allina.governance.approvals import ApprovalService
from src.allina.persistence.database import DatabaseManager
from src.allina.persistence.repositories import (
    ApprovalRepository,
    PlanRepository,
    RequestRepository,
)
from src.allina.schemas.approvals import ApprovalDecisionType
from src.allina.schemas.plans import FileCandidate, RemediationPlan
from src.allina.schemas.requests import RemediationRequest, RequestStatus


def _setup_service(allowlist=None):
    db = DatabaseManager(":memory:")
    req_repo = RequestRepository(db)
    plan_repo = PlanRepository(db)
    appr_repo = ApprovalRepository(db)
    service = ApprovalService(
        request_repo=req_repo,
        plan_repo=plan_repo,
        approval_repo=appr_repo,
        approver_allowlist=allowlist if allowlist is not None else ["auth-approver-aad-1", "auth-approver-aad-2"],
    )
    return service, req_repo, plan_repo, appr_repo


def _create_pending_request_and_plan(req_repo, plan_repo, requester="requester-alice", expired=False):
    req_id = "req-gov-001"
    req = RemediationRequest(
        request_id=req_id,
        requester_aad_id=requester,
        requester_name="Alice Requester",
        original_message="Clean EC2 logs",
        status=RequestStatus.AWAITING_APPROVAL,
    )
    req_repo.create_request(req)

    now = datetime.now(timezone.utc)
    expires_at = now - timedelta(minutes=5) if expired else now + timedelta(minutes=30)

    plan = RemediationPlan(
        request_id=req_id,
        target_instance_id="i-testinstance",
        target_dir="/opt/allina/test-logs",
        eligible_files=[
            FileCandidate(
                path="/opt/allina/test-logs/allina-poc-1.log",
                basename="allina-poc-1.log",
                size_bytes=1024,
                mtime=now - timedelta(hours=48),
                age_hours=48.0,
            )
        ],
        estimated_recovery_bytes=1024,
        created_at=now - timedelta(minutes=10),
        expires_at=expires_at,
    ).finalize_hash()
    plan_repo.save_plan(plan)
    return req, plan


def test_valid_approval_succeeds_and_transitions_state():
    service, req_repo, plan_repo, appr_repo = _setup_service()
    req, plan = _create_pending_request_and_plan(req_repo, plan_repo)

    result = service.process_decision(
        request_id=req.request_id,
        approver_aad_id="auth-approver-aad-1",
        approver_name="Bob Approver",
        decision_type=ApprovalDecisionType.APPROVED,
    )

    assert result.success is True
    assert result.decision == ApprovalDecisionType.APPROVED
    assert "Approved" in result.message

    # Verify atomic state transition in DB
    updated_req = req_repo.get_request(req.request_id)
    assert updated_req.status == RequestStatus.APPROVED

    # Verify decision record in DB
    decision = appr_repo.get_decision(req.request_id)
    assert decision is not None
    assert decision.approver_aad_id == "auth-approver-aad-1"
    assert decision.decision == ApprovalDecisionType.APPROVED


def test_self_approval_is_blocked():
    service, req_repo, plan_repo, _ = _setup_service(allowlist=["requester-alice"])
    req, _ = _create_pending_request_and_plan(req_repo, plan_repo, requester="requester-alice")

    result = service.process_decision(
        request_id=req.request_id,
        approver_aad_id="requester-alice",  # Same as requester
        approver_name="Alice Requester",
        decision_type=ApprovalDecisionType.APPROVED,
    )

    assert result.success is False
    assert "Governance Violation" in result.message
    assert "cannot approve their own" in result.message

    # Status must remain unchanged
    assert req_repo.get_request(req.request_id).status == RequestStatus.AWAITING_APPROVAL


def test_unauthorized_approver_is_blocked():
    service, req_repo, plan_repo, _ = _setup_service()
    req, _ = _create_pending_request_and_plan(req_repo, plan_repo)

    result = service.process_decision(
        request_id=req.request_id,
        approver_aad_id="random-unauthorized-user",
        approver_name="Eve Intruder",
        decision_type=ApprovalDecisionType.APPROVED,
    )

    assert result.success is False
    assert "Unauthorized Approver" in result.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.AWAITING_APPROVAL


def test_replay_approval_is_blocked():
    service, req_repo, plan_repo, _ = _setup_service()
    req, _ = _create_pending_request_and_plan(req_repo, plan_repo)

    # First approval succeeds
    res1 = service.process_decision(
        request_id=req.request_id,
        approver_aad_id="auth-approver-aad-1",
        approver_name="Bob Approver",
        decision_type=ApprovalDecisionType.APPROVED,
    )
    assert res1.success is True

    # Replayed second approval fails
    res2 = service.process_decision(
        request_id=req.request_id,
        approver_aad_id="auth-approver-aad-2",
        approver_name="Charlie Approver",
        decision_type=ApprovalDecisionType.APPROVED,
    )
    assert res2.success is False
    assert "Approval Rejected" in res2.message
    assert "is not awaiting approval" in res2.message


def test_expired_plan_is_rejected():
    service, req_repo, plan_repo, _ = _setup_service()
    req, _ = _create_pending_request_and_plan(req_repo, plan_repo, expired=True)

    result = service.process_decision(
        request_id=req.request_id,
        approver_aad_id="auth-approver-aad-1",
        approver_name="Bob Approver",
        decision_type=ApprovalDecisionType.APPROVED,
    )

    assert result.success is False
    assert "expired" in result.message.lower()
    assert req_repo.get_request(req.request_id).status == RequestStatus.EXPIRED


def test_rejection_decision_transitions_to_rejected():
    service, req_repo, plan_repo, appr_repo = _setup_service()
    req, _ = _create_pending_request_and_plan(req_repo, plan_repo)

    result = service.process_decision(
        request_id=req.request_id,
        approver_aad_id="auth-approver-aad-1",
        approver_name="Bob Approver",
        decision_type=ApprovalDecisionType.REJECTED,
        reason="Scheduled maintenance window closed",
    )

    assert result.success is True
    assert result.decision == ApprovalDecisionType.REJECTED
    assert "Rejected" in result.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.REJECTED

    decision = appr_repo.get_decision(req.request_id)
    assert decision.decision == ApprovalDecisionType.REJECTED
    assert decision.reason == "Scheduled maintenance window closed"


def test_self_approval_permitted_when_demo_flag_enabled():
    """When demo flag is enabled, the requester can approve their own request."""
    service, req_repo, plan_repo, _ = _setup_service(allowlist=[])
    req, plan = _create_pending_request_and_plan(req_repo, plan_repo, requester="requester-alice")

    with patch.object(settings, "allina_allow_self_approval_for_demo", True):
        result = service.process_decision(
            request_id=req.request_id,
            approver_aad_id="requester-alice",
            approver_name="Alice Requester",
            decision_type=ApprovalDecisionType.APPROVED,
        )

    assert result.success is True
    assert "Approved" in result.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.APPROVED


def test_colleague_approval_permitted_when_demo_flag_enabled_empty_allowlist():
    """When demo flag is enabled and allowlist is empty, an independent colleague can also approve."""
    service, req_repo, plan_repo, _ = _setup_service(allowlist=[])
    req, plan = _create_pending_request_and_plan(req_repo, plan_repo, requester="requester-alice")

    with patch.object(settings, "allina_allow_self_approval_for_demo", True):
        result = service.process_decision(
            request_id=req.request_id,
            approver_aad_id="colleague-bob",
            approver_name="Bob Colleague",
            decision_type=ApprovalDecisionType.APPROVED,
        )

    assert result.success is True
    assert "Approved" in result.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.APPROVED


def test_colleague_approval_governed_when_allowlist_configured():
    """When explicit allowlist is configured in demo mode, colleague must be in allowlist while requester is also allowed."""
    service, req_repo, plan_repo, _ = _setup_service(allowlist=["authorized-colleague-bob"])
    req, plan = _create_pending_request_and_plan(req_repo, plan_repo, requester="requester-alice")

    with patch.object(settings, "allina_allow_self_approval_for_demo", True):
        # 1. Requester can approve
        # 2. Authorized colleague can approve
        res_colleague = service.process_decision(
            request_id=req.request_id,
            approver_aad_id="authorized-colleague-bob",
            approver_name="Bob Colleague",
            decision_type=ApprovalDecisionType.APPROVED,
        )
        assert res_colleague.success is True

    # 3. Create another request to test unauthorized colleague
    req2 = RemediationRequest(
        request_id="req-gov-002",
        requester_aad_id="requester-alice",
        requester_name="Alice Requester",
        original_message="Clean logs",
        status=RequestStatus.AWAITING_APPROVAL,
    )
    req_repo.create_request(req2)
    plan2 = RemediationPlan(
        request_id=req2.request_id,
        target_instance_id="i-testinstance",
        target_dir="/opt/allina/test-logs",
        eligible_files=[],
        estimated_recovery_bytes=0,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    ).finalize_hash()
    plan_repo.save_plan(plan2)

    with patch.object(settings, "allina_allow_self_approval_for_demo", True):
        res_unauthorized = service.process_decision(
            request_id=req2.request_id,
            approver_aad_id="unauthorized-colleague-charlie",
            approver_name="Charlie",
            decision_type=ApprovalDecisionType.APPROVED,
        )
        assert res_unauthorized.success is False
        assert "Unauthorized Approver" in res_unauthorized.message


