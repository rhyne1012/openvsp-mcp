# Tool metadata (0.7.0)

The 0.6.0 tool catalog had short descriptions, sparse parameter documentation and
no annotations. Version 0.7.0 makes the existing `tools/list` sufficient to choose
between inspection, editing, preflight, solving and batch control without adding
OpenVSP/VSPAERO capabilities.

## Scope and compatibility

- All 16 public tool names, request/response shapes, types, defaults, validation
  constraints and nested `request` objects remain unchanged.
- All tool descriptions and 15 outer request descriptions are documented. The
  existing model fields describe paths, defaults, native units, selector precedence,
  conditional requirements, resource budgets and artifact meanings.
- Existing typed output fields gain descriptions. The nine dynamic dictionary
  outputs retain their open schemas; no response envelope or required keys are added.
- No solver commands, model manipulation, result parsing, numerical checks,
  timeouts, CPU admission, batch/retry logic or dependencies change.
- Both `pyproject.toml` and the reported package version become 0.7.0. The package
  SHA-256 necessarily changes because it includes Python source, including metadata.

`batch_resume` still verifies exact package/native identities and source/result
hashes. Batches from 0.6.0 must be resumed using the original unmodified runtime,
model and artifacts, or replaced by a newly submitted batch. This release does not
relax that check; metadata-only changes also change package identity. Complete
pending batches before switching runtime when possible.

## Annotation rationale

MCP SDK `>=1.20,<2` already supports `ToolAnnotations`, structured output and
nested Pydantic field descriptions; no dependency upgrade is required. Annotations
are advisory metadata, not new enforcement or permissions.

| Tools | Read only | Destructive | Idempotent | Open world | Reason |
| --- | --- | --- | --- | --- | --- |
| inspect, read_results | true | omitted | omitted | false | Read saved local files without native processes or writes. |
| health, query | false | false | false | false | Native probes/queries create temporary files; query may update its capability cache. |
| create_model, modify, preview, preflight, run_vspaero, sweep | false | true | false | true | Accept trusted AngelScript with server privileges; script I/O is unrestricted even when normal wrapper operations preserve the source. |
| set_parameters | false | true | false | false | Validated edits replace the source and every call creates run artifacts. |
| batch_submit | false | false | false | false | Adds a new persistent batch, private snapshots and solver artifacts. |
| batch_status | false | false | true | false | Opens/creates the ownership lock; does not rewrite the manifest or execute jobs. |
| batch_cancel, batch_resume | false | true | false | false | Update existing job state; cancel may terminate work and resume creates new attempts. |
| batch_export | false | false | false | false | Adds a fresh export directory on each call. |

The destructive/open-world hints for tools accepting `set_commands` reflect their
most permissive supported inputs, not a claim that every preview or solve overwrites
its source. Temporary private work and repeated persistent artifacts are described
separately so source preservation is not mistaken for absence of file effects.

## Tool selection review

This mapping was checked against the published descriptions and nested schemas,
without relying on README workflow instructions.

| Intent | Existing tool and metadata guidance |
| --- | --- |
| Create a model | `openvsp.create_model`: template/custom creation, required output parent, no input model. |
| Edit an existing model | `openvsp.modify`: commands and/or ID edits; `openvsp.set_parameters`: focused typed edits. Both replace the source after validation. |
| Read geometry | `openvsp.inspect`: XML components; `openvsp.query`: native parameters/analysis defaults. |
| Check before solving | `openvsp.preflight`: actual geometry sets and warnings, no solver or mesh-quality certification. |
| Solve one condition | `openvsp.run_vspaero`: native execution, verified settings and retained results. |
| Run an AoA sweep | `openvsp.sweep`: explicit independent settings, shared model snapshot, sequential execution and total budget. |
| Run independent cases concurrently | `openvsp.batch_submit`: per-case edits/settings, bounded parallelism, durable background status. |
| Read batch progress | `openvsp.batch_status`: pagination, aggregate counts and interrupted-owner detection. |
| Read previous results | `openvsp.read_results` for an operation manifest; `openvsp.batch_export` for saved batch CSV/JSON. |
| Identify source changes | `modify`/`set_parameters` replace it; other model workflows preserve it under normal wrapper operations. Trusted commands can perform their own I/O. |

## Deliberately retained

- Existing naming patterns, including `batch_submit` and `run_vspaero`, avoid an
  unnecessary API rename. No tools are merged, split, added or removed.
- The shared `run_vspaero` input stays present; metadata explains that named MCP
  operations override it. Query selector precedence and conditional fields are
  documented rather than given new validation rules.
- `length_unit` remains documentation only. No dimensional conversions, derived
  atmosphere, new native analysis descriptions or convergence claims are introduced.
- Dynamic outputs and error handling remain unchanged. A stricter typed output
  model or new error envelope would exceed this documentation-only scope.
- TDQS improvements are expected in parameter semantics, usage guidance, side
  effects and selection clarity; no specific score is promised without Glama reassessment.

## Verification

`tests/fixtures/tool_contract_0_6.json` records canonical schema digests from
unmodified main commit `42d792b8777cd2dd9b32181a47e717ba4f963b52`. Only descriptions
are removed before hashing: types, required/default values, titles, enums, bounds,
extra-field rules and nested references remain in the comparison. This compact
fixture avoids repeating shared schemas for all 16 tools. Do not regenerate it
from the changed implementation to make a failure pass.

Metadata tests check that baseline, nested field documentation and annotations;
the existing real-stdio test checks client-visible serialization and server startup.
The existing execution, source-preservation, timeout, cancellation and batch tests
continue to exercise behavior. Run `python -m pytest` and `ruff check .`.

Native regression commands remain `python examples/simple_aircraft/run_smoke.py`
and `python examples/simple_aircraft/batch_smoke.py`. They validate the workflow,
not aerodynamic accuracy or mesh convergence. Per-host execution evidence is
reported with the PR; hosted CI does not have native OpenVSP/VSPAERO binaries.

### Local results (2026-09-21)

- Python 3.13, MCP SDK 1.30.0: `python -m pytest -p no:cacheprovider` passes
  **105 tests**; `python -m ruff check --no-cache .` passes. Two pre-existing
  dependency deprecation warnings remain. Tests use a Python path without spaces
  because two existing native-process stubs put `sys.executable` in a shebang.
- Both native smoke commands pass on macOS Apple Silicon with OpenVSP 3.51.3 and
  VSPAERO 7.2.2. Single-case and alpha 0/3 sweep checks preserve the regression
  alpha 3 values: `CLtot=0.233342828966`, `CDtot=0.009594823037`.
- Batch smoke passes on the conventional aircraft and rectangular wing, covering
  parallel native solves, private per-case edits, cancellation, verified resume
  reusing successes, exports, invalid-edit rejection and source preservation.
- The built 0.7.0 wheel loads from outside the checkout in isolated Python, exposes
  16 annotated tools over stdio and inspects its packaged model. Its Python/model
  files match the tested source. No daily runtime is replaced by these checks.
- A source audit confirms model defaults/validators and tool callback statements
  are unchanged after removing only field documentation wrappers. All 16 other
  production Python/model files are byte-identical to the 0.6.0 baseline.
