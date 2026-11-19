import re
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.allina.governance.approvals import ApprovalService
from src.allina.persistence.database import DatabaseManager
from src.allina.schemas.requests import RequestStatus
from src.allina.teams.message_handlers import AllinaTeamsDispatcher


@pytest.fixture
def test_db():
    return DatabaseManager(":memory:")


@pytest.fixture
def dispatcher(test_db):
    disp = AllinaTeamsDispatcher(db_manager=test_db)
    disp.approval_service = ApprovalService(
        request_repo=disp.request_repo,
        plan_repo=disp.plan_repo,
        approval_repo=disp.approval_repo,
        approver_allowlist=["approver-bob"],
    )
    return disp


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
async def test_dispatcher_remediation_flow(dispatcher, test_db):
    ctx = _make_mock_ctx(
        "Our EC2 test server is running out of disk space. Please investigate and safely clean up eligible old test log files."
    )
    await dispatcher.dispatch(ctx)

    assert ctx.send.await_count == 2
    assert "Investigating Request" in ctx.send.await_args_list[0][0][0]
    reply_text = ctx.send.await_args_list[1][0][0]
    assert "Remediation Plan Generated" in reply_text
    assert "allina-poc-service-test1.log" in reply_text
    assert "Estimated Space Recovery:" in reply_text
    assert "AWAITING_APPROVAL" in reply_text

    with test_db.get_connection() as conn:
        row = conn.execute("SELECT * FROM requests").fetchone()
        assert row is not None
        assert row["status"] == RequestStatus.AWAITING_APPROVAL.value
        assert row["requester_aad_id"] == "user-alice"


@pytest.mark.asyncio
async def test_dispatcher_pending_and_status(dispatcher):
    ctx_req = _make_mock_ctx(
        "Our EC2 test server is running out of disk space. Please investigate and safely clean up eligible old test log files."
    )
    await dispatcher.dispatch(ctx_req)
    req_reply = ctx_req.send.await_args[0][0]

    match = re.search(r"Request ID:\*\* `(req-[a-f0-9]+)`", req_reply)
    assert match is not None
    req_id = match.group(1)

    # PENDING
    ctx_pending = _make_mock_ctx("PENDING")
    await dispatcher.dispatch(ctx_pending)
    pending_reply = ctx_pending.send.await_args[0][0]
    assert req_id in pending_reply

    # STATUS
    ctx_status = _make_mock_ctx(f"STATUS {req_id}")
    await dispatcher.dispatch(ctx_status)
    status_reply = ctx_status.send.await_args[0][0]
    assert "AWAITING_APPROVAL" in status_reply
    assert req_id in status_reply


@pytest.mark.asyncio
async def test_dispatcher_conversational_approval_and_self_approval_block(dispatcher, test_db):
    # 1. Requester triggers remediation
    ctx_req = _make_mock_ctx(
        "Our EC2 test server is running out of disk space. Please investigate and safely clean up eligible old test log files.",
        sender_aad="user-alice",
        sender_name="Alice Requester",
    )
    await dispatcher.dispatch(ctx_req)
    req_reply = ctx_req.send.await_args[0][0]
    req_id = re.search(r"Request ID:\*\* `(req-[a-f0-9]+)`", req_reply).group(1)

    # 2. Requester attempts self-approval
    ctx_self = _make_mock_ctx(
        f"APPROVE {req_id}",
        sender_aad="user-alice",
        sender_name="Alice Requester",
    )
    await dispatcher.dispatch(ctx_self)
    self_reply = ctx_self.send.await_args[0][0]
    assert "Governance Violation" in self_reply
    assert "cannot approve their own" in self_reply

    # Status must still be AWAITING_APPROVAL
    assert dispatcher.request_repo.get_request(req_id).status == RequestStatus.AWAITING_APPROVAL

    # 3. Authorized independent approver approves
    ctx_approve = _make_mock_ctx(
        f"APPROVE {req_id}",
        sender_aad="approver-bob",
        sender_name="Bob Approver",
    )
    await dispatcher.dispatch(ctx_approve)
    assert ctx_approve.send.await_count == 3
    approve_reply = ctx_approve.send.await_args_list[0][0][0]
    progress_reply = ctx_approve.send.await_args_list[1][0][0]
    exec_reply = ctx_approve.send.await_args_list[2][0][0]
    assert "Approved" in approve_reply
    assert req_id in approve_reply
    assert "Executing Remediation" in progress_reply
    assert "DRY-RUN COMPLETE" in exec_reply

    # Status must now be SUCCEEDED (executed in dry-run mode)
    assert dispatcher.request_repo.get_request(req_id).status == RequestStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_dispatcher_conversational_rejection(dispatcher):
    # 1. Trigger remediation
    ctx_req = _make_mock_ctx("Please clean up EC2 logs")
    await dispatcher.dispatch(ctx_req)
    req_reply = ctx_req.send.await_args[0][0]
    req_id = re.search(r"Request ID:\*\* `(req-[a-f0-9]+)`", req_reply).group(1)

    # 2. Authorized approver rejects
    ctx_reject = _make_mock_ctx(
        f"REJECT {req_id}",
        sender_aad="approver-bob",
        sender_name="Bob Approver",
    )
    await dispatcher.dispatch(ctx_reject)
    reject_reply = ctx_reject.send.await_args[0][0]
    assert "Rejected" in reject_reply
    assert req_id in reject_reply

    # Status must now be REJECTED
    assert dispatcher.request_repo.get_request(req_id).status == RequestStatus.REJECTED


@pytest.mark.asyncio
async def test_dispatcher_echo_fallback(dispatcher):
    ctx = _make_mock_ctx("Unrelated query 12345")
    await dispatcher.dispatch(ctx)

    ctx.send.assert_awaited_once_with("You said 'Unrelated query 12345'")


@pytest.mark.asyncio
async def test_dispatcher_greeting(dispatcher):
    ctx = _make_mock_ctx("Hello ALLINA")
    await dispatcher.dispatch(ctx)

    ctx.send.assert_awaited_once()
    assert "ALLINA IT Operations Assistant" in ctx.send.await_args[0][0]


@pytest.mark.asyncio
async def test_dispatcher_demo_mode_allows_self_approval(dispatcher):
    """When demo mode is enabled, requester self-approval succeeds through Teams dispatcher."""
    # 1. Trigger remediation as Suganth
    ctx_req = _make_mock_ctx(
        "Please investigate and clean up eligible test logs",
        sender_aad="suganth-aad-123",
        sender_name="Suganth",
    )
    await dispatcher.dispatch(ctx_req)
    req_reply = ctx_req.send.await_args[0][0]
    req_id = re.search(r"Request ID:\*\* `(req-[a-f0-9]+)`", req_reply).group(1)

    # 2. Suganth approves their own request with demo flag enabled
    from unittest.mock import patch
    from src.allina.config import settings

    with patch.object(settings, "allina_allow_self_approval_for_demo", True):
        ctx_approve = _make_mock_ctx(
            f"APPROVE {req_id}",
            sender_aad="suganth-aad-123",
            sender_name="Suganth",
        )
        await dispatcher.dispatch(ctx_approve)

        assert ctx_approve.send.await_count == 3
        approve_msg = ctx_approve.send.await_args_list[0][0][0]
        progress_msg = ctx_approve.send.await_args_list[1][0][0]
        exec_msg = ctx_approve.send.await_args_list[2][0][0]

        assert "Approved" in approve_msg
        assert "Executing Remediation" in progress_msg
        assert "Suganth" in approve_msg
        assert "DRY-RUN COMPLETE" in exec_msg

    assert dispatcher.request_repo.get_request(req_id).status == RequestStatus.SUCCEEDED

