"""Help texts that callers rely on."""

from __future__ import annotations

import pytest

from soft_ue_cli.__main__ import build_parser


def _help(capsys, *argv):
    with pytest.raises(SystemExit):
        build_parser().parse_args([*argv, "--help"])
    return " ".join(capsys.readouterr().out.split())


def test_test_timeout_is_for_the_whole_call(capsys):
    text = _help(capsys, "run-automation")
    assert "for all the tests of the call together, not per test" in text
    assert "default: 60 s per matched test" in text


def test_pie_tick_is_marked_broken(capsys):
    assert "BROKEN: the bridge blocks the game thread" in _help(capsys, "pie-tick")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    assert "BROKEN: advance the PIE world" in " ".join(capsys.readouterr().out.split())


def test_pie_session_wait_for_is_marked_broken(capsys):
    text = _help(capsys, "pie-session")
    assert "wait-for — BROKEN" in text
    assert "freezes the editor" in text


def test_shutdown_help_names_pid_and_all(capsys):
    text = _help(capsys, "shutdown")
    assert "--pid N" in text and "--all" in text
    assert "never touched" in text
