"""Find the SoftUEBridge server (the editor) a command talks to.

Several editors can run at once (main checkouts of different projects, git worktrees), each with its own
bridge on the first free port from 18080. A command must only ever reach the editor of the project it was
run for, so the target is resolved per project:

1. An explicit ``--server`` / ``SOFT_UE_BRIDGE_URL`` / ``SOFT_UE_BRIDGE_PORT`` is used as given.
2. Otherwise the project is resolved (``--config``'s folder, else the nearest folder above the cwd holding a
   ``.uproject``, ``soft-ue.config.json`` or ``.soft-ue-bridge/instance.json``) and only that project's
   ``.soft-ue-bridge/instance.json`` is used: missing, written by another checkout, or written by an editor
   that has exited, it means "this project's editor is not running" (``EditorNotRunning``); a bridge on its
   port that reports another project is refused (``WrongProjectBridge``). There is no port fallback.
3. Only when no project can be resolved at all: ``http://127.0.0.1:18080``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from .errors import BridgeError, ErrorKind

DEFAULT_URL = "http://127.0.0.1:18080"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

_config_path: Path | None = None
_checked: set[tuple[str, str, object]] = set()


class EditorNotRunning(BridgeError):
    """The resolved project has no live editor (no instance.json, or a stale or foreign one)."""

    def __init__(self, project_dir: Path, reason: str) -> None:
        super().__init__(
            ErrorKind.EXPECTED,
            f"the editor of {project_dir} is not running ({reason}). Start it (build-start), "
            "or pass --server / SOFT_UE_BRIDGE_PORT to reach a specific editor.",
            "",
            {},
        )
        self.project_dir = project_dir


class WrongProjectBridge(BridgeError):
    """The bridge at the URL belongs to another project's editor."""

    def __init__(self, url: str, project_dir: Path, reported: str) -> None:
        super().__init__(
            ErrorKind.EXPECTED,
            f"the bridge at {url} belongs to another project ({reported}), not {project_dir}; refusing to use it.",
            "",
            {},
        )
        self.url = url
        self.project_dir = project_dir
        self.reported = reported


# -- explicit server -------------------------------------------------------------


def set_config_path(path: str | Path | None) -> None:
    """Bind the project to a soft-ue.config.json given with --config (None unbinds)."""
    global _config_path
    _config_path = Path(path).resolve() if path else None


def config_path() -> Path | None:
    return _config_path


def explicit_server_url() -> str | None:
    """URL from --server / SOFT_UE_BRIDGE_URL / SOFT_UE_BRIDGE_PORT, which bypass discovery."""
    if url := os.environ.get("SOFT_UE_BRIDGE_URL"):
        return url.rstrip("/")
    if port_str := os.environ.get("SOFT_UE_BRIDGE_PORT"):
        try:
            return f"http://127.0.0.1:{int(port_str)}"
        except ValueError:
            pass
    return None


def requested_port() -> int | None:
    """Port of an explicit local server, to hand to an editor the CLI launches."""
    url = explicit_server_url()
    if not url:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    return port if port and parts.hostname in _LOCAL_HOSTS else None


# -- project resolution ----------------------------------------------------------


def _is_project_dir(directory: Path) -> bool:
    return (
        any(directory.glob("*.uproject"))
        or (directory / "soft-ue.config.json").is_file()
        or (directory / ".soft-ue-bridge" / "instance.json").is_file()
    )


def _nearest_project_dir(start: Path) -> Path | None:
    for directory in [start, *start.parents]:
        try:
            if _is_project_dir(directory):
                return directory
        except OSError:
            continue
    return None


def resolve_project_dir() -> Path | None:
    """The project the command is for: --config's folder, else the nearest project folder above the cwd."""
    if _config_path is not None:
        start = _config_path.parent
        if any(start.glob("*.uproject")):
            return start
        return _nearest_project_dir(start) or start
    return _nearest_project_dir(Path.cwd().resolve())


def find_uproject(project_dir: Path | None) -> Path | None:
    if project_dir is None:
        return None
    found = sorted(project_dir.glob("*.uproject"))
    return found[0] if found else None


# -- instance.json -----------------------------------------------------------------


def _same_dir(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists, we just can't open it
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _instance_file(project_dir: Path) -> Path:
    return project_dir / ".soft-ue-bridge" / "instance.json"


def _read_instance(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _instance_url(data: dict) -> str:
    return f"http://{data.get('host', '127.0.0.1')}:{data.get('port', 18080)}"


def _load_instance_file(path: Path) -> str | None:
    """URL recorded in an instance.json, or None when it is missing or unreadable."""
    data = _read_instance(path)
    return _instance_url(data) if data is not None else None


def _find_instance_file() -> Path | None:
    """instance.json of the resolved project, if it exists."""
    project = resolve_project_dir()
    if project is None:
        return None
    candidate = _instance_file(project)
    return candidate if candidate.is_file() else None


def _find_project_instance() -> str | None:
    """URL from the resolved project's instance.json, unchecked, or None."""
    candidate = _find_instance_file()
    return _load_instance_file(candidate) if candidate else None


def bridge_project_dir(url: str, timeout: float = 2.0) -> str | None:
    """project_dir reported by the bridge at url (None: not reported, unreachable or busy)."""
    try:
        import httpx

        reported = httpx.get(f"{url}/bridge", timeout=timeout).json().get("project_dir")
    except Exception:
        return None
    return reported if isinstance(reported, str) and reported else None


def project_bridge_url(project_dir: Path) -> str:
    """URL of the project's own live bridge.

    Raises EditorNotRunning when the project's instance.json is missing, unreadable, written by another
    checkout's editor (its project_dir) or by an editor that has exited (its pid). Such a file is ignored,
    not deleted: the editor may be rewriting it right now. Raises WrongProjectBridge when the bridge on the
    recorded port reports another project. A bridge that does not answer (busy or still starting) or does
    not report project_dir (older plugin) is trusted.
    """
    path = _instance_file(project_dir)
    if not path.is_file():
        raise EditorNotRunning(project_dir, f"no {path}")
    data = _read_instance(path)
    if data is None:
        raise EditorNotRunning(project_dir, f"{path} is unreadable")
    url = _instance_url(data)
    recorded = data.get("project_dir")
    if isinstance(recorded, str) and recorded and not _same_dir(recorded, project_dir):
        raise EditorNotRunning(project_dir, f"{path} was written by the editor of {recorded}")
    pid = data.get("pid")
    if isinstance(pid, int) and not _pid_alive(pid):
        raise EditorNotRunning(project_dir, f"{path} is stale: the editor that wrote it (pid {pid}) has exited")

    key = (url, os.path.normcase(str(project_dir)), pid)
    if key in _checked:
        return url
    reported = bridge_project_dir(url)
    if reported is None:
        return url  # not answering (busy, starting) or an older bridge: the pid check above covers stale files
    if not _same_dir(reported, project_dir):
        raise WrongProjectBridge(url, project_dir, reported)
    _checked.add(key)
    return url


def get_server_url() -> str:
    """Base URL of the bridge this command talks to (see the module docstring for the order).

    Raises EditorNotRunning / WrongProjectBridge (both BridgeError) when a project was resolved and its own
    editor is not reachable: never falls back to another editor's port.
    """
    if url := explicit_server_url():
        return url
    project = resolve_project_dir()
    if project is None:
        return DEFAULT_URL
    return project_bridge_url(project)
