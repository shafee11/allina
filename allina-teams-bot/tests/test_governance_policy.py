from src.allina.governance.policy import (
    ActionRiskTier,
    GovernancePolicyEngine,
)


def test_governance_policy_risk_tiers():
    # 1. Read-only inspection is pre-authorized
    decision = GovernancePolicyEngine.evaluate_action("INSPECT_DISK")
    assert decision.allowed is True
    assert decision.requires_human_approval is False
    assert decision.risk_tier == ActionRiskTier.T1_READ_ONLY

    # 2. Destructive cleanup requires human approval
    decision = GovernancePolicyEngine.evaluate_action("DELETE_APPROVED_TEST_LOGS")
    assert decision.allowed is True
    assert decision.requires_human_approval is True
    assert decision.risk_tier == ActionRiskTier.T2_CONTROLLED_CHANGE

    # 3. Default-deny on unknown action
    decision = GovernancePolicyEngine.evaluate_action("UNKNOWN_DANGEROUS_ACTION")
    assert decision.allowed is False
    assert "Default-deny" in decision.reason


def test_governance_policy_approver_allowlist():
    allowlist = ["approver-1", "approver-2"]

    assert GovernancePolicyEngine.is_approver_authorized("approver-1", allowlist) is True
    assert GovernancePolicyEngine.is_approver_authorized("intruder-9", allowlist) is False
    assert GovernancePolicyEngine.is_approver_authorized("approver-1", allowlist=[]) is False

