from unittest.mock import patch
from src.allina.config import Settings


def test_settings_fail_closed_defaults():
    with patch.dict("os.environ", {}, clear=True):
        s = Settings(_env_file=None)
        assert s.allina_dry_run is True
        assert s.allina_enable_real_execution is False
        assert s.allina_backup_confirmed is False
        assert s.is_live_execution_permitted is False


def test_approver_ids_parsing():
    s = Settings(allina_approver_aad_ids="id-1, id-2 ,id-3")
    assert s.allina_approver_aad_ids == ["id-1", "id-2", "id-3"]

    s_empty = Settings(allina_approver_aad_ids="")
    assert s_empty.allina_approver_aad_ids == []

