"""Typed MCP tools; native work never runs on the transport event loop."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .batch import batch_status, cancel_batch, export_batch, resume_batch, submit_batch
from .core import execute_openvsp
from .describe import describe_geometry
from .health import health_check
from .models import (
    BatchCancelRequest,
    BatchExportRequest,
    BatchRequest,
    BatchResumeRequest,
    BatchStatusRequest,
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
from .runtime import run_async, run_control
from .workflows import create_model, preflight_model, preview_model, run_sweep


def build_tool(app: FastMCP) -> None:
    @app.tool(
        name="openvsp.health",
        description=(
            "Check installed OpenVSP/VSPAERO readiness, versions, paths and package identity. "
            "Launches a temporary OpenVSP API probe and a solver version command; no aerodynamic "
            "solve or user model is involved. Returns status=error with check details when "
            "unavailable; use openvsp.preflight to check a particular model's geometry sets."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def health() -> dict[str, Any]:
        return await run_async(health_check)

    @app.tool(
        name="openvsp.create_model",
        description=(
            "Create a new .vsp3 model from the four-component simple_aircraft template or custom "
            "trusted AngelScript, without an input model. Runs OpenVSP and retains the model, "
            "script, log and manifest in a unique directory under output_dir; no VSPAERO solve. "
            "Use openvsp.modify to edit an existing source model."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
        ),
    )
    async def create(
        request: Annotated[
            CreateModelRequest, Field(description="New model template and output settings.")
        ],
    ) -> OpenVSPResponse:
        return await run_async(create_model, request)

    @app.tool(
        name="openvsp.inspect",
        description=(
            "Read component IDs, wing names and a component summary directly from .vsp3 XML. "
            "Does not launch OpenVSP, write files or modify the source; unreadable or invalid "
            "XML fails. Use openvsp.query for native parameter values or analysis defaults."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def inspect(
        request: Annotated[
            OpenVSPGeometryRequest, Field(description="Model file to inspect as XML.")
        ],
    ) -> OpenVSPInspectResponse:
        return await run_async(describe_geometry, request.geometry_file)

    @app.tool(
        name="openvsp.modify",
        description=(
            "Edit an existing model with trusted AngelScript and/or parameter ID/value edits. "
            "Runs OpenVSP on a snapshot, retains run artifacts and replaces the source only "
            "after validation; refuses replacement if the source changed during the run. "
            "No VSPAERO solve; use openvsp.set_parameters for a focused typed-edit request."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
        ),
    )
    async def modify(
        request: Annotated[
            OpenVSPRequest,
            Field(description="Source and edits; this tool always modifies without solving."),
        ],
    ) -> OpenVSPResponse:
        return await run_async(execute_openvsp, request, operation="modify")

    @app.tool(
        name="openvsp.preview",
        description=(
            "Export preview.svg and preview.stl for a model, optionally applying edits to a "
            "private copy. Runs OpenVSP and retains exports, model, script, log and manifest "
            "in a unique run directory without replacing the source or running VSPAERO. "
            "Use openvsp.inspect for file-only metadata, or openvsp.preflight for geometry-set checks."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
        ),
    )
    async def preview(
        request: Annotated[
            OpenVSPRequest,
            Field(description="Model, optional private-copy edits and export location."),
        ],
    ) -> OpenVSPResponse:
        return await run_async(preview_model, request)

    @app.tool(
        name="openvsp.preflight",
        description=(
            "Check selected thick/thin geometry sets before a VSPAERO run; rejects missing, "
            "empty or overlapping sets and reports reference/unit warnings. Runs OpenVSP on a "
            "private copy and retains run artifacts without replacing the source or executing "
            "VSPAERO. Does not assess intersections, mesh quality or aerodynamic accuracy; "
            "use openvsp.run_vspaero for coefficients and verified solver-file settings."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
        ),
    )
    async def preflight(
        request: Annotated[
            OpenVSPRequest,
            Field(description="Model and analysis settings to check without solving."),
        ],
    ) -> OpenVSPResponse:
        return await run_async(preflight_model, request)

    @app.tool(
        name="openvsp.run_vspaero",
        description=(
            "Run one steady subsonic VSPAERO condition after geometry-set preflight. "
            "Runs native processes on a private model copy, retaining solver artifacts, "
            "coefficients and verified settings without replacing the source; the native "
            "timeout excludes preparation, CPU admission and validation. "
            "Failures retain run/log paths, and success does not certify convergence; use "
            "openvsp.sweep for sequential conditions or openvsp.batch_submit for independent jobs."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
        ),
    )
    async def solve(
        request: Annotated[
            OpenVSPRequest,
            Field(description="Model, optional private-copy edits and one flight condition."),
        ],
    ) -> OpenVSPResponse:
        return await run_async(execute_openvsp, request, operation="run_vspaero")

    @app.tool(
        name="openvsp.sweep",
        description=(
            "Solve 1-25 explicit steady conditions sequentially using one source snapshot and "
            "shared optional commands; each condition has its own complete analysis settings. "
            "Runs OpenVSP/VSPAERO and retains per-condition artifacts plus sweep.json without "
            "replacing the source; timeout_seconds is the whole-sweep budget and failures "
            "retain partial results. Use openvsp.batch_submit for durable independent cases, "
            "per-case parameter edits and bounded parallel execution."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
        ),
    )
    async def sweep(
        request: Annotated[
            SweepRequest,
            Field(description="Shared source, explicit conditions and total sweep budget."),
        ],
    ) -> dict[str, Any]:
        return await run_async(run_sweep, request)

    @app.tool(
        name="openvsp.query",
        description=(
            "List installed analyses, read analysis input types/defaults, or read paginated "
            "geometry parameter IDs, values and limits. Launches OpenVSP using temporary "
            "files and an optional model snapshot, preserving the source; model-free "
            "capability lists may use a five-minute cache. Native defaults may depend on "
            "the model and do not imply wrapper execution support; use openvsp.inspect "
            "for XML-only inspection and openvsp.set_parameters to apply returned IDs."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def query(
        request: Annotated[
            QueryRequest,
            Field(description="Native query kind, optional model and parameter pagination."),
        ],
    ) -> dict[str, Any]:
        return await run_async(query_model, request)

    @app.tool(
        name="openvsp.set_parameters",
        description=(
            "Apply typed parameter ID/value edits obtained from openvsp.query in one OpenVSP "
            "load/update. Verifies limits and final readback, retains run artifacts and "
            "replaces the source after validation; rejects concurrent source changes. "
            "No VSPAERO solve; use openvsp.modify when trusted AngelScript commands are needed."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
        ),
    )
    async def parameters(
        request: Annotated[
            ParameterEditRequest,
            Field(description="Source model and typed edits that replace it after validation."),
        ],
    ) -> OpenVSPResponse:
        return await run_async(set_parameters, request)

    @app.tool(
        name="openvsp.read_results",
        description=(
            "Read a saved operation manifest's coefficients, settings, diagnostics and optional "
            "bounded log tail without launching OpenVSP/VSPAERO or writing files. Pass an "
            "operation's manifest_path as manifest_file; missing coefficient names or invalid "
            "manifests fail. Use openvsp.batch_status or openvsp.batch_export for batch.json, "
            "which is not an operation manifest."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def results(
        request: Annotated[
            ResultRequest,
            Field(description="Saved operation manifest, coefficient selection and log bounds."),
        ],
    ) -> dict[str, Any]:
        return await run_async(read_results, request)

    @app.tool(
        name="openvsp.batch_submit",
        description=(
            "Submit 1-1000 independent steady cases with per-case settings/parameter edits and "
            "bounded parallel jobs/CPU use. Creates a durable batch directory and private "
            "model snapshots, then runs OpenVSP/VSPAERO in background workers while preserving "
            "the source; returns an initial status without waiting for completion. "
            "No automatic retry; use openvsp.batch_status, openvsp.batch_cancel, "
            "openvsp.batch_resume and openvsp.batch_export for lifecycle control, or "
            "openvsp.sweep for a blocking sequential conditions list."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def batch_submit(
        request: Annotated[
            BatchRequest,
            Field(description="Shared model, independent cases and batch resource limits."),
        ],
    ) -> dict[str, Any]:
        return await run_control(submit_batch, request)

    @app.tool(
        name="openvsp.batch_status",
        description=(
            "Read batch status, counts, resource limits and a paginated case summary, including "
            "after restart. Opens or creates .batch.lock to check runner ownership, but does "
            "not rewrite batch.json or run a solver. Reports interrupted when a saved running "
            "batch has no owner; use openvsp.batch_resume explicitly to retry it."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def status(
        request: Annotated[
            BatchStatusRequest, Field(description="Existing batch directory and case-page bounds.")
        ],
    ) -> dict[str, Any]:
        return await run_control(batch_status, request)

    @app.tool(
        name="openvsp.batch_cancel",
        description=(
            "Request cancellation of selected case IDs, or the whole batch when case_ids is "
            "empty. Updates live job state and signals active native work; returns current "
            "status, so poll openvsp.batch_status for cleanup completion. Preserves successful "
            "results and does not delete artifacts; a batch owned by another server must be "
            "cancelled through that owner, and retry requires openvsp.batch_resume."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
        ),
    )
    async def cancel(
        request: Annotated[
            BatchCancelRequest, Field(description="Existing batch and optional cases to cancel.")
        ],
    ) -> dict[str, Any]:
        return await run_control(cancel_batch, request)

    @app.tool(
        name="openvsp.batch_resume",
        description=(
            "Explicitly retry selected non-successful cases, or all when case_ids is empty, "
            "in an idle batch. Verifies source/snapshot, package and native binary identities, "
            "specification and successful artifacts before updating job state and launching "
            "background work; successful cases are reused, never rerun. Identity changes "
            "including metadata-only package updates are rejected; use the original runtime "
            "to resume or openvsp.batch_submit for a new batch."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
        ),
    )
    async def resume(
        request: Annotated[
            BatchResumeRequest,
            Field(description="Idle batch and optional non-successful cases to retry."),
        ],
    ) -> dict[str, Any]:
        return await run_control(resume_batch, request)

    @app.tool(
        name="openvsp.batch_export",
        description=(
            "Export a saved batch manifest snapshot, including inputs, coefficients and "
            "pending/failed rows, to results.csv and results.json. Creates a new export "
            "directory inside the batch on every call without running a solver, rewriting "
            "batch.json or changing existing artifacts. Use openvsp.batch_status for a "
            "paginated progress summary or openvsp.read_results for one operation manifest."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def export(
        request: Annotated[
            BatchExportRequest,
            Field(description="Existing batch directory to export without solving."),
        ],
    ) -> dict[str, Any]:
        return await run_control(export_batch, request)
