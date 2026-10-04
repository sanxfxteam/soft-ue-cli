"""Discover the SoftUEBridge HTTP server port."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _load_instance_file(path: Path) -> str | None:
    """Read a .soft-ue-bridge/instance.json and return the URL, or None on failure."""
    try:
        data = json.loads(path.read_text())
        host = data.get("host", "127.0.0.1")
        port = data.get("port", 18080)
        return f"http://{host}:{port}"
    except Exception:
        return None


def _find_instance_file() -> Path | None:
    """Walk up from cwd looking for .soft-ue-bridge/instance.json (project-local)."""
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / ".soft-ue-bridge" / "instance.json"
        if candidate.exists():
            return candidate
    return None


def _find_project_instance() -> str | None:
    """URL from the closest project-local instance.json, or None."""
    candidate = _find_instance_file()
    return _load_instance_file(candidate) if candidate else None


def _same_dir(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


_checked: set[tuple[str, str]] = set()


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stale(url: str, project_dir: Path, why: str):
    from .errors import BridgeError, ErrorKind

    return BridgeError(
        ErrorKind.EXPECTED,
        f"{why}: this project's editor is not running and "
        f"{project_dir / '.soft-ue-bridge' / 'instance.json'} is stale (bridge URL {url}).",
        "",
        {},
    )


def _check_bridge_project(url: str, project_dir: Path, instance: dict) -> None:
    """Refuse a bridge that belongs to another project.

    instance.json can outlive its editor (closed without cleanup). Another editor, e.g. a git worktree's,
    can then hold the same port, and every command would silently drive it. Bridges since 1.37.2 record
    their pid and project_dir: a dead pid means a stale file, whatever answers on the port; a bridge that
    reports another project_dir is refused. Older bridges, and no bridge listening, pass (callers report
    the connection error as before).
    """
    key = (url, str(project_dir))
    if key in _checked:
        return
    pid = instance.get("pid")
    if isinstance(pid, int) and not _pid_alive(pid):
        raise _stale(url, project_dir, f"the editor that wrote it (pid {pid}) has exited")
    try:
        import httpx

        response = httpx.get(f"{url}/bridge", timeout=2.0)
        reported = response.json().get("project_dir")
    except Exception:
        return  # nothing listening, or busy: the pid check above already covers stale files from new bridges
    if reported and not _same_dir(reported, project_dir):
        raise _stale(url, project_dir, f"the bridge at {url} belongs to another project ({reported})")
    _checked.add(key)


def get_server_url() -> str:
    """Return the base URL of the running SoftUEBridge server.

    Resolution order:
    1. SOFT_UE_BRIDGE_URL env var (full URL)
    2. SOFT_UE_BRIDGE_PORT env var (port only)
    3. .soft-ue-bridge/instance.json in cwd or any parent (project-local, written by plugin); raises
       BridgeError if the bridge on that port reports another project
    4. Default: http://127.0.0.1:18080
    """
    if url := os.environ.get("SOFT_UE_BRIDGE_URL"):
        return url.rstrip("/")

    if port_str := os.environ.get("SOFT_UE_BRIDGE_PORT"):
        try:
            return f"http://127.0.0.1:{int(port_str)}"
        except ValueError:
            pass

    if candidate := _find_instance_file():
        if url := _load_instance_file(candidate):
            try:
                instance = json.loads(candidate.read_text())
            except Exception:
                instance = {}
            _check_bridge_project(url, candidate.parent.parent, instance if isinstance(instance, dict) else {})
            return url

    return "http://127.0.0.1:18080"
