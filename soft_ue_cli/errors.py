"""Error classification and bug-report nudge helpers for soft-ue-cli."""

from __future__ import annotations

import enum


class ErrorKind(enum.Enum):
    """Whether an error is expected (operational) or unexpected (likely a bug)."""

    EXPECTED = "expected"
    UNEXPECTED = "unexpected"


class BridgeError(Exception):
    """Raised by call_tool() on any failure, carrying classification metadata."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        tool_name: str,
        arguments: dict,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.tool_name = tool_name
        self.arguments = arguments

