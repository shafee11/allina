from src.allina.knowledge.runbook_parser import load_approved_runbook


def test_load_approved_runbook():
    policy = load_approved_runbook()
    assert policy.runbook_id == "RB-A7-EC2-DISK-CLEANUP-001"
    assert policy.allowed_prefix == "allina-poc-"
    assert policy.allowed_extension == ".log"
    assert policy.min_file_age_hours == 24
    assert "Mandatory Boundary Constraints" in policy.raw_markdown

