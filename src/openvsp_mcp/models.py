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
    ncpu: int = Field(4, ge=1, le=255)
    wake_iterations: int = Field(30, ge=3, le=255)
    wake_nodes: int = Field(32, ge=4, le=1024)
    fixed_wake: bool = False
    forward_gmres_tolerance_factor: float = Field(1.0, gt=0, le=1e12)
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
    parameter_edits: list[ParameterEdit] = Field(default_factory=list, max_length=200)


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
    effective_settings: dict = Field(default_factory=dict)
    parameter_values: dict[str, float] = Field(default_factory=dict)
    timings: dict[str, float] = Field(default_factory=dict)


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


class ParameterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    parm_id: str = Field(min_length=1)
    value: float


class ParameterEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_file: str
    edits: list[ParameterEdit] = Field(min_length=1, max_length=200)
    output_dir: str | None = None
    timeout_seconds: int = Field(120, ge=1, le=600)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({e.parm_id for e in self.edits}) != len(self.edits):
            raise ValueError("Duplicate parameter IDs are not allowed")
        return self


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["capabilities", "analysis", "parameters"] = "capabilities"
    geometry_file: str | None = None
    analysis_name: str = "VSPAEROSweep"
    geom_id: str | None = None
    parm_ids: list[str] = Field(default_factory=list, max_length=200)
    offset: int = Field(0, ge=0)
    limit: int = Field(100, ge=1, le=200)
    timeout_seconds: int = Field(30, ge=1, le=120)

    @model_validator(mode="after")
    def parameter_source(self):
        if self.kind == "parameters" and not self.geometry_file:
            raise ValueError("Parameter queries require geometry_file")
        return self


class ResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_file: str
    coefficient_names: list[str] = Field(default_factory=list, max_length=200)
    log: Literal["none", "openvsp", "solver"] = "none"
    log_tail_lines: int = Field(40, ge=1, le=200)


OpenVSPRequest.model_rebuild()


class BatchCase(BaseModel):
    """An independent steady solve; edits affect only this case's private snapshot."""

    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    analysis: VSPAeroSettings = Field(default_factory=VSPAeroSettings)
    parameter_edits: list[ParameterEdit] = Field(default_factory=list, max_length=200)
    timeout_seconds: int = Field(600, ge=1, le=86400)

    @model_validator(mode="after")
    def unique_parameters(self):
        if len({e.parm_id for e in self.parameter_edits}) != len(self.parameter_edits):
            raise ValueError("Duplicate parameter IDs are not allowed")
        return self


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_file: str
    output_dir: str | None = None
    cases: list[BatchCase] = Field(min_length=1, max_length=1000)
    max_parallel_jobs: int = Field(1, ge=1, le=16)
    cpu_budget: int = Field(4, ge=1, le=255)
    failure_policy: Literal["stop", "continue"] = "stop"

    @model_validator(mode="after")
    def valid_cases(self):
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case IDs are not allowed")
        if any(c.analysis.ncpu > self.cpu_budget for c in self.cases):
            raise ValueError("Each case's ncpu must fit within cpu_budget")
        return self


class BatchStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_directory: str
    offset: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=100)


class BatchCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_directory: str
    case_ids: list[str] = Field(default_factory=list, max_length=1000)


class BatchResumeRequest(BatchCancelRequest):
    """Empty case_ids retries every non-successful case; successes are verified and reused."""


class BatchExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_directory: str
