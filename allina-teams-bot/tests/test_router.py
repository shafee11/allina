from src.allina.ingress.router import CommandRouter


def test_command_router_approval_commands():
    # PENDING
    cmd = CommandRouter.parse("PENDING")
    assert cmd.command_type == "PENDING"

    cmd = CommandRouter.parse("  pending  ")
    assert cmd.command_type == "PENDING"

    # APPROVE
    cmd = CommandRouter.parse("APPROVE req-12345")
    assert cmd.command_type == "APPROVE"
    assert cmd.request_id == "req-12345"

    # REJECT
    cmd = CommandRouter.parse("reject req-abc-def")
    assert cmd.command_type == "REJECT"
    assert cmd.request_id == "req-abc-def"

    # STATUS
    cmd = CommandRouter.parse("STATUS req-999")
    assert cmd.command_type == "STATUS"
    assert cmd.request_id == "req-999"


def test_command_router_remediation_intent():
    phrases = [
        "Our EC2 test server is running out of disk space. Please investigate and safely clean up eligible old test log files.",
        "Please clean up ec2 logs",
        "Investigate disk space on server",
        "We need test log remediation",
    ]
    for p in phrases:
        cmd = CommandRouter.parse(p)
        assert cmd.command_type == "REMEDIATE", f"Failed for phrase: {p}"


def test_command_router_greeting_and_echo():
    # Greeting
    cmd = CommandRouter.parse("Hello there!")
    assert cmd.command_type == "GREETING"

    cmd = CommandRouter.parse("Hi bot")
    assert cmd.command_type == "GREETING"

    # Fallback Echo
    cmd = CommandRouter.parse("What is the weather in Seattle?")
    assert cmd.command_type == "ECHO"

