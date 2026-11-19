"""Command router for classifying inbound Teams messages."""

import re
from src.allina.schemas.approvals import ParsedCommand


APPROVE_PATTERN = re.compile(r"^APPROVE\s+([a-zA-Z0-9_\-]+)$", re.IGNORECASE)
REJECT_PATTERN = re.compile(r"^REJECT\s+([a-zA-Z0-9_\-]+)$", re.IGNORECASE)
STATUS_PATTERN = re.compile(r"^STATUS\s+([a-zA-Z0-9_\-]+)$", re.IGNORECASE)
PENDING_PATTERN = re.compile(r"^PENDING$", re.IGNORECASE)
GREETING_PATTERN = re.compile(r"^(hello|hi|hey|greetings)\b", re.IGNORECASE)

REMEDIATION_KEYWORDS = [
    "disk space",
    "disk-space",
    "clean up",
    "cleanup",
    "test log",
    "test logs",
    "test-log",
    "ec2",
    "remediate",
    "remediation",
]


class CommandRouter:
    """Classifies user messages into typed commands or workflow triggers."""

    @classmethod
    def parse(cls, text: str) -> ParsedCommand:
        """Parse clean message text into a ParsedCommand."""
        clean = text.strip()

        # 1. Approval Commands
        if PENDING_PATTERN.match(clean):
            return ParsedCommand(command_type="PENDING", raw_text=clean)

        approve_match = APPROVE_PATTERN.match(clean)
        if approve_match:
            return ParsedCommand(
                command_type="APPROVE",
                request_id=approve_match.group(1),
                raw_text=clean,
            )

        reject_match = REJECT_PATTERN.match(clean)
        if reject_match:
            return ParsedCommand(
                command_type="REJECT",
                request_id=reject_match.group(1),
                raw_text=clean,
            )

        status_match = STATUS_PATTERN.match(clean)
        if status_match:
            return ParsedCommand(
                command_type="STATUS",
                request_id=status_match.group(1),
                raw_text=clean,
            )

        # 2. Greeting
        if GREETING_PATTERN.match(clean):
            return ParsedCommand(command_type="GREETING", raw_text=clean)

        # 3. Remediation Trigger
        lower_text = clean.lower()
        if any(keyword in lower_text for keyword in REMEDIATION_KEYWORDS):
            return ParsedCommand(command_type="REMEDIATE", raw_text=clean)

        # 4. Fallback / Echo Bot preservation
        return ParsedCommand(command_type="ECHO", raw_text=clean)

