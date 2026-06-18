"""Tests for the command whitelist feature."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from soft_ue_cli.whitelist import is_command_allowed
from soft_ue_cli.__main__ import main
from soft_ue_cli.mcp_server import create_server


def test_whitelist_is_command_allowed():
    # Allowed whitelisted commands
    assert is_command_allowed("run-python-script") is True
    assert is_command_allowed("trigger-live-coding") is True
    assert is_command_allowed("capture-screenshot") is True
    assert is_command_allowed("capture-viewport") is True
    assert is_command_allowed("get-console-var") is True
    assert is_command_allowed("set-console-var") is True
    assert is_command_allowed("exec-console-command") is True
    assert is_command_allowed("run-automation") is True
    assert is_command_allowed("shutdown") is True
    assert is_command_allowed("build") is True
    assert is_command_allowed("build-start") is True
    assert is_command_allowed("shutdown-build-restart") is True

    # Allowed infrastructure commands
    assert is_command_allowed("status") is True
    assert is_command_allowed("check-setup") is True
    assert is_command_allowed("wait-for-ready") is True
    assert is_command_allowed("await-bridge") is True

    # Blocked commands
    assert is_command_allowed("setup") is False
    assert is_command_allowed("mcp-serve") is False
    assert is_command_allowed("spawn-actor") is False
    assert is_command_allowed("query-level") is False


def test_cli_blocks_non_whitelisted_commands(capsys):
    # Simulate calling a blocked command
    with patch("sys.argv", ["soft-ue-cli", "spawn-actor", "MyActor"]):
        with pytest.raises(SystemExit) as exc:
            main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "error: command 'spawn-actor' is not whitelisted" in captured.err


@patch("soft_ue_cli.__main__.sys.exit")
@patch("soft_ue_cli.__main__.sys.stderr")
@patch("soft_ue_cli.__main__.build_parser")
def test_cli_allows_whitelisted_commands(mock_build_parser, mock_stderr, mock_exit):
    # Setup mock parser and args
    mock_parser = MagicMock()
    mock_args = MagicMock()
    mock_args.command = "run-python-script"
    mock_args.server = None
    mock_args.timeout = None
    mock_args.asset_path = None
    
    mock_build_parser.return_value = mock_parser
    mock_parser.parse_args.return_value = mock_args

    main()

    mock_exit.assert_not_called()
    mock_args.func.assert_called_once_with(mock_args)


def test_mcp_server_filters_non_whitelisted_tools():
    # Skip if mcp not installed
    pytest.importorskip("mcp")

    server = create_server()
    assert server._tool_manager is not None
    tools = server._tool_manager._tools

    # Allowed tools should be registered
    assert "run-python-script" in tools
    assert "trigger-live-coding" in tools
    assert "capture-screenshot" in tools
    assert "capture-viewport" in tools
    assert "get-console-var" in tools
    assert "set-console-var" in tools
    assert "exec-console-command" in tools
    assert "run-automation" in tools

    # Infrastructure commands should be registered (if CLIENT_SIDE_COMMANDS)
    assert "status" in tools
    assert "wait-for-ready" in tools
    assert "check-setup" in tools

    # Blocked tools should NOT be registered
    assert "spawn-actor" not in tools
    assert "query-level" not in tools
    assert "add-graph-node" not in tools
    assert "mcp-serve" not in tools
