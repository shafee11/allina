import pytest
from src.allina.execution.commands import (
    CommandSafetyError,
    build_dry_run_command,
    build_inspect_disk_command,
    build_inspect_files_command,
    sanitize_basename,
    sanitize_directory,
)


def test_sanitize_directory_valid_and_invalid():
    assert sanitize_directory("/opt/allina/test-logs") == "/opt/allina/test-logs"

    with pytest.raises(CommandSafetyError):
        sanitize_directory("../../etc")

    with pytest.raises(CommandSafetyError):
        sanitize_directory("relative/path")


def test_sanitize_basename_valid_and_invalid():
    valid = sanitize_basename("allina-poc-test1.log", "allina-poc-", ".log")
    assert valid == "allina-poc-test1.log"

    # Injected characters
    with pytest.raises(CommandSafetyError):
        sanitize_basename("allina-poc-test;rm -rf /;.log", "allina-poc-", ".log")

    # Wrong prefix
    with pytest.raises(CommandSafetyError):
        sanitize_basename("other-poc-test.log", "allina-poc-", ".log")

    # Wrong extension
    with pytest.raises(CommandSafetyError):
        sanitize_basename("allina-poc-test.sh", "allina-poc-", ".log")


def test_build_inspect_commands():
    disk_cmd = build_inspect_disk_command("/opt/allina/test-logs")
    script = "\n".join(disk_cmd)
    assert "df -P -k '/opt/allina/test-logs'" in script

    files_cmd = build_inspect_files_command("/opt/allina/test-logs", "allina-poc-", ".log", 24)
    files_script = "\n".join(files_cmd)
    assert "TARGET_DIR='/opt/allina/test-logs'" in files_script
    assert "MIN_AGE_MINUTES=1440" in files_script
    assert "-type f ! -type l" in files_script


def test_build_dry_run_command():
    dry_cmd = build_dry_run_command("/opt/allina/test-logs", ["allina-poc-1.log", "allina-poc-2.log"])
    dry_script = "\n".join(dry_cmd)
    assert "=== ALLINA DRY RUN SIMULATION ===" in dry_script
    assert "WOULD_DELETE: allina-poc-1.log" in dry_script
    assert "WOULD_DELETE: allina-poc-2.log" in dry_script

