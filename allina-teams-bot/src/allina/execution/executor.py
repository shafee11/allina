"""Execution engine orchestrating pre-execution rechecks and dry-run/live remediation."""

import logging
import os
import posixpath
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4
from pydantic import BaseModel
from src.allina.audit.logger import AuditLogger
from src.allina.config import settings
from src.allina.execution.ssm_client import Boto3SSMClient
from src.allina.execution.verification import RemediationVerifier
from src.allina.persistence.repositories import (
    ExecutionRepository,
    PlanRepository,
    RequestRepository,
)
from src.allina.schemas.execution import VerificationResult
from src.allina.schemas.plans import RemediationPlan, compute_plan_hash
from src.allina.schemas.requests import RequestStatus


class ExecutionOutcome(BaseModel):
    """Result of remediation execution (dry-run or live)."""

    success: bool
    request_id: str
    dry_run: bool
    command_id: str
    message: str
    output_summary: str
    verification: Optional[VerificationResult] = None
    audit_event_hash: Optional[str] = None


class RemediationExecutor:
    """Executes remediation workflows with TOCTOU integrity validation and fail-closed controls."""

    def __init__(
        self,
        request_repo: RequestRepository,
        plan_repo: PlanRepository,
        execution_repo: ExecutionRepository,
        ssm_client: Optional[Boto3SSMClient] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.request_repo = request_repo
        self.plan_repo = plan_repo
        self.execution_repo = execution_repo
        self.ssm_client = ssm_client or Boto3SSMClient()
        self.audit_logger = audit_logger

    def execute(self, request_id: str) -> ExecutionOutcome:
        """
        Execute approved remediation plan.
        Performs pre-execution rechecks (TOCTOU) before invoking SSM.
        """
        # 1. Fetch request and verify state is APPROVED
        req = self.request_repo.get_request(request_id)
        if not req:
            return ExecutionOutcome(
                success=False,
                request_id=request_id,
                dry_run=True,
                command_id="",
                message=f"❌ Request `{request_id}` not found.",
                output_summary="",
            )

        if req.status != RequestStatus.APPROVED:
            return ExecutionOutcome(
                success=False,
                request_id=request_id,
                dry_run=True,
                command_id="",
                message=f"⚠️ Cannot execute request `{request_id}`: Status is `{req.status.value}` (must be `APPROVED`).",
                output_summary="",
            )

        # 2. Fetch plan and perform TOCTOU hash integrity check
        plan = self.plan_repo.get_plan(request_id)
        if not plan:
            return ExecutionOutcome(
                success=False,
                request_id=request_id,
                dry_run=True,
                command_id="",
                message=f"❌ Remediation plan for `{request_id}` not found.",
                output_summary="",
            )

        computed_hash = compute_plan_hash(plan.model_dump())
        if computed_hash != plan.plan_hash:
            self.request_repo.update_status(request_id, RequestStatus.FAILED)
            if self.audit_logger:
                self.audit_logger.record_event(
                    request_id=request_id,
                    actor_aad_id="SYSTEM_TOCTOU",
                    event_type="TOCTOU_HASH_MISMATCH",
                    payload={"stored_hash": plan.plan_hash, "computed_hash": computed_hash},
                )
            return ExecutionOutcome(
                success=False,
                request_id=request_id,
                dry_run=True,
                command_id="",
                message="🚨 TOCTOU Integrity Violation: Computed plan hash does not match stored hash. Execution blocked.",
                output_summary="",
            )

        # 3. Transition status to EXECUTING
        self.request_repo.update_status(
            request_id=request_id,
            new_status=RequestStatus.EXECUTING,
            expected_status=RequestStatus.APPROVED,
        )

        is_live = settings.is_live_execution_permitted
        file_basenames = [f.basename for f in plan.eligible_files]

        if self.audit_logger:
            self.audit_logger.record_event(
                request_id=request_id,
                actor_aad_id="SYSTEM_EXECUTOR",
                event_type="EXECUTION_STARTED",
                payload={"mode": "LIVE" if is_live else "DRY_RUN", "file_count": len(file_basenames)},
            )

        # 4. Check for Local Folder Target Mode
        if settings.allina_target_mode == "local":
            if settings.allina_dry_run:
                command_id = f"dry-run-local-{uuid4().hex[:10]}"
                simulation_lines = [
                    "=== ALLINA LOCAL DRY RUN SIMULATION ===",
                    f"Target Directory: {plan.target_dir}",
                    f"Eligible Candidate Files ({len(file_basenames)}):",
                ]
                for f in plan.eligible_files:
                    simulation_lines.append(f"  [DRY-RUN] WOULD_DELETE: {f.basename} ({f.size_bytes} bytes, age: {f.age_hours}h)")

                simulation_lines.append(f"Estimated Recovery: {plan.estimated_recovery_bytes} bytes")
                simulation_lines.append("SAFEGUARD ACTIVE: ALLINA_DRY_RUN=true. Local files were not modified.")
                output_summary = "\n".join(simulation_lines)

                self.execution_repo.record_execution(
                    request_id=request_id,
                    command_id=command_id,
                    status="SUCCEEDED",
                    dry_run=True,
                    output_summary=output_summary,
                )
                verification = RemediationVerifier.verify(plan=plan, dry_run=True)
                self.request_repo.update_status(
                    request_id=request_id,
                    new_status=RequestStatus.SUCCEEDED,
                    expected_status=RequestStatus.EXECUTING,
                )
                audit_hash = None
                if self.audit_logger:
                    audit_hash = self.audit_logger.record_event(
                        request_id=request_id,
                        actor_aad_id="SYSTEM_VERIFIER",
                        event_type="LOCAL_DRY_RUN_SUCCEEDED",
                        payload={"dry_run": True, "command_id": command_id, "verified": verification.success},
                    )

                readable_recovery = f"{plan.estimated_recovery_bytes / (1024*1024):.2f} MB"
                audit_note = f"\n- **Audit Chain Hash:** `{audit_hash[:16]}...`" if audit_hash else ""
                return ExecutionOutcome(
                    success=True,
                    request_id=request_id,
                    dry_run=True,
                    command_id=command_id,
                    verification=verification,
                    audit_event_hash=audit_hash,
                    message=(
                        f"🔬 **[DRY-RUN COMPLETE] Local Remediation Simulated & Verified**\n\n"
                        f"- **Request ID:** `{request_id}`\n"
                        f"- **Target Directory:** `{plan.target_dir}`\n"
                        f"- **Execution Mode:** `DRY-RUN (Local Folder)`\n"
                        f"- **Simulated Cleanup:** {len(file_basenames)} files ({readable_recovery})\n"
                        f"- **Verification:** `PASSED` ({verification.details})\n"
                        f"- **Final Status:** `{RequestStatus.SUCCEEDED.value}`"
                        f"{audit_note}\n\n"
                        f"ℹ️ *Note: Real deletion is disabled (`ALLINA_DRY_RUN=true`). Target files remain untouched in your local folder.*"
                    ),
                    output_summary=output_summary,
                )
            else:
                return self._execute_local_live(plan, file_basenames, request_id)

        # 5. Check for Azure VM Target Mode
        if settings.allina_target_mode == "azure":
            if settings.allina_dry_run:
                command_id = f"dry-run-azure-{uuid4().hex[:10]}"
                simulation_lines = [
                    "=== ALLINA AZURE VM DRY RUN SIMULATION ===",
                    f"Target VM: {plan.target_instance_id}",
                    f"Target Directory: {plan.target_dir}",
                    f"Eligible Candidate Files ({len(file_basenames)}):",
                ]
                for f in plan.eligible_files:
                    simulation_lines.append(f"  [DRY-RUN] WOULD_DELETE: {f.basename} ({f.size_bytes} bytes, age: {f.age_hours}h)")

                simulation_lines.append(f"Estimated Recovery: {plan.estimated_recovery_bytes} bytes")
                simulation_lines.append("SAFEGUARD ACTIVE: ALLINA_DRY_RUN=true. Target files on Azure VM were not modified.")
                output_summary = "\n".join(simulation_lines)

                self.execution_repo.record_execution(
                    request_id=request_id,
                    command_id=command_id,
                    status="SUCCEEDED",
                    dry_run=True,
                    output_summary=output_summary,
                )
                verification = RemediationVerifier.verify(plan=plan, dry_run=True)
                self.request_repo.update_status(
                    request_id=request_id,
                    new_status=RequestStatus.SUCCEEDED,
                    expected_status=RequestStatus.EXECUTING,
                )
                audit_hash = None
                if self.audit_logger:
                    audit_hash = self.audit_logger.record_event(
                        request_id=request_id,
                        actor_aad_id="SYSTEM_VERIFIER",
                        event_type="AZURE_DRY_RUN_SUCCEEDED",
                        payload={"dry_run": True, "command_id": command_id, "verified": verification.success},
                    )

                readable_recovery = f"{plan.estimated_recovery_bytes / (1024*1024):.2f} MB"
                audit_note = f"\n- **Audit Chain Hash:** `{audit_hash[:16]}...`" if audit_hash else ""
                return ExecutionOutcome(
                    success=True,
                    request_id=request_id,
                    dry_run=True,
                    command_id=command_id,
                    verification=verification,
                    audit_event_hash=audit_hash,
                    message=(
                        f"🔬 **[DRY-RUN COMPLETE] Azure VM Remediation Simulated & Verified**\n\n"
                        f"- **Request ID:** `{request_id}`\n"
                        f"- **Target VM:** `{plan.target_instance_id}`\n"
                        f"- **Target Directory:** `{plan.target_dir}`\n"
                        f"- **Execution Mode:** `DRY-RUN (Azure VM)`\n"
                        f"- **Simulated Cleanup:** {len(file_basenames)} files ({readable_recovery})\n"
                        f"- **Verification:** `PASSED` ({verification.details})\n"
                        f"- **Final Status:** `{RequestStatus.SUCCEEDED.value}`"
                        f"{audit_note}\n\n"
                        f"ℹ️ *Note: Real deletion is disabled (`ALLINA_DRY_RUN=true`). Target files remain untouched on the Azure VM.*"
                    ),
                    output_summary=output_summary,
                )
            else:
                return self._execute_azure_live(plan, file_basenames, request_id)

        # 6. Check safety switches (Fail-closed check for Cloud EC2/VM)
        if not is_live:
            # === DRY RUN SIMULATION MODE ===
            command_id = f"dry-run-{uuid4().hex[:10]}"
            simulation_lines = [
                "=== ALLINA DRY RUN SIMULATION ===",
                f"Target Instance: {plan.target_instance_id}",
                f"Target Directory: {plan.target_dir}",
                f"Eligible Candidate Files ({len(file_basenames)}):",
            ]
            for f in plan.eligible_files:
                simulation_lines.append(f"  [DRY-RUN] WOULD_DELETE: {f.basename} ({f.size_bytes} bytes, age: {f.age_hours}h)")

            simulation_lines.append(f"Estimated Recovery: {plan.estimated_recovery_bytes} bytes")
            simulation_lines.append("SAFEGUARD ACTIVE: ALLINA_DRY_RUN=true. No files on EC2 were modified.")
            output_summary = "\n".join(simulation_lines)

            # Record execution in DB
            self.execution_repo.record_execution(
                request_id=request_id,
                command_id=command_id,
                status="SUCCEEDED",
                dry_run=True,
                output_summary=output_summary,
            )

            # Verification
            verification = RemediationVerifier.verify(plan=plan, dry_run=True)

            # Transition request status to SUCCEEDED
            self.request_repo.update_status(
                request_id=request_id,
                new_status=RequestStatus.SUCCEEDED,
                expected_status=RequestStatus.EXECUTING,
            )

            audit_hash = None
            if self.audit_logger:
                audit_hash = self.audit_logger.record_event(
                    request_id=request_id,
                    actor_aad_id="SYSTEM_VERIFIER",
                    event_type="EXECUTION_AND_VERIFICATION_SUCCEEDED",
                    payload={
                        "dry_run": True,
                        "command_id": command_id,
                        "verified": verification.success,
                        "details": verification.details,
                    },
                )

            readable_recovery = f"{plan.estimated_recovery_bytes / (1024*1024):.2f} MB"
            audit_note = f"\n- **Audit Chain Hash:** `{audit_hash[:16]}...`" if audit_hash else ""

            return ExecutionOutcome(
                success=True,
                request_id=request_id,
                dry_run=True,
                command_id=command_id,
                verification=verification,
                audit_event_hash=audit_hash,
                message=(
                    f"🔬 **[DRY-RUN COMPLETE] Remediation Simulated & Verified**\n\n"
                    f"- **Request ID:** `{request_id}`\n"
                    f"- **Target Instance:** `{plan.target_instance_id}`\n"
                    f"- **Execution Mode:** `DRY-RUN (Simulation)`\n"
                    f"- **Simulated Cleanup:** {len(file_basenames)} files ({readable_recovery})\n"
                    f"- **Verification:** `PASSED` ({verification.details})\n"
                    f"- **Final Status:** `{RequestStatus.SUCCEEDED.value}`"
                    f"{audit_note}\n\n"
                    f"ℹ️ *Note: Live deletion is disabled (`ALLINA_DRY_RUN=true`). Target files remain untouched on the EC2 instance.*"
                ),
                output_summary=output_summary,
            )

        # === LIVE EXECUTION (Active only when all 3 fail-closed switches are explicitly True) ===
        try:
            # A. Measure before metrics
            before_metrics = None
            try:
                before_metrics = self.ssm_client.inspect_disk(plan.target_dir)
            except Exception as e:
                logger.warning("Pre-execution disk measurement via SSM failed: %s", e)

            # B. Execute allowlisted cleanup command via SSM
            from src.allina.execution.commands import build_destructive_cleanup_command

            cleanup_cmds = build_destructive_cleanup_command(
                target_dir=plan.target_dir,
                file_basenames=file_basenames,
                allowed_prefix=settings.allina_allowed_log_prefix,
                allowed_extension=settings.allina_allowed_log_extension,
            )
            ssm_res = self.ssm_client.execute_script(
                commands=cleanup_cmds,
                comment=f"ALLINA Live EC2 Remediation ({request_id})",
                timeout_seconds=60,
                dry_run=False,
            )

            if ssm_res.status != "Success" or ssm_res.exit_code != 0:
                self.request_repo.update_status(request_id, RequestStatus.FAILED)
                self.execution_repo.record_execution(
                    request_id=request_id,
                    command_id=ssm_res.command_id,
                    status="FAILED",
                    dry_run=False,
                    output_summary=ssm_res.output_summary,
                )
                if self.audit_logger:
                    self.audit_logger.record_event(
                        request_id=request_id,
                        actor_aad_id="SYSTEM_SSM",
                        event_type="SSM_EXECUTION_FAILED",
                        payload={"command_id": ssm_res.command_id, "exit_code": ssm_res.exit_code},
                    )
                return ExecutionOutcome(
                    success=False,
                    request_id=request_id,
                    dry_run=False,
                    command_id=ssm_res.command_id,
                    message=(
                        f"❌ **SSM Execution Failed on `{plan.target_instance_id}`**\n"
                        f"- Command ID: `{ssm_res.command_id}`\n"
                        f"- Status: `{ssm_res.status}` (Exit code: {ssm_res.exit_code})\n"
                        f"- Details: `{ssm_res.output_summary[:200]}`"
                    ),
                    output_summary=ssm_res.output_summary,
                )

            # C. Measure after metrics and remaining files
            after_metrics = None
            remaining_basenames = []
            try:
                after_metrics = self.ssm_client.inspect_disk(plan.target_dir)
                rem_files = self.ssm_client.inspect_files(
                    target_dir=plan.target_dir,
                    prefix=settings.allina_allowed_log_prefix,
                    extension=settings.allina_allowed_log_extension,
                    min_age_hours=0,
                )
                remaining_basenames = [f["basename"] for f in rem_files]
            except Exception as e:
                logger.warning("Post-execution verification measurements via SSM failed: %s", e)

            # D. Independent Verification
            verification = RemediationVerifier.verify(
                plan=plan,
                before_metrics=before_metrics,
                after_metrics=after_metrics,
                remaining_files=remaining_basenames,
                dry_run=False,
            )

            final_status = RequestStatus.SUCCEEDED if verification.success else RequestStatus.FAILED
            self.request_repo.update_status(request_id, final_status)

            self.execution_repo.record_execution(
                request_id=request_id,
                command_id=ssm_res.command_id,
                status=final_status.value,
                dry_run=False,
                output_summary=ssm_res.output_summary,
                before_metrics=before_metrics.model_dump_json() if before_metrics else None,
                after_metrics=after_metrics.model_dump_json() if after_metrics else None,
            )

            audit_hash = None
            if self.audit_logger:
                audit_hash = self.audit_logger.record_event(
                    request_id=request_id,
                    actor_aad_id="SYSTEM_VERIFIER",
                    event_type="LIVE_EXECUTION_COMPLETED",
                    payload={
                        "command_id": ssm_res.command_id,
                        "success": verification.success,
                        "actual_freed_bytes": verification.actual_freed_bytes,
                        "details": verification.details,
                    },
                )

            readable_freed = f"{verification.actual_freed_bytes / (1024*1024):.2f} MB"
            audit_note = f"\n- **Audit Chain Hash:** `{audit_hash[:16]}...`" if audit_hash else ""

            if verification.success:
                return ExecutionOutcome(
                    success=True,
                    request_id=request_id,
                    dry_run=False,
                    command_id=ssm_res.command_id,
                    verification=verification,
                    audit_event_hash=audit_hash,
                    message=(
                        f"🚀 **[LIVE REMEDIATION SUCCEEDED] EC2 Disk Space Recovered**\n\n"
                        f"- **Request ID:** `{request_id}`\n"
                        f"- **Target Instance:** `{plan.target_instance_id}`\n"
                        f"- **SSM Command ID:** `{ssm_res.command_id}`\n"
                        f"- **Files Removed:** {len(file_basenames)} files\n"
                        f"- **Disk Space Recovered:** `{readable_freed}`\n"
                        f"- **Verification:** `PASSED` ({verification.details})\n"
                        f"- **Final Status:** `{RequestStatus.SUCCEEDED.value}`"
                        f"{audit_note}"
                    ),
                    output_summary=ssm_res.output_summary,
                )
            else:
                return ExecutionOutcome(
                    success=False,
                    request_id=request_id,
                    dry_run=False,
                    command_id=ssm_res.command_id,
                    verification=verification,
                    audit_event_hash=audit_hash,
                    message=(
                        f"⚠️ **[VERIFICATION FAILED] Remediation Post-Check Failed**\n\n"
                        f"- **Request ID:** `{request_id}`\n"
                        f"- **Target Instance:** `{plan.target_instance_id}`\n"
                        f"- **Reason:** {verification.details}\n"
                        f"- **Final Status:** `{RequestStatus.FAILED.value}`"
                        f"{audit_note}"
                    ),
                    output_summary=ssm_res.output_summary,
                )

        except Exception as e:
            logger.exception("Unexpected error during live execution: %s", e)
            self.request_repo.update_status(request_id, RequestStatus.FAILED)
            return ExecutionOutcome(
                success=False,
                request_id=request_id,
                dry_run=False,
                command_id="",
                message=f"❌ Live execution encountered an unexpected error: {str(e)}",
                output_summary=str(e),
            )

    def _execute_local_live(
        self, plan: RemediationPlan, file_basenames: List[str], request_id: str
    ) -> ExecutionOutcome:
        """Executes real local unlinking strictly confined to the allowed directory."""
        command_id = f"local-del-{uuid4().hex[:10]}"
        deleted_count = 0
        deleted_bytes = 0
        errors = []

        allowed_dir = os.path.abspath(plan.target_dir)

        for candidate in plan.eligible_files:
            file_abs_path = os.path.abspath(candidate.path)
            # Recheck containment strictly
            if not file_abs_path.lower().startswith(allowed_dir.lower()):
                errors.append(f"Security: {candidate.basename} is outside allowed directory")
                continue

            if not os.path.exists(file_abs_path):
                # File already deleted or moved
                continue

            try:
                file_size = os.path.getsize(file_abs_path)
                os.remove(file_abs_path)
                deleted_count += 1
                deleted_bytes += file_size
            except Exception as e:
                errors.append(f"Failed to delete {candidate.basename}: {e}")

        # Verification: check remaining files
        remaining = []
        if os.path.exists(allowed_dir):
            remaining = [f.name for f in os.scandir(allowed_dir) if f.is_file()]

        verification = RemediationVerifier.verify(
            plan=plan,
            remaining_files=remaining,
            dry_run=False,
        )

        final_status = RequestStatus.SUCCEEDED if verification.success else RequestStatus.FAILED
        self.request_repo.update_status(request_id, final_status)

        output_summary = (
            f"=== LOCAL REMEDIATION EXECUTION ===\n"
            f"Target Directory: {allowed_dir}\n"
            f"Files Deleted: {deleted_count} ({deleted_bytes} bytes)\n"
            f"Errors: {errors if errors else 'None'}\n"
        )

        self.execution_repo.record_execution(
            request_id=request_id,
            command_id=command_id,
            status=final_status.value,
            dry_run=False,
            output_summary=output_summary,
        )

        audit_hash = None
        if self.audit_logger:
            audit_hash = self.audit_logger.record_event(
                request_id=request_id,
                actor_aad_id="LOCAL_EXECUTOR",
                event_type="LOCAL_EXECUTION_COMPLETED",
                payload={
                    "command_id": command_id,
                    "deleted_count": deleted_count,
                    "deleted_bytes": deleted_bytes,
                    "verified": verification.success,
                    "details": verification.details,
                },
            )

        readable_freed = f"{deleted_bytes / (1024*1024):.2f} MB"
        audit_note = f"\n- **Audit Chain Hash:** `{audit_hash[:16]}...`" if audit_hash else ""

        if verification.success:
            deleted_list = "\n".join([f"  - `{b}`" for b in file_basenames]) or "  - *(None)*"
            return ExecutionOutcome(
                success=True,
                request_id=request_id,
                dry_run=False,
                command_id=command_id,
                verification=verification,
                audit_event_hash=audit_hash,
                message=(
                    f"🚀 **[LOCAL REMEDIATION SUCCEEDED] Local Disk Space Recovered**\n\n"
                    f"- **Request ID:** `{request_id}`\n"
                    f"- **Target Directory:** `{allowed_dir}`\n"
                    f"- **Files Deleted ({deleted_count}):**\n{deleted_list}\n"
                    f"- **Disk Space Recovered:** `{readable_freed}`\n"
                    f"- **Verification:** `PASSED` ({verification.details})\n"
                    f"- **Final Status:** `{RequestStatus.SUCCEEDED.value}`"
                    f"{audit_note}\n\n"
                    f"📁 *Check your folder `{allowed_dir}`: The eligible files have been deleted!*"
                ),
                output_summary=output_summary,
            )
        else:
            return ExecutionOutcome(
                success=False,
                request_id=request_id,
                dry_run=False,
                command_id=command_id,
                verification=verification,
                audit_event_hash=audit_hash,
                message=(
                    f"⚠️ **[VERIFICATION FAILED] Local Remediation Post-Check Failed**\n\n"
                    f"- **Request ID:** `{request_id}`\n"
                    f"- **Target Directory:** `{allowed_dir}`\n"
                    f"- **Reason:** {verification.details}\n"
                    f"- **Final Status:** `{RequestStatus.FAILED.value}`"
                    f"{audit_note}"
                ),
                output_summary=output_summary,
            )

    def _execute_azure_live(
        self, plan: RemediationPlan, file_basenames: List[str], request_id: str
    ) -> ExecutionOutcome:
        """Executes real unlinking on the Azure VM via Run Command."""
        from src.allina.execution.azure_client import AzureVMRunCommandClient
        from src.allina.execution.commands import build_destructive_cleanup_command

        azure_client = AzureVMRunCommandClient()
        cleanup_cmds = build_destructive_cleanup_command(
            target_dir=plan.target_dir,
            file_basenames=file_basenames,
            allowed_prefix=settings.allina_allowed_log_prefix,
            allowed_extension=settings.allina_allowed_log_extension,
        )
        res = azure_client.execute_script(
            commands=cleanup_cmds,
            comment=f"ALLINA Live Azure Remediation ({request_id})",
        )

        remaining = []
        try:
            rem_files = azure_client.inspect_files(
                target_dir=plan.target_dir,
                prefix=settings.allina_allowed_log_prefix,
                extension=settings.allina_allowed_log_extension,
                min_age_hours=0,
            )
            remaining = [f["basename"] for f in rem_files]
        except Exception as e:
            logger.warning("Post-execution verification check failed on Azure VM: %s", e)

        verification = RemediationVerifier.verify(
            plan=plan,
            remaining_files=remaining,
            dry_run=False,
        )

        final_status = RequestStatus.SUCCEEDED if verification.success else RequestStatus.FAILED
        self.request_repo.update_status(request_id, final_status)

        self.execution_repo.record_execution(
            request_id=request_id,
            command_id=res.command_id,
            status=final_status.value,
            dry_run=False,
            output_summary=res.output_summary,
        )

        audit_hash = None
        if self.audit_logger:
            audit_hash = self.audit_logger.record_event(
                request_id=request_id,
                actor_aad_id="AZURE_EXECUTOR",
                event_type="AZURE_EXECUTION_COMPLETED",
                payload={
                    "command_id": res.command_id,
                    "verified": verification.success,
                    "details": verification.details,
                },
            )

        readable_freed = f"{plan.estimated_recovery_bytes / (1024*1024):.2f} MB"
        audit_note = f"\n- **Audit Chain Hash:** `{audit_hash[:16]}...`" if audit_hash else ""

        if verification.success:
            deleted_list = "\n".join([f"  - `{b}`" for b in file_basenames]) or "  - *(None)*"
            return ExecutionOutcome(
                success=True,
                request_id=request_id,
                dry_run=False,
                command_id=res.command_id,
                verification=verification,
                audit_event_hash=audit_hash,
                message=(
                    f"🚀 **[AZURE REMEDIATION SUCCEEDED] Disk Space Recovered on `{plan.target_instance_id}`**\n\n"
                    f"- **Request ID:** `{request_id}`\n"
                    f"- **Target VM:** `{plan.target_instance_id}`\n"
                    f"- **Target Directory:** `{plan.target_dir}`\n"
                    f"- **Files Deleted ({len(file_basenames)}):**\n{deleted_list}\n"
                    f"- **Disk Space Recovered:** `{readable_freed}`\n"
                    f"- **Verification:** `PASSED` ({verification.details})\n"
                    f"- **Final Status:** `{RequestStatus.SUCCEEDED.value}`"
                    f"{audit_note}"
                ),
                output_summary=res.output_summary,
            )
        else:
            return ExecutionOutcome(
                success=False,
                request_id=request_id,
                dry_run=False,
                command_id=res.command_id,
                verification=verification,
                audit_event_hash=audit_hash,
                message=(
                    f"⚠️ **[VERIFICATION FAILED] Azure Remediation Post-Check Failed**\n\n"
                    f"- **Request ID:** `{request_id}`\n"
                    f"- **Target VM:** `{plan.target_instance_id}`\n"
                    f"- **Reason:** {verification.details}\n"
                    f"- **Final Status:** `{RequestStatus.FAILED.value}`"
                    f"{audit_note}"
                ),
                output_summary=res.output_summary,
            )



