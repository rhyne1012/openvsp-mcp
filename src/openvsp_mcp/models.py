"""Typed request/response models for OpenVSP MCP calls."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VSPCommand(BaseModel):
    command: str = Field(
        ...,
        description="Trusted AngelScript statement executed with server-process privileges. May perform arbitrary file or external I/O; private-copy/source-preservation guarantees do not constrain the statement itself.",
    )


class OpenVSPGeometryRequest(BaseModel):
    geometry_file: str = Field(
        ...,
        description="Path to an existing .vsp3 file, read as XML without native processes. Tilde expands; relative paths use the server working directory.",
    )


class VSPAeroSettings(BaseModel):
    """One steady flight condition; geometry and dimensional inputs must use consistent units."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    thick_geom_set: int = Field(
        -1,
        ge=-1,
        description="OpenVSP set index for thick surfaces; -1 disables thick surfaces (default). Selected sets must exist, be nonempty and share no geometry with the thin set.",
    )
    thin_geom_set: int = Field(
        0,
        ge=-1,
        description="OpenVSP set index for thin surfaces; 0 selects all geometry by default, -1 disables thin surfaces. Must differ from the thick set; at least one surface type must be selected.",
    )
    mach: float = Field(
        0.1,
        ge=0,
        lt=1,
        description="Dimensionless Mach number for this steady subsonic case (0 <= Mach < 1). Independent of vinf, rho and reynolds; no atmosphere is derived.",
    )
    alpha: float = Field(
        3.0,
        description="Angle of attack in degrees, passed to VSPAERO AlphaStart/AlphaEnd for one condition.",
    )
    beta: float = Field(
        0.0,
        description="Sideslip angle in degrees, passed to VSPAERO BetaStart/BetaEnd for one condition.",
    )
    sref: float = Field(
        1.0,
        gt=0,
        description="Reference area in squared model length units; passed unchanged as Sref. Supply a model-specific value; the default is 1.",
    )
    bref: float = Field(
        1.0,
        gt=0,
        description="Reference span in model length units; passed unchanged as bref. Supply a model-specific value; the default is 1.",
    )
    cref: float = Field(
        1.0,
        gt=0,
        description="Reference chord in model length units; passed unchanged as cref. Also identifies the chord basis of reynolds.",
    )
    vinf: float = Field(
        34.03,
        gt=0,
        description="Freestream speed in length/time units consistent with the model and rho (e.g. m/s in SI). Passed unchanged; not computed from Mach and not converted by length_unit.",
    )
    rho: float = Field(
        1.225,
        gt=0,
        description="Freestream mass density in mass/volume units consistent with geometry and vinf (e.g. kg/m^3 in SI). Passed unchanged; no atmosphere or unit conversion.",
    )
    reynolds: float = Field(
        2900000.0,
        gt=0,
        description="Dimensionless Reynolds number based on cref, passed as ReCref (not in millions). Independent of Mach, vinf and rho.",
    )
    xcg: float = Field(
        0.0,
        description="X coordinate of the VSPAERO moment reference in model length units and model coordinates; passed unchanged as Xcg.",
    )
    ycg: float = Field(
        0.0,
        description="Y coordinate of the VSPAERO moment reference in model length units and model coordinates; passed unchanged as Ycg.",
    )
    zcg: float = Field(
        0.0,
        description="Z coordinate of the VSPAERO moment reference in model length units and model coordinates; passed unchanged as Zcg.",
    )
    ncpu: int = Field(
        4,
        ge=1,
        le=255,
        description="Requested native CPU threads per solve, 1-255 (default 4). Must fit the server CPU budget and, for batch cases, the batch cpu_budget.",
    )
    wake_iterations: int = Field(
        30,
        ge=3,
        le=255,
        description="Requested WakeNumIter, 3-255 (default 30). When fixed_wake is true, the verified solver file instead has WakeIters=0.",
    )
    wake_nodes: int = Field(
        32,
        ge=4,
        le=1024,
        description="Number of wake nodes passed as NumWakeNodes, 4-1024 (default 32).",
    )
    fixed_wake: bool = Field(
        False,
        description="Use the official FixedWakeFlag; true requires WakeIters=0 in the generated solver file. Does not certify convergence.",
    )
    forward_gmres_tolerance_factor: float = Field(
        1.0,
        gt=0,
        le=1000000000000.0,
        description="Positive factor passed unchanged as ForwardGMRESConvergenceFactor, at most 1e12 (default 1); not an absolute residual tolerance.",
    )
    length_unit: Literal["unspecified", "m", "ft"] = Field(
        "unspecified",
        description="Documentation only: m, ft or unspecified (default). Does not scale geometry or convert any input; all dimensional quantities must already be consistent.",
    )

    @model_validator(mode="after")
    def disjoint_sets(self):
        if self.thick_geom_set == self.thin_geom_set:
            raise ValueError("Thick and thin sets must differ; at least one must be selected")
        return self


class OpenVSPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_file: str = Field(
        ...,
        description="Existing .vsp3 path; tilde expands and relative paths use the server working directory. modify replaces this source after validation; preview, preflight and run_vspaero use private copies.",
    )
    set_commands: list[VSPCommand] = Field(
        default_factory=list,
        description="Ordered trusted AngelScript statements applied to the snapshot before parameter_edits and Update. Defaults to none; statements may perform their own I/O with server privileges.",
    )
    run_vspaero: bool = Field(
        True,
        description="Legacy shared-request selector (default true). MCP modify/preview/preflight/run_vspaero force their named operation regardless of this value; it selects solve versus modify only when execute_openvsp has no explicit operation.",
    )
    case_name: str = Field(
        "case",
        pattern="^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description="Run-directory prefix and artifact filename stem, not a path. Defaults to case; use output_dir to choose the parent directory.",
    )
    output_dir: str | None = Field(
        None,
        description="Parent for a unique persistent run directory. Omitted/null uses openvsp_runs beside the resolved source; tilde and relative paths resolve on the server. Existing run directories are not reused.",
    )
    timeout_seconds: int = Field(
        600,
        ge=1,
        le=86400,
        description="Native OpenVSP process timeout in seconds (default 600), including an invoked solver. Preparation, CPU admission and validation are outside this per-call native timeout.",
    )
    analysis: VSPAeroSettings = Field(
        default_factory=VSPAeroSettings,
        description="One complete flight/solver settings object. Defaults do not come from the model; used by preflight and run_vspaero, ignored by modify/preview.",
    )
    parameter_edits: list[ParameterEdit] = Field(
        default_factory=list,
        max_length=200,
        description="Parameter ID/value edits applied after set_commands, before one Update; defaults to none. Limits and final values are verified; obtain IDs with openvsp.query.",
    )


class OpenVSPInspectResponse(BaseModel):
    geom_ids: list[str] = Field(
        description="Component IDs found in Vehicle/Geom XML entries, in file order."
    )
    wing_names: list[str] = Field(
        default_factory=list,
        description="Names of components whose XML type is Wing; empty when none exist.",
    )
    info_log: str = Field(
        description="Newline-separated component summaries in ID:name:type form; no solver log."
    )


class OpenVSPResponse(BaseModel):
    script_path: str = Field(
        description="Absolute path to the retained AngelScript automation script."
    )
    result_path: str | None = Field(
        None,
        description="Absolute path to the solver .adb result; null for operations without a VSPAERO solve.",
    )
    geometry_path: str = Field(
        description="Absolute path to the validated model saved in the run directory, including for source-replacing edits."
    )
    run_directory: str = Field(
        description="Absolute path to the unique persistent directory containing this operation's artifacts."
    )
    log_path: str = Field(description="Absolute path to the retained OpenVSP process log.")
    manifest_path: str = Field(
        description="Absolute path to this operation's manifest.json; pass as manifest_file to openvsp.read_results."
    )
    artifacts: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of run-relative artifact names to absolute file paths; files remain after the call.",
    )
    coefficients: dict[str, float] = Field(
        default_factory=dict,
        description="Native polar column names and unscaled numeric values for a verified solve; empty otherwise. Includes condition columns as well as dimensionless aerodynamic coefficients; AoA/Beta are degrees and Re/1e6 is Reynolds in millions.",
    )
    analysis_inputs: dict = Field(
        default_factory=dict,
        description="Requested analysis settings for solve/preflight, including defaults; empty for other operations. Units follow VSPAeroSettings and no conversion is applied.",
    )
    operation: str = Field(
        "run_vspaero",
        description="Executed operation: create, modify, preview, preflight or run_vspaero; reflects the enforced operation rather than the legacy input flag.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Reference/unit/atmospheric consistency warnings from existing checks; no automatic corrections.",
    )
    preflight: dict = Field(
        default_factory=dict,
        description="Selected thick/thin geometry IDs and requested settings when preflight ran; empty otherwise. Does not certify mesh quality.",
    )
    numerical_quality: dict = Field(
        default_factory=dict,
        description="Observed iteration-history diagnostics for a solve; convergence is not_assessed and mesh study not_performed. Empty for non-solver operations.",
    )
    versions: dict = Field(
        default_factory=dict,
        description="Package version, MCP SDK version and package SHA-256, with observed native versions when available. Package identity changes on source/metadata updates.",
    )
    effective_settings: dict = Field(
        default_factory=dict,
        description="Verified solver-file values and observed CPU-thread information for a solve; empty before solving. Separate from requested analysis_inputs.",
    )
    parameter_values: dict[str, float] = Field(
        default_factory=dict,
        description="Final native readback values keyed by edited parameter ID; native units, empty when no parameters were edited.",
    )
    timings: dict[str, float] = Field(
        default_factory=dict,
        description="Measured operation phase durations in seconds; available phase keys depend on execution.",
    )


class CreateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_dir: str = Field(
        description="Required parent directory for a new unique persistent run. Tilde expands and relative paths use the server working directory; no input model is needed."
    )
    case_name: str = Field(
        "aircraft",
        pattern="^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description="Run-directory prefix and model filename stem (default aircraft), not a filesystem path.",
    )
    template: Literal["simple_aircraft", "custom"] = Field(
        "simple_aircraft",
        description="simple_aircraft creates a fuselage, main wing and horizontal/vertical tails; custom starts empty and requires set_commands that add geometry.",
    )
    set_commands: list[VSPCommand] = Field(
        default_factory=list,
        description="Trusted AngelScript statements appended after template commands, or used to build a custom model. May perform arbitrary I/O with server privileges; defaults to none.",
    )
    timeout_seconds: int = Field(
        120,
        ge=1,
        le=600,
        description="Native OpenVSP process timeout in seconds (default 120); preparation, resource admission and validation are outside this timeout.",
    )

    @model_validator(mode="after")
    def custom_has_commands(self):
        if self.template == "custom" and not self.set_commands:
            raise ValueError("Custom creation requires set_commands adding geometry")
        return self


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_file: str = Field(
        description="Existing .vsp3 path snapshotted once for all conditions; the wrapper preserves the source. Tilde expands and relative paths use the server working directory."
    )
    set_commands: list[VSPCommand] = Field(
        default_factory=list,
        description="Same ordered trusted AngelScript statements applied independently to each condition's private model. Defaults to none; statements may perform their own I/O.",
    )
    case_name: str = Field(
        "sweep",
        pattern="^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description="Sweep-directory prefix (default sweep); each condition uses point_000, point_001, etc. as its artifact stem.",
    )
    output_dir: str | None = Field(
        None,
        description="Parent for a unique persistent sweep directory; omitted/null uses openvsp_runs beside the resolved source. Tilde and relative paths resolve on the server.",
    )
    timeout_seconds: int = Field(
        600,
        ge=1,
        le=86400,
        description="Total sweep budget in seconds (default 600), shared across conditions and resource waits; completed results remain if the budget is exhausted.",
    )
    conditions: list[VSPAeroSettings] = Field(
        min_length=1,
        max_length=25,
        description="1-25 explicit, complete steady analysis settings, executed sequentially. Omitted fields take VSPAeroSettings defaults independently; settings never carry over from previous entries.",
    )


class ParameterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    parm_id: str = Field(
        min_length=1,
        description="Native parameter ID obtained from openvsp.query for the target model; not a display name.",
    )
    value: float = Field(
        description="Requested finite value in the native units of that parameter, without conversion. Native limits and post-Update readback must accept the value."
    )


class ParameterEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_file: str = Field(
        description="Existing .vsp3 source to replace only after validating all edits and checking for concurrent source changes. Tilde and relative paths resolve on the server."
    )
    edits: list[ParameterEdit] = Field(
        min_length=1,
        max_length=200,
        description="1-200 unique parameter ID/value edits applied in one load/update. Obtain IDs with openvsp.query; duplicate IDs are rejected.",
    )
    output_dir: str | None = Field(
        None,
        description="Parent for a unique persistent validation/artifact run; omitted/null uses openvsp_runs beside the source. The validated output also replaces geometry_file.",
    )
    timeout_seconds: int = Field(
        120,
        ge=1,
        le=600,
        description="Native OpenVSP process timeout in seconds (default 120); excludes preparation, CPU admission and validation.",
    )

    @model_validator(mode="after")
    def unique_ids(self):
        if len({e.parm_id for e in self.edits}) != len(self.edits):
            raise ValueError("Duplicate parameter IDs are not allowed")
        return self


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["capabilities", "analysis", "parameters"] = Field(
        "capabilities",
        description="capabilities lists installed analyses; analysis reads native input types/defaults; parameters reads paginated parameter IDs/values/limits and requires geometry_file.",
    )
    geometry_file: str | None = Field(
        None,
        description="Optional existing .vsp3 path copied to a temporary model; required for parameters. Without a model, analysis defaults come from an empty model; tilde and relative paths resolve on the server.",
    )
    analysis_name: str = Field(
        "VSPAEROSweep",
        description="Installed analysis name queried only for kind=analysis (default VSPAEROSweep). Enumerated analyses are not necessarily executable through this wrapper.",
    )
    geom_id: str | None = Field(
        None,
        description="For kind=parameters, optionally select one geometry component when parm_ids is empty. Ignored when explicit parm_ids are supplied.",
    )
    parm_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="Explicit parameter IDs for kind=parameters; defaults to enumeration from geom_id or all components. Takes precedence over geom_id; offset/limit also apply to this list.",
    )
    offset: int = Field(
        0,
        ge=0,
        description="Zero-based offset into parameter results (default 0); used only for kind=parameters.",
    )
    limit: int = Field(
        100,
        ge=1,
        le=200,
        description="Maximum parameter entries returned, 1-200 (default 100); used only for kind=parameters.",
    )
    timeout_seconds: int = Field(
        30,
        ge=1,
        le=120,
        description="Native query-process timeout in seconds (default 30). Model-free capability cache hits do not launch OpenVSP; temporary files are removed after the query.",
    )

    @model_validator(mode="after")
    def parameter_source(self):
        if self.kind == "parameters" and not self.geometry_file:
            raise ValueError("Parameter queries require geometry_file")
        return self


class ResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_file: str = Field(
        description="Path to a saved operation manifest.json, as returned in manifest_path; not sweep.json or batch.json. Tilde and relative paths resolve on the server; maximum read size is 4 MiB."
    )
    coefficient_names: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="Exact saved polar column names to return; empty returns all saved entries. Unknown names fail; no coefficient conversion or recomputation.",
    )
    log: Literal["none", "openvsp", "solver"] = Field(
        "none",
        description="none omits logs (default); openvsp reads openvsp.log; solver reads solver.log beside the manifest. Missing requested logs fail.",
    )
    log_tail_lines: int = Field(
        40,
        ge=1,
        le=200,
        description="Maximum trailing log lines, 1-200 (default 40). Only the final 64 KiB is read, so fewer complete lines may be returned; unused when log=none.",
    )


OpenVSPRequest.model_rebuild()


class BatchCase(BaseModel):
    """An independent steady solve; edits affect only this case's private snapshot."""

    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(
        pattern="^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description="Unique case identifier and artifact filename stem within the batch; must match the schema pattern.",
    )
    analysis: VSPAeroSettings = Field(
        default_factory=VSPAeroSettings,
        description="Complete settings for this independent steady solve; omitted fields use VSPAeroSettings defaults. ncpu must fit both batch and server budgets.",
    )
    parameter_edits: list[ParameterEdit] = Field(
        default_factory=list,
        max_length=200,
        description="Unique parameter ID/value edits on this case's private model copy; defaults to none. Limits and readback are verified; source model is preserved.",
    )
    timeout_seconds: int = Field(
        600,
        ge=1,
        le=86400,
        description="Native OpenVSP/VSPAERO process timeout for this case in seconds (default 600); not an overall batch deadline and excludes resource waiting.",
    )

    @model_validator(mode="after")
    def unique_parameters(self):
        if len({e.parm_id for e in self.parameter_edits}) != len(self.parameter_edits):
            raise ValueError("Duplicate parameter IDs are not allowed")
        return self


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_file: str = Field(
        description="Existing nonempty .vsp3 model to snapshot for all cases; source is preserved and hashed for resume checks. Tilde and relative paths resolve on the server."
    )
    output_dir: str | None = Field(
        None,
        description="Parent for a new unique persistent batch directory; omitted/null uses openvsp_runs beside the resolved source. Existing batches are not overwritten.",
    )
    cases: list[BatchCase] = Field(
        min_length=1,
        max_length=1000,
        description="1-1000 independent steady cases with unique case_id values; each has its own settings and optional parameter edits.",
    )
    max_parallel_jobs: int = Field(
        1,
        ge=1,
        le=16,
        description="Maximum concurrent case workers in this batch, 1-16 (default 1). Effective concurrency is also limited by CPU budgets and each case's ncpu.",
    )
    cpu_budget: int = Field(
        4,
        ge=1,
        le=255,
        description="Maximum sum of admitted case CPU threads for this batch, 1-255 (default 4). Each case ncpu must fit; must not exceed the shared per-server OPENVSP_CPU_BUDGET.",
    )
    failure_policy: Literal["stop", "continue"] = Field(
        "stop",
        description="stop (default) stops scheduling queued cases after a failure while admitted work may finish; continue attempts remaining cases. Neither policy automatically retries failures.",
    )

    @model_validator(mode="after")
    def valid_cases(self):
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case IDs are not allowed")
        if any(c.analysis.ncpu > self.cpu_budget for c in self.cases):
            raise ValueError("Each case's ncpu must fit within cpu_budget")
        return self


class BatchStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_directory: str = Field(
        description="Existing batch directory returned by batch_submit; it must remain at its original absolute location. Tilde expands; batch.json and the ownership lock are read there."
    )
    offset: int = Field(
        0,
        ge=0,
        description="Zero-based offset into the ordered case summary (default 0); counts always cover the entire batch.",
    )
    limit: int = Field(
        50,
        ge=1,
        le=100,
        description="Maximum case summaries to return, 1-100 (default 50); aggregate status/counts are not paginated.",
    )


class BatchCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_directory: str = Field(
        description="Existing batch directory at its original absolute location. Cancel active work through its owner; resume requires an idle batch with unchanged source/package/binary identities. Tilde and relative paths resolve on the server."
    )
    case_ids: list[str] = Field(
        default_factory=list,
        max_length=1000,
        description="Selected case IDs. Empty (default) means the whole batch for batch_cancel, or every non-successful case for batch_resume. Unknown IDs fail; successful results are preserved.",
    )


class BatchResumeRequest(BatchCancelRequest):
    """Empty case_ids retries every non-successful case; successes are verified and reused."""


class BatchExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_directory: str = Field(
        description="Existing batch directory at its original absolute location. A fresh export directory containing CSV/JSON is added here on every call; no solver runs."
    )
