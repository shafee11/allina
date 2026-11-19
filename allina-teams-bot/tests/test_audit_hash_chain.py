from src.allina.audit.logger import AuditLogger, sanitize_payload
from src.allina.persistence.database import DatabaseManager


def test_audit_hash_chain_recording_and_verification():
    db = DatabaseManager(":memory:")
    logger = AuditLogger(db)

    # Record series of events
    h1 = logger.record_event("req-1", "user-1", "REQUEST_INTAKE", {"action": "clean"})
    h2 = logger.record_event("req-1", "SYSTEM_A7", "PLAN_GENERATED", {"files": 3})
    h3 = logger.record_event("req-1", "approver-bob", "APPROVAL_GRANTED", {"approved": True})

    assert len(h1) == 64
    assert len(h2) == 64
    assert len(h3) == 64
    assert h1 != h2 != h3

    # Verify entire chain is intact
    is_valid, err = logger.verify_chain_integrity()
    assert is_valid is True
    assert err is None


def test_audit_hash_chain_detects_tampered_payload():
    db = DatabaseManager(":memory:")
    logger = AuditLogger(db)

    logger.record_event("req-1", "user-1", "REQUEST_INTAKE", {"amount": 100})
    logger.record_event("req-1", "approver-bob", "APPROVAL_GRANTED", {"approved": True})

    # Intentionally tamper with record #1 payload in SQLite
    with db.get_connection() as conn:
        conn.execute("UPDATE audit_events SET payload_json = '{\"amount\":99999}' WHERE id = 1")
        conn.commit()

    is_valid, err = logger.verify_chain_integrity()
    assert is_valid is False
    assert "Tamper detected at record #1" in err


def test_audit_hash_chain_detects_deleted_record():
    db = DatabaseManager(":memory:")
    logger = AuditLogger(db)

    logger.record_event("req-1", "user-1", "EVENT_1", {"step": 1})
    logger.record_event("req-1", "user-1", "EVENT_2", {"step": 2})
    logger.record_event("req-1", "user-1", "EVENT_3", {"step": 3})

    # Delete record #2
    with db.get_connection() as conn:
        conn.execute("DELETE FROM audit_events WHERE id = 2")
        conn.commit()

    is_valid, err = logger.verify_chain_integrity()
    assert is_valid is False
    assert "Chain break at record #3" in err


def test_audit_logger_masks_secrets():
    payload = {
        "user": "Alice",
        "api_key": "secret-token-value-12345",
        "nested": {
            "client_secret": "my-app-password",
            "safe_param": "hello",
        },
    }
    clean = sanitize_payload(payload)

    assert clean["api_key"] == "[REDACTED_SECRET]"
    assert clean["nested"]["client_secret"] == "[REDACTED_SECRET]"
    assert clean["nested"]["safe_param"] == "hello"
    assert clean["user"] == "Alice"

