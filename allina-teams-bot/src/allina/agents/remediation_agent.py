"""A7 Remediation Agent: Read-only investigation and deterministic plan synthesis."""

import os
import posixpath
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from src.allina.agents.investigation import (
    BaseInvestigationAdapter,
    LocalInvestigationAdapter,
    MockInvestigationAdapter,
)
from src.allina.config import settings
from src.allina.knowledge.runbook_parser import RunbookPolicy, load_approved_runbook
from src.allina.persistence.repositories import PlanRepository, RequestRepository
from src.allina.schemas.plans import FileCandidate, RemediationPlan
from src.allina.schemas.requests import RemediationRequest, RequestStatus


class RemediationAgentError(Exception):
    """Raised when investigation or planning fails."""
    pass


class A7RemediationAgent:
    """Specialist agent responsible for EC2 test-log investigation and planning."""

    def __init__(
        self,
        request_repo: Optional[RequestRepository] = None,
        plan_repo: Optional[PlanRepository] = None,
        investigation_adapter: Optional[BaseInvestigationAdapter] = None,
    ):
        self.request_repo = request_repo
        self.plan_repo = plan_repo
        if investigation_adapter:
            self.investigation_adapter = investigation_adapter
        elif settings.allina_target_mode == "azure":
            from src.allina.agents.investigation import AzureInvestigationAdapter
            self.investigation_adapter = AzureInvestigationAdapter()
        elif settings.allina_target_mode == "local":
            self.investigation_adapter = LocalInvestigationAdapter()
        else:
            self.investigation_adapter = MockInvestigationAdapter()

    def investigate_and_plan(
        self, request: RemediationRequest, policy: Optional[RunbookPolicy] = None
    ) -> RemediationPlan:
        """
        Execute read-only investigation, evaluate runbook boundaries,
        and generate a cryptographically bound remediation plan.
        """
        runbook = policy or load_approved_runbook()
        instance_id = settings.azure_vm_name if settings.allina_target_mode == "azure" else settings.allina_ec2_instance_id
        target_dir = runbook.allowed_directory.rstrip("/\\")

        # 1. Update status to INVESTIGATING
        if self.request_repo:
            self.request_repo.update_status(
                request.request_id,
                new_status=RequestStatus.INVESTIGATING,
                expected_status=RequestStatus.RECEIVED,
            )

        # 2. Collect evidence via investigation adapter
        disk_metrics, raw_files = self.investigation_adapter.inspect(
            instance_id=instance_id, log_dir=target_dir
        )

        # 3. Filter candidates strictly against runbook safeguards
        now = datetime.now(timezone.utc)
        eligible_files: List[FileCandidate] = []
        running_total_bytes = 0

        # Canonical log directory normalization
        normalized_allowed_dir = posixpath.normpath(target_dir.replace("\\", "/"))
        norm_dir_compare = normalized_allowed_dir.lower()

        for f in raw_files:
            file_path = f.get("path", "").replace("\\", "/")
            normalized_path = posixpath.normpath(file_path)
            norm_path_compare = normalized_path.lower()

            # Safeguard A: Path containment & Traversal rejection
            if ".." in normalized_path:
                continue
            if not (
                norm_path_compare == norm_dir_compare
                or norm_path_compare.startswith(norm_dir_compare + "/")
            ):
                continue

            # Safeguard B: Exclude symlinks
            if f.get("is_symlink", False):
                continue

            # Safeguard C: Exclude active/open files
            if f.get("is_active", False):
                continue

            basename = f.get("basename", posixpath.basename(normalized_path))

            # Safeguard D: Enforce approved prefix and extension
            if not basename.startswith(runbook.allowed_prefix):
                continue
            if not basename.endswith(runbook.allowed_extension):
                continue

            # Safeguard E: Enforce minimum age
            mtime = f.get("mtime")
            if not isinstance(mtime, datetime):
                continue
            if mtime.tzinfo is None:
                mtime = mtime.replace(tzinfo=timezone.utc)

            age_hours = (now - mtime).total_seconds() / 3600.0
            if age_hours < runbook.min_file_age_hours:
                continue

            # Safeguard F: Safety ceilings on count and volume
            file_size = int(f.get("size_bytes", 0))
            if len(eligible_files) >= runbook.max_cleanup_files:
                break
            if running_total_bytes + file_size > runbook.max_cleanup_bytes:
                break

            candidate = FileCandidate(
                path=normalized_path,
                basename=basename,
                size_bytes=file_size,
                mtime=mtime,
                age_hours=round(age_hours, 1),
            )
            eligible_files.append(candidate)
            running_total_bytes += file_size

        # 4. Synthesize deterministic plan
        expires_at = now + timedelta(minutes=settings.allina_request_expiry_minutes)
        preconditions = [
            f"Target EC2 instance is {instance_id}",
            f"SSM agent is online and responsive",
            f"Target directory is {target_dir}",
            f"Files must match prefix '{runbook.allowed_prefix}' and extension '{runbook.allowed_extension}'",
            f"Minimum file age is {runbook.min_file_age_hours} hours",
            f"Pre-execution TOCTOU recheck verifies file existence and plan hash match",
        ]

        plan = RemediationPlan(
            request_id=request.request_id,
            target_instance_id=instance_id,
            target_dir=target_dir,
            eligible_files=eligible_files,
            estimated_recovery_bytes=running_total_bytes,
            action="DELETE_APPROVED_TEST_LOGS",
            preconditions=preconditions,
            created_at=now,
            expires_at=expires_at,
        ).finalize_hash()

        # 5. Persist plan and transition request to AWAITING_APPROVAL
        if self.plan_repo:
            self.plan_repo.save_plan(plan)

        if self.request_repo:
            self.request_repo.update_status(
                request.request_id,
                new_status=RequestStatus.AWAITING_APPROVAL,
                expected_status=RequestStatus.INVESTIGATING,
            )

        return plan

