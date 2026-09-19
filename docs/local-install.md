# Staged local upgrades

`scripts/install_local.py` is an optional POSIX installer. It leaves client
configuration unchanged and never reinstalls into the currently selected venv.
It creates a new release at its permanent path, installs the requested package,
runs `pip check` and the real OpenVSP/VSPAERO health probe, then atomically switches
`current`. Failed candidates and prior releases are retained with JSON records.
Venv paths are never moved after creation, preserving executable shebangs.

1. Build or obtain the exact wheel/source revision to install.
2. Set `OPENVSP_BIN` and `VSPAERO_BIN` for this computer.
3. Choose a local runtime root outside any cloud-sync folder.

```sh
python3 scripts/install_local.py \
  --package /absolute/path/openvsp_mcp-0.4.0-py3-none-any.whl \
  --runtime-root "$HOME/Library/Application Support/OpenVSP-MCP/staged"
```

Optionally pass `--requirements /absolute/path/runtime-lock.txt` with pinned
runtime dependencies. Without it, pip resolves dependencies according to the
package constraints; a version/fingerprint match does not imply identical
transitive dependencies. Do not pass another machine's venv as the package.

After testing the selected candidate, configure the client's Python command as
`<runtime-root>/current/bin/python`, with arguments `-m openvsp_mcp`, and retain
this computer's binary environment values. Existing layouts do not need to move.
The installer refuses to replace `current` if it is a real directory. It does
not edit Codex configuration, remove old versions, migrate cloud files or upgrade
OpenVSP itself. An existing MCP server keeps its loaded version until restarted.

A candidate health check proves executable/API readiness. Run
`examples/simple_aircraft/run_smoke.py` using the candidate interpreter before
using a release for analysis. Compare `package_version`, `package_sha256`,
`mcp_sdk_version` and the two binary versions between Macs, then perform the
native smoke independently. Absolute paths can legitimately differ.

To roll back after stopping/restarting the client, atomically replace the
`current` symlink with the retained previous release identified in the install
record. No environments are automatically deleted.
