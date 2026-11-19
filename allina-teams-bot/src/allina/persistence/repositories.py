"""Data access repositories for ALLINA entities."""

import json
from datetime import datetime, timezone
from typing import List, Optional
from src.allina.persistence.database import DatabaseManager
from src.allina.schemas.approvals import ApprovalDecisionType, ApprovalRecord
from src.allina.schemas.plans import RemediationPlan
from src.allina.schemas.requests import RemediationRequest, RequestStatus


class RequestRepository:
    """Repository for managing RemediationRequest lifecycle with atomic transitions."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def create_request(self, request: RemediationRequest) -> RemediationRequest:
        """Insert a newly received remediation request."""
        sql = """
        INSERT INTO requests (
            request_id, tenant_id, requester_aad_id, requester_name,
            original_message, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self.db.get_connection() as conn:
            conn.execute(
                sql,
                (
                    request.request_id,
                    request.tenant_id,
                    request.requester_aad_id,
                    request.requester_name,
                    request.original_message,
                    request.status.value,
                    request.created_at.isoformat(),
                    request.updated_at.isoformat(),
                ),
            )
            conn.commit()
        return request

    def get_request(self, request_id: str) -> Optional[RemediationRequest]:
        """Retrieve a request by its unique identifier."""
        sql = "SELECT * FROM requests WHERE request_id = ?"
        with self.db.get_connection() as conn:
            row = conn.execute(sql, (request_id,)).fetchone()
            if not row:
                return None
            return RemediationRequest(
                request_id=row["request_id"],
                tenant_id=row["tenant_id"],
                requester_aad_id=row["requester_aad_id"],
                requester_name=row["requester_name"],
                original_message=row["original_message"],
                status=RequestStatus(row["status"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def update_status(
        self,
        request_id: str,
        new_status: RequestStatus,
        expected_status: Optional[RequestStatus] = None,
    ) -> bool:
        """Atomically update status. If expected_status is given, performs CAS."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            if expected_status is not None:
                sql = """
                UPDATE requests
                SET status = ?, updated_at = ?
                WHERE request_id = ? AND status = ?
                """
                cursor = conn.execute(
                    sql, (new_status.value, now_iso, request_id, expected_status.value)
                )
            else:
                sql = """
                UPDATE requests
                SET status = ?, updated_at = ?
                WHERE request_id = ?
                """
                cursor = conn.execute(sql, (new_status.value, now_iso, request_id))
            conn.commit()
            return cursor.rowcount > 0

    def list_pending_requests(self) -> List[RemediationRequest]:
        """List all requests currently awaiting approval."""
        sql = "SELECT * FROM requests WHERE status = ? ORDER BY created_at ASC"
        with self.db.get_connection() as conn:
            rows = conn.execute(sql, (RequestStatus.AWAITING_APPROVAL.value,)).fetchall()
            return [
                RemediationRequest(
                    request_id=row["request_id"],
                    tenant_id=row["tenant_id"],
                    requester_aad_id=row["requester_aad_id"],
                    requester_name=row["requester_name"],
                    original_message=row["original_message"],
                    status=RequestStatus(row["status"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
                for row in rows
            ]


class PlanRepository:
    """Repository for persisting and retrieving structured remediation plans."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def save_plan(self, plan: RemediationPlan):
        """Insert or replace a plan for a request."""
        sql = """
        INSERT OR REPLACE INTO plans (
            request_id, target_instance_id, target_dir, plan_json,
            plan_hash, estimated_recovery_bytes, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self.db.get_connection() as conn:
            conn.execute(
                sql,
                (
                    plan.request_id,
                    plan.target_instance_id,
                    plan.target_dir,
                    plan.model_dump_json(),
                    plan.plan_hash,
                    plan.estimated_recovery_bytes,
                    plan.expires_at.isoformat(),
                    plan.created_at.isoformat(),
                ),
            )
            conn.commit()

    def get_plan(self, request_id: str) -> Optional[RemediationPlan]:
        """Retrieve a stored plan by request ID."""
        sql = "SELECT plan_json FROM plans WHERE request_id = ?"
        with self.db.get_connection() as conn:
            row = conn.execute(sql, (request_id,)).fetchone()
            if not row:
                return None
            return RemediationPlan.model_validate_json(row["plan_json"])


class ConversationRepository:
    """Repository for storing conversation contexts for proactive notifications."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def save_conversation(
        self,
        user_aad_id: str,
        conversation_id: str,
        service_url: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ):
        """Save or update conversation references for a user."""
        sql = """
        INSERT OR REPLACE INTO conversations (
            user_aad_id, conversation_id, service_url, tenant_id, last_seen_at
        ) VALUES (?, ?, ?, ?, ?)
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(sql, (user_aad_id, conversation_id, service_url, tenant_id, now_iso))
            conn.commit()

    def get_conversation(self, user_aad_id: str) -> Optional[dict]:
        """Fetch conversation reference by Entra Object ID."""
        sql = "SELECT * FROM conversations WHERE user_aad_id = ?"
        with self.db.get_connection() as conn:
            row = conn.execute(sql, (user_aad_id,)).fetchone()
            if not row:
                return None
            return dict(row)


class ApprovalRepository:
    """Repository for persisting human approval decisions."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def record_decision(self, record: ApprovalRecord) -> None:
        """Insert approval record."""
        sql = """
        INSERT INTO approvals (
            request_id, approver_aad_id, approver_name, decision,
            plan_hash, reason, decided_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        with self.db.get_connection() as conn:
            conn.execute(
                sql,
                (
                    record.request_id,
                    record.approver_aad_id,
                    record.approver_name,
                    record.decision.value,
                    record.plan_hash,
                    record.reason,
                    record.decided_at.isoformat(),
                ),
            )
            conn.commit()

    def get_decision(self, request_id: str) -> Optional[ApprovalRecord]:
        """Fetch latest approval decision for a request."""
        sql = "SELECT * FROM approvals WHERE request_id = ? ORDER BY decided_at DESC LIMIT 1"
        with self.db.get_connection() as conn:
            row = conn.execute(sql, (request_id,)).fetchone()
            if not row:
                return None
            return ApprovalRecord(
                request_id=row["request_id"],
                approver_aad_id=row["approver_aad_id"],
                approver_name=row["approver_name"],
                decision=ApprovalDecisionType(row["decision"]),
                plan_hash=row["plan_hash"],
                reason=row["reason"],
                decided_at=datetime.fromisoformat(row["decided_at"]),
            )


class ExecutionRepository:
    """Repository for recording SSM command invocations and verification metrics."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def record_execution(
        self,
        request_id: str,
        command_id: str,
        status: str,
        dry_run: bool,
        output_summary: str,
        before_metrics: Optional[str] = None,
        after_metrics: Optional[str] = None,
    ) -> None:
        """Insert execution history record."""
        sql = """
        INSERT INTO executions (
            request_id, command_id, status, dry_run, output_summary,
            before_metrics_json, after_metrics_json, executed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                sql,
                (
                    request_id,
                    command_id,
                    status,
                    1 if dry_run else 0,
                    output_summary,
                    before_metrics,
                    after_metrics,
                    now_iso,
                ),
            )
            conn.commit()

    def get_execution(self, request_id: str) -> Optional[dict]:
        """Fetch execution record for a request."""
        sql = "SELECT * FROM executions WHERE request_id = ? ORDER BY executed_at DESC LIMIT 1"
        with self.db.get_connection() as conn:
            row = conn.execute(sql, (request_id,)).fetchone()
            if not row:
                return None
            return dict(row)

