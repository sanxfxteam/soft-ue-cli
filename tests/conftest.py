"""Shared fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_discovery(monkeypatch):
    """Each test starts with no --config binding and no cached bridge checks."""
    monkeypatch.setattr("soft_ue_cli.discovery._config_path", None)
    monkeypatch.setattr("soft_ue_cli.discovery._checked", set())
