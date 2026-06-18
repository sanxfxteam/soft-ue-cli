---
name: soft-ue-cli
description: How to use the soft-ue-cli command-line interface or MCP server to inspect, edit, and test Unreal Engine projects. Use when the user asks to run CLI commands, execute automation tests, run build workflows, or manage UE configuration files.
---

# Using soft-ue-cli

`soft-ue-cli` is a powerful tool to inspect, modify, and test Unreal Engine projects either locally (offline) or via a live HTTP bridge plugin (online) running on port `18080` (default).

## Diagnostics
Verify the plugin installation and connection using these commands:
- `soft-ue-cli status` -- queries the running bridge server for health statistics and registered tools.

## Build & Relaunch Workflow
When making C++ changes or editing Angelscript scripts, use these commands to compile and restart the editor:
- `soft-ue-cli build-start` -- runs the build command, launches the editor, and tail-follows the log file in real-time, printing any Angelscript compilation errors (`LogAngelscript: Error:`) until the bridge becomes ready.
- `soft-ue-cli shutdown-build-restart` -- requests editor shutdown via the bridge, wait a moment, runs the build command, and launches the editor while monitoring for errors.

## Running Automation Tests
Automation spec/integration tests can be run from the command line:
- `soft-ue-cli run-automation <TestPattern>` -- runs tests via the Session Frontend and prints PASS/FAIL status. Supports wildcard filters (e.g. `ProjectShiva.Abilities.*`).
- Overrides: Use `--test-timeout <seconds>` to set a custom maximum execution limit.
- Note: On first launch, the local automation controller worker might be inactive. `run-automation` includes self-healing logic that automatically bootstraps the controller if 0 tests are discovered initially.
