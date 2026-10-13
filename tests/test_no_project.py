"""Outside a project (no soft-ue.config.json / .uproject above the cwd): no silent 18080 fallback."""

from __future__ import annotations

import json
import sys
import time

import pytest


def _no_http(*a, **k):
    raise AssertionError("no HTTP request may be sent outside a project")


@pytest.fixture
def outside(tmp_path, monkeypatch):
    """cwd = a folder with a project one level down (like the Playable repo root), no explicit server."""
    root = tmp_path / "Playable"
    (root / "ProjectShiva").mkdir(parents=True)
    (root / "ProjectShiva" / "ProjectShiva.uproject").write_text("{}")
    monkeypatch.chdir(root)
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
    monkeypatch.setattr("soft_ue_cli.discovery._nearest_project_dir", lambda start: None)
    monkeypatch.setattr("httpx.post", _no_http)
    monkeypatch.setattr("httpx.get", _no_http)
    return root


def _run_main(monkeypatch, *argv):
    from soft_ue_cli.__main__ import main

    monkeypatch.setattr(sys, "argv", ["soft-ue-cli", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    return exc.value.code


def test_bridge_command_outside_a_project_fails_fast_and_names_the_project(outside, monkeypatch, capsys):
    started = time.monotonic()
    code = _run_main(monkeypatch, "run-python-script", "--script", "print(1)")
    assert code == 1
    err = capsys.readouterr().err
    assert "no soft-ue.config.json or .uproject in" in err
    assert "cd ProjectShiva" in err
    assert time.monotonic() - started < 5


def test_run_automation_outside_a_project_fails_before_waiting(outside, monkeypatch, capsys):
    code = _run_main(monkeypatch, "run-automation", "Some.Test", "--test-timeout", "120")
    assert code == 1
    assert "cd ProjectShiva" in capsys.readouterr().err


def test_handler_calling_the_bridge_directly_gets_a_clean_error(outside, monkeypatch, capsys):
    # Handlers that call call_tool outside _run_tool: main() turns the BridgeError into "error: ...".
    from soft_ue_cli.client import call_tool

    monkeypatch.setattr("soft_ue_cli.__main__.cmd_status", lambda args: call_tool("pie-session", {}))
    code = _run_main(monkeypatch, "status")
    assert code == 1
    assert capsys.readouterr().err.startswith("error: no soft-ue.config.json")


def test_explicit_server_still_works_outside_a_project(outside, monkeypatch, capsys):
    import httpx

    sent = []

    def post(url, **kw):
        sent.append(url)
        body = {"jsonrpc": "2.0", "id": "1", "result": {"content": [{"type": "text", "text": json.dumps({"success": True, "output": "1"})}]}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.post", post)
    from soft_ue_cli.__main__ import main

    monkeypatch.setattr(sys, "argv", ["soft-ue-cli", "--server", "http://127.0.0.1:18191", "run-python-script", "--script", "print(1)"])
    main()
    assert sent == ["http://127.0.0.1:18191/bridge"]
    assert capsys.readouterr().out.strip() == "1"


def test_check_setup_outside_a_project_reports_instead_of_crashing(outside, monkeypatch, capsys):
    code = _run_main(monkeypatch, "check-setup")
    assert code == 1
    out = capsys.readouterr().out
    assert "[FAIL] Bridge server unreachable at this project's editor: no soft-ue.config.json" in out


def test_check_setup_project_path_targets_that_project(outside, monkeypatch, capsys):
    project = outside / "ProjectShiva"
    (project / ".soft-ue-bridge").mkdir()
    import os

    (project / ".soft-ue-bridge" / "instance.json").write_text(
        json.dumps({"port": 18192, "pid": os.getpid(), "project_dir": str(project)})
    )
    seen = []

    class _R:
        def raise_for_status(self):
            return None

        def json(self):
            return {"running": True, "project_dir": str(project)}

    monkeypatch.setattr("httpx.get", lambda url, timeout=None: seen.append(url) or _R())
    code = _run_main(monkeypatch, "check-setup", str(project))
    assert code == 1  # plugin files missing in the fake project
    assert set(seen) == {"http://127.0.0.1:18192/bridge"}
    assert "Bridge server reachable." in capsys.readouterr().out


def test_status_outside_a_project_reports_why(outside, monkeypatch, capsys):
    from soft_ue_cli.__main__ import main

    monkeypatch.setattr(sys, "argv", ["soft-ue-cli", "status"])
    main()
    out = json.loads(capsys.readouterr().out)
    assert "cd ProjectShiva" in out["error"]


def test_config_bridge_lookup_outside_a_project_is_skipped(outside):
    from soft_ue_cli.__main__ import _try_bridge_config_get

    assert _try_bridge_config_get("Core.Log", "LogTemp", "Engine") is None


# -- config command timeouts -------------------------------------------------------


def test_config_command_timeout_ends_the_whole_tree(tmp_path):
    from soft_ue_cli.__main__ import _run_config_command

    # shell -> python child -> python grandchild, all holding the output pipe.
    script = tmp_path / "slow.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    assert _run_config_command(f'"{sys.executable}" "{script}"', tmp_path, 1.0) is None
    assert time.monotonic() - started < 20


def test_config_command_result(tmp_path):
    from soft_ue_cli.__main__ import _run_config_command

    code, out, err = _run_config_command(f'"{sys.executable}" -c "print(42)"', tmp_path, 30.0)
    assert (code, out.strip(), err) == (0, "42", "")


def test_angelscript_check_that_hangs_is_skipped(tmp_path, monkeypatch, capsys):
    from soft_ue_cli import __main__ as main_mod

    (tmp_path / "soft-ue.config.json").write_text(
        json.dumps({"check-angelscript-command": f'"{sys.executable}" -c "import time; time.sleep(60)"'})
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_mod, "_ANGELSCRIPT_CHECK_TIMEOUT", 1.0)
    assert main_mod._check_angelscript() is None
    assert "did not finish within 1s" in capsys.readouterr().err


def test_process_check_that_hangs_counts_as_unconfigured(tmp_path, monkeypatch):
    from soft_ue_cli import __main__ as main_mod

    (tmp_path / "soft-ue.config.json").write_text(
        json.dumps({"check-ue-process-command": f'"{sys.executable}" -c "import time; time.sleep(60)"'})
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_mod, "_PROCESS_CHECK_TIMEOUT", 1.0)
    assert main_mod._check_ue_processes() is None


def test_several_projects_below_are_listed_without_picking_one(tmp_path):
    from soft_ue_cli.discovery import NoProjectFound

    message = str(NoProjectFound(tmp_path, [tmp_path / "Blank", tmp_path / "ProjectShiva"]))
    assert "Projects one folder down: Blank, ProjectShiva" in message
    assert "cd Blank" not in message
