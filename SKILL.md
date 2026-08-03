---
name: soft-ue-cli
description: How to use the soft-ue-cli command-line interface to inspect, edit, and test Unreal Engine projects. Use when the user asks to run CLI commands, execute automation tests, run build workflows, or manage UE configuration files.
---

# Using soft-ue-cli

`soft-ue-cli` is a powerful tool to inspect, modify, and test Unreal Engine projects either locally (offline) or via a live HTTP bridge plugin (online) running on port `18080` (default).

## Diagnostics
Verify the plugin installation and connection using these commands:
- `soft-ue-cli status` -- queries the running bridge server for health statistics. When the editor is running, it also runs `check-angelscript-command` and prints any AngelScript compilation errors after the bridge status. When the bridge is not up yet, it inspects the editor process state (`check-ue-process-command` in `soft-ue.config.json`) and reports one of: `not_running` (editor not started), `angelscript_errors` (editor loading but AngelScript failed to compile — errors are printed), or `loading` (editor still starting; waits up to 20s for the bridge before giving up).

## Build & Relaunch Workflow
When making C++ changes or editing Angelscript scripts, use these commands to compile and restart the editor:
- `soft-ue-cli build-start` -- runs the build command, launches the editor, and tail-follows the log file in real-time, printing any Angelscript compilation errors (`LogAngelscript: Error:`) until the bridge becomes ready.
- `soft-ue-cli shutdown-build-restart` -- requests editor shutdown via the bridge, waits until the editor process has fully exited (polling `check-ue-process-command`, force-killing after `--wait-timeout`), runs the build command, and launches the editor while monitoring for errors.
- `soft-ue-cli shutdown` -- requests editor shutdown via the bridge and waits until the editor process has fully exited. Force-kills the process tree if it doesn't exit within `--wait-timeout` seconds (default 30) and reports `"killed": true`.

## Running Automation Tests
Automation spec/integration tests can be run from the command line:
- `soft-ue-cli run-automation <TestPattern>` -- runs tests via the Session Frontend and prints PASS/FAIL status. Supports wildcard filters (e.g. `ProjectShiva.Abilities.*`). Before running, it executes `check-angelscript-command` (from `soft-ue.config.json`); if AngelScript has compilation errors it prints them and aborts with exit 1 without running any tests.
- Overrides: Use `--test-timeout <seconds>` to set a custom maximum execution limit.
- Note: On first launch, the local automation controller worker might be inactive. `run-automation` includes self-healing logic that automatically bootstraps the controller if 0 tests are discovered initially.

## Scripting
Run scripts inside the editor through the bridge:
- `soft-ue-cli run-python-script --script "..."` (or `--script-path file.py`) -- executes Python in the editor's Python environment. Supports `--capture-logs` (optionally filtered by `--log-filter` or `--log-category`) to capture and print UE console logs emitted during execution, and `--json` to output raw JSON results instead of plaintext output.
- `soft-ue-cli run-lua-script --script "..."` (or `--script-path file.lua`) -- executes Lua in-process through the NeoStack plugin's Lua runner (requires the NeoStackAI plugin).

### Passing variables to a Python script
The script runs inside the **editor's** Python process, so environment variables exported in your shell (`$env:MY_PHASE=...`, `export MY_PHASE=...`) never reach it. Pass values as arguments instead:

- `--set KEY=VALUE` -- repeatable, auto-typed: `true`/`false` become booleans, integer and float literals become numbers, everything else stays a string. Values are transported verbatim, so Windows paths and quotes are safe.
- `--arguments '{"key": value}'` -- a full JSON object, for nested structures or values the auto-typing would mangle (e.g. the literal string `"true"`).
- Both may be combined: `--arguments` supplies the base object and `--set` overrides matching keys. Repeating the same `--set` key keeps the last one.

The script reads them with `unreal.get_mcp_args()`, which always returns a dict (empty when nothing was passed) and never carries over values from a previous run. Works identically for `--script` and `--script-path`.

```powershell
soft-ue-cli run-python-script --script-path build_shells.py --set phase=shells --set dry_run=true --set retries=3
```

```python
import unreal
args = unreal.get_mcp_args()
phase = args.get("phase", "all")     # "shells"
dry_run = args.get("dry_run", False)  # True (a real bool)
```

### Reusing Lua across calls## Lua Scripting
All functions documented in `SharedPlugins/NeoStackAI/Docs/Reference/`:The Lua runtime opens only `base`, `string`, `table`, `math`, and `coroutine` — there is **no `require`** (no `package`/`io`/`os`). Each call gets a fresh state, so nothing persists between invocations.

To reference shared Lua in another file, use `loadfile` (a `base` builtin). Write the shared file as a returning module and load it at the top of your script.
- Paths are read with raw file I/O — any absolute path works and is **not** restricted to the NeoStack project/temp sandbox.
- The file is re-read and re-compiled on every call (no module cache), and counts against the per-script instruction limit.
