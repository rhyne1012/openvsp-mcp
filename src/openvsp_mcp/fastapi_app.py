"""Factory for the OpenVSP FastAPI surface."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response

from .batch import (
    batch_lifespan,
    batch_status,
    cancel_batch,
    export_batch,
    resume_batch,
    submit_batch,
)
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
from .version import __version__
from .workflows import create_model, preflight_model, preview_model, run_sweep


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenVSP MCP Service",
        version=__version__,
        description="Inspect and automate OpenVSP geometry edits, with optional VSPAero runs.",
        lifespan=batch_lifespan,
    )

    @app.post("/vsp/inspect", response_model=OpenVSPInspectResponse)
    async def inspect(request: OpenVSPGeometryRequest) -> OpenVSPInspectResponse:
        try:
            return await run_async(describe_geometry, request.geometry_file)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/modify", response_model=OpenVSPResponse)
    async def modify(request: OpenVSPRequest) -> OpenVSPResponse:
        try:
            return await run_async(execute_openvsp, request, operation="modify")
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/run", response_model=OpenVSPResponse)
    async def run_vspaero(request: OpenVSPRequest) -> OpenVSPResponse:
        try:
            return await run_async(execute_openvsp, request, operation="run_vspaero")
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/health")
    async def health(response: Response) -> dict:
        result = await run_async(health_check)
        response.status_code = 200 if result["status"] == "ok" else 503
        return result

    @app.post("/vsp/create", response_model=OpenVSPResponse)
    async def create(request: CreateModelRequest):
        try:
            return await run_async(create_model, request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/preview", response_model=OpenVSPResponse)
    async def preview(request: OpenVSPRequest):
        try:
            return await run_async(preview_model, request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/preflight", response_model=OpenVSPResponse)
    async def preflight(request: OpenVSPRequest):
        try:
            return await run_async(preflight_model, request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/sweep")
    async def sweep(request: SweepRequest):
        try:
            return await run_async(run_sweep, request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/query")
    async def query(request: QueryRequest):
        try:
            return await run_async(query_model, request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/parameters", response_model=OpenVSPResponse)
    async def parameters(request: ParameterEditRequest):
        try:
            return await run_async(set_parameters, request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/results")
    async def results(request: ResultRequest):
        try:
            return await run_async(read_results, request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def control(function, request):
        try:
            return await run_control(function, request)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/vsp/batch/submit")
    async def batch_submit(request: BatchRequest):
        return await control(submit_batch, request)

    @app.post("/vsp/batch/status")
    async def status(request: BatchStatusRequest):
        return await control(batch_status, request)

    @app.post("/vsp/batch/cancel")
    async def cancel(request: BatchCancelRequest):
        return await control(cancel_batch, request)

    @app.post("/vsp/batch/resume")
    async def resume(request: BatchResumeRequest):
        return await control(resume_batch, request)

    @app.post("/vsp/batch/export")
    async def export(request: BatchExportRequest):
        return await control(export_batch, request)

    return app


app = create_app()

__all__ = ["app", "create_app"]
