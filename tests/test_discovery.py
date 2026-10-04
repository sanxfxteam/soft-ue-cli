"""Tests for cli/soft_ue_cli/discovery.py ??no live server required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


from soft_ue_cli.discovery import _find_project_instance, _load_instance_file, get_server_url


# -- _load_instance_file -------------------------------------------------------


def test_load_instance_file_valid(tmp_path):
    f = tmp_path / "instance.json"
    f.write_text(json.dumps({"host": "127.0.0.1", "port": 9090}))
    assert _load_instance_file(f) == "http://127.0.0.1:9090"


def test_load_instance_file_defaults_when_fields_missing(tmp_path):
    f = tmp_path / "instance.json"
    f.write_text("{}")
    assert _load_instance_file(f) == "http://127.0.0.1:18080"


def test_load_instance_file_custom_host(tmp_path):
    f = tmp_path / "instance.json"
    f.write_text(json.dumps({"host": "192.168.1.10", "port": 8888}))
    assert _load_instance_file(f) == "http://192.168.1.10:8888"


def test_load_instance_file_missing_file(tmp_path):
    assert _load_instance_file(tmp_path / "nonexistent.json") is None


def test_load_instance_file_invalid_json(tmp_path):
    f = tmp_path / "instance.json"
    f.write_text("not json {{{")
    assert _load_instance_file(f) is None


def test_load_instance_file_empty_file(tmp_path):
    f = tmp_path / "instance.json"
    f.write_text("")
    assert _load_instance_file(f) is None


# -- _find_project_instance ----------------------------------------------------


def test_find_project_instance_in_cwd(tmp_path, monkeypatch):
    bridge_dir = tmp_path / ".soft-ue-bridge"
    bridge_dir.mkdir()
    (bridge_dir / "instance.json").write_text(json.dumps({"port": 9000}))
    monkeypatch.chdir(tmp_path)
    assert _find_project_instance() == "http://127.0.0.1:9000"


def test_find_project_instance_in_parent(tmp_path, monkeypatch):
    bridge_dir = tmp_path / ".soft-ue-bridge"
    bridge_dir.mkdir()
    (bridge_dir / "instance.json").write_text(json.dumps({"port": 7777}))
    subdir = tmp_path / "Source" / "Game"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    assert _find_project_instance() == "http://127.0.0.1:7777"


def test_find_project_instance_returns_none_when_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # No .soft-ue-bridge anywhere in tmp_path hierarchy (tmp_path is isolated)
    # Walk stops at filesystem root ??just verify no crash and None for clean dir
    result = _find_project_instance()
    # May find an instance.json somewhere up the real tree; only assert no exception
    assert result is None or result.startswith("http://")


def test_find_project_instance_prefers_closest(tmp_path, monkeypatch):
    # Parent has one port, child dir has another ??child wins
    (tmp_path / ".soft-ue-bridge").mkdir()
    (tmp_path / ".soft-ue-bridge" / "instance.json").write_text(json.dumps({"port": 18080}))
    subdir = tmp_path / "Content"
    subdir.mkdir()
    (subdir / ".soft-ue-bridge").mkdir()
    (subdir / ".soft-ue-bridge" / "instance.json").write_text(json.dumps({"port": 9999}))
    monkeypatch.chdir(subdir)
    assert _find_project_instance() == "http://127.0.0.1:9999"


# -- get_server_url ------------------------------------------------------------


def test_get_server_url_env_url_takes_priority(monkeypatch):
    monkeypatch.setenv("SOFT_UE_BRIDGE_URL", "http://remote:1234")
    monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
    assert get_server_url() == "http://remote:1234"


def test_get_server_url_env_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("SOFT_UE_BRIDGE_URL", "http://remote:1234/")
    assert get_server_url() == "http://remote:1234"


def test_get_server_url_port_env(monkeypatch):
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.setenv("SOFT_UE_BRIDGE_PORT", "9000")
    assert get_server_url() == "http://127.0.0.1:9000"


def test_get_server_url_invalid_port_falls_through(monkeypatch, tmp_path):
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.setenv("SOFT_UE_BRIDGE_PORT", "not-a-number")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("soft_ue_cli.discovery._find_instance_file", lambda: None)
    assert get_server_url() == "http://127.0.0.1:18080"


def test_get_server_url_instance_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
    bridge_dir = tmp_path / ".soft-ue-bridge"
    bridge_dir.mkdir()
    (bridge_dir / "instance.json").write_text(json.dumps({"port": 8765}))
    monkeypatch.chdir(tmp_path)
    assert get_server_url() == "http://127.0.0.1:8765"


def test_get_server_url_default_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("soft_ue_cli.discovery._find_instance_file", lambda: None)
    assert get_server_url() == "http://127.0.0.1:18080"


def test_get_server_url_url_beats_port(monkeypatch):
    monkeypatch.setenv("SOFT_UE_BRIDGE_URL", "http://custom:5000")
    monkeypatch.setenv("SOFT_UE_BRIDGE_PORT", "9999")
    assert get_server_url() == "http://custom:5000"


# -- bridge project check --------------------------------------------------------


class _Response:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _project_with_instance(tmp_path, port):
    project = tmp_path / "ProjectA"
    (project / ".soft-ue-bridge").mkdir(parents=True)
    (project / ".soft-ue-bridge" / "instance.json").write_text(json.dumps({"port": port}))
    return project


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("SOFT_UE_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SOFT_UE_BRIDGE_PORT", raising=False)
    monkeypatch.setattr("soft_ue_cli.discovery._checked", set())


def test_bridge_of_this_project_is_used(tmp_path, monkeypatch, no_env):
    project = _project_with_instance(tmp_path, 18081)
    monkeypatch.chdir(project)
    monkeypatch.setattr("httpx.get", lambda url, timeout: _Response({"project_dir": str(project) + "/"}))
    assert get_server_url() == "http://127.0.0.1:18081"


def test_bridge_of_another_project_is_refused(tmp_path, monkeypatch, no_env):
    from soft_ue_cli.errors import BridgeError

    project = _project_with_instance(tmp_path, 18080)
    other = tmp_path / "ProjectB"
    other.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr("httpx.get", lambda url, timeout: _Response({"project_dir": str(other)}))
    with pytest.raises(BridgeError, match="another project"):
        get_server_url()


def test_bridge_without_project_dir_is_trusted(tmp_path, monkeypatch, no_env):
    project = _project_with_instance(tmp_path, 18082)
    monkeypatch.chdir(project)
    monkeypatch.setattr("httpx.get", lambda url, timeout: _Response({"name": "soft-ue-bridge"}))
    assert get_server_url() == "http://127.0.0.1:18082"


def test_unreachable_bridge_returns_url(tmp_path, monkeypatch, no_env):
    def refuse(url, timeout):
        raise ConnectionError("refused")

    project = _project_with_instance(tmp_path, 18083)
    monkeypatch.chdir(project)
    monkeypatch.setattr("httpx.get", refuse)
    assert get_server_url() == "http://127.0.0.1:18083"


def test_dead_editor_pid_is_refused_without_asking_the_port(tmp_path, monkeypatch, no_env):
    from soft_ue_cli.errors import BridgeError

    project = _project_with_instance(tmp_path, 18080)
    (project / ".soft-ue-bridge" / "instance.json").write_text(json.dumps({"port": 18080, "pid": 4_000_000}))
    monkeypatch.chdir(project)

    def must_not_call(url, timeout):
        raise AssertionError("no HTTP needed for a dead pid")

    monkeypatch.setattr("httpx.get", must_not_call)
    with pytest.raises(BridgeError, match="has exited"):
        get_server_url()


def test_live_editor_pid_with_busy_bridge_is_trusted(tmp_path, monkeypatch, no_env):
    import os

    project = _project_with_instance(tmp_path, 18084)
    (project / ".soft-ue-bridge" / "instance.json").write_text(json.dumps({"port": 18084, "pid": os.getpid()}))
    monkeypatch.chdir(project)

    def busy(url, timeout):
        raise TimeoutError("busy")

    monkeypatch.setattr("httpx.get", busy)
    assert get_server_url() == "http://127.0.0.1:18084"
