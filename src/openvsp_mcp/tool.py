"""python-sdk MCP integration helper."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .core import execute_openvsp
from .describe import describe_geometry
from .health import health_check
from .models import (
    OpenVSPGeometryRequest,
    OpenVSPInspectResponse,
    OpenVSPRequest,
    OpenVSPResponse,
)
from .version import __version__
from .workflows import create_model, preflight_model, preview_model, run_sweep


def build_tool(app: FastMCP) -> None:
    """Register OpenVSP automation tooling on an MCP server."""

    app.tool(
        name="openvsp.health",
        description="Probe OpenVSP API and VSPAERO executables; "
        "report package/SDK/binary versions and package fingerprint.",
    )(health_check)
    app.tool(
        name="openvsp.create_model",
        description="Create a model without an existing file. "
        "simple_aircraft uses metres, sets 3/4, Sref=12, Bref=10, Cref=1.2444444444, Xcg=3.",
    )(create_model)
    app.tool(
        name="openvsp.preview",
        description="Export SVG views and STL from a copy; leaves the source model unchanged.",
    )(preview_model)
    app.tool(
        name="openvsp.preflight",
        description="Check actual selected geometry sets and "
        "report reference/units warnings without launching VSPAERO.",
    )(preflight_model)
    app.tool(
        name="openvsp.sweep",
        description="Run up to 25 explicitly specified conditions, "
        "sequentially, within one total timeout; preserve per-condition and partial results.",
    )(run_sweep)

    @app.tool(
        name="openvsp.inspect",
        description=(
            "Describe an OpenVSP geometry without modifying it. Returns component IDs and raw info."
        ),
        meta={"version": __version__, "categories": ["geometry", "aero", "inspection"]},
    )
    def inspect(request: OpenVSPGeometryRequest) -> OpenVSPInspectResponse:
        return describe_geometry(request.geometry_file)

    @app.tool(
        name="openvsp.modify",
        description=(
            "Apply scripted parameter edits to an OpenVSP model without running VSPAero. "
            "Updates the input file only after validation; returns persistent artifacts."
        ),
        meta={"version": __version__, "categories": ["geometry"]},
    )
    def modify(request: OpenVSPRequest) -> OpenVSPResponse:
        return execute_openvsp(request.model_copy(update={"run_vspaero": False}))

    @app.tool(
        name="openvsp.run_vspaero",
        description=(
            "Run one steady subsonic VSPAERO condition using the Analysis API. Supply analysis settings, geometry commands and case_name; preserves the input model."
        ),
        meta={"version": __version__, "categories": ["geometry", "aero"]},
    )
    def run_vspaero(request: OpenVSPRequest) -> OpenVSPResponse:
        return execute_openvsp(request.model_copy(update={"run_vspaero": True}))


__all__ = ["build_tool"]
