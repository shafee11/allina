from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from src.allina.config import Settings
from src.allina.execution.executor import RemediationExecutor
from src.allina.persistence.database import DatabaseManager
from src.allina.persistence.repositories import (
    ExecutionRepository,
    PlanRepository,
    RequestRepository,
)
from src.allina.schemas.execution import SSMExecutionResult
from src.allina.schemas.plans import DiskMetrics, FileCandidate, RemediationPlan
from src.allina.schemas.requests import RemediationRequest, RequestStatus


def _setup_approved_plan(db):
    req_repo = RequestRepository(db)
    plan_repo = PlanRepository(db)
    exec_repo = ExecutionRepository(db)

    req_id = "req-live-001"
    req = RemediationRequest(
        request_id=req_id,
        requester_aad_id="user-alice",
        requester_name="Alice User",
        original_message="Clean logs",
        status=RequestStatus.APPROVED,
    )
    req_repo.create_request(req)

    now = datetime.now(timezone.utc)
    plan = RemediationPlan(
        request_id=req_id,
        target_instance_id="i-live-instance",
        target_dir="/opt/allina/test-logs",
        eligible_files=[
            FileCandidate(
                path="/opt/allina/test-logs/allina-poc-1.log",
                basename="allina-poc-1.log",
                size_bytes=100 * 1024 * 1024,
                mtime=now - timedelta(hours=48),
                age_hours=48.0,
            )
        ],
        estimated_recovery_bytes=100 * 1024 * 1024,
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    ).finalize_hash()
    plan_repo.save_plan(plan)
    return req, plan, req_repo, plan_repo, exec_repo


def test_live_execution_succeeds_when_flags_enabled():
    db = DatabaseManager(":memory:")
    req, plan, req_repo, plan_repo, exec_repo = _setup_approved_plan(db)

    mock_ssm = MagicMock()
    # Before metrics: 200MB avail
    before = DiskMetrics(mount_point="/", total_bytes=1000 * 1024 * 1024, used_bytes=800 * 1024 * 1024, available_bytes=200 * 1024 * 1024, percent_used=80.0)
    # After metrics: 300MB avail (100MB recovered)
    after = DiskMetrics(mount_point="/", total_bytes=1000 * 1024 * 1024, used_bytes=700 * 1024 * 1024, available_bytes=300 * 1024 * 1024, percent_used=70.0)

    mock_ssm.inspect_disk.side_effect = [before, after]
    mock_ssm.execute_script.return_value = SSMExecutionResult(
        command_id="cmd-live-999",
        instance_id="i-live-instance",
        status="Success",
        exit_code=0,
        output_summary="UNLINKED: allina-poc-1.log",
        dry_run=False,
    )
    mock_ssm.inspect_files.return_value = []  # No remaining files

    executor = RemediationExecutor(
        request_repo=req_repo,
        plan_repo=plan_repo,
        execution_repo=exec_repo,
        ssm_client=mock_ssm,
    )

    # Patch settings to enable live execution
    from src.allina.config import settings
    with patch.object(settings, "allina_dry_run", False), \
         patch.object(settings, "allina_enable_real_execution", True), \
         patch.object(settings, "allina_backup_confirmed", True):
        outcome = executor.execute(req.request_id)

    assert outcome.success is True
    assert outcome.dry_run is False
    assert "LIVE REMEDIATION SUCCEEDED" in outcome.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.SUCCEEDED


def test_live_execution_handles_ssm_script_failure():
    db = DatabaseManager(":memory:")
    req, plan, req_repo, plan_repo, exec_repo = _setup_approved_plan(db)

    mock_ssm = MagicMock()
    mock_ssm.inspect_disk.return_value = None
    mock_ssm.execute_script.return_value = SSMExecutionResult(
        command_id="cmd-fail-111",
        instance_id="i-live-instance",
        status="Failed",
        exit_code=1,
        output_summary="Permission denied / disk write error",
        dry_run=False,
    )

    executor = RemediationExecutor(
        request_repo=req_repo,
        plan_repo=plan_repo,
        execution_repo=exec_repo,
        ssm_client=mock_ssm,
    )

    from src.allina.config import settings
    with patch.object(settings, "allina_dry_run", False), \
         patch.object(settings, "allina_enable_real_execution", True), \
         patch.object(settings, "allina_backup_confirmed", True):
        outcome = executor.execute(req.request_id)

    assert outcome.success is False
    assert "SSM Execution Failed" in outcome.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.FAILED
