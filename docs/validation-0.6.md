# 0.6.0 validation

Validation on 2026-09-21 uses macOS arm64, Python 3.12.14, MCP SDK 1.30.0,
OpenVSP 3.51.3 and VSPAERO 7.2.2. The base is merged 0.5.0 commit
`3b1bb8ec309bfd71d49864f59736c147e390668e`. Testing uses independent environments
and public examples; it does not update the daily MCP runtime or publish user
models/results.

## Regression coverage

- Ruff and 101 automated tests pass. New tests cover aggregate CPU admission
  across batches/direct work, failure policies, selected-case and whole-batch
  cancellation, explicit resume without replaying successful cases, pagination,
  CSV/JSON metadata, and REST lifecycle cleanup.
- Recovery rejects changed source/snapshot, edited specifications, changed native
  identity, modified saved responses/artifacts, live runner locks and potentially
  live native PIDs. Mid-batch snapshot changes prevent subsequent launches.
  Moving a batch directory is rejected, and CPU admission preserves the legacy
  sweep's total-time deadline.
- A real MCP stdio subprocess test submits work, keeps ping responsive, cancels
  the native process, reconnects through a fresh server, resumes the batch, and
  verifies shutdown cancellation. The existing POSIX parent/child cleanup test
  remains part of the suite. Batch control still responds with both legacy
  worker slots occupied.
- Native smoke runs independent cases on a generated conventional aircraft and
  the bundled rectangular wing. It verifies parameter edits/readback, source
  preservation, queued-case cancellation, resumed success, reuse of prior runs,
  and CSV/JSON results. The conventional alpha=3 result retains
  `CLtot=0.233342828966`, `CDtot=0.009594823037` within 1e-8.
- Invalid parameter IDs fail explicitly. The rectangular wing at alpha=0 has
  undefined efficiency ratios; its nonfinite polar row is also an expected
  failure. The existing strict nonfinite-output policy is preserved.
- A normal wheel installed in an independent environment passes both native
  smoke programs outside the checkout with source-path injection disabled,
  covering all 16 MCP tools. Installed sources/model data match the candidate,
  and dependency checks pass. Source-distribution regression tests also run from
  an extracted archive, including its installer helper and examples.

## Native throughput and sampled memory

The [reproducible benchmark](../scripts/benchmark_batch.py) solves the same four
rectangular-wing cases (alpha=1,2,3,4 degrees) at a total budget of four requested
threads. Three samples per configuration rotate execution order. Each case's
native log confirms its requested OpenMP thread count. All returned coefficients
agree exactly in this run; the script enforces relative 1e-6 / absolute 1e-8
tolerances. [Raw samples](benchmarks/0.6.0-macos-arm64.json) contain no private paths
or model identifiers.

| Parallel cases × threads per case | Median end-to-end time | Median sampled aggregate RSS peak |
| --- | ---: | ---: |
| 1 × 4 | 20.56 s | 147.8 MiB |
| 2 × 2 | 10.32 s | 223.4 MiB |
| 4 × 1 | 5.20 s | 396.7 MiB |

For this small public workload, case concurrency improves batch throughput while
increasing memory. Per-case elapsed times remain around five seconds, so fixed
startup/I/O/wait overhead can dominate. This does not establish a universal
speedup or an optimal allocation for a larger aircraft. RSS is sampled every
50 ms across the server and descendants, may double-count shared pages and may
miss short peaks. The allocator does not impose a memory limit or CPU affinity.

## Boundaries

Native validation is limited to the version/platform pair above. Windows native
cleanup, network-filesystem locking and abrupt OS-kill recovery are not certified.
OpenVSP 3.52.0 retains the earlier source-only audit. No aerodynamic convergence,
accuracy or control-derivative equivalence is claimed. Sensitivity/control-effectiveness
calculations are a separate follow-up to the batch foundation.
