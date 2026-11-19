"""Execution and verification schemas."""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field
from src.allina.schemas.plans import DiskMetrics


class SSMExecutionResult(BaseModel):
    """Result from AWS Systems Manager Run Command invocation."""

    command_id: str
    instance_id: str
    status: str
    exit_code: int
    output_summary: str
    dry_run: bool = True
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VerificationResult(BaseModel):
    """Verification outcome asserting files were cleaned and disk freed."""

    success: bool
    actual_freed_bytes: int = 0
    files_remaining: List[str] = Field(default_factory=list)
    before_metrics: Optional[DiskMetrics] = None
    after_metrics: Optional[DiskMetrics] = None
    details: str = ""
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

