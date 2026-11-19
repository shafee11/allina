from datetime import datetime, timezone
from src.allina.execution.verification import RemediationVerifier
from src.allina.schemas.plans import DiskMetrics, FileCandidate, RemediationPlan


def _create_test_plan():
    now = datetime.now(timezone.utc)
    return RemediationPlan(
        request_id="req-verify-01",
        target_instance_id="i-test",
        target_dir="/opt/allina/test-logs",
        eligible_files=[
            FileCandidate(
                path="/opt/allina/test-logs/allina-poc-1.log",
                basename="allina-poc-1.log",
                size_bytes=100 * 1024 * 1024,  # 100 MB
                mtime=now,
                age_hours=48.0,
            )
        ],
        estimated_recovery_bytes=100 * 1024 * 1024,
        created_at=now,
        expires_at=now,
    ).finalize_hash()


def test_dry_run_verification():
    plan = _create_test_plan()
    res = RemediationVerifier.verify(plan=plan, dry_run=True)
    assert res.success is True
    assert res.actual_freed_bytes == plan.estimated_recovery_bytes
    assert "Dry-run simulation verified" in res.details


def test_live_verification_success():
    plan = _create_test_plan()
    before = DiskMetrics(mount_point="/", total_bytes=1000, used_bytes=800, available_bytes=200, percent_used=80.0)
    after = DiskMetrics(mount_point="/", total_bytes=1000, used_bytes=700, available_bytes=300, percent_used=70.0)

    res = RemediationVerifier.verify(
        plan=plan,
        before_metrics=before,
        after_metrics=after,
        remaining_files=[],
        dry_run=False,
    )
    assert res.success is True
    assert res.actual_freed_bytes == 100
    assert "Verification Succeeded" in res.details


def test_live_verification_fails_if_files_remain():
    plan = _create_test_plan()
    res = RemediationVerifier.verify(
        plan=plan,
        remaining_files=["allina-poc-1.log"],
        dry_run=False,
    )
    assert res.success is False
    assert "planned files were not removed" in res.details


def test_live_verification_fails_on_same_volume_move():
    plan = _create_test_plan()
    # Used bytes identical -> file was only moved on same volume
    before = DiskMetrics(mount_point="/", total_bytes=1000, used_bytes=800, available_bytes=200, percent_used=80.0)
    after = DiskMetrics(mount_point="/", total_bytes=1000, used_bytes=800, available_bytes=200, percent_used=80.0)

    res = RemediationVerifier.verify(
        plan=plan,
        before_metrics=before,
        after_metrics=after,
        remaining_files=[],
        dry_run=False,
    )
    assert res.success is False
    assert "same volume is not valid space recovery" in res.details

