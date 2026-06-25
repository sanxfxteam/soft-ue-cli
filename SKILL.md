---
name: soft-ue-cli
description: How to use the soft-ue-cli command-line interface or MCP server to inspect, edit, and test Unreal Engine projects. Use when the user asks to run CLI commands, execute automation tests, run build workflows, or manage UE configuration files.
---

# Using soft-ue-cli

`soft-ue-cli` is a powerful tool to inspect, modify, and test Unreal Engine projects either locally (offline) or via a live HTTP bridge plugin (online) running on port `18080` (default).

## Diagnostics
Verify the plugin installation and connection using these commands:
- `soft-ue-cli status` -- queries the running bridge server for health statistics. When the editor is running, it also runs `check-angelscript-command` and prints any AngelScript compilation errors after the bridge status. When the bridge is not up yet, it inspects the editor process state (`check-ue-process-command` in `soft-ue.config.json`) and reports one of: `not_running` (editor not started), `angelscript_errors` (editor loading but AngelScript failed to compile — errors are printed), or `loading` (editor still starting; waits up to 20s for the bridge before giving up).

## Build & Relaunch Workflow
When making C++ changes or editing Angelscript scripts, use these commands to compile and restart the editor:
- `soft-ue-cli build-start` -- runs the build command, launches the editor, and tail-follows the log file in real-time, printing any Angelscript compilation errors (`LogAngelscript: Error:`) until the bridge becomes ready.
- `soft-ue-cli shutdown-build-restart` -- requests editor shutdown via the bridge, wait a moment, runs the build command, and launches the editor while monitoring for errors.

## Running Automation Tests
Automation spec/integration tests can be run from the command line:
- `soft-ue-cli run-automation <TestPattern>` -- runs tests via the Session Frontend and prints PASS/FAIL status. Supports wildcard filters (e.g. `ProjectShiva.Abilities.*`). Before running, it executes `check-angelscript-command` (from `soft-ue.config.json`); if AngelScript has compilation errors it prints them and aborts with exit 1 without running any tests.
- Overrides: Use `--test-timeout <seconds>` to set a custom maximum execution limit.
- Note: On first launch, the local automation controller worker might be inactive. `run-automation` includes self-healing logic that automatically bootstraps the controller if 0 tests are discovered initially.

## Scripting
Run scripts inside the editor through the bridge:
- `soft-ue-cli run-python-script --script "..."` (or `--script-path file.py`) -- executes Python in the editor's Python environment (requires the Python Editor Script Plugin).
- `soft-ue-cli run-lua-script --script "..."` (or `--script-path file.lua`) -- executes Lua in-process through the NeoStack plugin's Lua runner (requires the NeoStackAI plugin). Drives NeoStack's Lua bindings; call `help()` to list them.

### Reusing Lua across calls
The Lua runtime opens only `base`, `string`, `table`, `math`, and `coroutine` — there is **no `require`** (no `package`/`io`/`os`). Each call gets a fresh state, so nothing persists between invocations.

To reference shared Lua in another file, use `loadfile` (a `base` builtin). Write the shared file as a returning module and load it at the top of your script:
```lua
-- C:/MyGame/Scripts/lib.lua
local M = {}
function M.greet(name) return "hi " .. name end
return M
```
```bash
soft-ue-cli run-lua-script --script "local lib = loadfile([[C:/MyGame/Scripts/lib.lua]])(); log(lib.greet('world'))"
```
- Prefer `loadfile(path)()` over `dofile(path)`: `dofile` runs the chunk through a C call, so any async NeoStack binding inside it (screenshot, generate_*, playtest waits, asset import) fails with *"attempt to yield across C-call boundary"*. `loadfile(path)()` is a plain Lua call, so yields propagate correctly.
- Paths are read with raw file I/O — any absolute path works and is **not** restricted to the NeoStack project/temp sandbox.
- The file is re-read and re-compiled on every call (no module cache), and counts against the per-script instruction limit.
