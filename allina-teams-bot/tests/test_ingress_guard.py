from unittest.mock import MagicMock
from src.allina.ingress.guard import IngressGuard


def _create_mock_activity(
    text="Our EC2 test server is running out of disk space",
    sender_aad_id="aad-123",
    sender_name="Alice Doe",
    tenant_id="allowed-tenant",
    conversation_id="conv-456",
):
    activity = MagicMock()
    activity.text = text
    activity.service_url = "https://smba.trafficmanager.net/amer/"

    from_prop = MagicMock()
    from_prop.aad_object_id = sender_aad_id
    from_prop.id = "teams-id-123"
    from_prop.name = sender_name
    from_prop.tenant_id = tenant_id
    activity.from_ = from_prop

    conversation = MagicMock()
    conversation.id = conversation_id
    conversation.tenant_id = tenant_id
    activity.conversation = conversation

    return activity


def test_ingress_guard_valid_activity():
    guard = IngressGuard(target_tenant_id="allowed-tenant")
    activity = _create_mock_activity()

    is_valid, reason, incoming = guard.validate_activity(activity)
    assert is_valid is True
    assert reason is None
    assert incoming is not None
    assert incoming.sender_aad_id == "aad-123"
    assert incoming.tenant_id == "allowed-tenant"


def test_ingress_guard_empty_text():
    guard = IngressGuard()
    activity = _create_mock_activity(text="   ")

    is_valid, reason, incoming = guard.validate_activity(activity)
    assert is_valid is False
    assert "empty" in reason.lower()
    assert incoming is None


def test_ingress_guard_oversized_text():
    guard = IngressGuard()
    activity = _create_mock_activity(text="A" * 2500)

    is_valid, reason, incoming = guard.validate_activity(activity)
    assert is_valid is False
    assert "exceeds" in reason.lower()


def test_ingress_guard_unauthorized_tenant():
    guard = IngressGuard(target_tenant_id="allowed-tenant")
    activity = _create_mock_activity(tenant_id="unauthorized-foreign-tenant")

    is_valid, reason, incoming = guard.validate_activity(activity)
    assert is_valid is False
    assert "tenant validation failed" in reason.lower()

