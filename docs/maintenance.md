# API and connection improvements (0.5.0, PR candidate)

- Correct native input limits; add FixedWakeFlag and GMRES mapping; explicitly
  select geometry sets and steady mode; verify the written solver inputs.
- Add native capability/analysis/parameter queries, typed parameter edits, and
  bounded saved-result/log reads through MCP, Python and REST.
- Keep MCP responsive with bounded worker execution and POSIX cancellation cleanup.
- Reject malformed polar/history data; preserve requested versus effective values.
- Cache immutable identity and empty-model capabilities; record phase timings.

See [the pinned API audit](api-audit-0.5.md) and [runtime notes](runtime-0.5.md).
Existing interfaces remain; formerly accepted out-of-range inputs now fail early.
0.6.0 is reserved for expanded batch and aerodynamic derivative workflows.
This candidate does not deploy to an active runtime or create a tag/Release.

---

# Workflow and health improvements (0.4.0)

Baseline: fork main `f46a872a0d59b8d56cc6756323acc9074b1583af` (0.3.0).

## Changes

- Real health probes replace path-only readiness. CLI, REST and MCP expose package,
  MCP SDK, binary versions and a deterministic package-content fingerprint. MCP
  initialization reports the package version instead of the SDK version.
- Creation no longer requires a preexisting model. The bundled template recreates
  the four-component regression aircraft; custom creation accepts trusted scripts.
- Preview exports persistent SVG/STL from a copy. Preflight checks the actual loaded
  model's thick/thin sets, including empty sets and overlapping members. Runs use
  preflight before the Analysis API. Unit references and missing unit conventions
  are reported explicitly without changing values.
- Sequential sweeps accept 1–25 explicit conditions, share one source snapshot,
  have a total time budget and preserve partial results on later failure.
- History diagnostics expose last-step coefficient changes and trailing ranges.
  They always distinguish completed execution from unassessed convergence; there
  is no automatic mesh-convergence or aerodynamic-accuracy PASS.
- The optional POSIX installer stages and probes a new environment before switching
  a symlink. Failed installs/probes leave the previous selection intact. No Codex
  configuration or cloud housekeeping is performed.

## Compatibility notes

Existing inspect/modify/single-run calls remain available. New preflight rejects
identical, missing, empty or overlapping selected sets that were previously passed
through to the solver. `length_unit` documents units only and performs no conversion.
Sweep `conditions` are independent complete settings; the single-run `analysis`
and `run_vspaero` fields are not accepted at the batch level.

HTTP MCP now defaults to `127.0.0.1`. REST `/health` returns 503 when the executable
or API probes fail. Neither interface adds authentication.

VSPAERO 7.2.2 prints its valid `-version` response with exit code 1 on the verified
installation. Only the exact version-only stdout with empty stderr permits that
exit status in the health probe. Formal analysis execution still requires exit 0,
API success, the unique completion marker and all original artifact/polar checks.

## Verification (2026-09-19)

- 59 automated tests pass locally; Ruff passes. Tests include actual MCP transport,
  structured output, readiness failures, invalid sets, incomplete previews,
  source preservation, sweep partial results and failed-install rollback.
- macOS Apple Silicon, Python 3.13.5, MCP SDK 1.30.0, OpenVSP 3.51.3 and VSPAERO
  7.2.2: all eight MCP tools exercised through real stdio. Invalid geometry sets
  (absent, empty, overlapping) were rejected before solver artifacts were created.
- The wheel was installed into an independent staged environment. Its package
  fingerprint matched the tested source, and the full native smoke passed again
  from outside the checkout with Python isolated mode. A real failed candidate
  install left the previously selected environment unchanged.
- A single alpha 3 solve and alpha 0/3 sweep produced these repeatable values:

| Alpha (deg) | CLtot | CDtot |
| --- | --- | --- |
| 0 | -0.015953738891 | 0.009436764098 |
| 3 | 0.233342828966 | 0.009594823037 |

The alpha 3 result matches the 0.3.0 regression. The history contains 30 iterations;
its diagnostic reports are not a convergence or physical-accuracy assessment.
The full smoke saves local model, preview, request, log and coefficient evidence.
Native solver evidence is not uploaded automatically to GitHub.

## Remaining work

Windows/Linux native integration, additional OpenVSP version pairs, intersection
and mesh-quality checks, and user-defined mesh/wake convergence studies remain
separate tasks. Source preservation does not constrain arbitrary trusted script I/O.

---

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
All 37 automated tests and `ruff check .` passed locally. GitHub Actions is enabled
for ongoing pull-request checks. The real MCP stdio smoke exercised all three
tools and the API-error path:

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

## Follow-up status after 0.4.0

Model creation, multi-condition sweeps, geometric-set checks and preview generation
are implemented in 0.4.0 above. Native compatibility beyond the tested Mac/version
pair and mesh/wake convergence studies remain open.

For each issue, record the OpenVSP/VSPAERO versions, a minimal model or build
script, the MCP request, expected behavior, and the run manifest/logs. Review
machine-specific paths and model contents before posting run artifacts publicly.
