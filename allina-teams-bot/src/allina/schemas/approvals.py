"""Approval schemas and commands."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ApprovalDecisionType(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalRecord(BaseModel):
    """Record of an explicit human approval or rejection."""

    request_id: str
    approver_aad_id: str
    approver_name: str
    decision: ApprovalDecisionType
    plan_hash: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: Optional[str] = None


class ParsedCommand(BaseModel):
    """Parsed conversational command from Teams chat."""

    command_type: str  # PENDING, APPROVE, REJECT, STATUS, REMEDIATE, GREETING, ECHO
    request_id: Optional[str] = None
    raw_text: str

