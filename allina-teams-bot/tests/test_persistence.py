from datetime import datetime, timezone, timedelta
from src.allina.persistence.database import DatabaseManager
from src.allina.persistence.repositories import (
    RequestRepository,
    PlanRepository,
    ConversationRepository,
)
from src.allina.schemas.requests import RemediationRequest, RequestStatus
from src.allina.schemas.plans import RemediationPlan, FileCandidate


def test_request_repository_crud_and_atomic_cas():
    db = DatabaseManager(":memory:")
    repo = RequestRepository(db)

    req = RemediationRequest(
        request_id="req-test-1",
        tenant_id="tenant-123",
        requester_aad_id="user-aad-1",
        requester_name="Bob Test",
        original_message="Clean logs",
        status=RequestStatus.RECEIVED,
    )
    repo.create_request(req)

    retrieved = repo.get_request("req-test-1")
    assert retrieved is not None
    assert retrieved.request_id == "req-test-1"
    assert retrieved.status == RequestStatus.RECEIVED

    # Atomic CAS: update when expected_status matches
    success = repo.update_status(
        "req-test-1",
        new_status=RequestStatus.INVESTIGATING,
        expected_status=RequestStatus.RECEIVED,
    )
    assert success is True

    updated = repo.get_request("req-test-1")
    assert updated.status == RequestStatus.INVESTIGATING

    # Atomic CAS: fails when expected_status does not match
    failed_cas = repo.update_status(
        "req-test-1",
        new_status=RequestStatus.APPROVED,
        expected_status=RequestStatus.RECEIVED,  # Current is INVESTIGATING
    )
    assert failed_cas is False

    # Status remains INVESTIGATING
    assert repo.get_request("req-test-1").status == RequestStatus.INVESTIGATING


def test_plan_persistence():
    db = DatabaseManager(":memory:")
    req_repo = RequestRepository(db)
    plan_repo = PlanRepository(db)

    # Need parent request for foreign key
    req = RemediationRequest(
        request_id="req-plan-1",
        requester_aad_id="user-1",
        requester_name="User 1",
        original_message="Clean logs",
    )
    req_repo.create_request(req)

    now = datetime.now(timezone.utc)
    plan = RemediationPlan(
        request_id="req-plan-1",
        target_instance_id="i-testinstance",
        target_dir="/opt/allina/test-logs",
        eligible_files=[
            FileCandidate(
                path="/opt/allina/test-logs/allina-poc-1.log",
                basename="allina-poc-1.log",
                size_bytes=5000,
                mtime=now,
                age_hours=48.0,
            )
        ],
        estimated_recovery_bytes=5000,
        expires_at=now + timedelta(minutes=30),
    ).finalize_hash()

    plan_repo.save_plan(plan)

    retrieved_plan = plan_repo.get_plan("req-plan-1")
    assert retrieved_plan is not None
    assert retrieved_plan.target_instance_id == "i-testinstance"
    assert retrieved_plan.plan_hash == plan.plan_hash
    assert len(retrieved_plan.eligible_files) == 1


def test_conversation_repository():
    db = DatabaseManager(":memory:")
    conv_repo = ConversationRepository(db)

    conv_repo.save_conversation(
        user_aad_id="aad-user-999",
        conversation_id="conv-12345",
        service_url="https://smba.trafficmanager.net/amer/",
        tenant_id="tenant-123",
    )

    ref = conv_repo.get_conversation("aad-user-999")
    assert ref is not None
    assert ref["conversation_id"] == "conv-12345"
    assert ref["service_url"] == "https://smba.trafficmanager.net/amer/"

