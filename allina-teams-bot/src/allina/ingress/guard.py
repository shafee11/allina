"""Ingress Guard for validating and sanitizing inbound Teams messages."""

import re
from typing import Optional, Tuple
from microsoft_teams.api import MessageActivity
from src.allina.config import settings
from src.allina.schemas.requests import IncomingMessage


MAX_MESSAGE_LENGTH = 2000
CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


class IngressGuardError(Exception):
    """Raised when an inbound message fails ingress policy checks."""
    pass


class IngressGuard:
    """Validates activity context, tenant boundaries, and message constraints."""

    def __init__(self, target_tenant_id: Optional[str] = None):
        self.target_tenant_id = target_tenant_id or settings.allina_tenant_id

    def validate_activity(
        self, activity: MessageActivity
    ) -> Tuple[bool, Optional[str], Optional[IncomingMessage]]:
        """
        Validate activity context.
        Returns: (is_valid, rejection_reason, incoming_message)
        """
        # 1. Text presence and basic size check
        raw_text = (activity.text or "").strip()
        if not raw_text:
            return False, "Message text cannot be empty.", None

        if len(raw_text) > MAX_MESSAGE_LENGTH:
            return (
                False,
                f"Message length ({len(raw_text)}) exceeds maximum permitted ({MAX_MESSAGE_LENGTH}).",
                None,
            )

        # 2. Sanitize control characters
        sanitized_text = CONTROL_CHARS_PATTERN.sub("", raw_text)

        # 3. Extract authenticated sender identity
        sender_prop = getattr(activity, "from_", None)
        if not sender_prop:
            return False, "Unable to extract authenticated sender from activity context.", None

        sender_aad_id = getattr(sender_prop, "aad_object_id", None) or getattr(
            sender_prop, "id", None
        )
        if not sender_aad_id:
            return False, "Sender lacks a verifiable Entra / Teams identifier.", None

        sender_name = getattr(sender_prop, "name", "Unknown User") or "Unknown User"

        # 4. Extract conversation and tenant context
        conversation = getattr(activity, "conversation", None)
        conversation_id = getattr(conversation, "id", "") if conversation else ""
        if not conversation_id:
            return False, "Activity lacks a valid conversation ID.", None

        tenant_id = getattr(conversation, "tenant_id", None) or getattr(
            sender_prop, "tenant_id", None
        )

        # 5. Strict Tenant Boundary Check (if configured)
        if self.target_tenant_id and self.target_tenant_id.strip():
            if tenant_id != self.target_tenant_id.strip():
                return (
                    False,
                    f"Tenant validation failed. Unauthorized tenant '{tenant_id}'.",
                    None,
                )

        service_url = getattr(activity, "service_url", None)

        incoming = IncomingMessage(
            raw_text=sanitized_text,
            sender_aad_id=sender_aad_id,
            sender_name=sender_name,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            service_url=str(service_url) if service_url else None,
        )

        return True, None, incoming

