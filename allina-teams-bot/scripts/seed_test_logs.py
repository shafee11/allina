"""Seed helper script to populate d:/allina_project/local-test-logs with realistic sample logs."""

import os
import sys
import time

TARGET_DIR = "d:/allina_project/local-test-logs"


def create_dummy_file(path: str, size_mb: int, age_hours: float):
    """Write dummy content and set modified timestamp."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write sparse/fast dummy data
    with open(path, "wb") as f:
        # Write chunks of 1MB
        chunk = b"X" * (1024 * 1024)
        for _ in range(size_mb):
            f.write(chunk)

    # Backdate mtime
    target_mtime = time.time() - (age_hours * 3600.0)
    os.utime(path, (target_mtime, target_mtime))


def seed():
    print(f"==================================================")
    print(f"  ALLINA Local Test Log Generator")
    print(f"  Target: {TARGET_DIR}")
    print(f"==================================================")

    files_to_create = [
        # (filename, size_mb, age_hours, expected_eligibility, reason)
        (
            "allina-poc-service-test1.log",
            10,
            72.0,
            "ELIGIBLE",
            "Prefix matches, age (72h) >= 24h",
        ),
        (
            "allina-poc-batch-run.log",
            25,
            48.0,
            "ELIGIBLE",
            "Prefix matches, age (48h) >= 24h",
        ),
        (
            "allina-poc-active-worker.log",
            5,
            1.0,
            "PROTECTED (Too recent)",
            "Prefix matches, but age (1h) < 24h",
        ),
        (
            "important-system-config.log",
            2,
            72.0,
            "PROTECTED (Wrong prefix)",
            "Does not start with 'allina-poc-'",
        ),
    ]

    for fname, size_mb, age_h, elig, reason in files_to_create:
        fpath = os.path.join(TARGET_DIR, fname)
        create_dummy_file(fpath, size_mb, age_h)
        status_icon = "🟢" if elig == "ELIGIBLE" else "🛡️"
        print(f"{status_icon} Created {fname:30} ({size_mb} MB, age {age_h:4.1f}h) -> {elig}")

    print(f"--------------------------------------------------")
    print(f"Summary:")
    print(f"  - Total files in folder: 4 (42 MB)")
    print(f"  - Files eligible for remediation: 2 (35 MB)")
    print(f"  - Protected files: 2 (7 MB)")
    print(f"==================================================")
    print(f"Ready for Teams test!")


if __name__ == "__main__":
    seed()

