"""Azure Virtual Machine Run Command client adapter."""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import RunCommandInput
from src.allina.config import settings
from src.allina.schemas.execution import SSMExecutionResult
from src.allina.schemas.plans import DiskMetrics

logger = logging.getLogger(__name__)


class AzureClientError(Exception):
    """Raised on Azure Run Command invocation or connectivity failure."""
    pass


class AzureVMRunCommandClient:
    """Encapsulates Azure Virtual Machine Run Command operations."""

    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        subscription_id: Optional[str] = None,
        resource_group: Optional[str] = None,
        vm_name: Optional[str] = None,
    ):
        self.tenant_id = tenant_id or settings.azure_tenant_id
        self.client_id = client_id or settings.azure_client_id
        self.client_secret = client_secret or settings.azure_client_secret
        self.subscription_id = subscription_id or settings.azure_subscription_id
        self.resource_group = resource_group or settings.azure_resource_group
        self.vm_name = vm_name or settings.azure_vm_name
        self._compute_client: Optional[ComputeManagementClient] = None

    def _get_client(self) -> ComputeManagementClient:
        """Lazy-initialize ComputeManagementClient."""
        if self._compute_client is not None:
            return self._compute_client

        if not all([self.tenant_id, self.client_id, self.client_secret, self.subscription_id]):
            raise AzureClientError("Missing required Azure credentials in configuration.")

        cred = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        self._compute_client = ComputeManagementClient(
            credential=cred,
            subscription_id=self.subscription_id,
        )
        return self._compute_client

    def check_instance_online(self) -> Tuple[bool, str]:
        """Check if the target Azure VM is running."""
        try:
            client = self._get_client()
            vm = client.virtual_machines.get(
                resource_group_name=self.resource_group,
                vm_name=self.vm_name,
                expand="instanceView",
            )
            statuses = vm.instance_view.statuses if vm.instance_view else []
            for s in statuses:
                if s.code and s.code.startswith("PowerState/running"):
                    return True, s.display_status or "VM running"

            status_str = ", ".join([s.display_status or s.code for s in statuses if s.code])
            return False, status_str or "VM not in running state"
        except Exception as e:
            logger.error("Failed to check Azure VM status for %s: %s", self.vm_name, e)
            return False, str(e)

    def execute_script(
        self,
        commands: List[str],
        comment: str = "ALLINA Azure Remediation",
        timeout_seconds: int = 120,
        dry_run: bool = False,
    ) -> SSMExecutionResult:
        """Execute bash commands remotely on the Azure VM via Run Command."""
        client = self._get_client()
        script_body = "\n".join(commands)

        try:
            poller = client.virtual_machines.begin_run_command(
                resource_group_name=self.resource_group,
                vm_name=self.vm_name,
                parameters=RunCommandInput(
                    command_id="RunShellScript",
                    script=[script_body],
                ),
            )
            result = poller.result()

            output_text = ""
            for val in result.value or []:
                if val.message:
                    output_text += val.message + "\n"

            # Parse stdout and stderr from Azure output format:
            # Enable succeeded: \n[stdout]\n...\n[stderr]\n...
            stdout = ""
            stderr = ""
            if "[stdout]" in output_text:
                parts = output_text.split("[stdout]", 1)
                if len(parts) > 1:
                    sub_parts = parts[1].split("[stderr]", 1)
                    stdout = sub_parts[0].strip()
                    if len(sub_parts) > 1:
                        stderr = sub_parts[1].strip()
            else:
                stdout = output_text.strip()

            exit_code = 0 if "Enable succeeded" in output_text else 1
            status = "Success" if exit_code == 0 else "Failed"

            return SSMExecutionResult(
                command_id=f"azure-run-{self.vm_name}",
                instance_id=self.vm_name,
                status=status,
                exit_code=exit_code,
                output_summary=stdout or output_text.strip(),
                dry_run=dry_run,
                executed_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error("Azure Run Command execution failed on %s: %s", self.vm_name, e)
            return SSMExecutionResult(
                command_id="azure-run-error",
                instance_id=self.vm_name,
                status="Failed",
                exit_code=1,
                output_summary=str(e),
                dry_run=dry_run,
                executed_at=datetime.now(timezone.utc),
            )

    def inspect_disk_and_files(
        self,
        target_dir: str,
        prefix: str,
        extension: str,
    ) -> Tuple[DiskMetrics, List[Dict[str, Any]]]:
        """Perform disk and file inspection in a single Azure Run Command execution to optimize latency."""
        script = f"""
echo '=== ALLINA_DISK_START ==='
df -Pk '{target_dir}'
echo '=== ALLINA_DISK_END ==='
echo '=== ALLINA_FILES_START ==='
find '{target_dir}' -maxdepth 1 -type f -name '{prefix}*{extension}' -printf '%p\\t%f\\t%s\\t%T@\\n' 2>/dev/null
echo '=== ALLINA_FILES_END ==='
"""
        res = self.execute_script(commands=[script], comment="ALLINA Combined Inspection")
        if res.status != "Success":
            raise AzureClientError(f"Failed to inspect Azure VM: {res.output_summary}")

        output = res.output_summary

        # 1. Parse Disk
        disk_section = ""
        if "=== ALLINA_DISK_START ===" in output and "=== ALLINA_DISK_END ===" in output:
            disk_section = output.split("=== ALLINA_DISK_START ===")[1].split("=== ALLINA_DISK_END ===")[0].strip()

        data_line = None
        for line in disk_section.splitlines():
            parts = line.split()
            if len(parts) >= 6 and parts[1].isdigit():
                data_line = parts
                break

        if not data_line:
            raise AzureClientError(f"Unexpected df output from Azure VM: {disk_section}")

        total_1k = int(data_line[1])
        used_1k = int(data_line[2])
        avail_1k = int(data_line[3])
        pct_str = data_line[4].rstrip("%")
        percent_used = float(pct_str)
        mount_point = data_line[5]

        metrics = DiskMetrics(
            mount_point=mount_point,
            total_bytes=total_1k * 1024,
            used_bytes=used_1k * 1024,
            available_bytes=avail_1k * 1024,
            percent_used=percent_used,
        )

        # 2. Parse Files
        files_section = ""
        if "=== ALLINA_FILES_START ===" in output and "=== ALLINA_FILES_END ===" in output:
            files_section = output.split("=== ALLINA_FILES_START ===")[1].split("=== ALLINA_FILES_END ===")[0].strip()

        candidates: List[Dict[str, Any]] = []
        for line in files_section.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue

            f_path, f_basename, f_size_str, f_mtime_str = parts[0], parts[1], parts[2], parts[3]
            try:
                size_bytes = int(f_size_str)
                mtime_epoch = float(f_mtime_str)
                mtime = datetime.fromtimestamp(mtime_epoch, tz=timezone.utc)
            except ValueError:
                continue

            candidates.append(
                {
                    "path": f_path,
                    "basename": f_basename,
                    "size_bytes": size_bytes,
                    "mtime": mtime,
                    "is_symlink": False,
                    "is_active": False,
                }
            )

        return metrics, candidates

    def inspect_disk(self, target_dir: str) -> DiskMetrics:
        """Inspect disk space on target directory via df -Pk."""
        res = self.execute_script(commands=[f"df -Pk '{target_dir}'"])
        if res.status != "Success":
            raise AzureClientError(f"Failed to inspect disk on Azure VM: {res.output_summary}")

        lines = [line.strip() for line in res.output_summary.strip().splitlines() if line.strip()]
        # df -P -k output line format: Filesystem 1024-blocks Used Available Capacity Mounted on
        # Find the row with data
        data_line = None
        for line in lines:
            parts = line.split()
            if len(parts) >= 6 and parts[1].isdigit():
                data_line = parts
                break

        if not data_line:
            raise AzureClientError(f"Unexpected df output format from Azure VM: {res.output_summary}")

        total_1k = int(data_line[1])
        used_1k = int(data_line[2])
        avail_1k = int(data_line[3])
        pct_str = data_line[4].rstrip("%")
        percent_used = float(pct_str)
        mount_point = data_line[5]

        return DiskMetrics(
            mount_point=mount_point,
            total_bytes=total_1k * 1024,
            used_bytes=used_1k * 1024,
            available_bytes=avail_1k * 1024,
            percent_used=percent_used,
        )

    def inspect_files(
        self,
        target_dir: str,
        prefix: str,
        extension: str,
        min_age_hours: int = 0,
    ) -> List[Dict[str, Any]]:
        """Inspect candidate log files on Azure VM via find and printf."""
        find_cmd = (
            f"find '{target_dir}' -maxdepth 1 -type f "
            f"-name '{prefix}*{extension}' "
            f"-printf '%p\\t%f\\t%s\\t%T@\\n' 2>/dev/null"
        )
        res = self.execute_script(commands=[find_cmd])
        if res.status != "Success":
            raise AzureClientError(f"Failed to scan files on Azure VM: {res.output_summary}")

        candidates: List[Dict[str, Any]] = []
        for line in res.output_summary.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue

            f_path, f_basename, f_size_str, f_mtime_str = parts[0], parts[1], parts[2], parts[3]
            try:
                size_bytes = int(f_size_str)
                mtime_epoch = float(f_mtime_str)
                mtime = datetime.fromtimestamp(mtime_epoch, tz=timezone.utc)
            except ValueError:
                continue

            candidates.append(
                {
                    "path": f_path,
                    "basename": f_basename,
                    "size_bytes": size_bytes,
                    "mtime": mtime,
                    "is_symlink": False,
                    "is_active": False,
                }
            )

        return candidates
