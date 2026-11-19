"""ALLINA configuration and validated settings."""

from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated settings for the ALLINA POC."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Teams / Bot App Registration
    client_id: Optional[str] = Field(default=None, description="Microsoft App Client ID")
    client_secret: Optional[str] = Field(default=None, description="Microsoft App Client Secret")
    tenant_id: Optional[str] = Field(default=None, description="Microsoft 365 Tenant ID")

    # AWS Configuration
    aws_profile: Optional[str] = Field(default=None, description="AWS named profile")
    aws_region: str = Field(default="us-east-1", description="AWS Region")

    # Azure VM Configuration
    azure_tenant_id: Optional[str] = Field(default=None, description="Azure Tenant ID for VM management")
    azure_client_id: Optional[str] = Field(default=None, description="Azure Client ID / App ID for VM management")
    azure_client_secret: Optional[str] = Field(default=None, description="Azure Client Secret for VM management")
    azure_subscription_id: Optional[str] = Field(default=None, description="Azure Subscription ID")
    azure_resource_group: Optional[str] = Field(default=None, description="Azure Resource Group Name")
    azure_vm_name: Optional[str] = Field(default=None, description="Target Azure Virtual Machine Name")

    # ALLINA Execution Mode & Boundaries
    allina_target_mode: str = Field(
        default="mock",
        description="Target environment: 'local', 'mock', 'aws', or 'azure'",
    )
    allina_ec2_instance_id: str = Field(
        default="i-placeholder",
        description="Single allowlisted EC2 instance ID for POC",
    )
    allina_allowed_log_dir: str = Field(
        default="/opt/allina/test-logs",
        description="Single allowlisted directory for log cleanup",
    )
    allina_allowed_log_prefix: str = Field(
        default="allina-poc-",
        description="Allowed file prefix (e.g. allina-poc-*.log)",
    )
    allina_allowed_log_extension: str = Field(
        default=".log",
        description="Allowed file extension",
    )
    allina_min_file_age_hours: int = Field(
        default=24,
        description="Minimum file age in hours before eligible for cleanup",
    )
    allina_max_cleanup_files: int = Field(
        default=50,
        description="Safety limit on maximum number of files deleted per run",
    )
    allina_max_cleanup_bytes: int = Field(
        default=10 * 1024 * 1024 * 1024,
        description="Safety limit on total bytes cleaned per run (default 10 GB)",
    )

    # Governance & Identities
    allina_tenant_id: Optional[str] = Field(
        default=None,
        description="Allowlisted Microsoft Tenant ID for ingress guard",
    )
    allina_approver_aad_ids: List[str] = Field(
        default_factory=list,
        description="Allowlisted Entra (Azure AD) Object IDs for approvers",
    )
    allina_allow_self_approval_for_demo: bool = Field(
        default=False,
        description="When true, allows requester to approve their own request for solo demo/testing while keeping it flexible for others.",
    )

    # Fail-Closed Safeguards
    allina_dry_run: bool = Field(
        default=True,
        description="When true, simulate remediation without modifying target instance",
    )
    allina_enable_real_execution: bool = Field(
        default=False,
        description="Master switch: live destructive execution requires true",
    )
    allina_backup_confirmed: bool = Field(
        default=False,
        description="Requires true and recorded backup reference before live deletion",
    )

    # Persistence
    allina_db_path: str = Field(
        default="data/allina_poc.db",
        description="Path to SQLite persistence database",
    )
    allina_request_expiry_minutes: int = Field(
        default=30,
        description="TTL for pending remediation plans",
    )

    @field_validator("allina_approver_aad_ids", mode="before")
    @classmethod
    def parse_approver_ids(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(item).strip() for item in v if str(item).strip()]
        return []

    @property
    def is_live_execution_permitted(self) -> bool:
        """Evaluate all fail-closed safety switches."""
        return (
            not self.allina_dry_run
            and self.allina_enable_real_execution
            and self.allina_backup_confirmed
        )


# Global singleton instance loaded once
settings = Settings()

