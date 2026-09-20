"""Typed MCP tools; native work never runs on the transport event loop."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from .core import execute_openvsp
from .describe import describe_geometry
from .health import health_check
from .models import (
    CreateModelRequest,
    OpenVSPGeometryRequest,
    OpenVSPInspectResponse,
    OpenVSPRequest,
    OpenVSPResponse,
    ParameterEditRequest,
    QueryRequest,
    ResultRequest,
    SweepRequest,
)
from .query import query_model, set_parameters
from .results import read_results
from .runtime import run_async
from .workflows import create_model, preflight_model, preview_model, run_sweep


def build_tool(app: FastMCP) -> None:
    @app.tool(name="openvsp.health", description="Fresh executable/API readiness and versions.")
    async def health() -> dict[str, Any]:
        return await run_async(health_check)

    @app.tool(name="openvsp.create_model", description="Create a template or custom model.")
    async def create(request: CreateModelRequest) -> OpenVSPResponse:
        return await run_async(create_model, request)

    @app.tool(name="openvsp.inspect", description="Read XML geometry metadata without OpenVSP.")
    async def inspect(request: OpenVSPGeometryRequest) -> OpenVSPInspectResponse:
        return await run_async(describe_geometry, request.geometry_file)

    @app.tool(name="openvsp.modify", description="Validate edits, then replace the source model.")
    async def modify(request: OpenVSPRequest) -> OpenVSPResponse:
        return await run_async(execute_openvsp, request, operation="modify")

    @app.tool(name="openvsp.preview", description="Export SVG/STL from a private model copy.")
    async def preview(request: OpenVSPRequest) -> OpenVSPResponse:
        return await run_async(preview_model, request)

    @app.tool(name="openvsp.preflight", description="Check loaded geometry sets without solving.")
    async def preflight(request: OpenVSPRequest) -> OpenVSPResponse:
        return await run_async(preflight_model, request)

    @app.tool(
        name="openvsp.run_vspaero",
        description=(
            "Solve one steady subsonic condition; verify actual solver inputs and results. "
            "Source preserved. fixed_wake selects the official fixed-wake flag."
        ),
    )
    async def solve(request: OpenVSPRequest) -> OpenVSPResponse:
        return await run_async(execute_openvsp, request, operation="run_vspaero")

    @app.tool(name="openvsp.sweep", description="Existing 1–25 condition sequential sweep.")
    async def sweep(request: SweepRequest) -> dict[str, Any]:
        return await run_async(run_sweep, request)

    @app.tool(
        name="openvsp.query",
        description=(
            "Query native capabilities, analysis input types/defaults, or geometry parameters. "
            "Read-only. Parameters are paginated. Analysis defaults may depend on the supplied model."
        ),
    )
    async def query(request: QueryRequest) -> dict[str, Any]:
        return await run_async(query_model, request)

    @app.tool(
        name="openvsp.set_parameters",
        description=(
            "Apply parameter ID/value edits in one load/update, verify limits and readback, "
            "then replace the source. Obtain IDs with openvsp.query."
        ),
    )
    async def parameters(request: ParameterEditRequest) -> OpenVSPResponse:
        return await run_async(set_parameters, request)

    @app.tool(
        name="openvsp.read_results",
        description=(
            "Read saved coefficients, settings and bounded log tails without running OpenVSP."
        ),
    )
    async def results(request: ResultRequest) -> dict[str, Any]:
        return await run_async(read_results, request)
