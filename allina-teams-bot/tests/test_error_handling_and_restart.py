import re
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.allina.governance.approvals import ApprovalService
from src.allina.persistence.database import DatabaseManager
from src.allina.schemas.requests import RequestStatus
from src.allina.teams.message_handlers import AllinaTeamsDispatcher


def _make_mock_ctx(text: str, sender_aad="user-alice", tenant="tenant-abc", sender_name="Alice User"):
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.reply = AsyncMock()

    activity = MagicMock()
    activity.text = text
    activity.service_url = "https://smba.trafficmanager.net/amer/"

    from_prop = MagicMock()
    from_prop.aad_object_id = sender_aad
    from_prop.name = sender_name
    from_prop.tenant_id = tenant
    activity.from_ = from_prop

    conversation = MagicMock()
    conversation.id = "conv-xyz"
    conversation.tenant_id = tenant
    activity.conversation = conversation

    ctx.activity = activity
    return ctx


@pytest.mark.asyncio
async def test_safe_error_boundary_on_unexpected_failure():
    db = DatabaseManager(":memory:")
    dispatcher = AllinaTeamsDispatcher(db_manager=db)

    # Force an unexpected exception during request creation
    with patch.object(dispatcher.request_repo, "create_request", side_effect=RuntimeError("Disk I/O error")):
        ctx = _make_mock_ctx("Our EC2 test server is running out of disk space")
        await dispatcher.dispatch(ctx)

        # Must NOT crash or send raw traceback to Teams
        ctx.send.assert_awaited_once()
        reply = ctx.send.await_args[0][0]
        assert "ALLINA Error" in reply
        assert "RuntimeError" not in reply
        assert "Disk I/O error" not in reply


@pytest.mark.asyncio
async def test_persistence_resilience_across_restart(tmp_path):
    db_file = str(tmp_path / "allina_restart_test.db")

    # === INSTANCE 1: Initial Session ===
    db1 = DatabaseManager(db_file)
    disp1 = AllinaTeamsDispatcher(db_manager=db1)
    disp1.approval_service = ApprovalService(
        request_repo=disp1.request_repo,
        plan_repo=disp1.plan_repo,
        approval_repo=disp1.approval_repo,
        approver_allowlist=["approver-bob"],
    )

    ctx_req = _make_mock_ctx("Our EC2 test server is running out of disk space")
    await disp1.dispatch(ctx_req)
    req_reply = ctx_req.send.await_args[0][0]
    req_id = re.search(r"Request ID:\*\* `(req-[a-f0-9]+)`", req_reply).group(1)

    # Simulate bot process exit / restart
    del disp1
    del db1

    # === INSTANCE 2: Post-Restart Session ===
    db2 = DatabaseManager(db_file)
    disp2 = AllinaTeamsDispatcher(db_manager=db2)
    disp2.approval_service = ApprovalService(
        request_repo=disp2.request_repo,
        plan_repo=disp2.plan_repo,
        approval_repo=disp2.approval_repo,
        approver_allowlist=["approver-bob"],
    )

    # 1. Verify status is retained across restart
    ctx_status = _make_mock_ctx(f"STATUS {req_id}")
    await disp2.dispatch(ctx_status)
    status_reply = ctx_status.send.await_args[0][0]
    assert req_id in status_reply
    assert "AWAITING_APPROVAL" in status_reply
    assert "Audit Ledger: `Verified Intact (SHA-256)`" in status_reply

    # 2. Complete approval in new session
    ctx_approve = _make_mock_ctx(f"APPROVE {req_id}", sender_aad="approver-bob", sender_name="Bob Approver")
    await disp2.dispatch(ctx_approve)
    assert ctx_approve.send.await_count == 3
    approve_reply = ctx_approve.send.await_args_list[0][0][0]
    progress_reply = ctx_approve.send.await_args_list[1][0][0]
    exec_reply = ctx_approve.send.await_args_list[2][0][0]
    assert "Approved" in approve_reply
    assert "Executing Remediation" in progress_reply
    assert "DRY-RUN COMPLETE" in exec_reply

    # 3. Final state in reloaded DB is SUCCEEDED
    assert disp2.request_repo.get_request(req_id).status == RequestStatus.SUCCEEDED

    # 4. Audit chain integrity is intact across both sessions
    audit_ok, err = disp2.audit_logger.verify_chain_integrity()
    assert audit_ok is True
    assert err is None

