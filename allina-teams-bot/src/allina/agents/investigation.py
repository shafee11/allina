"""Investigation adapters for EC2 evidence collection."""

import logging
import os
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from src.allina.schemas.plans import DiskMetrics

logger = logging.getLogger(__name__)


class BaseInvestigationAdapter(ABC):
    """Abstract interface for gathering EC2 disk and file evidence."""

    @abstractmethod
    def inspect(
        self, instance_id: str, log_dir: str
    ) -> Tuple[DiskMetrics, List[Dict[str, Any]]]:
        """
        Inspect instance and log directory.
        Returns:
            Tuple of (DiskMetrics, list of raw file dictionaries)
        """
        pass


class AzureInvestigationAdapter(BaseInvestigationAdapter):
    """Investigation adapter targeting Azure Virtual Machines via Run Command."""

    def __init__(self, azure_client: Optional[Any] = None):
        from src.allina.execution.azure_client import AzureVMRunCommandClient
        self.azure_client = azure_client or AzureVMRunCommandClient()

    def inspect(
        self, instance_id: str, log_dir: str
    ) -> Tuple[DiskMetrics, List[Dict[str, Any]]]:
        from src.allina.config import settings
        is_online, status_str = self.azure_client.check_instance_online()
        if not is_online:
            raise RuntimeError(f"Target Azure VM is not in running state: {status_str}")

        return self.azure_client.inspect_disk_and_files(
            target_dir=log_dir,
            prefix=settings.allina_allowed_log_prefix,
            extension=settings.allina_allowed_log_extension,
        )



class LocalInvestigationAdapter(BaseInvestigationAdapter):
    """
    Adapter that inspects real files and disk metrics on the local filesystem.
    Enables local simulation and testing without cloud credentials.
    """

    def inspect(
        self, instance_id: str, log_dir: str
    ) -> Tuple[DiskMetrics, List[Dict[str, Any]]]:
        # Normalize path
        norm_dir = os.path.abspath(log_dir)
        if not os.path.exists(norm_dir):
            os.makedirs(norm_dir, exist_ok=True)

        # 1. Real disk usage on the local drive
        try:
            total, used, free = shutil.disk_usage(norm_dir)
            percent_used = round((used / total) * 100, 2)
            mount_point = os.path.splitdrive(norm_dir)[0] or norm_dir
        except Exception:
            total, used, free, percent_used, mount_point = (
                50 * 1024 * 1024 * 1024,
                40 * 1024 * 1024 * 1024,
                10 * 1024 * 1024 * 1024,
                80.0,
                norm_dir,
            )

        metrics = DiskMetrics(
            mount_point=mount_point,
            total_bytes=total,
            used_bytes=used,
            available_bytes=free,
            percent_used=percent_used,
        )

        # 2. Real directory scan
        raw_files: List[Dict[str, Any]] = []
        try:
            with os.scandir(norm_dir) as entries:
                for entry in entries:
                    if entry.is_file():
                        stat = entry.stat()
                        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                        raw_files.append(
                            {
                                "path": entry.path.replace("\\", "/"),
                                "basename": entry.name,
                                "size_bytes": stat.st_size,
                                "mtime": mtime,
                                "is_symlink": entry.is_symlink(),
                                "is_active": False,
                            }
                        )
        except Exception as e:
            logger.warning("Error scanning local directory %s: %s", norm_dir, e)

        return metrics, raw_files


class MockInvestigationAdapter(BaseInvestigationAdapter):
    """
    Mock adapter returning deterministic evidence fixtures.
    Enables thorough unit testing without AWS credentials.
    """

    def __init__(self, custom_files: List[Dict[str, Any]] = None, custom_metrics: DiskMetrics = None):
        self.custom_files = custom_files
        self.custom_metrics = custom_metrics

    def inspect(
        self, instance_id: str, log_dir: str
    ) -> Tuple[DiskMetrics, List[Dict[str, Any]]]:
        now = datetime.now(timezone.utc)

        # Default mock disk metrics (88% full disk)
        metrics = self.custom_metrics or DiskMetrics(
            mount_point="/",
            total_bytes=50 * 1024 * 1024 * 1024,      # 50 GB
            used_bytes=44 * 1024 * 1024 * 1024,       # 44 GB (88%)
            available_bytes=6 * 1024 * 1024 * 1024,   # 6 GB free
            percent_used=88.0,
        )

        if self.custom_files is not None:
            return metrics, self.custom_files

        # Realistic default test log file candidates
        files = [
            {
                "path": f"{log_dir}/allina-poc-service-test1.log",
                "basename": "allina-poc-service-test1.log",
                "size_bytes": 450 * 1024 * 1024,  # 450 MB
                "mtime": now - timedelta(hours=72),
                "is_symlink": False,
                "is_active": False,
            },
            {
                "path": f"{log_dir}/allina-poc-batch-run.log",
                "basename": "allina-poc-batch-run.log",
                "size_bytes": 820 * 1024 * 1024,  # 820 MB
                "mtime": now - timedelta(hours=48),
                "is_symlink": False,
                "is_active": False,
            },
            {
                "path": f"{log_dir}/allina-poc-stress-eval.log",
                "basename": "allina-poc-stress-eval.log",
                "size_bytes": 1200 * 1024 * 1024,  # 1.2 GB
                "mtime": now - timedelta(hours=36),
                "is_symlink": False,
                "is_active": False,
            },
            # Edge case 1: Active file (should be filtered out)
            {
                "path": f"{log_dir}/allina-poc-active-worker.log",
                "basename": "allina-poc-active-worker.log",
                "size_bytes": 200 * 1024 * 1024,
                "mtime": now - timedelta(hours=30),
                "is_symlink": False,
                "is_active": True,
            },
            # Edge case 2: Too new (should be filtered out: age < 24h)
            {
                "path": f"{log_dir}/allina-poc-fresh-test.log",
                "basename": "allina-poc-fresh-test.log",
                "size_bytes": 100 * 1024 * 1024,
                "mtime": now - timedelta(hours=2),
                "is_symlink": False,
                "is_active": False,
            },
            # Edge case 3: Symlink (should be filtered out)
            {
                "path": f"{log_dir}/allina-poc-symlink.log",
                "basename": "allina-poc-symlink.log",
                "size_bytes": 50 * 1024 * 1024,
                "mtime": now - timedelta(hours=50),
                "is_symlink": True,
                "is_active": False,
            },
            # Edge case 4: Non-POC file (should be filtered out)
            {
                "path": f"{log_dir}/application-prod.log",
                "basename": "application-prod.log",
                "size_bytes": 500 * 1024 * 1024,
                "mtime": now - timedelta(hours=100),
                "is_symlink": False,
                "is_active": False,
            },
        ]

        return metrics, files

