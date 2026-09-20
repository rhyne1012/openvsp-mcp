# Multi-case analysis in 0.6.0

Submit independent steady VSPAERO cases, inspect progress, cancel selected cases,
explicitly resume incomplete work, and export saved results. The original 11
tools remain available; five batch tools bring the total to 16. This release does
not add stability derivatives, control mixing, a distributed scheduler, or a
persistent native Python backend.

## Submit and observe

Pass the following as the `request` argument to `openvsp.batch_submit`:

```json
{
  "geometry_file": "/local/models/aircraft.vsp3",
  "output_dir": "/local/results",
  "max_parallel_jobs": 2,
  "cpu_budget": 4,
  "failure_policy": "continue",
  "cases": [
    {"case_id": "alpha0", "analysis": {"alpha": 0, "ncpu": 2}},
    {"case_id": "alpha3", "analysis": {"alpha": 3, "ncpu": 2}}
  ]
}
```

Each case has a unique ID, a full `VSPAeroSettings` object, optional typed
`parameter_edits`, and a native-operation timeout. Obtain parameter IDs with
`openvsp.query`; edits are validated on each case's private model. Configure the
correct geometry sets, references and consistent dimensional inputs for your
model. Omitted settings retain the documented single-case defaults. Arbitrary
AngelScript is not accepted by the batch API; the existing trusted-script tool
remains separate.

The response includes a `batch_directory`. Submit snapshots the model and hashes
the native binaries; it returns after admission, without waiting for the solves.
Up to 1,000 cases and 16 concurrent case workers per batch are accepted. Default
execution is sequential (`max_parallel_jobs=1`) with a batch CPU budget of 4;
parallelism is explicit. At most 16 batches may be active in one server.

| Tool | Request fields | Behavior |
| --- | --- | --- |
| `openvsp.batch_status` | `batch_directory`, optional `offset`, `limit` | Counts and paginated case states; limit defaults to 50, maximum 100 |
| `openvsp.batch_cancel` | `batch_directory`, optional `case_ids` | Cancel selected cases; an empty list cancels the whole batch |
| `openvsp.batch_resume` | `batch_directory`, optional `case_ids` | Retry selected non-successful cases; empty selects all non-successes |
| `openvsp.batch_export` | `batch_directory` | Create new CSV and JSON files without rerunning the solver |

REST uses the same request bodies at `/vsp/batch/submit`, `/status`, `/cancel`,
`/resume`, and `/export`. The Python functions are also exported by the package.
Embedded Python callers must call `shutdown_batches()` before exiting; packaged
MCP and REST servers install lifecycle cleanup hooks.

## CPU allocation

`analysis.ncpu` is the requested VSPAERO OpenMP thread count for that case.
`cpu_budget` limits the sum of thread requests admitted by a batch. A shared
process-local allocator additionally covers all batches and direct create,
modify, preview, preflight and solve operations in the server. Non-solver model
operations reserve one slot. Capability/health probes are lightweight operations
outside this allocator. Batch control requests bypass the two legacy worker slots
so cancellation/status remain available while those slots are busy.

Set `OPENVSP_CPU_BUDGET` before starting the server to establish its shared
budget. The default is `max(4, os.cpu_count() or 4)`, preserving the existing
four-thread request default even on hosts reporting fewer CPUs. A request larger
than the server budget is rejected with configuration guidance. This is a change
from accepting any native-valid NCPU value regardless of machine resources.
Health and batch status report the current server budget.

This is admission control, not physical core affinity, an OS CPU quota, or a
memory limit. Independently launched MCP servers have independent budgets;
opening more servers can still oversubscribe the computer. Monitor memory and
measure actual workload behavior before increasing parallelism. CPU allocation
does not guarantee linear speedup and does not choose an optimal configuration.

The native timeout begins after CPU admission. Global-resource wait time is
reported separately for direct operations; batch case elapsed time also includes
waiting and artifact verification. There is no automatic batch deadline; use the
explicit batch cancellation operation when needed.

## Failure, cancellation and restart

Cases move through `queued`, `waiting_resources`, `running` and terminal states
`success`, `failed`, `cancelled` or `skipped`; `cancelling` is transitional.
`failure_policy=stop` prevents further launches after the first failure, marks
pending cases skipped, and lets already launched cases finish. `continue` runs
the remaining cases. A batch reports success only when every case succeeds.

Cancellation is cooperative until native execution, where the existing POSIX
process-group cleanup terminates the OpenVSP process and solver children. A case
that already validated may finish successfully before a late cancellation.
Normal server shutdown cancels owned batches and waits for worker cleanup.
Disconnecting an individual HTTP client or cancelling a completed submit request
does not cancel a batch; use `batch_cancel`.

State is written through atomic manifest replacement. An OS ownership lock
prevents two runners from resuming the same directory. A server restart exposes
unfinished jobs as `interrupted`; it never automatically replays them. Resume
checks the original model and snapshot hashes, saved request fingerprint,
package/SDK/native binary identities, and successful response/artifact hashes.
Changed inputs or missing/modified successful artifacts require a new batch.
Successful cases are never repeated by resume. Up to 100 recorded attempts per
case are allowed. A case's native PID is recorded, and resume refuses to proceed
when a recorded process may still be alive.

An abrupt OS kill/power loss can bypass cleanup and leave native children; this
is not a process supervisor. Confirm leftover processes have stopped before
recovery. PID publication has a small startup window and PID reuse is handled
conservatively by refusing resume. Windows native child cleanup and non-local
filesystem locking have not been validated. Keep active batch directories on
local disk, and retain original absolute paths when resuming.

## Results and privacy

CSV rows contain case status, errors, requested conditions/reference dimensions,
native coefficient names, parameter edits/readback, elapsed time, versions,
effective solver settings and numerical-quality information. JSON includes the
same rows plus input identity and unit/convention metadata. Angles are in degrees;
native VSPAERO coefficient names and conventions are preserved without coordinate
conversion. Failed/pending cases are explicit rows with no fabricated coefficients.
Recognized solver logs also report native VSPAERO version and observed OpenMP
thread count; a count differing from requested NCPU fails validation. Unknown
log layouts explicitly leave that observation unavailable.
Exports are snapshots; exporting a running batch does not wait for completion.

Each case retains its native files. Manifests, CSV/JSON and error messages can
contain local paths and model parameters: these are local user outputs, not
automatically uploaded artifacts. Repository smoke tests use only the public
rectangular-wing fixture and generated conventional-aircraft example. Project
names, private models and control-layout assumptions are not part of the API.

## Validation and performance

`examples/simple_aircraft/batch_smoke.py` exercises native cases on both public
configurations, parameter edits, cancellation, successful-result reuse and exports.
`scripts/benchmark_batch.py --output /local/results/benchmark --samples 3`
compares 1×4, 2×2 and 4×1 job/thread allocations at an equal total CPU budget.
The rectangular-wing cases use alpha 1–4 degrees; its exact zero-lift case has
undefined efficiency ratios and is retained as an expected rejection smoke test.
Install the `dev` extra for its process-memory sampler. It reports end-to-end
times, sampled aggregate RSS, actual native thread settings and coefficient
agreement. RSS sums may double-count shared pages and miss peaks between samples.
Do not run competing solver workloads during this comparison.

Workflow verification does not establish mesh convergence, physical accuracy, or
the equivalence of native control surfaces and geometric Hinge perturbations.
Parameter sensitivities/control-effectiveness analysis remains a separate,
numerically validated follow-up.
