# TODO - SoftUEBridge Plugin

- [x] **Investigate if we have some kind of shutdown command to close the editor.**
- [x] **Whitelist commands on the bridge**
Limit access to commands outside the whitelist that is listed here: `D:\Projects\soft-ue-cli\soft_ue_cli\whitelist.py`
- [x] **Improve trigger-live-coding output**
Provide the output of the console. please update the cli too `D:\Projects\soft-ue-cli\`

- [x] **Improve tests discovery realibility**
We found a workaround: **Bootstrapping Test Discovery**: If `run-automation` reports `No tests discovered after refresh`, running `Automation RunTests <TestName>` once via console command bootstraps the Automation Controller and registers the local worker.
