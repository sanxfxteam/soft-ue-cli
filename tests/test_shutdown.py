"""shutdown / shutdown-build-restart stop only their own editor (fake bridges and fake processes, nothing real
is ever killed: _pid_alive, the process check and both kill paths are replaced)."""

from __future__ import annotations

import argparse
import json
import time
import types

import pytest

from tests.fake_bridge import FakeBridge

A, B, C = 900001, 900003, 900005  # odd: never a real Windows pid


class Editors:
    """Fake editor processes of the checkout. An editor whose bridge got 'shutdown' exits, unless hung."""

    def __init__(self, monkeypatch, project, pids, hung=()):
        self.project = project
        self.alive = set(pids)
        self.hung = set(hung)
        self.bridges: dict[int, FakeBridge] = {}
        self.killed: list[int] = []
        uproject = str(project / "FPS.uproject")
        self.procs = {pid: {"pid": pid, "projectPath": uproject, "projectName": "FPS"} for pid in pids}
        monkeypatch.setattr("soft_ue_cli.discovery._pid_alive", self.is_alive)
        monkeypatch.setattr("soft_ue_cli.discovery._process_start_time", lambda pid: None)
        monkeypatch.setattr("soft_ue_cli.__main__._check_ue_processes", self.check)
        monkeypatch.setattr("soft_ue_cli.__main__._kill_ue_process", self.kill)
        monkeypatch.setattr("soft_ue_cli.__main__._force_kill_pid", self.kill)

    def is_alive(self, pid):
        bridge = self.bridges.get(pid)
        if bridge is not None and "shutdown" in bridge.methods and pid not in self.hung:
            self.alive.discard(pid)
        return pid in self.alive

    def check(self, *a, **k):
        return [self.procs[p] for p in sorted(self.alive) if p in self.procs and self.is_alive(p)]

    def kill(self, pid, *a, **k):
        self.killed.append(pid)
        self.alive.discard(pid)
        return True

    def bridge(self, pid, mode="ok", **kw):
        bridge = FakeBridge(mode, pid=pid, project_dir=str(self.project) + "/", **kw)
        self.bridges[pid] = bridge
        return bridge

    def register(self, bridge):
        (self.project / ".soft-ue-bridge").mkdir(exist_ok=True)
        (self.project / ".soft-ue-bridge" / "instance.json").write_text(
            json.dumps({"host": "127.0.0.1", "port": bridge.port, "pid": bridge.pid, "project_dir": str(self.project)})
        )


@pytest.fixture
def project(tmp_path, monkeypatch):
    from soft_ue_cli import __main__ as main_mod

    project = tmp_path / "FPS"
    project.mkdir()
    (project / "FPS.uproject").write_text("{}")
    monkeypatch.chdir(project)
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
    real_sleep = time.sleep
    monkeypatch.setattr(main_mod, "time", types.SimpleNamespace(monotonic=time.monotonic, sleep=lambda s: real_sleep(0.01)))
    return project


def _shutdown(capsys, *argv):
    from soft_ue_cli.__main__ import build_parser

    args = build_parser().parse_args(["shutdown", "--wait-timeout", "0.3", *argv])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    return code, json.loads(capsys.readouterr().out)


def test_shutdown_stops_only_the_bridges_editor(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B])
    with editors.bridge(A) as bridge_a, editors.bridge(B) as bridge_b:
        editors.register(bridge_a)
        code, out = _shutdown(capsys)
    assert code == 0
    assert out == {"success": True, "pid": A, "message": "Editor shut down and process exited."}
    assert bridge_a.methods == ["shutdown"] and bridge_b.methods == []
    assert editors.alive == {B} and editors.killed == []


def test_hung_editor_is_force_killed_alone(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B], hung=[A])
    with editors.bridge(A) as bridge_a:
        editors.register(bridge_a)
        code, out = _shutdown(capsys)
    assert code == 0 and out["killed"] is True and out["pid"] == A
    assert editors.killed == [A] and editors.alive == {B}


def test_explicit_server_stops_that_editor_only(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B])
    with editors.bridge(A) as bridge_a, editors.bridge(B) as bridge_b:
        editors.register(bridge_a)
        monkeypatch.setenv("SOFT_UE_BRIDGE_URL", bridge_b.url)
        code, out = _shutdown(capsys)
    assert code == 0 and out["pid"] == B
    assert bridge_a.methods == [] and bridge_b.methods == ["shutdown"]
    assert editors.alive == {A} and editors.killed == []


def test_no_instance_json_kills_nothing(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [B])  # an editor of the checkout without a reachable bridge
    code, out = _shutdown(capsys)
    assert code == 1
    assert out["code"] == "SHUTDOWN_REQUEST_FAILED" and out["pids"] == [B]
    assert "--pid" in out["next"]
    assert editors.killed == [] and editors.alive == {B}


def test_stale_instance_json_kills_nothing(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B])
    with FakeBridge("ok", pid=C) as dead:
        editors.register(dead)  # pid C has exited
        code, out = _shutdown(capsys)
    assert code == 1 and out["code"] == "SHUTDOWN_REQUEST_FAILED"
    assert dead.methods == [] and editors.killed == [] and editors.alive == {A, B}


def test_nothing_running_is_a_success(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [])
    code, out = _shutdown(capsys)
    assert code == 0 and out == {"success": True, "running": False, "message": "Unreal Editor is not running."}
    assert editors.killed == []


def test_failed_request_waits_but_does_not_kill(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B])
    with editors.bridge(A, mode="close") as bridge_a:  # the reply is lost: the editor may or may not stop
        editors.register(bridge_a)
        editors.hung.add(A)
        code, out = _shutdown(capsys)
    assert code == 1 and out["code"] == "EDITOR_STILL_RUNNING" and out["pid"] == A
    assert f"--pid {A}" in out["error"]
    assert editors.killed == [] and editors.alive == {A, B}


def test_failed_request_with_pid_kills_that_editor(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B])
    with editors.bridge(A, mode="close") as bridge_a:
        editors.register(bridge_a)
        editors.hung.add(A)
        code, out = _shutdown(capsys, "--pid", str(A))
    assert code == 0 and out["killed"] is True
    assert editors.killed == [A] and editors.alive == {B}


def test_pid_of_another_editor_is_not_sent_to_the_found_bridge(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B])
    with editors.bridge(A) as bridge_a:
        editors.register(bridge_a)
        code, out = _shutdown(capsys, "--pid", str(B))
    assert code == 0 and out["pid"] == B and out["killed"] is True
    assert bridge_a.methods == []
    assert editors.killed == [B] and editors.alive == {A}


def test_pid_of_another_project_is_refused(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A])
    editors.alive.add(C)  # alive, but not an editor of this project
    code, out = _shutdown(capsys, "--pid", str(C))
    assert code == 1 and out["code"] == "PID_NOT_THIS_PROJECT"
    assert editors.killed == []


def test_busy_bridge_uses_the_instance_pid(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B])
    with editors.bridge(A, get_mode="hang") as bridge_a:  # GET /bridge never answers
        editors.register(bridge_a)
        code, out = _shutdown(capsys)
    assert code == 0 and out["pid"] == A
    assert editors.alive == {B}


def test_bridge_without_pid_is_waited_on_by_port(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B])
    bridge = FakeBridge("ok", pid=None, project_dir=str(project) + "/")
    with bridge:
        monkeypatch.setenv("SOFT_UE_BRIDGE_URL", bridge.url)
        monkeypatch.setattr("soft_ue_cli.__main__._wait_for_bridge_down", lambda url, timeout: True)
        code, out = _shutdown(capsys)
    assert code == 0 and "reports no pid" in out["message"]
    assert bridge.methods == ["shutdown"] and editors.killed == []


def test_all_keeps_the_old_every_editor_behaviour(project, monkeypatch, capsys):
    editors = Editors(monkeypatch, project, [A, B], hung=[A, B])
    with editors.bridge(A) as bridge_a:
        editors.register(bridge_a)
        code, out = _shutdown(capsys, "--all")
    assert code == 0 and out["killed"] is True
    assert sorted(editors.killed) == [A, B]


def test_pid_and_all_are_exclusive():
    from soft_ue_cli.__main__ import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["shutdown", "--pid", "1", "--all"])
    ns = build_parser().parse_args(["shutdown-build-restart", "--pid", "12"])
    assert ns.pid == 12 and ns.all is False and ns.wait_timeout == 30.0


def test_shutdown_outside_a_project_stops_nothing(tmp_path, monkeypatch, capsys):
    (tmp_path / "FPS").mkdir()
    (tmp_path / "FPS" / "FPS.uproject").write_text("{}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
    monkeypatch.setattr("soft_ue_cli.discovery._nearest_project_dir", lambda start: None)
    editors = Editors(monkeypatch, tmp_path / "FPS", [A])
    code, out = _shutdown(capsys)
    assert code == 1 and out["code"] == "NO_PROJECT" and "cd FPS" in out["error"]
    assert editors.killed == []


def test_shutdown_build_restart_does_not_build_when_the_editor_stays(project, monkeypatch, capsys):
    from unittest.mock import patch

    from soft_ue_cli.__main__ import build_parser

    editors = Editors(monkeypatch, project, [B])
    args = build_parser().parse_args(["shutdown-build-restart", "--wait-timeout", "0.3"])
    with patch("soft_ue_cli.__main__.cmd_build_start") as build_start:
        with pytest.raises(SystemExit) as exc:
            args.func(args)
    assert exc.value.code == 1
    build_start.assert_not_called()
    assert json.loads(capsys.readouterr().out)["command"] == "shutdown-build-restart"
    assert editors.killed == []


def test_shutdown_build_restart_builds_after_stopping_its_editor(project, monkeypatch):
    from unittest.mock import patch

    from soft_ue_cli.__main__ import build_parser

    editors = Editors(monkeypatch, project, [A, B])
    args = build_parser().parse_args(["shutdown-build-restart", "--wait-timeout", "0.3"])
    with editors.bridge(A) as bridge_a, patch("soft_ue_cli.__main__.cmd_build_start") as build_start:
        editors.register(bridge_a)
        args.func(args)
    build_start.assert_called_once()
    assert editors.alive == {B} and editors.killed == []
