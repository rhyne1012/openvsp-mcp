# 0.9.0 API and MCP maintenance audit

This is a source compatibility audit of the wrapper's supported operations,
reviewed on 2026-10-25. The latest official release is OpenVSP 3.55.0, announced
on [2026-10-12](https://openvsp.org/blogs/announcements/2026/10/12/openvsp-3-55-0-released).
The [tagged changelog](https://github.com/OpenVSP/OpenVSP/blob/OpenVSP_3.55.0/CHANGELOG.md)
dates that release 2026-10-11; these are the announcement and changelog dates,
respectively. Source compatibility alone does not establish a tested native
build, coefficient baseline, or aerodynamic accuracy.

## Changes since the previous 3.53.1 target

[OpenVSP 3.54.0](https://openvsp.org/blogs/announcements/2026/10/06/openvsp-3-54-0-released)
added Clone geometry and geometry flipping. The wrapper's XML inspection keeps
reporting the stored component type; `wing_names` means XML components whose
type is Wing, not an inferred classification of a Clone's original. Native
parameter queries remain available for the loaded model.

The same release corrected VSPAERO's viscous-force evaluation location, control
groups and adjoint side-force derivatives, and a solver launch race. Numerical
results can therefore change even when the VSPAERO version string does not.
Keep coefficient baselines tied to both executable hashes; do not reuse an old
baseline solely because the solver reports the same version. Adjoint and
control-derivative workflows remain outside this wrapper's steady-solve scope.

3.54 also separated FEA meshing from file export (`FeaMeshAnalysis` versus
`FeaMeshExport`). Those analyses may appear in capability queries, but this
wrapper does not execute them through a dedicated typed tool.

[OpenVSP 3.55.0](https://openvsp.org/blogs/announcements/2026/10/12/openvsp-3-55-0-released)
adds ID-based API variants alongside existing calls and improves API error
reporting. It also corrects mode/set persistence and several geometry operations.
The additive API changes do not require removing the wrapper's existing tools
or rewriting its parameter-ID editing interface. The Parasite Drag, CFD/FEA
meshing and trimmed CAD improvements do not expand this release's supported
analysis or export contract.

## Audited native interface

The audit uses the immutable release tag `OpenVSP_3.55.0`, rather than the
upstream development branch:

- [ScriptMgr.cpp](https://github.com/OpenVSP/OpenVSP/blob/OpenVSP_3.55.0/src/geom_core/ScriptMgr.cpp#L3885)
  registers `GetAnalysisDoc` and `GetAnalysisInputDoc` with string return types.
  The corrected binding permits adding exactly `OpenVSP 3.55.0` to the
  analysis-description allowlist. Older unsafe and unaudited future versions
  remain guarded; no permissive version-range assumption is needed.
- [AnalysisMgr.cpp: geometry inputs](https://github.com/OpenVSP/OpenVSP/blob/OpenVSP_3.55.0/src/geom_core/AnalysisMgr.cpp#L2048)
  retains `GeomSet`, `ThinGeomSet`, and `UseModeFlag` for
  `VSPAEROComputeGeometry`.
- [AnalysisMgr.cpp: sweep inputs](https://github.com/OpenVSP/OpenVSP/blob/OpenVSP_3.55.0/src/geom_core/AnalysisMgr.cpp#L2244)
  retains the names and input handling used by `VSPAEROSweep`:
  geometry sets/mode flag; `RefFlag`, `Sref`, `bref`, `cref`; alpha, beta and
  Mach start/end/count; `ReCref`, `ReCrefEnd`, `ReCrefNpts`; `Vinf`, `Rho`;
  `Xcg`, `Ycg`, `Zcg`; `NCPU`, `UnsteadyType`; `FixedWakeFlag`, `WakeNumIter`,
  `NumWakeNodes`, `ForwardGMRESConvergenceFactor`; and `RedirectFile`.
- [VSPAEROMgr.cpp: solver-file output](https://github.com/OpenVSP/OpenVSP/blob/OpenVSP_3.55.0/src/geom_core/VSPAEROMgr.cpp#L1337)
  still writes `NumWakeNodes`, `ForwardGMRESConvergenceFactor`, and `WakeIters`
  as zero for a fixed wake or the requested iteration count otherwise.
  [The trailing stability field](https://github.com/OpenVSP/OpenVSP/blob/OpenVSP_3.55.0/src/geom_core/VSPAEROMgr.cpp#L1465)
  remains `VSP_StabilityType`. The effective-settings verifier remains applicable.

This is intentionally an audit of the interface this package uses. Capability
enumeration is not a promise to execute every upstream analysis, and generic
trusted AngelScript remains outside the typed steady-analysis guarantees.

## MCP maintenance decisions

Retain the 16 public tools and their request/typed response contracts. The
apparently overlapping tools serve different use cases: `inspect` reads XML
without starting OpenVSP; `query` reads native metadata; `set_parameters` provides
typed edits while `modify` accepts trusted scripts; `sweep` is a blocking,
sequential operation while the batch tools manage durable background jobs.
Removing these would break callers without eliminating the shared domain
implementation beneath them.

The audit found two small runtime fixes worth prioritizing: health/native
metadata probes need the same weighted CPU admission as solve operations, and
the capability-cache key needs the complete executable stat identity so a
same-size replacement with a preserved modification time cannot return stale
metadata. A positive allocation-wait deadline must keep the bounded probe
behavior, and cache hits should still avoid native work. These can be corrected
without adding tools or changing schemas.

Derive published version pairs from the validated-build registry instead of
maintaining a second literal matrix. Keep exact native hashes and platform
checks: a successful health probe establishes readiness, not completion of the
published validation workflows.

Keep FastAPI in the existing installation because the REST adapter is a
supported interface. Moving it to an optional extra could reduce dependencies
for stdio-only users, but would require an installation migration and explicit
REST/development extras; that change is deferred. MCP SSE/streamable HTTP remain
transports of the official MCP SDK, separate from `openvsp_mcp.fastapi_app`.
AnyIO, Pydantic and the MCP SDK are used by the core server. `psutil` is used by
the batch benchmark and already belongs to development dependencies, so it is
not dead runtime weight.

No persistent interpreter, broader result cache, SDK major-version migration,
or general-purpose framework rewrite is needed for this upgrade. These would
change isolation or compatibility assumptions without resolving the observed
issues.

## Regression checks

- Run a 3.55.0 analysis query and verify nonempty input descriptions; keep a
  generated-script assertion that unknown versions cannot call the guarded
  documentation binding.
- Hold the CPU pool at capacity and verify uncached queries and health probes
  wait, cancel, or exhaust their own bounded admission budget without launching
  native work. Verify file reads and batch controls remain responsive.
- Rewrite a fake capability-query executable with the same byte count, restore
  its modification time, and verify the cache misses. Keep the existing cache
  copy-isolation check.
- Retain tool-schema compatibility tests, package/wheel checks, and REST adapter
  tests with the existing dependencies installed.
- Establish native workflow evidence and coefficient repeatability separately
  before adding a validated build or baseline. Do not widen an old numerical
  tolerance to accommodate a new binary.
