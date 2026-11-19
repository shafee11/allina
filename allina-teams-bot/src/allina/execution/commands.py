"""Fixed, allowlisted shell commands for AWS SSM Run Command.

CRITICAL SECURITY RULE:
Never concatenate arbitrary user or model text into shell commands.
All commands here use static templates with server-side validated parameters only.
"""

import posixpath
import re
from typing import List

# Allowlist pattern for file basenames: only alphanumeric, dashes, dots, underscores
SAFE_BASENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


class CommandSafetyError(Exception):
    """Raised when a parameter fails strict shell safety checks."""
    pass


def sanitize_directory(directory: str) -> str:
    """Normalize and validate directory path."""
    normalized = posixpath.normpath(directory.replace("\\", "/"))
    if ".." in normalized or not normalized.startswith("/"):
        raise CommandSafetyError(f"Invalid target directory path: '{directory}'")
    return normalized


def sanitize_basename(basename: str, allowed_prefix: str, allowed_extension: str) -> str:
    """Validate that a filename strictly conforms to allowlist rules."""
    if not SAFE_BASENAME_PATTERN.match(basename):
        raise CommandSafetyError(f"Basename '{basename}' contains illegal characters.")
    if not basename.startswith(allowed_prefix):
        raise CommandSafetyError(f"Basename '{basename}' does not start with '{allowed_prefix}'.")
    if not basename.endswith(allowed_extension):
        raise CommandSafetyError(f"Basename '{basename}' does not end with '{allowed_extension}'.")
    return basename


def build_inspect_disk_command(mount_or_dir: str) -> List[str]:
    """Generate POSIX df command to inspect filesystem space."""
    clean_dir = sanitize_directory(mount_or_dir)
    return [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"df -P -k '{clean_dir}' | tail -n 1",
    ]


def build_inspect_files_command(
    target_dir: str, allowed_prefix: str, allowed_extension: str, min_age_hours: int
) -> List[str]:
    """
    Generate read-only script to discover candidate log files.
    Excludes symlinks, active files, and files newer than min_age_hours.
    Outputs: path|size_bytes|mtime_epoch
    """
    clean_dir = sanitize_directory(target_dir)
    min_age_minutes = int(min_age_hours * 60)

    return [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"TARGET_DIR='{clean_dir}'",
        f"PREFIX='{allowed_prefix}'",
        f"EXT='{allowed_extension}'",
        f"MIN_AGE_MINUTES={min_age_minutes}",
        "",
        'if [ ! -d "$TARGET_DIR" ]; then',
        '  echo "ERROR: Directory does not exist: $TARGET_DIR" >&2',
        "  exit 2",
        "fi",
        "",
        "# Find regular files only, no symlinks, matching prefix and extension, older than N minutes",
        'find "$TARGET_DIR" -maxdepth 1 -type f ! -type l -name "${PREFIX}*${EXT}" -mmin "+${MIN_AGE_MINUTES}" -printf "%p|%s|%T@\\n" 2>/dev/null || true',
    ]


def build_dry_run_command(target_dir: str, file_basenames: List[str]) -> List[str]:
    """
    Generate dry-run simulation script.
    Checks that candidate files exist and reports what would happen without deleting anything.
    """
    clean_dir = sanitize_directory(target_dir)

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"TARGET_DIR='{clean_dir}'",
        'echo "=== ALLINA DRY RUN SIMULATION ==="',
        'echo "Target Directory: $TARGET_DIR"',
        "TOTAL_BYTES=0",
        "FILE_COUNT=0",
        "",
    ]

    for basename in file_basenames:
        # Extra assertion on each filename
        if not SAFE_BASENAME_PATTERN.match(basename):
            continue
        lines.append(f"TARGET_FILE=\"$TARGET_DIR/{basename}\"")
        lines.append('if [ -f "$TARGET_FILE" ] && [ ! -L "$TARGET_FILE" ]; then')
        lines.append('  SIZE=$(stat -c "%s" "$TARGET_FILE" 2>/dev/null || wc -c < "$TARGET_FILE")')
        lines.append('  TOTAL_BYTES=$((TOTAL_BYTES + SIZE))')
        lines.append('  FILE_COUNT=$((FILE_COUNT + 1))')
        lines.append(f'  echo "[DRY-RUN] WOULD_DELETE: {basename} ($SIZE bytes)"')
        lines.append("else")
        lines.append(f'  echo "[DRY-RUN] FILE_NOT_FOUND_OR_SYMLINK: {basename}"')
        lines.append("fi")

    lines.append("")
    lines.append('echo "SUMMARY: Would clean $FILE_COUNT files recovering $TOTAL_BYTES bytes."')
    lines.append('echo "STATUS: SUCCESS (No files were modified)"')

    return lines


def build_destructive_cleanup_command(
    target_dir: str, file_basenames: List[str], allowed_prefix: str, allowed_extension: str
) -> List[str]:
    """
    Generate live cleanup script.
    Executed ONLY when all safety switches are confirmed.
    """
    clean_dir = sanitize_directory(target_dir)

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"TARGET_DIR='{clean_dir}'",
        'echo "=== ALLINA LIVE REMEDIATION EXECUTION ==="',
        "REMOVED_COUNT=0",
        "REMOVED_BYTES=0",
        "",
    ]

    for basename in file_basenames:
        clean_name = sanitize_basename(basename, allowed_prefix, allowed_extension)
        lines.append(f"TARGET=\"$TARGET_DIR/{clean_name}\"")
        lines.append('if [ -f "$TARGET" ] && [ ! -L "$TARGET" ]; then')
        lines.append('  SZ=$(stat -c "%s" "$TARGET" 2>/dev/null || echo 0)')
        lines.append('  rm -f "$TARGET"')
        lines.append('  REMOVED_COUNT=$((REMOVED_COUNT + 1))')
        lines.append('  REMOVED_BYTES=$((REMOVED_BYTES + SZ))')
        lines.append(f'  echo "UNLINKED: {clean_name} ($SZ bytes)"')
        lines.append("else")
        lines.append(f'  echo "SKIPPED_NOT_FOUND_OR_SYMLINK: {clean_name}"')
        lines.append("fi")

    lines.append("")
    lines.append('echo "EXECUTION_COMPLETE: Removed $REMOVED_COUNT files ($REMOVED_BYTES bytes)"')
    return lines

