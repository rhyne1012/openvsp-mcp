# First maintenance pass (0.3.0)

Baseline: upstream commit `0982c71cb196611da3dd01cad43950469106b2bf`.

## Problems fixed

1. **Successful scripts reported as failures.** On the tested macOS installation,
   `void main()` produced varying nonzero process statuses on successful scripts.
   Generated scripts now use `int main()` and explicit returns. No arbitrary
   nonzero exit codes are allowlisted. API errors and a per-run completion marker
   are checked in addition to the process status.
2. **Incorrect solver invocation.** The old wrapper passed a `.vsp3` directly to
   the VSPAERO executable. The new path calls `VSPAEROComputeGeometry`, followed by
   `VSPAEROSweep`, and uses explicit single-point inputs for both analyses.
3. **Returned paths disappeared.** Scripts formerly lived in a deleted temporary
   directory. Persistent, unique run directories now preserve input snapshots,
   scripts, models, logs, solver files and a status manifest.
4. **Inspection saved the source.** The old inspect path reused the modification
   script. Inspection now reads XML directly and counts only `Vehicle/Geom`
   components, avoiding nested internal `Geom` nodes.
5. **Unverifiable success.** Missing/empty files, missing completion markers,
   invalid polar rows, mismatched conditions, and API failures are rejected.
   Failed edits leave the original input intact. A concurrent source change is
   detected before a successful edit replaces the original.
6. **MCP dependency drift.** Retain the pre-existing local `mcp<2` adjustment;
   FastMCP 2.x migration is a separate task. Generated egg-info and build products
   are excluded from version control.

## Migration from 0.2.0

- `openvsp.modify` retains in-place semantics after validation.
- `openvsp.run_vspaero` now preserves its source. Use the returned `geometry_path`
  for the edited analysis model, or call modify first to update your source.
- Solver output is in a unique run directory, rather than a guessed relative
  `case.adb` path. Use the returned paths.
- `case_name` is now a short name, not a filesystem path. Use `output_dir` to choose
  the destination. Unknown request/analysis fields and non-finite inputs fail
  validation instead of silently falling back to defaults.
- Analysis defaults are documented in README; users should set model-specific
  reference area, span, chord, center of gravity and geometry sets explicitly.
- OpenVSP 3.51.3 / VSPAERO 7.2.2 is the verified target for this maintenance pass.
  Older analysis schemas need explicit compatibility work.

## Regression evidence

Verified on 2026-09-19 using macOS Apple Silicon, Python 3.12 and MCP SDK 1.30.0.
The real MCP stdio smoke exercised all three tools and the API-error path:

| Condition/result | Value |
| --- | --- |
| Mach / alpha / beta | 0.1 / 3 degrees / 0 degrees |
| Sref / bref / cref | 12 / 10 / 1.2444444444 |
| Reynolds number | 2,900,000 |
| CLtot | 0.233342828966 |
| CDtot | 0.009594823037 |

These numbers are a workflow regression only. No aerodynamic convergence or
accuracy claim is made. Tests also cover missing artifacts, stale external files,
nonzero exit codes, missing binaries, invalid inputs and timeout termination.

## Subsequent issues

- A first-class MCP create-model operation, so an existing `.vsp3` is not needed
  to begin a modeling session. This pass supplies a repeatable build script.
- Multi-condition sweeps with explicit result selection.
- Windows/Linux real-binary integration and version compatibility checks.
- Geometric checks and preview generation before aerodynamic analysis.
- Mesh/wake convergence studies before using coefficients for design decisions.

For each issue, record the OpenVSP/VSPAERO versions, a minimal model or build
script, the MCP request, expected behavior, and the run manifest/logs. Review
machine-specific paths and model contents before posting run artifacts publicly.
