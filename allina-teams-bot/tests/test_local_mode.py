import os
import shutil
import tempfile
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest

from src.allina.agents.investigation import LocalInvestigationAdapter
from src.allina.agents.remediation_agent import A7RemediationAgent
from src.allina.config import settings
from src.allina.execution.executor import RemediationExecutor
from src.allina.governance.approvals import ApprovalService
from src.allina.persistence.database import DatabaseManager
from src.allina.persistence.repositories import (
    ApprovalRepository,
    ExecutionRepository,
    PlanRepository,
    RequestRepository,
)
from src.allina.schemas.approvals import ApprovalDecisionType
from src.allina.schemas.requests import RemediationRequest, RequestStatus


@pytest.fixture
def temp_local_log_dir():
    """Create a temporary directory simulating local-test-logs."""
    temp_dir = tempfile.mkdtemp(prefix="allina_test_logs_")
    now_ts = time.time()

    # 1. Eligible old log 1 (72h old)
    f1 = os.path.join(temp_dir, "allina-poc-service1.log")
    with open(f1, "wb") as f:
        f.write(b"X" * 1024 * 1024)  # 1 MB
    os.utime(f1, (now_ts - 72 * 3600, now_ts - 72 * 3600))

    # 2. Eligible old log 2 (48h old)
    f2 = os.path.join(temp_dir, "allina-poc-service2.log")
    with open(f2, "wb") as f:
        f.write(b"Y" * 2 * 1024 * 1024)  # 2 MB
    os.utime(f2, (now_ts - 48 * 3600, now_ts - 48 * 3600))

    # 3. Protected too-new log (1h old)
    f3 = os.path.join(temp_dir, "allina-poc-recent.log")
    with open(f3, "wb") as f:
        f.write(b"Z" * 512 * 1024)  # 512 KB
    os.utime(f3, (now_ts - 3600, now_ts - 3600))

    # 4. Protected wrong-prefix file (72h old)
    f4 = os.path.join(temp_dir, "important-system.log")
    with open(f4, "wb") as f:
        f.write(b"W" * 256 * 1024)
    os.utime(f4, (now_ts - 72 * 3600, now_ts - 72 * 3600))

    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_local_investigation_adapter(temp_local_log_dir):
    adapter = LocalInvestigationAdapter()
    metrics, raw_files = adapter.inspect("local-machine", temp_local_log_dir)

    assert metrics.total_bytes > 0
    assert metrics.available_bytes > 0
    assert len(raw_files) == 4
    basenames = {f["basename"] for f in raw_files}
    assert "allina-poc-service1.log" in basenames
    assert "allina-poc-service2.log" in basenames
    assert "allina-poc-recent.log" in basenames
    assert "important-system.log" in basenames


def test_remediation_agent_local_mode_filters_properly(temp_local_log_dir):
    db = DatabaseManager(":memory:")
    req_repo = RequestRepository(db)
    plan_repo = PlanRepository(db)

    agent = A7RemediationAgent(
        request_repo=req_repo,
        plan_repo=plan_repo,
        investigation_adapter=LocalInvestigationAdapter(),
    )

    req = RemediationRequest(
        request_id="req-local-01",
        requester_aad_id="user-123",
        requester_name="Suganth",
        original_message="Clean local logs",
    )
    req_repo.create_request(req)

    with patch.object(settings, "allina_allowed_log_dir", temp_local_log_dir):
        plan = agent.investigate_and_plan(req)

    # Exactly the 2 eligible files must be included!
    assert len(plan.eligible_files) == 2
    eligible_names = {f.basename for f in plan.eligible_files}
    assert eligible_names == {"allina-poc-service1.log", "allina-poc-service2.log"}
    # Protected files must be excluded
    assert "allina-poc-recent.log" not in eligible_names
    assert "important-system.log" not in eligible_names


def test_remediation_executor_local_live_deletion(temp_local_log_dir):
    db = DatabaseManager(":memory:")
    req_repo = RequestRepository(db)
    plan_repo = PlanRepository(db)
    exec_repo = ExecutionRepository(db)

    agent = A7RemediationAgent(
        request_repo=req_repo,
        plan_repo=plan_repo,
        investigation_adapter=LocalInvestigationAdapter(),
    )

    req = RemediationRequest(
        request_id="req-local-02",
        requester_aad_id="user-123",
        requester_name="Suganth",
        original_message="Clean local logs",
    )
    req_repo.create_request(req)

    with patch.object(settings, "allina_allowed_log_dir", temp_local_log_dir):
        plan = agent.investigate_and_plan(req)

    # Transition to APPROVED
    req_repo.update_status(req.request_id, RequestStatus.APPROVED)

    executor = RemediationExecutor(
        request_repo=req_repo,
        plan_repo=plan_repo,
        execution_repo=exec_repo,
    )

    # Execute in local live deletion mode
    with patch.object(settings, "allina_target_mode", "local"), patch.object(
        settings, "allina_dry_run", False
    ):
        outcome = executor.execute(req.request_id)

    assert outcome.success is True
    assert "LOCAL REMEDIATION SUCCEEDED" in outcome.message
    assert req_repo.get_request(req.request_id).status == RequestStatus.SUCCEEDED

    # Verify files on disk:
    # 1. Eligible files MUST be gone
    assert not os.path.exists(os.path.join(temp_local_log_dir, "allina-poc-service1.log"))
    assert not os.path.exists(os.path.join(temp_local_log_dir, "allina-poc-service2.log"))

    # 2. Protected files MUST STILL EXIST!
    assert os.path.exists(os.path.join(temp_local_log_dir, "allina-poc-recent.log"))
    assert os.path.exists(os.path.join(temp_local_log_dir, "important-system.log"))

