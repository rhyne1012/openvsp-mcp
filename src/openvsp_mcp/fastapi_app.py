"""Factory for the OpenVSP FastAPI surface."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response

from .core import execute_openvsp
from .describe import describe_geometry
from .health import health_check
from .models import (
    CreateModelRequest,
    OpenVSPGeometryRequest,
    OpenVSPInspectResponse,
    OpenVSPRequest,
    OpenVSPResponse,
    SweepRequest,
)
from .version import __version__
from .workflows import create_model, preflight_model, preview_model, run_sweep


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenVSP MCP Service",
        version=__version__,
        description="Inspect and automate OpenVSP geometry edits, with optional VSPAero runs.",
    )

    @app.post("/vsp/inspect", response_model=OpenVSPInspectResponse)
    def inspect(request: OpenVSPGeometryRequest) -> OpenVSPInspectResponse:
        try:
            return describe_geometry(request.geometry_file)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/modify", response_model=OpenVSPResponse)
    def modify(request: OpenVSPRequest) -> OpenVSPResponse:
        try:
            return execute_openvsp(request.model_copy(update={"run_vspaero": False}))
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/run", response_model=OpenVSPResponse)
    def run_vspaero(request: OpenVSPRequest) -> OpenVSPResponse:
        try:
            return execute_openvsp(request.model_copy(update={"run_vspaero": True}))
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/health")
    def health(response: Response) -> dict:
        result = health_check()
        response.status_code = 200 if result["status"] == "ok" else 503
        return result

    @app.post("/vsp/create", response_model=OpenVSPResponse)
    def create(request: CreateModelRequest):
        try:
            return create_model(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/preview", response_model=OpenVSPResponse)
    def preview(request: OpenVSPRequest):
        try:
            return preview_model(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/preflight", response_model=OpenVSPResponse)
    def preflight(request: OpenVSPRequest):
        try:
            return preflight_model(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/vsp/sweep")
    def sweep(request: SweepRequest):
        try:
            return run_sweep(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


app = create_app()

__all__ = ["app", "create_app"]
