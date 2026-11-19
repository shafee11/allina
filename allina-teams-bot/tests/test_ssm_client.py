from unittest.mock import MagicMock
from src.allina.execution.ssm_client import Boto3SSMClient


def test_ssm_preflight_checks():
    mock_ssm = MagicMock()

    # 1. Invalid placeholder instance ID fails immediately
    client_bad = Boto3SSMClient(instance_id="i-placeholder", ssm_client=mock_ssm)
    is_ready, msg = client_bad.preflight_check()
    assert is_ready is False
    assert "Invalid or placeholder" in msg

    # 2. Instance not registered with SSM fails
    mock_ssm.describe_instance_information.return_value = {"InstanceInformationList": []}
    client = Boto3SSMClient(instance_id="i-0123456789abcdef0", ssm_client=mock_ssm)
    is_ready, msg = client.preflight_check()
    assert is_ready is False
    assert "not registered with Systems Manager" in msg

    # 3. Instance offline fails
    mock_ssm.describe_instance_information.return_value = {
        "InstanceInformationList": [{"PingStatus": "ConnectionLost"}]
    }
    is_ready, msg = client.preflight_check()
    assert is_ready is False
    assert "not online in SSM" in msg

    # 4. Instance online succeeds
    mock_ssm.describe_instance_information.return_value = {
        "InstanceInformationList": [{"PingStatus": "Online"}]
    }
    is_ready, msg = client.preflight_check()
    assert is_ready is True
    assert "online and managed by SSM" in msg


def test_ssm_execute_script_and_poll():
    mock_ssm = MagicMock()
    mock_ssm.send_command.return_value = {"Command": {"CommandId": "cmd-test-123"}}
    mock_ssm.get_command_invocation.return_value = {
        "Status": "Success",
        "ResponseCode": 0,
        "StandardOutputContent": "Script completed successfully",
        "StandardErrorContent": "",
    }

    client = Boto3SSMClient(instance_id="i-0123456789abcdef0", ssm_client=mock_ssm)
    res = client.execute_script(["echo hello"], comment="Test Command", dry_run=True)

    assert res.command_id == "cmd-test-123"
    assert res.status == "Success"
    assert res.exit_code == 0
    assert "Script completed successfully" in res.output_summary


def test_ssm_inspect_disk_parsing():
    mock_ssm = MagicMock()
    mock_ssm.send_command.return_value = {"Command": {"CommandId": "cmd-df-1"}}
    # Typical df -P -k output:
    # Filesystem 1024-blocks Used Available Capacity Mounted on
    # /dev/xvda1   52428800 46000000   6428800      88% /
    mock_ssm.get_command_invocation.return_value = {
        "Status": "Success",
        "ResponseCode": 0,
        "StandardOutputContent": "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/xvda1 52428800 46000000 6428800 88% /\n",
    }

    client = Boto3SSMClient(instance_id="i-0123456789abcdef0", ssm_client=mock_ssm)
    metrics = client.inspect_disk("/opt/allina/test-logs")

    assert metrics.mount_point == "/"
    assert metrics.total_bytes == 52428800 * 1024
    assert metrics.percent_used == 88.0
    assert metrics.available_bytes == 6428800 * 1024

