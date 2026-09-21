# OpenVSP MCP (Maintained Fork)

Maintained fork of [Three-Little-Birds/openvsp-mcp](https://github.com/Three-Little-Birds/openvsp-mcp).
Adds OpenVSP geometry inspection, typed parameter editing, SVG/STL previews,
validated VSPAERO analysis, and durable parallel batches with progress,
cancellation, resume, and CSV/JSON export.

This fork is maintained by [rhyne1012](https://github.com/rhyne1012) and is not an
official OpenVSP or NASA project. Original project credit belongs to Three Little
Birds; the original [MIT license](LICENSE), copyright notice, and Git history are
retained. The repository and Python distribution name remain `openvsp-mcp`.

**0.6.0** adds durable multi-case analysis with bounded parallel execution,
shared CPU admission, progress/cancellation, verified explicit resume and CSV/JSON
exports. Each case can change conditions and typed parameters on a private model
copy. See the [batch guide](docs/batch-0.6.md), [native batch regression](examples/simple_aircraft/batch_smoke.py)
and [reproducible throughput/RSS benchmark](scripts/benchmark_batch.py).
Measured results and coverage limits are in [0.6 validation](docs/validation-0.6.md).
The [0.5 API audit](docs/api-audit-0.5.md) and existing single-case workflows remain applicable.

## Install

Python 3.10+ and a separate OpenVSP installation are required. Real integration
is verified on macOS Apple Silicon with **OpenVSP 3.51.3 / VSPAERO 7.2.2**.
Other binary versions/platforms have not been integration-tested. This pipeline
requires the VSPAERO 7 thick/thin geometry-set interface. Binaries are not included.
The MCP SDK is constrained to `>=1.20,<2` for the FastMCP 1.x interface.

Keep Python environments, caches, and launchers on each computer's local disk.
Source copies, models and result files can be synced; do not sync a venv or copy
another computer's absolute-path client configuration. Validate each Mac separately.

```sh
git clone https://github.com/rhyne1012/openvsp-mcp.git
cd openvsp-mcp
# Use a local directory outside iCloud/Dropbox for the environment.
python3 -m venv "$HOME/Developer/Codex/.venvs/openvsp-mcp"
. "$HOME/Developer/Codex/.venvs/openvsp-mcp/bin/activate"
python -m pip install '.[dev]'

# Example macOS paths; adjust to this computer's installation.
export OPENVSP_BIN=/Applications/OpenVSP.app/Contents/Resources/vspscript
export VSPAERO_BIN=/Applications/OpenVSP.app/Contents/Resources/vspaero
python -m openvsp_mcp --health
```

`OPENVSP_BIN` identifies `vspscript`, or a `vsp` accepting `-script`.
`VSPAERO_BIN` identifies the solver installation; OpenVSP calls it through its
Analysis API. The wrapper does not pass a `.vsp3` directly to the solver.

`--describe` reports package/SDK versions and a SHA-256 fingerprint of packaged
Python/model files. `--health` additionally launches a small OpenVSP geometry/API
probe and queries VSPAERO's version; it exits 1 when either check fails. Health
reports the binary paths and whether the version pair matches the tested pair.
Health is a readiness check, not a full solve or a convergence certificate.

For upgrades that preserve the previously selected environment until the candidate
passes installation and health checks, see [the local installer](docs/local-install.md).

## MCP tools

Start with `openvsp-mcp` or `python -m openvsp_mcp` (stdio by default). Configure
the client with that computer's absolute Python path and binary environment values.
All tools return structured results. All except `openvsp.health` take a nested
`request` object; health takes `{}`.

| Tool | Behavior |
| --- | --- |
| `openvsp.health` | Probe binaries/API; report versions, paths and package fingerprint. |
| `openvsp.create_model` | Create a four-component aircraft template or custom model without an input file. |
| `openvsp.inspect` | Read `.vsp3` XML metadata without launching OpenVSP or saving the source. |
| `openvsp.modify` | Apply commands, validate the output, then replace the input file. |
| `openvsp.preview` | Export SVG and STL from a copy; preserve the source. |
| `openvsp.preflight` | Check selected geometry sets in the loaded model and report reference/unit warnings; no solver. |
| `openvsp.run_vspaero` | Prepare and solve one condition; validate artifacts and matching polar; preserve source. |
| `openvsp.query` | Discover analyses, inspect their input types/defaults, or read paginated geometry parameters. |
| `openvsp.set_parameters` | Apply typed ID/value edits in one load/update; verify limits and final readback before replacing the source. |
| `openvsp.read_results` | Read saved coefficient subsets and bounded log tails without launching OpenVSP. |
| `openvsp.sweep` | Solve 1–25 explicitly specified conditions sequentially; retain partial results on failure. |
| `openvsp.batch_submit` | Submit independent cases with per-case parameters, parallel-job and CPU limits. |
| `openvsp.batch_status` | Read paginated progress and detect interrupted batches after restart. |
| `openvsp.batch_cancel` | Cancel selected cases or the whole batch. |
| `openvsp.batch_resume` | Explicitly retry incomplete cases after verifying inputs and successful artifacts. |
| `openvsp.batch_export` | Export saved case results and metadata as CSV/JSON. |

Batch defaults are sequential, four CPU threads per case, and a four-thread
batch budget. Set `max_parallel_jobs` and each case's `analysis.ncpu` together.
`OPENVSP_CPU_BUDGET` limits shared native work in one server; by default it is the
larger of four and the reported logical CPU count. Requests exceeding this budget
are rejected. Multiple server processes do not share this limit. See the
[batch guide](docs/batch-0.6.md) for lifecycle, resume and resource semantics.

Create a model:

```json
{"request": {"output_dir": "/absolute/path/runs", "template": "simple_aircraft"}}
```

The template contains a fuselage, main wing, horizontal tail and vertical tail.
Use its returned `geometry_path` for subsequent calls. `template: "custom"`
requires `set_commands` that add geometry to the initially empty model.

Inspect it:

```json
{"request": {"geometry_file": "/absolute/path/aircraft.vsp3"}}
```

Preview accepts the same minimal request. Preflight and solve accept these settings
for the bundled aircraft:

```json
{
  "request": {
    "geometry_file": "/absolute/path/aircraft.vsp3",
    "case_name": "single_point",
    "output_dir": "/absolute/path/runs",
    "timeout_seconds": 600,
    "analysis": {
      "thick_geom_set": 3,
      "thin_geom_set": 4,
      "mach": 0.1,
      "alpha": 3.0,
      "beta": 0.0,
      "sref": 12.0,
      "bref": 10.0,
      "cref": 1.2444444444,
      "xcg": 3.0,
      "vinf": 34.03,
      "rho": 1.225,
      "reynolds": 2900000.0,
      "length_unit": "m"
    }
  }
}
```

Set 3 is the fuselage and set 4 is the three lifting surfaces **in this template
only**. Supply model-specific references and sets for other aircraft. `-1` disables
one surface type. Nonexistent/empty selected sets, identical set indices, or actual
geometry overlap between thick and thin sets are rejected before solving.
Preflight does not check surface intersections, mesh quality or physical validity.

Defaults remain all geometry as thin surfaces, no thick surfaces, unit reference
area/span/chord, Mach 0.1 and alpha 3 degrees. Angles are degrees. `length_unit`
(`m`, `ft`, or `unspecified`) documents your convention; it does **not** convert
any inputs. Geometry, references, speed and density must use consistent units.
Mach, speed, density and Reynolds are independent; no atmospheric consistency is
derived. Unit references and unspecified units produce warnings, not automatic
corrections.

For a sweep, replace `analysis` with a `conditions` list. Each entry is a complete
analysis settings object with the same defaults; settings do not carry over from
one entry to the next. Top-level `analysis` and `run_vspaero` are not accepted by
this tool. For example, duplicate the explicit analysis object above and change
`alpha` to 0 and 3. `timeout_seconds` budgets the entire batch. Each condition has
its own run directory and verified polar. The batch uses a stable source snapshot.

To edit, call `openvsp.modify` with `set_commands`, for example:

```json
{
  "request": {
    "geometry_file": "/absolute/path/aircraft.vsp3",
    "set_commands": [
      {"command": "SetGeomName(FindGeom(\"Main_Wing\",0),\"Renamed_Wing\")"}
    ]
  }
}
```

Commands are trusted AngelScript with server-process privileges; use trusted local
clients. Preview/preflight/solve may apply commands to their private copy.
`modify` and `set_parameters` replace the original after validation.
Read-only preservation refers to the wrapper's
normal operations; arbitrary trusted script commands can perform their own I/O.

## Results and failures

Model creation, editing, preview, preflight and solve create a unique directory under
`output_dir`, or `openvsp_runs` beside the source. Creation requires `output_dir`.
Runs preserve scripts, models, logs and `manifest.json`; operations on an existing
model also preserve its input snapshot and hash. Solver runs retain `.vspgeom`,
`.vspaero`, `.adb`, `.history`, `.polar`, `solver.log` and `history.csv`.
Preview adds `preview.svg` and `preview.stl`. Sweep batches have `sweep.json`, with
completed conditions retained if a later one fails. Runs are not automatically deleted.

Responses include absolute artifact paths, coefficients, applied settings,
operation, warnings, preflight, numerical quality and package/SDK fingerprint.
A solve requires zero script exit status, a unique completion marker, nonempty
geometry, fresh nonempty solver artifacts, and one finite polar row matching Mach,
alpha, beta and Reynolds. API errors and failures expose run/log paths. POSIX
timeouts kill the process group, including the solver; Windows child cleanup has
not been integration-verified.

`numerical_quality` reports observed last-step coefficient changes and the range
of the final five recorded iterations when the history format is recognized.
It explicitly reports `convergence_status: "not_assessed"` and
`mesh_study: "not_performed"`. Completed execution, small iteration changes, and
sweep success do not establish aerodynamic accuracy or mesh convergence.

Archived scripts read their own snapshot. Rerunning a script can overwrite that
run's artifacts; copy the run first when preserving evidence.

## Verification

```sh
python -m pytest
ruff check .
# Real OpenVSP/VSPAERO required; exercise the legacy and batch tools over MCP stdio:
python examples/simple_aircraft/run_smoke.py
python examples/simple_aircraft/batch_smoke.py
```

The real smoke queries native capabilities and analysis defaults, reads and edits
parameters, verifies fixed-wake/GMRES settings, reads saved results, creates and previews an aircraft, verifies source preservation,
checks actual geometry sets, rejects absent/empty/overlapping sets before solving,
renames a wing, rejects an invalid parameter edit, runs one condition and an
alpha 0/3 degree sweep. Full responses are saved in `smoke_outputs/smoke_result.json`;
set `OPENVSP_SMOKE_OUTPUT` to choose a different output directory.
The alpha 3 case gives approximately CL 0.233343 and CD 0.00959482. These numbers
verify the workflow, not accuracy, convergence or design suitability.

Hosted CI uses no native binaries. Native smoke testing remains opt-in and must
be repeated for each computer and binary version.

## New typed operations

```json
{"request": {"kind": "analysis", "analysis_name": "VSPAEROSweep"}}
```

Use `kind: "parameters"` with `geometry_file`; optionally select `geom_id` or
`parm_ids`, and paginate with `offset`/`limit` (default 100, maximum 200).
`kind: "capabilities"` lists installed analyses. Listing an analysis does not
imply that this wrapper supports running it. Analysis input descriptions are
omitted because of an audited upstream AngelScript binding defect; see the audit.

Call `openvsp.set_parameters` with `geometry_file` and
`edits: [{"parm_id": "ID_FROM_QUERY", "value": 1.5}]`. This operation modifies
the source after validation, like `openvsp.modify`.

Call `openvsp.read_results` with a returned `manifest_file` path and optional
`coefficient_names: ["CLtot", "CDtot"]`, `log: "solver"`, `log_tail_lines: 40`.
(The execution response calls this path `manifest_path`.)

For a single solve, set `analysis.fixed_wake: true` to select official
`FixedWakeFlag`; the effective file must contain `WakeIters=0`.
`wake_iterations` now accepts 3–255 and `ncpu` 1–255. Values accepted previously
outside these native limits are rejected rather than silently clamped.
`forward_gmres_tolerance_factor` defaults to 1 and accepts positive values up to
1e12. Wrapper defaults remain explicit; loaded-model/native defaults are reported
separately by query. See the audit for intentionally narrower wrapper limits.

`effective_settings` reports verified solver-file fields. The request and
`analysis_inputs` remain the requested values; preflight alone does not verify a
solver file. `timings` reports preparation/native/validation/total seconds.

## Other interfaces

Python exports include `CreateModelRequest`, `SweepRequest`, `OpenVSPRequest`,
`VSPAeroSettings`, `create_model`, `preview_model`, `preflight_model`, `run_sweep`,
`health_check`, `execute_openvsp`, `QueryRequest`, `ParameterEditRequest`,
`ResultRequest`, `query_model`, `set_parameters` and `read_results`.

HTTP MCP binds to loopback by default:

```sh
python -m openvsp_mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
python -m uvicorn openvsp_mcp.fastapi_app:create_app --factory --host 127.0.0.1 --port 8002
```

REST exposes `GET /health` (200 ready, 503 unhealthy) and `POST /vsp/inspect`,
`/vsp/create`, `/vsp/modify`, `/vsp/preview`, `/vsp/preflight`, `/vsp/run`, and
`/vsp/sweep`, `/vsp/query`, `/vsp/parameters`, and `/vsp/results`.
Batch routes are `POST /vsp/batch/submit`, `/vsp/batch/status`, `/vsp/batch/cancel`,
`/vsp/batch/resume` and `/vsp/batch/export`.
POST bodies contain the request object without the MCP wrapper.
No authentication is provided; these interfaces are intended for trusted local use.

## Maintenance

Keep `upstream` pointing to the original project and `origin` to this fork. Use a
small branch per reproducible issue and retain a verified environment before
switching a daily MCP client. See [maintenance notes](docs/maintenance.md) and [LICENSE](LICENSE).
