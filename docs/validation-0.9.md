# 0.9.0 validation: OpenVSP 3.55.0

Verified 2026-10-25 on Ubuntu 24.04 x86_64, Python 3.12.3 and MCP SDK 1.30.0.
The official [download page](https://openvsp.org/download.php) listed 3.55.0 as
current. The Ubuntu 24.04 package was extracted into a separate directory for
validation; the system installation and active MCP runtime were not replaced.

## Native identity

| Artifact | SHA-256 |
| --- | --- |
| OpenVSP-3.55.0-Ubuntu-24.04_amd64.deb | `865c24f560bca8bdfd186a9c6933c4c9c016204896d7f7e0bc9ce78030337a72` |
| vspscript | `cedb365999f2293954cbd7dbf81f7eff3e33b6a2b81d8fb9c63178035dc99afc` |
| vspaero | `6ec1ba90757a074b9c38d15ffdad292ef6fa5280b46b59e76e93317ae15e390f` |

The archive was retrieved from
`https://openvsp.org/zips/current/linux/OpenVSP-3.55.0-Ubuntu-24.04_amd64.deb`.
Hashes identify the downloaded files; they are not a publisher signature.
`GetVSPVersion()` reports OpenVSP 3.55.0 and `vspaero -version` reports 7.2.2.
The exact version-only exit status 1 remains accepted; solve execution still
requires success status and validated artifacts.

## Native checks

With `DISPLAY` and `WAYLAND_DISPLAY` unset, the legacy MCP stdio smoke passed:
health, capability enumeration/cache, model creation/inspection, SVG/STL preview,
preflight, modification, parameter query/edit/readback, invalid edits and invalid
geometry-set rejection, saved results, a single solve, a fixed-wake/GMRES solve,
and a two-point sweep. Source preservation checks passed.

The five batch MCP tools also passed on the public conventional-aircraft and
rectangular-wing models: parallel solves, private per-case edits, queued-case
cancellation, explicit resume reusing successful cases, CSV/JSON export, invalid
parameter rejection and source preservation. A zero-lift case retained finite
forces while reporting undefined efficiency separately. Together, the native
smoke programs exercise all 16 tools.

Separate native queries verified string descriptions for all returned inputs of
`VSPAEROComputeGeometry` (10), `VSPAEROSweep` (80), `ParasiteDrag` (28),
`CompGeom` (8) and `FeaMeshExport` (23). These are query checks, not execution
validation for the three additional analyses.

The generated simple aircraft uses Mach 0.1, Reynolds 2,900,000, Vinf 34.03,
Sref 12, bref 10, cref 1.2444444444, Xcg 3, thick set 3 and thin set 4.
Two initial independent alpha 3 solves agreed at all recorded decimal places.
The later MCP solve and alpha 3 sweep point reproduced the same baseline:

| Alpha (degrees) | CLtot | CDtot |
| --- | --- | --- |
| 0 | -0.009798494470 | 0.014713219576 |
| 3 | 0.239303963962 | 0.014654218723 |

The existing absolute tolerance of `1e-8` is unchanged. Baseline selection still
requires both native executable hashes. Previous 3.53.1 Mac and Ubuntu entries
remain available; no equivalence between native builds is assumed. The 3.54
VSPAERO fixes make reusing the old coefficient values inappropriate even though
the solver version string is unchanged. See the [source audit](api-audit-0.9.md).

## Automated and packaging checks

- 154 tests passed, including 16-tool schema compatibility, real stdio
  cancellation/process cleanup, shared resource admission, preserved-mtime cache
  invalidation, version guards and exact build/baseline matching.
- Ruff and `git diff --check` passed.
- A final wheel built successfully with the MIT SPDX license metadata and its
  bundled model. An independent venv installed it with MCP 1.30.0 and FastAPI
  0.162.0; `uv pip check` passed for all 33 installed distributions.
- Isolated Python outside the checkout passed native health with
  `tested_version_pair=true` and `tested_native_build=true`. The installed wheel
  also passed stdio initialization/listing of all 16 tools, a native 3.55.0
  analysis-description query, and REST `/health` returning HTTP 200.

The main regression environment used FastAPI 0.143.2 and Starlette 1.8.0. The
fresh wheel environment resolved FastAPI 0.162.0 and Starlette 1.10.0. TestClient
emits an upstream deprecation warning for httpx; runtime checks pass. No runtime
SDK major-version or REST dependency migration is included.

The restricted executor stalled an existing AnyIO worker test in event-loop
wakeup. The same tests passed in the ordinary local environment; that environment
was also used for stdio integration. Native binaries themselves ran successfully
in both environments.

## Reproduce and scope

Set `OPENVSP_BIN` and `VSPAERO_BIN` to the audited binaries, use a separate
installation of the 0.9.0 wheel, and keep `OPENVSP_CPU_BUDGET=4`. From this source
checkout, run:

```sh
python -m pytest
ruff check .
python -I -m openvsp_mcp --health
python examples/simple_aircraft/run_smoke.py
python examples/simple_aircraft/batch_smoke.py
```

`OPENVSP_SMOKE_OUTPUT` and `OPENVSP_BATCH_SMOKE_OUTPUT` select local evidence
directories. Full logs/models are retained locally, not committed. The compact
[validation summary](benchmarks/0.9.0-ubuntu-355.json) contains no private paths
or user models.

This validates supported workflow behavior, not aerodynamic accuracy, mesh or
wake convergence, or aircraft suitability. 3.55.0 macOS, Windows, Ubuntu 26.04,
SSH/network-failure behavior, new Clone-specific semantics and other native
analyses were not integration-tested in this release. Historical 3.53.1 validation
remains historical evidence. Existing batches must be resumed with their original
package and native identities. No active runtime/client configuration was changed
and no old installations were deleted.
