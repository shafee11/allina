"""Proactive messaging helper with safe fallback to conversational pull."""

import logging
from typing import List, Optional
from microsoft_teams.apps import App
from src.allina.persistence.repositories import ConversationRepository
from src.allina.schemas.plans import RemediationPlan

logger = logging.getLogger(__name__)


async def notify_approvers_of_pending_plan(
    app: App,
    approver_aad_ids: List[str],
    plan: RemediationPlan,
    conv_repo: ConversationRepository,
) -> int:
    """
    Attempt proactive notification to authorized approvers if conversation context exists.
    Returns the count of successfully sent proactive messages.
    """
    if not approver_aad_ids:
        return 0

    sent_count = 0
    notification_text = (
        f"🔔 **Action Required: ALLINA Remediation Approval Request**\n\n"
        f"- **Request ID:** `{plan.request_id}`\n"
        f"- **Target Instance:** `{plan.target_instance_id}`\n"
        f"- **Target Directory:** `{plan.target_dir}`\n"
        f"- **Eligible Files:** {len(plan.eligible_files)}\n"
        f"- **Estimated Recovery:** `{plan.estimated_recovery_bytes / (1024*1024):.2f} MB`\n"
        f"- **Plan Hash:** `{plan.plan_hash[:16]}...`\n\n"
        f"Reply: `APPROVE {plan.request_id}` or `REJECT {plan.request_id}`"
    )

    for aad_id in approver_aad_ids:
        conv_ref = conv_repo.get_conversation(aad_id)
        if not conv_ref:
            logger.info("No stored conversation reference for approver %s; relying on pull flow.", aad_id)
            continue

        conv_id = conv_ref.get("conversation_id")
        service_url = conv_ref.get("service_url")
        if not conv_id:
            continue

        try:
            # microsoft-teams-apps App.send(conversation_id, activity, service_url=...)
            await app.send(conv_id, notification_text, service_url=service_url)
            sent_count += 1
            logger.info("Successfully sent proactive notification to approver %s.", aad_id)
        except Exception as e:
            logger.warning(
                "Proactive message delivery failed for approver %s (%s). Falling back to pull command (PENDING).",
                aad_id,
                e,
            )

    return sent_count

