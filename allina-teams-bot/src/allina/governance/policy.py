"""Governance policy definitions and risk classification."""

from enum import Enum
from typing import List, Optional
from src.allina.config import settings


class ActionRiskTier(str, Enum):
    """POC Risk Tiers for IT Operations."""

    T0_INFORMATIONAL = "T0_INFORMATIONAL"      # Zero impact (e.g. status lookup)
    T1_READ_ONLY = "T1_READ_ONLY"              # Read-only inspection (auto-permitted)
    T2_CONTROLLED_CHANGE = "T2_CONTROLLED_CHANGE"  # Bounded destructive action (human approval required)
    T3_HIGH_RISK = "T3_HIGH_RISK"              # Unbounded / critical infra (blocked in POC)


ACTION_RISK_MAP = {
    "INSPECT_DISK": ActionRiskTier.T1_READ_ONLY,
    "DELETE_APPROVED_TEST_LOGS": ActionRiskTier.T2_CONTROLLED_CHANGE,
    "ARCHIVE_AND_DELETE": ActionRiskTier.T2_CONTROLLED_CHANGE,
}


class PolicyDecision:
    """Outcome of governance policy evaluation."""

    def __init__(
        self,
        allowed: bool,
        requires_human_approval: bool,
        risk_tier: ActionRiskTier,
        reason: str,
    ):
        self.allowed = allowed
        self.requires_human_approval = requires_human_approval
        self.risk_tier = risk_tier
        self.reason = reason


class GovernancePolicyEngine:
    """Evaluates operations against default-deny and authorization rules."""

    @classmethod
    def evaluate_action(cls, action_name: str) -> PolicyDecision:
        """Evaluate an operational action against the POC policy matrix."""
        risk_tier = ACTION_RISK_MAP.get(action_name)
        if risk_tier is None:
            return PolicyDecision(
                allowed=False,
                requires_human_approval=True,
                risk_tier=ActionRiskTier.T3_HIGH_RISK,
                reason=f"Default-deny: Unrecognized action '{action_name}'.",
            )

        if risk_tier == ActionRiskTier.T1_READ_ONLY:
            return PolicyDecision(
                allowed=True,
                requires_human_approval=False,
                risk_tier=risk_tier,
                reason="Read-only operations are pre-authorized under Tier 1 policy.",
            )

        if risk_tier == ActionRiskTier.T2_CONTROLLED_CHANGE:
            return PolicyDecision(
                allowed=True,
                requires_human_approval=True,
                risk_tier=risk_tier,
                reason="Destructive operations require explicit authorization from an independent approver.",
            )

        return PolicyDecision(
            allowed=False,
            requires_human_approval=True,
            risk_tier=risk_tier,
            reason="Action exceeds permitted risk ceiling for this POC.",
        )

    @classmethod
    def is_approver_authorized(cls, approver_aad_id: str, allowlist: Optional[List[str]] = None) -> bool:
        """Validate if an approver Entra ID is in the allowlist."""
        allowed_ids = allowlist if allowlist is not None else settings.allina_approver_aad_ids
        if not allowed_ids:
            # If allowlist is empty in configuration, fail closed
            return False
        return approver_aad_id in allowed_ids

