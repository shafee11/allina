import pytest
from unittest.mock import patch
from src.allina.config import settings


@pytest.fixture(autouse=True)
def isolate_settings():
    """Ensure tests run with fail-closed enterprise defaults regardless of local .env values."""
    with patch.object(settings, "allina_allow_self_approval_for_demo", False), patch.object(
        settings, "allina_target_mode", "mock"
    ), patch.object(settings, "allina_dry_run", True):
        yield

