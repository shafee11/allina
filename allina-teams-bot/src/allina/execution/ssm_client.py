"""AWS Systems Manager (SSM) Boto3 client adapter for EC2 operations."""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from src.allina.config import settings
from src.allina.execution.commands import (
    build_dry_run_command,
    build_inspect_disk_command,
    build_inspect_files_command,
)
from src.allina.schemas.execution import SSMExecutionResult
from src.allina.schemas.plans import DiskMetrics

logger = logging.getLogger(__name__)

INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")


class SSMClientError(Exception):
    """Raised on SSM invocation or AWS connectivity failure."""
    pass


class Boto3SSMClient:
    """Encapsulates AWS Systems Manager Run Command operations."""

    def __init__(
        self,
        instance_id: Optional[str] = None,
        region: Optional[str] = None,
        profile: Optional[str] = None,
        ssm_client: Optional[Any] = None,
    ):
        self.instance_id = instance_id or settings.allina_ec2_instance_id
        self.region = region or settings.aws_region
        self.profile = profile or settings.aws_profile
        self._ssm = ssm_client

    def _get_ssm(self):
        """Lazy-initialize Boto3 SSM client."""
        if self._ssm is not None:
            return self._ssm

        session_kwargs = {"region_name": self.region}
        if self.profile:
            session_kwargs["profile_name"] = self.profile

        session = boto3.Session(**session_kwargs)
        self._ssm = session.client("ssm")
        return self._ssm

    def preflight_check(self) -> Tuple[bool, str]:
        """
        Validate AWS credentials, instance ID format, and SSM online status.
        Returns: (is_ready, message)
        """
        if not self.instance_id or not INSTANCE_ID_PATTERN.match(self.instance_id):
            return (
                False,
                f"Invalid or placeholder EC2 Instance ID: '{self.instance_id}'. "
                f"Please configure a valid AWS EC2 instance ID (e.g. i-0123456789abcdef0).",
            )

        try:
            ssm = self._get_ssm()
            resp = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [self.instance_id]}]
            )
            instances = resp.get("InstanceInformationList", [])
            if not instances:
                return (
                    False,
                    f"EC2 instance '{self.instance_id}' is not registered with Systems Manager. "
                    f"Verify SSM Agent is running and IAM instance profile is attached.",
                )

            info = instances[0]
            ping_status = info.get("PingStatus", "Unknown")
            if ping_status != "Online":
                return (
                    False,
                    f"EC2 instance '{self.instance_id}' is not online in SSM (Current status: {ping_status}).",
                )

            return True, f"Target instance '{self.instance_id}' is online and managed by SSM."

        except (ClientError, BotoCoreError) as e:
            return False, f"AWS SSM preflight failed: {str(e)}"
        except Exception as e:
            return False, f"Unexpected error during AWS preflight: {str(e)}"

    def execute_script(
        self,
        commands: List[str],
        comment: str,
        timeout_seconds: int = 30,
        dry_run: bool = True,
    ) -> SSMExecutionResult:
        """
        Send command script via SSM Run Command and poll to terminal status.
        """
        ssm = self._get_ssm()
        script_text = "\n".join(commands)

        try:
            send_resp = ssm.send_command(
                InstanceIds=[self.instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [script_text]},
                TimeoutSeconds=timeout_seconds,
                Comment=comment[:100],
            )
            command_id = send_resp["Command"]["CommandId"]
        except (ClientError, BotoCoreError) as e:
            raise SSMClientError(f"Failed to send SSM command: {str(e)}") from e

        # Poll for completion
        terminal_statuses = {"Success", "Failed", "TimedOut", "Cancelled"}
        max_attempts = 15
        delay = 2.0

        for _ in range(max_attempts):
            time.sleep(delay)
            try:
                inv = ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=self.instance_id,
                )
                status = inv.get("Status", "Pending")
                if status in terminal_statuses:
                    exit_code = inv.get("ResponseCode", -1)
                    output = inv.get("StandardOutputContent", "") or inv.get(
                        "StandardErrorContent", ""
                    )
                    return SSMExecutionResult(
                        command_id=command_id,
                        instance_id=self.instance_id,
                        status=status,
                        exit_code=exit_code,
                        output_summary=output[:2000].strip(),
                        dry_run=dry_run,
                    )
            except (ClientError, BotoCoreError) as e:
                logger.warning("Error polling SSM command %s: %s", command_id, e)

        return SSMExecutionResult(
            command_id=command_id,
            instance_id=self.instance_id,
            status="TimedOut",
            exit_code=-1,
            output_summary="Command invocation timed out waiting for SSM completion.",
            dry_run=dry_run,
        )

    def inspect_disk(self, mount_or_dir: str) -> DiskMetrics:
        """Execute read-only df command and parse DiskMetrics."""
        commands = build_inspect_disk_command(mount_or_dir)
        result = self.execute_script(commands, comment="ALLINA Read-only Disk Inspection", dry_run=True)

        if result.status != "Success" or result.exit_code != 0:
            raise SSMClientError(f"Disk inspection failed: {result.output_summary}")

        # df -P -k output line format: Filesystem 1024-blocks Used Available Capacity Mounted on
        line = result.output_summary.strip().splitlines()[-1]
        parts = line.split()
        if len(parts) < 6:
            raise SSMClientError(f"Unexpected df output format: '{line}'")

        try:
            total_kib = int(parts[1])
            used_kib = int(parts[2])
            avail_kib = int(parts[3])
            pct_str = parts[4].rstrip("%")
            percent_used = float(pct_str)
            mount_point = parts[5]

            return DiskMetrics(
                mount_point=mount_point,
                total_bytes=total_kib * 1024,
                used_bytes=used_kib * 1024,
                available_bytes=avail_kib * 1024,
                percent_used=percent_used,
            )
        except ValueError as e:
            raise SSMClientError(f"Failed to parse df numbers: {e}") from e

    def inspect_files(
        self, target_dir: str, prefix: str, extension: str, min_age_hours: int
    ) -> List[Dict[str, Any]]:
        """Discover candidate files matching runbook rules on EC2 instance."""
        commands = build_inspect_files_command(target_dir, prefix, extension, min_age_hours)
        result = self.execute_script(commands, comment="ALLINA Read-only File Inspection", dry_run=True)

        if result.status != "Success" or result.exit_code != 0:
            raise SSMClientError(f"File inspection failed: {result.output_summary}")

        files: List[Dict[str, Any]] = []
        for line in result.output_summary.strip().splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|")
            if len(parts) >= 3:
                file_path = parts[0]
                size_bytes = int(parts[1])
                epoch = float(parts[2])
                mtime = datetime.fromtimestamp(epoch, tz=timezone.utc)
                basename = file_path.split("/")[-1]

                files.append(
                    {
                        "path": file_path,
                        "basename": basename,
                        "size_bytes": size_bytes,
                        "mtime": mtime,
                        "is_symlink": False,
                        "is_active": False,
                    }
                )

        return files

    def run_dry_run_simulation(
        self, target_dir: str, file_basenames: List[str]
    ) -> SSMExecutionResult:
        """Run dry-run simulation via SSM."""
        commands = build_dry_run_command(target_dir, file_basenames)
        return self.execute_script(
            commands,
            comment="ALLINA Dry-Run Remediation Simulation",
            dry_run=True,
        )

