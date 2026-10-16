#!/bin/sh
# Ubuntu's official OpenVSP package installs binaries in /opt/OpenVSP.
set -eu
runtime_root="${OPENVSP_MCP_RUNTIME_ROOT:-$HOME/.local/share/OpenVSP-MCP}"
export OPENVSP_BIN="${OPENVSP_BIN:-/opt/OpenVSP/vspscript}"
export VSPAERO_BIN="${VSPAERO_BIN:-/opt/OpenVSP/vspaero}"
export OPENVSP_CPU_BUDGET="${OPENVSP_CPU_BUDGET:-4}"
export PYTHONDONTWRITEBYTECODE=1
cd "$runtime_root"
exec "$runtime_root/current/bin/python" -I -m openvsp_mcp "$@"
