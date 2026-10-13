"""Cut, empty and partial bridge replies against a fake bridge (no editor needed), and the GET /bridge probe."""

from __future__ import annotations

import argparse
import json
import os
import time

import pytest

from soft_ue_cli.client import call_tool
from soft_ue_cli.errors import BridgeError
from tests.fake_bridge import FakeBridge


@pytest.fixture
def at(monkeypatch):
    """Point the CLI at a fake bridge's URL (explicit server)."""

    def point(bridge: FakeBridge) -> FakeBridge:
        monkeypatch.setenv("SOFT_UE_BRIDGE_URL", bridge.url)
        monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
        return bridge

    return point


@pytest.mark.parametrize("mode", ["close", "rst", "midbody", "headersonly"])
def test_cut_connection_is_a_bridge_error_saying_the_command_may_have_run(at, mode):
    with at(FakeBridge(mode)):
        with pytest.raises(BridgeError) as exc:
            call_tool("run-python-script", {"script": "print(1)"}, timeout=5)
    assert "closed the connection without a full response" in exc.value.message
    assert "may have run" in exc.value.message


def test_empty_body_is_an_error(at):
    with at(FakeBridge("empty200")):
        with pytest.raises(BridgeError, match="sent an empty response"):
            call_tool("run-python-script", {"script": "print(1)"}, timeout=5)


def test_reply_without_result_or_error_is_an_error(at):
    with at(FakeBridge("emptyjson200")):
        with pytest.raises(BridgeError, match="neither 'result' nor 'error'"):
            call_tool("run-python-script", {"script": "print(1)"}, timeout=5)


def test_read_timeout_says_the_command_may_have_run(at):
    with at(FakeBridge("hang")):
        started = time.monotonic()
        with pytest.raises(BridgeError) as exc:
            call_tool("run-python-script", {"script": "print(1)"}, timeout=1)
    assert time.monotonic() - started < 10
    assert "timed out" in exc.value.message
    assert "may have run" in exc.value.message


def test_connect_failure_does_not_claim_the_command_ran(monkeypatch):
    bridge = FakeBridge()  # bound, never started: grab a free port, then close it
    bridge._sock.close()
    monkeypatch.setenv("SOFT_UE_BRIDGE_URL", bridge.url)
    with pytest.raises(BridgeError) as exc:
        call_tool("run-python-script", {}, timeout=2)
    assert "cannot connect" in exc.value.message or "timed out" in exc.value.message  # Windows: ConnectTimeout
    assert "may have run" not in exc.value.message


# -- run-python-script output --------------------------------------------------------


def _run_python(script="print(1)"):
    from soft_ue_cli.__main__ import build_parser

    args = build_parser().parse_args(["run-python-script", "--script", script])
    args.func(args)


def test_run_python_script_prints_the_output(at, capsys):
    with at(FakeBridge("ok", output="hello\n")):
        _run_python()
    captured = capsys.readouterr()
    assert captured.out.strip() == "hello"
    assert "note:" not in captured.err


def test_run_python_script_says_when_the_script_printed_nothing(at, capsys):
    with at(FakeBridge("ok", output="")):
        _run_python()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "note: the script ran and printed nothing" in captured.err


@pytest.mark.parametrize("mode", ["emptyresult", "emptytext"])
def test_run_python_script_flags_a_reply_without_success(at, capsys, mode):
    with at(FakeBridge(mode)):
        _run_python()
    assert "no 'success' field" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["close", "empty200", "emptyjson200"])
def test_run_python_script_fails_loudly_on_a_lost_reply(at, capsys, mode):
    with at(FakeBridge(mode)):
        with pytest.raises(SystemExit) as exc:
            _run_python()
    assert exc.value.code == 1
    assert "may have run" in capsys.readouterr().err


# -- GET /bridge probe -----------------------------------------------------------------


def _project(tmp_path, monkeypatch, instance):
    project = tmp_path / "FPS"
    (project / ".soft-ue-bridge").mkdir(parents=True)
    (project / "FPS.uproject").write_text("{}")
    (project / ".soft-ue-bridge" / "instance.json").write_text(json.dumps(instance))
    monkeypatch.chdir(project)
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
    return project


def test_current_instance_json_needs_no_probe(tmp_path, monkeypatch):
    from soft_ue_cli.discovery import get_server_url

    with FakeBridge("ok", get_mode="hang") as bridge:  # a busy editor: a probe would be abandoned after 2 s
        _project(tmp_path, monkeypatch, {"port": bridge.port, "pid": os.getpid(), "project_dir": str(tmp_path / "FPS")})
        started = time.monotonic()
        assert get_server_url() == bridge.url
        assert time.monotonic() - started < 1
        call_tool("run-python-script", {"script": "print(1)"}, timeout=5)
    assert bridge.requests == [("POST", "tools/call")]


def test_older_instance_json_is_still_probed(tmp_path, monkeypatch):
    from soft_ue_cli.discovery import WrongProjectBridge, get_server_url

    with FakeBridge("ok", project_dir="C:/Other/Project/") as bridge:
        _project(tmp_path, monkeypatch, {"port": bridge.port, "pid": os.getpid()})  # no project_dir
        with pytest.raises(WrongProjectBridge):
            get_server_url()
    assert bridge.requests == [("GET", "/bridge")]


def test_reused_pid_means_not_running(tmp_path, monkeypatch):
    from soft_ue_cli.discovery import EditorNotRunning, get_server_url

    _project(tmp_path, monkeypatch, {"port": 1, "pid": os.getpid(), "project_dir": str(tmp_path / "FPS")})
    monkeypatch.setattr("soft_ue_cli.discovery._process_start_time", lambda pid: time.time() + 3600)
    with pytest.raises(EditorNotRunning, match="started after the file was written"):
        get_server_url()


@pytest.mark.skipif(os.name != "nt", reason="process start time is read on Windows only")
def test_process_start_time_of_this_process():
    from soft_ue_cli.discovery import _process_start_time

    started = _process_start_time(os.getpid())
    assert started is not None and started <= time.time()
    assert _process_start_time(4_000_000) is None
