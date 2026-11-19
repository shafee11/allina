"""Tamper-evident audit logging engine with SHA-256 hash chaining.

DISCLAIMER:
This is a proof-of-concept tamper-evident application-level audit ledger.
It provides cryptographic integrity checking against database tampering,
but is not certified enterprise WORM (Write Once Read Many) compliant storage.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
from src.allina.persistence.database import DatabaseManager

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64
SENSITIVE_KEY_PATTERN = re.compile(r"(secret|key|token|password|credential|auth)", re.IGNORECASE)


def sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively mask sensitive values to prevent credential leakage in audit logs."""
    clean: Dict[str, Any] = {}
    for k, v in payload.items():
        if SENSITIVE_KEY_PATTERN.search(k):
            clean[k] = "[REDACTED_SECRET]"
        elif isinstance(v, dict):
            clean[k] = sanitize_payload(v)
        elif isinstance(v, list):
            clean[k] = [sanitize_payload(item) if isinstance(item, dict) else item for item in v]
        elif isinstance(v, datetime):
            clean[k] = v.isoformat()
        else:
            clean[k] = v
    return clean


def canonical_json(data: Dict[str, Any]) -> str:
    """Produce deterministic, sorted JSON string."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


class AuditLogger:
    """Manages append-only audit event records with SHA-256 hash chaining."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def record_event(
        self,
        request_id: str,
        actor_aad_id: str,
        event_type: str,
        payload: Dict[str, Any],
        timestamp: Optional[datetime] = None,
    ) -> str:
        """
        Record a new audit event, cryptographically linking to the previous event hash.
        Returns the computed event_hash.
        """
        now = timestamp or datetime.now(timezone.utc)
        created_at_iso = now.isoformat()
        event_id = f"evt-{uuid4().hex[:12]}"

        clean_payload = sanitize_payload(payload)
        payload_str = canonical_json(clean_payload)

        with self.db.get_connection() as conn:
            # 1. Fetch the latest event hash in the chain
            row = conn.execute(
                "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = row["event_hash"] if row else GENESIS_HASH

            # 2. Compute canonical event hash
            hash_input = (
                f"{prev_hash}|{event_id}|{request_id}|{actor_aad_id}|{event_type}|{payload_str}|{created_at_iso}"
            )
            event_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

            # 3. Append to audit log
            sql = """
            INSERT INTO audit_events (
                event_id, request_id, actor_aad_id, event_type,
                payload_json, prev_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            conn.execute(
                sql,
                (
                    event_id,
                    request_id,
                    actor_aad_id,
                    event_type,
                    payload_str,
                    prev_hash,
                    event_hash,
                    created_at_iso,
                ),
            )
            conn.commit()

        logger.info(
            "Audit event recorded [%s]: %s (request: %s, hash: %s...)",
            event_id,
            event_type,
            request_id,
            event_hash[:8],
        )
        return event_hash

    def verify_chain_integrity(self) -> Tuple[bool, Optional[str]]:
        """
        Validate the entire cryptographic hash chain.
        Returns: (is_valid, error_detail_if_invalid)
        """
        sql = "SELECT * FROM audit_events ORDER BY id ASC"
        with self.db.get_connection() as conn:
            rows = conn.execute(sql).fetchall()

        if not rows:
            return True, None

        expected_prev_hash = GENESIS_HASH

        for row in rows:
            actual_prev_hash = row["prev_hash"]
            if actual_prev_hash != expected_prev_hash:
                return (
                    False,
                    f"Chain break at record #{row['id']} (event_id: {row['event_id']}): "
                    f"prev_hash mismatch (expected {expected_prev_hash[:12]}..., got {actual_prev_hash[:12]}...).",
                )

            # Recompute event hash
            hash_input = (
                f"{actual_prev_hash}|{row['event_id']}|{row['request_id']}|"
                f"{row['actor_aad_id']}|{row['event_type']}|{row['payload_json']}|{row['created_at']}"
            )
            recomputed_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

            if recomputed_hash != row["event_hash"]:
                return (
                    False,
                    f"Tamper detected at record #{row['id']} (event_id: {row['event_id']}): "
                    f"event_hash mismatch (stored {row['event_hash'][:12]}..., recomputed {recomputed_hash[:12]}...).",
                )

            expected_prev_hash = row["event_hash"]

        return True, None

    def get_events_for_request(self, request_id: str) -> List[Dict[str, Any]]:
        """Retrieve audit history for a specific request."""
        sql = "SELECT * FROM audit_events WHERE request_id = ? ORDER BY id ASC"
        with self.db.get_connection() as conn:
            rows = conn.execute(sql, (request_id,)).fetchall()
            return [dict(r) for r in rows]

