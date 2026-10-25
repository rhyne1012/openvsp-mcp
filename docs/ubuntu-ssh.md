# Ubuntu 24.04 and SSH stdio

The current local stdio target is the official OpenVSP **3.55.0 Ubuntu 24.04
amd64** Debian package with VSPAERO 7.2.2 and Python 3.12.3. See
[0.9 validation](validation-0.9.md) for native identities and results.
Mac-to-Ubuntu SSH transport evidence remains the historical
[3.53.1 validation](validation-ubuntu-3531.md); SSH was not rerun for 3.55.0. OpenVSP Python bindings are not used:
the MCP launches `vspscript -script`, which invokes VSPAERO through the Analysis API.

## Install on Ubuntu

Download the matching package from the [official download page](https://openvsp.org/download.php).
For the build tested here:

```sh
mkdir -p "$HOME/.cache/openvsp-install"
curl -fL 'https://openvsp.org/zips/current/linux/OpenVSP-3.55.0-Ubuntu-24.04_amd64.deb' \
  -o "$HOME/.cache/openvsp-install/OpenVSP-3.55.0-Ubuntu-24.04_amd64.deb"
sha256sum "$HOME/.cache/openvsp-install/OpenVSP-3.55.0-Ubuntu-24.04_amd64.deb"
# Recorded archive SHA-256:
# 865c24f560bca8bdfd186a9c6933c4c9c016204896d7f7e0bc9ce78030337a72
sudo apt-get update
sudo apt-get install python3-venv git desktop-file-utils \
  "$HOME/.cache/openvsp-install/OpenVSP-3.55.0-Ubuntu-24.04_amd64.deb"
```

Inspect any checksum difference instead of assuming the same filename is the same
build. The recorded hash identifies the retrieved official archive; it is not an
independent publisher signature. The Debian package installs `/opt/OpenVSP`,
command symlinks in `/usr/local/bin` and a desktop menu entry. APT resolves native
libraries including `libcminpack1` and `libglew2.2`. No compiler was needed.

Create source and runtime directories on the Ubuntu computer's local disk. Check
out the exact source revision or obtain its wheel and matching source distribution.
Do not transfer a Mac venv. From that source checkout:

```sh
export OPENVSP_BIN=/opt/OpenVSP/vspscript
export VSPAERO_BIN=/opt/OpenVSP/vspaero
export OPENVSP_CPU_BUDGET=4
python3 scripts/install_local.py \
  --package /absolute/path/openvsp_mcp-0.9.0-py3-none-any.whl \
  --runtime-root "$HOME/.local/share/OpenVSP-MCP"
mkdir -p "$HOME/Developer/Codex/tools"
install -m 755 scripts/launch_linux.sh "$HOME/Developer/Codex/tools/launch-openvsp-mcp.sh"
env -u DISPLAY -u WAYLAND_DISPLAY "$HOME/Developer/Codex/tools/launch-openvsp-mcp.sh" --health
```

The installer creates a permanent release venv, checks dependencies and native
health, and then atomically selects `current`. Retain prior releases for rollback.
The launcher supports `OPENVSP_MCP_RUNTIME_ROOT`, `OPENVSP_BIN`, `VSPAERO_BIN` and
`OPENVSP_CPU_BUDGET` overrides. Its default CPU budget is four, **per server process**;
separate SSH connections do not share a machine-wide quota.

The audited CLI workflow, including SVG/STL exports, works with both display
variables unset. Xvfb, X forwarding and a logged-in desktop are unnecessary for
this build and these operations. This does not validate interactive GUI display
or other native builds.

## Connect from a Mac or another SSH client

Verify the SSH host fingerprint independently and arrange noninteractive public
key authentication before configuring MCP. Use an existing authorized identity
where appropriate. Do not store a password in the launcher or client config.

Create a local executable wrapper using this computer's actual paths:

```sh
#!/bin/sh
set -eu
exec /absolute/local/python3 /absolute/local/openvsp-mcp/scripts/launch_ssh.py \
  --host user@ubuntu-host \
  --identity /absolute/local/ssh-private-key \
  --launcher /home/user/Developer/Codex/tools/launch-openvsp-mcp.sh -- "$@"
```

`--identity` is optional when an SSH agent/config selects the desired identity.
The helper executes OpenSSH with `-T`, `BatchMode=yes`, strict host-key checking,
a connection timeout and keepalives. It quotes the remote executable and arguments
for the remote shell. Stdout is reserved for MCP; diagnostics go to stderr. A
restricted SSH key with PTY allocation disabled is compatible.

Add a distinct client entry, preserving any local OpenVSP server:

```toml
[mcp_servers.openvsp_ubuntu]
command = "/absolute/local/launch-openvsp-ubuntu-mcp.sh"
args = []
startup_timeout_sec = 120
tool_timeout_sec = 1260
```

Restart/reconnect the MCP client to load the new entry. The remote launcher supplies
its own environment: setting Mac `OPENVSP_BIN` does not configure remote binaries.
All model, output and returned artifact paths refer to Ubuntu. Transfer inputs and
retrieve outputs separately with SCP/SFTP/rsync; a Mac absolute path is not a remote
model path. No listening MCP HTTP port is needed.

## Verify and recover

Run the two native smoke programs on Ubuntu with the selected runtime interpreter,
from the matching source checkout/distribution:

```sh
export OPENVSP_BIN=/opt/OpenVSP/vspscript VSPAERO_BIN=/opt/OpenVSP/vspaero
export OPENVSP_CPU_BUDGET=4
unset DISPLAY WAYLAND_DISPLAY
"$HOME/.local/share/OpenVSP-MCP/current/bin/python" examples/simple_aircraft/run_smoke.py
"$HOME/.local/share/OpenVSP-MCP/current/bin/python" examples/simple_aircraft/batch_smoke.py
```

On the Mac, in an environment with the project's dependencies, run:

```sh
python scripts/smoke_ssh.py \
  --command /absolute/local/launch-openvsp-ubuntu-mcp.sh \
  --remote-output /home/user/openvsp-runs/ssh-smoke \
  --output /absolute/local/ssh-evidence
```

This exercises all 16 tools, a real solve/sweep, parameter edits, invalid input,
batch cancellation/resume/export and reconnecting to read completed results.
The native smoke uses both executable hashes to choose its coefficient baseline;
unknown builds fail explicitly. It does not widen tolerance based on platform.

Closing a stdio server triggers its batch shutdown/cancellation. Disconnecting an
individual request is not a promise to keep a remote batch running. The tested
EOF and killed-local-SSH cases cleaned up active native children; a silent network
partition or abrupt server power loss has different detection timing and was not
tested. Reconnect, inspect persisted status and explicitly resume only when no old
owner/native process remains. Resume still verifies input, package and binary
identities; use the original runtime for batches created before an upgrade.

To roll back, stop the relevant client/server and atomically point `current` at
its retained previous release. The Mac SSH wrapper can remain unchanged. Removing
the `openvsp_ubuntu` client entry disables this connection without changing local
OpenVSP or other MCP servers.
