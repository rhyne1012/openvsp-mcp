# 0.8.0 validation

Validation on 2026-10-04 uses macOS arm64, Python 3.13.5, MCP SDK 1.30.0,
OpenVSP 3.53.1 and VSPAERO 7.2.2. Changes build on merged 0.7.0
`3f3416588f2f521998f1d00f3bd5d9d9ed0ef305`. Only public/generated models are used.
The daily MCP environment is separate from the candidate validation environment.

## Regression and packaging

- 126 automated tests pass, including the existing 16-tool/schema contract,
  stdio ping/cancellation/reconnect, REST, process cleanup and artifact checks.
- New tests cover 48 queued native requests with independent read/control
  admission, cancellation while queued, queue timing, stat-cache invalidation
  after a same-size/same-mtime rewrite, full hash refresh, finite-force validation,
  known unavailable vs unknown coefficient names, and invalid numerical history.
- Batch coverage includes compact status reads without detail deserialization,
  immutable specifications, response/spec/artifact corruption rejection,
  persistence failure without false success, interrupted attempt publication,
  legacy schema read/export and rejection of changed runtime identity.
- Ruff passes. Wheel and source distribution include the new modules, public
  fixture and version-specific smoke baseline. The source distribution also
  includes the JSON tool-contract fixture needed to reproduce its tests.
- Independent wheel installation passes dependency checks and native stdio smoke
  outside the checkout. No editable install or source-path injection is used for
  that validation. Existing upstream test-client/build deprecation warnings do
  not alter the tested contract.

## Native workflows

The two smoke programs cover all 16 tools: create, inspect, preview, preflight,
analysis documentation, parameter query/edit/readback, source-preserving solve,
fixed wake and GMRES settings, saved results, sweep, parallel batch execution,
cancel/resume with success reuse and CSV/JSON export. Invalid geometry sets and
parameter IDs still fail explicitly. Both generated conventional aircraft and
the bundled rectangular wing pass. The zero-lift wing now succeeds with finite
forces and explicitly unavailable `E`; no NaN is emitted as a JSON coefficient.

Native executable identities observed:

| Executable | SHA-256 |
| --- | --- |
| vspscript | `856bf05b14f9ecf56581b071fe0f801165286d22ab0b0f74b30b7f36889969d8` |
| vspaero | `7ddca47bf20fecf6db416f8ed531b14afc277927f133fa0995a5c87ba058f169` |

At alpha=3 degrees, the conventional example has these version-specific
regression values with the same absolute tolerance of 1e-8:

| OpenVSP | CLtot | CDtot |
| --- | ---: | ---: |
| 3.51.3, historical 0.6/0.7 validation | 0.233342828966 | 0.009594823037 |
| 3.53.1, this validation | 0.233063213237 | 0.014829214640 |

The new baseline is reproduced by separate single-case and batch workflows;
the old baseline remains in [baselines.json](../examples/simple_aircraft/baselines.json).
These checks establish workflow repeatability, not aerodynamic truth or old/new
solver equivalence. The upstream Reynolds/geometry changes are discussed in
[compatibility notes](runtime-0.8.md#audited-upstream-changes); no controlled
experiment isolates their individual contributions to the coefficient difference.

## Metadata overhead

[benchmark_overhead.py](../scripts/benchmark_overhead.py) uses 1,000 identical
synthetic completed-case layouts with 40 coefficients, 100 parameter values and
15 artifact hashes each. Seven repetitions compare the legacy loader/writer with
the compact index and warm immutable-specification cache on local temporary
storage. [Raw samples](benchmarks/0.8.0-metadata-macos-arm64.json):

| Measurement | Legacy layout | Compact layout |
| --- | ---: | ---: |
| Rewritten index size | 6,162,588 bytes | 234,015 bytes |
| Median load/validation | 23.49 ms | 5.31 ms |
| Median atomic index rewrite | 79.20 ms | 2.47 ms |

This isolates warm status/index work. It excludes initial immutable-record
creation, native execution, full export and resume integrity checks; it is not
an end-to-end speedup claim. Disk/cache/model size will change these values.

## Native throughput

The existing benchmark solves the same four public rectangular-wing cases with
a total budget of four threads, rotating execution order over three samples.
[Raw samples](benchmarks/0.8.0-macos-arm64.json) verify requested thread counts
and identical returned coefficients across configurations in this run:

| Parallel cases × threads | Median end-to-end time | Median sampled aggregate RSS peak |
| --- | ---: | ---: |
| 1 × 4 | 20.37 s | 163.41 MiB |
| 2 × 2 | 10.24 s | 247.06 MiB |
| 4 × 1 | 5.16 s | 415.00 MiB |

This is close to the earlier 0.6 small-model timings. The release improves queue
isolation and metadata scaling, not native solver algorithms. RSS is sampled
across server/descendants every 50 ms; shared pages may be counted twice and
short peaks missed. No universal optimal concurrency or memory limit is claimed.

Reproduce with a local candidate environment and configured native executable
paths:

```sh
python examples/simple_aircraft/run_smoke.py
python examples/simple_aircraft/batch_smoke.py
python scripts/benchmark_overhead.py --output /local/results/metadata.json
python scripts/benchmark_batch.py --output /local/results/native --samples 3
```

Windows native cleanup, network filesystem locking, power-loss durability,
mesh convergence, aerodynamic accuracy, control derivatives and unsteady
workflows are outside this validation. Health alone does not run these tests.
