"""Runbook parser and constraints loader."""

import os
import re
from typing import Optional
from pydantic import BaseModel, Field
from src.allina.config import settings


class RunbookPolicy(BaseModel):
    """Enforceable operational policy loaded from runbook."""

    runbook_id: str = "RB-A7-EC2-DISK-CLEANUP-001"
    title: str = "EC2 Disk-Space / Test-Log Cleanup"
    allowed_directory: str
    allowed_prefix: str
    allowed_extension: str
    min_file_age_hours: int
    max_cleanup_files: int
    max_cleanup_bytes: int
    raw_markdown: str = ""


def load_approved_runbook(runbook_path: Optional[str] = None) -> RunbookPolicy:
    """
    Load the approved runbook markdown and merge with active server settings.
    Server settings always override or bound runbook defaults for safety.
    """
    if runbook_path is None:
        base_dir = os.path.dirname(__file__)
        runbook_path = os.path.join(base_dir, "runbooks", "disk_cleanup.md")

    raw_text = ""
    if os.path.exists(runbook_path):
        with open(runbook_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

    # Extract Runbook ID
    rb_id_match = re.search(r"\*\*Runbook ID:\*\*\s*`([^`]+)`", raw_text)
    rb_id = rb_id_match.group(1) if rb_id_match else "RB-A7-EC2-DISK-CLEANUP-001"

    return RunbookPolicy(
        runbook_id=rb_id,
        allowed_directory=settings.allina_allowed_log_dir,
        allowed_prefix=settings.allina_allowed_log_prefix,
        allowed_extension=settings.allina_allowed_log_extension,
        min_file_age_hours=settings.allina_min_file_age_hours,
        max_cleanup_files=settings.allina_max_cleanup_files,
        max_cleanup_bytes=settings.allina_max_cleanup_bytes,
        raw_markdown=raw_text,
    )

