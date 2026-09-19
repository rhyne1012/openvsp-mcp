"""Typed request/response models for OpenVSP MCP calls."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VSPCommand(BaseModel):
    command: str = Field(
        ..., description="Trusted AngelScript statement; runs with server privileges"
    )


class OpenVSPGeometryRequest(BaseModel):
    geometry_file: str = Field(..., description="Path to the .vsp3 file")


class VSPAeroSettings(BaseModel):
    """One steady flight condition; geometry and dimensional inputs must use consistent units."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    thick_geom_set: int = Field(-1, ge=-1)
    thin_geom_set: int = Field(0, ge=-1)
    mach: float = Field(0.1, ge=0, lt=1)
    alpha: float = 3.0
    beta: float = 0.0
    sref: float = Field(1.0, gt=0)
    bref: float = Field(1.0, gt=0)
    cref: float = Field(1.0, gt=0)
    vinf: float = Field(34.03, gt=0)
    rho: float = Field(1.225, gt=0)
    reynolds: float = Field(2.9e6, gt=0)
    xcg: float = 0.0
    ycg: float = 0.0
    zcg: float = 0.0
    ncpu: int = Field(4, ge=1, le=256)
    wake_iterations: int = Field(30, ge=1, le=1000)
    wake_nodes: int = Field(32, ge=4, le=1024)
    length_unit: Literal["unspecified", "m", "ft"] = "unspecified"

    @model_validator(mode="after")
    def disjoint_sets(self):
        if self.thick_geom_set == self.thin_geom_set:
            raise ValueError("Thick and thin sets must differ; at least one must be selected")
        return self


class OpenVSPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_file: str = Field(
        ..., description="Existing .vsp3 input; run_vspaero leaves it unchanged"
    )
    set_commands: list[VSPCommand] = Field(default_factory=list)
    run_vspaero: bool = True
    case_name: str = Field("case", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    output_dir: str | None = Field(None, description="Parent directory for unique, persistent runs")
    timeout_seconds: int = Field(600, ge=1, le=86400)
    analysis: VSPAeroSettings = Field(default_factory=VSPAeroSettings)


class OpenVSPInspectResponse(BaseModel):
    geom_ids: list[str]
    wing_names: list[str] = Field(default_factory=list)
    info_log: str


class OpenVSPResponse(BaseModel):
    script_path: str
    result_path: str | None = None
    geometry_path: str
    run_directory: str
    log_path: str
    manifest_path: str
    artifacts: dict[str, str] = Field(default_factory=dict)
    coefficients: dict[str, float] = Field(default_factory=dict)
    analysis_inputs: dict = Field(default_factory=dict)
    operation: str = "run_vspaero"
    warnings: list[str] = Field(default_factory=list)
    preflight: dict = Field(default_factory=dict)
    numerical_quality: dict = Field(default_factory=dict)
    versions: dict = Field(default_factory=dict)


class CreateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_dir: str
    case_name: str = Field("aircraft", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    template: Literal["simple_aircraft", "custom"] = "simple_aircraft"
    set_commands: list[VSPCommand] = Field(default_factory=list)
    timeout_seconds: int = Field(120, ge=1, le=600)

    @model_validator(mode="after")
    def custom_has_commands(self):
        if self.template == "custom" and not self.set_commands:
            raise ValueError("Custom creation requires set_commands adding geometry")
        return self


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_file: str
    set_commands: list[VSPCommand] = Field(default_factory=list)
    case_name: str = Field("sweep", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    output_dir: str | None = None
    timeout_seconds: int = Field(600, ge=1, le=86400)
    conditions: list[VSPAeroSettings] = Field(min_length=1, max_length=25)
