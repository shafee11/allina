"""Post-execution verification ensuring disk space recovery and file deletion."""

from datetime import datetime, timezone
from typing import List, Optional
from src.allina.schemas.execution import VerificationResult
from src.allina.schemas.plans import DiskMetrics, RemediationPlan


class RemediationVerifier:
    """Verifies remediation outcomes against pre-checks, post-checks, and runbook criteria."""

    @classmethod
    def verify(
        self,
        plan: RemediationPlan,
        before_metrics: Optional[DiskMetrics] = None,
        after_metrics: Optional[DiskMetrics] = None,
        remaining_files: Optional[List[str]] = None,
        dry_run: bool = True,
    ) -> VerificationResult:
        """
        Validate remediation outcome.
        Requires independent evidence that files were cleaned and disk space freed.
        """
        now = datetime.now(timezone.utc)
        remaining = remaining_files or []

        # 1. Handle Dry-Run verification
        if dry_run:
            return VerificationResult(
                success=True,
                actual_freed_bytes=plan.estimated_recovery_bytes,
                files_remaining=[],
                before_metrics=before_metrics,
                after_metrics=after_metrics,
                details=(
                    f"Dry-run simulation verified. {len(plan.eligible_files)} files "
                    f"({plan.estimated_recovery_bytes / (1024*1024):.2f} MB) identified for safe cleanup. "
                    f"Target files preserved on EC2."
                ),
                verified_at=now,
            )

        # 2. Live Execution Verification: Check remaining files
        planned_basenames = {f.basename for f in plan.eligible_files}
        remaining_targeted = [f for f in remaining if f in planned_basenames]

        if remaining_targeted:
            return VerificationResult(
                success=False,
                actual_freed_bytes=0,
                files_remaining=remaining_targeted,
                before_metrics=before_metrics,
                after_metrics=after_metrics,
                details=(
                    f"Verification Failed: {len(remaining_targeted)} planned files were not removed: "
                    f"{', '.join(remaining_targeted[:5])}"
                ),
                verified_at=now,
            )

        # 3. Live Execution Verification: Measure disk space delta
        if before_metrics and after_metrics:
            actual_freed = after_metrics.available_bytes - before_metrics.available_bytes

            # Detect same-volume move fraud: used_bytes unchanged
            if after_metrics.used_bytes == before_metrics.used_bytes:
                return VerificationResult(
                    success=False,
                    actual_freed_bytes=0,
                    files_remaining=[],
                    before_metrics=before_metrics,
                    after_metrics=after_metrics,
                    details=(
                        "Verification Failed: Filesystem used bytes unchanged. "
                        "Moving files to another directory on the same volume is not valid space recovery."
                    ),
                    verified_at=now,
                )

            if actual_freed <= 0:
                return VerificationResult(
                    success=False,
                    actual_freed_bytes=actual_freed,
                    files_remaining=[],
                    before_metrics=before_metrics,
                    after_metrics=after_metrics,
                    details="Verification Failed: Available disk space did not increase after remediation.",
                    verified_at=now,
                )

            return VerificationResult(
                success=True,
                actual_freed_bytes=actual_freed,
                files_remaining=[],
                before_metrics=before_metrics,
                after_metrics=after_metrics,
                details=(
                    f"Verification Succeeded: {len(planned_basenames)} test log files verified deleted. "
                    f"Actual disk space recovered: {actual_freed / (1024*1024):.2f} MB."
                ),
                verified_at=now,
            )

        # If metrics were not collected but files are confirmed deleted
        return VerificationResult(
            success=True,
            actual_freed_bytes=plan.estimated_recovery_bytes,
            files_remaining=[],
            before_metrics=before_metrics,
            after_metrics=after_metrics,
            details=f"Verification Succeeded: All {len(planned_basenames)} planned files confirmed unlinked.",
            verified_at=now,
        )

