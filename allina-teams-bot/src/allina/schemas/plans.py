"""Remediation plan and evidence schemas."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FileCandidate(BaseModel):
    """Metadata for an eligible test log file on the target EC2 instance."""

    path: str
    basename: str
    size_bytes: int
    mtime: datetime
    age_hours: float


class DiskMetrics(BaseModel):
    """Disk metrics before or after remediation."""

    mount_point: str
    total_bytes: int
    used_bytes: int
    available_bytes: int
    percent_used: float


def compute_plan_hash(payload: Dict[str, Any]) -> str:
    """Compute SHA-256 hash over canonical (sorted, serialized) JSON of a plan."""
    cleaned = {k: v for k, v in payload.items() if k != "plan_hash"}
    
    def serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, BaseModel):
            return obj.model_dump()
        raise TypeError(f"Type {type(obj)} not serializable")

    canonical_json = json.dumps(cleaned, sort_keys=True, default=serializer)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class RemediationPlan(BaseModel):
    """Structured, immutable remediation plan produced by A7."""

    request_id: str
    target_instance_id: str
    target_dir: str
    eligible_files: List[FileCandidate] = Field(default_factory=list)
    estimated_recovery_bytes: int = 0
    action: str = "DELETE_APPROVED_TEST_LOGS"
    preconditions: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    plan_hash: str = ""

    def finalize_hash(self) -> "RemediationPlan":
        """Compute and set canonical SHA-256 hash."""
        self.plan_hash = compute_plan_hash(self.model_dump())
        return self

