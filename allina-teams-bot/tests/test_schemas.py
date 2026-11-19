from datetime import datetime, timezone, timedelta
from src.allina.schemas.requests import RemediationRequest, RequestStatus
from src.allina.schemas.plans import RemediationPlan, FileCandidate, compute_plan_hash


def test_remediation_request_creation():
    req = RemediationRequest(
        requester_aad_id="user-123",
        requester_name="Alice Doe",
        original_message="Clean EC2 logs please",
    )
    assert req.request_id.startswith("req-")
    assert req.status == RequestStatus.RECEIVED
    assert req.created_at is not None


def test_plan_hash_determinism():
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=30)
    
    plan1 = RemediationPlan(
        request_id="req-001",
        target_instance_id="i-test1234",
        target_dir="/opt/allina/test-logs",
        eligible_files=[
            FileCandidate(
                path="/opt/allina/test-logs/allina-poc-1.log",
                basename="allina-poc-1.log",
                size_bytes=1024,
                mtime=now,
                age_hours=25.0,
            )
        ],
        estimated_recovery_bytes=1024,
        created_at=now,
        expires_at=expires,
    ).finalize_hash()

    plan2 = RemediationPlan(
        request_id="req-001",
        target_instance_id="i-test1234",
        target_dir="/opt/allina/test-logs",
        eligible_files=[
            FileCandidate(
                path="/opt/allina/test-logs/allina-poc-1.log",
                basename="allina-poc-1.log",
                size_bytes=1024,
                mtime=now,
                age_hours=25.0,
            )
        ],
        estimated_recovery_bytes=1024,
        created_at=now,
        expires_at=expires,
    ).finalize_hash()

    assert plan1.plan_hash != ""
    assert plan1.plan_hash == plan2.plan_hash

    # Altering target dir changes hash
    plan_altered = plan1.model_copy()
    plan_altered.target_dir = "/different/dir"
    plan_altered.finalize_hash()
    assert plan_altered.plan_hash != plan1.plan_hash

