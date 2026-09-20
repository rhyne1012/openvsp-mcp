# 0.5.0 candidate validation

Local checks on 2026-09-21 used macOS arm64, Python 3.12.14, MCP SDK 1.30.0,
OpenVSP 3.51.3 and VSPAERO 7.2.2. The baseline was 0.4.0 commit
`6355c2c82406dce83c638cbb41dd09b48d66484e`. Validation used an independent
environment and the bundled public aircraft example; no active MCP installation
or user model was updated.

- Ruff and 76 tests passed, including a real MCP cancellation notification while
  a blocking test subprocess and child were running. Ping remained responsive,
  cancellation terminated the process group, and the manifest recorded cancellation.
- Native MCP smoke exercised all 11 tools: capability/analysis queries, parameter
  query/edit/readback, rejected invalid/out-of-range edits, creation, inspection,
  modification, preflight, preview, single-point solve, saved results and the
  existing two-point sweep. Invalid/empty/overlapping geometry sets were rejected.
- The default alpha=3 case preserved baseline coefficients:
  `CLtot=0.233342828966`, `CDtot=0.009594823037`. The source hash remained unchanged
  by read-only operations. This is a regression comparison, not independent
  aerodynamic validation or proof of convergence.
- Fixed wake produced `WakeIters=0`; the requested GMRES factor of 0.001 was
  verified in the generated solver input, alongside the other audited settings.
- A normal wheel installed into the independent environment passed the same
  native smoke outside the checkout with source-path injection disabled. Installed
  package sources matched the candidate; bundled model data and `pip check`
  passed. The rebuilt source distribution also passed all 76 tests, including
  the installer tests whose helper script must be included in the archive.

## Connection measurements

Five samples per version used the same host, interpreter and SDK. The
[raw samples](benchmarks/0.5.0-macos-arm64.json) include median and empirical p95;
with only five samples the reported p95 is the maximum. Times below are medians.

| Measurement | 0.4.0 | 0.5.0 |
| --- | ---: | ---: |
| Start MCP and list tools | 213.5 ms | 213.5 ms |
| Inspect model XML | 2.65 ms | 2.45 ms |
| Fresh native health probe | 49.4 ms | 49.8 ms |
| Cached capability query | unavailable | 1.70 ms |
| Ping during a simulated two-second native operation | 2055 ms | 1.48 ms |

The evidence supports improved transport responsiveness under blocking work.
Startup and fresh health timing are effectively unchanged at this sample size.
The blocking workload is deliberately simulated to isolate the transport;
these numbers do not measure VSPAERO solve speed or memory use. Reproduce with
`scripts/benchmark_connection.py` as described in [runtime notes](runtime-0.5.md).

## Coverage limits

OpenVSP 3.52.0 was source-compared only. Windows process cleanup and a persistent
Python backend were not validated. Linux CI tests use mocked native outputs and
real transport/process tests; they do not run a native aerodynamic solver.
Publishing a release, merging the PR and upgrading active MCP installations are
separate steps.
