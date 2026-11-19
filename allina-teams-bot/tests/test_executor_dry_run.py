from datetime import datetime, timezone, timedelta
from src.allina.execution.executor import RemediationExecutor
from src.allina.persistence.database import DatabaseManager
from src.allina.persistence.repositories import (
    ExecutionRepository,
    PlanRepository,
    RequestRepository,
)
from src.allina.schemas.plans import FileCandidate, RemediationPlan
from src.allina.schemas.requests import RemediationRequest, RequestStatus


def _setup_executor_env(status=RequestStatus.APPROVED):
    db = DatabaseManager(":memory:")
    req_repo = RequestRepository(db)
    plan_repo = PlanRepository(db)
    exec_repo = ExecutionRepository(db)

    req_id = "req-exec-001"
    req = RemediationRequest(
        request_id=req_id,
        requester_aad_id="user-alice",
        requester_name="Alice User",
        original_message="Clean logs",
        status=status,
    )
    req_repo.create_request(req)

    now = datetime.now(timezone.utc)
    plan = RemediationPlan(
        request_id=req_id,
        target_instance_id="i-testinstance",
        target_dir="/opt/allina/test-logs",
        eligible_files=[
            FileCandidate(
                path="/opt/allina/test-logs/allina-poc-1.log",
                basename="allina-poc-1.log",
                size_bytes=2048,
                mtime=now - timedelta(hours=48),
                age_hours=48.0,
            )
        ],
        estimated_recovery_bytes=2048,
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    ).finalize_hash()
    plan_repo.save_plan(plan)

    executor = RemediationExecutor(
        request_repo=req_repo,
        plan_repo=plan_repo,
        execution_repo=exec_repo,
    )
    return executor, req_repo, plan_repo, exec_repo, req, plan


def test_executor_blocks_unapproved_request():
    executor, req_repo, _, _, req, _ = _setup_executor_env(status=RequestStatus.AWAITING_APPROVAL)

    outcome = executor.execute(req.request_id)
    assert outcome.success is False
    assert "must be `APPROVED`" in outcome.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.AWAITING_APPROVAL


def test_executor_blocks_tampered_plan_hash():
    executor, req_repo, plan_repo, _, req, plan = _setup_executor_env(status=RequestStatus.APPROVED)

    # Tamper with the plan in DB
    tampered_plan = plan.model_copy()
    tampered_plan.target_dir = "/malicious/target/path"
    # Keep the original plan_hash to simulate tampering
    plan_repo.save_plan(tampered_plan)

    outcome = executor.execute(req.request_id)
    assert outcome.success is False
    assert "TOCTOU Integrity Violation" in outcome.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.FAILED


def test_executor_dry_run_simulation_succeeds():
    executor, req_repo, _, exec_repo, req, plan = _setup_executor_env(status=RequestStatus.APPROVED)

    outcome = executor.execute(req.request_id)
    assert outcome.success is True
    assert outcome.dry_run is True
    assert "DRY-RUN COMPLETE" in outcome.message
    assert "No files on EC2 were modified" in outcome.output_summary

    # Status must transition to SUCCEEDED
    assert req_repo.get_request(req.request_id).status == RequestStatus.SUCCEEDED

    # Verify execution record in DB
    record = exec_repo.get_execution(req.request_id)
    assert record is not None
    assert record["status"] == "SUCCEEDED"
    assert record["dry_run"] == 1
    assert "DRY RUN SIMULATION" in record["output_summary"]

