from datetime import datetime, timezone, timedelta
from src.allina.agents.investigation import MockInvestigationAdapter
from src.allina.agents.remediation_agent import A7RemediationAgent
from src.allina.persistence.database import DatabaseManager
from src.allina.persistence.repositories import PlanRepository, RequestRepository
from src.allina.schemas.requests import RemediationRequest, RequestStatus


def test_remediation_agent_filters_safeguards_and_creates_plan():
    db = DatabaseManager(":memory:")
    req_repo = RequestRepository(db)
    plan_repo = PlanRepository(db)

    req = RemediationRequest(
        request_id="req-rem-001",
        tenant_id="tenant-123",
        requester_aad_id="user-aad-1",
        requester_name="Alice Ops",
        original_message="Clean EC2 logs",
        status=RequestStatus.RECEIVED,
    )
    req_repo.create_request(req)

    agent = A7RemediationAgent(
        request_repo=req_repo,
        plan_repo=plan_repo,
        investigation_adapter=MockInvestigationAdapter(),
    )

    plan = agent.investigate_and_plan(req)

    # 1. Assert exactly 3 eligible files were selected from the 7 mock candidates
    # (Excludes: active file, file < 24h old, symlink, and non-poc file)
    assert len(plan.eligible_files) == 3
    basenames = [f.basename for f in plan.eligible_files]
    assert "allina-poc-service-test1.log" in basenames
    assert "allina-poc-batch-run.log" in basenames
    assert "allina-poc-stress-eval.log" in basenames
    assert "allina-poc-active-worker.log" not in basenames
    assert "allina-poc-fresh-test.log" not in basenames
    assert "allina-poc-symlink.log" not in basenames
    assert "application-prod.log" not in basenames

    # 2. Check estimated recovery calculation (450MB + 820MB + 1200MB)
    expected_bytes = (450 + 820 + 1200) * 1024 * 1024
    assert plan.estimated_recovery_bytes == expected_bytes

    # 3. Check plan hash determinism & persistence
    assert len(plan.plan_hash) == 64
    persisted_plan = plan_repo.get_plan(req.request_id)
    assert persisted_plan is not None
    assert persisted_plan.plan_hash == plan.plan_hash

    # 4. Check state transitions: request must now be AWAITING_APPROVAL
    updated_req = req_repo.get_request(req.request_id)
    assert updated_req.status == RequestStatus.AWAITING_APPROVAL


def test_remediation_agent_rejects_path_traversal_and_escapes():
    db = DatabaseManager(":memory:")
    req_repo = RequestRepository(db)
    plan_repo = PlanRepository(db)

    req = RemediationRequest(
        request_id="req-traversal-001",
        requester_aad_id="user-aad-1",
        requester_name="Alice Ops",
        original_message="Clean EC2 logs",
    )
    req_repo.create_request(req)

    now = datetime.now(timezone.utc)
    malicious_files = [
        # Directory traversal
        {
            "path": "/opt/allina/test-logs/../../../etc/passwd",
            "basename": "allina-poc-passwd.log",
            "size_bytes": 1024,
            "mtime": now - timedelta(hours=48),
            "is_symlink": False,
            "is_active": False,
        },
        # Absolute path outside allowed directory
        {
            "path": "/var/log/syslog",
            "basename": "allina-poc-syslog.log",
            "size_bytes": 1024,
            "mtime": now - timedelta(hours=48),
            "is_symlink": False,
            "is_active": False,
        },
    ]

    agent = A7RemediationAgent(
        request_repo=req_repo,
        plan_repo=plan_repo,
        investigation_adapter=MockInvestigationAdapter(custom_files=malicious_files),
    )

    plan = agent.investigate_and_plan(req)

    # Must reject both malicious candidates
    assert len(plan.eligible_files) == 0
    assert plan.estimated_recovery_bytes == 0

