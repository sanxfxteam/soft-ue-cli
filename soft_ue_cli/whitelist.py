"""Command whitelist configuration and check helpers."""

from __future__ import annotations

# The hardcoded set of allowed commands.
ALLOWED_COMMANDS: frozenset[str] = frozenset({
    # Whitelisted CLI/Bridge commands
    "run-python-script",
    "trigger-live-coding",
    "capture-screenshot",
    "capture-viewport",
    "get-console-var",
    "set-console-var",
    "exec-console-command",
    "run-automation",
    "shutdown",
    "build",
    "build-start",
    "shutdown-build-restart",

    # Allowed infrastructure / diagnostic commands
    "status",
    "check-setup",
})


def is_command_allowed(command_name: str) -> bool:
    """Check if a command is allowed under the whitelist."""
    return command_name in ALLOWED_COMMANDS


def is_running_tests() -> bool:
    """Check if the code is currently running under pytest."""
    import sys
    return "pytest" in sys.modules
