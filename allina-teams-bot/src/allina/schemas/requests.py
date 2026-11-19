"""Request schemas and lifecycle states for ALLINA."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class RequestStatus(str, Enum):
    """Lifecycle statuses for a remediation request."""

    RECEIVED = "RECEIVED"
    INVESTIGATING = "INVESTIGATING"
    PLAN_READY = "PLAN_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    ESCALATED = "ESCALATED"
    EXPIRED = "EXPIRED"


class IncomingMessage(BaseModel):
    """Normalized intake message extracted from Teams activity context."""

    raw_text: str
    sender_aad_id: str
    sender_name: str
    tenant_id: Optional[str] = None
    conversation_id: str
    service_url: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RemediationRequest(BaseModel):
    """Durable record of a remediation request."""

    request_id: str = Field(default_factory=lambda: f"req-{uuid4().hex[:12]}")
    tenant_id: Optional[str] = None
    requester_aad_id: str
    requester_name: str
    original_message: str
    status: RequestStatus = RequestStatus.RECEIVED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

