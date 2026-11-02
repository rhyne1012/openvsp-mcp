# macOS 3.55.0 validation

Validated on 2026-11-01 using only public/generated models. macOS 27.2 on Apple
M5 (arm64), Python 3.13, MCP SDK 1.30.0, OpenVSP 3.55.0 and VSPAERO 7.2.2.
The candidate is based on main `3ce7004d1cb7bc123b20662ae689e16bb57ac99e`.
It retains package version 0.10.0; its package-content fingerprint distinguishes
the candidate from the released package. No GitHub release is implied.

## Native identity

| Artifact | SHA-256 |
| --- | --- |
| OpenVSP-3.55.0-macos-15-ARM64-Python3.13.zip | `2414624802ca8201049417396d4129e228cc013b8f1425c1385494a5de717e9b` |
| vspscript | `ff189b1b2a8f2c0bb15c539de1d051289c8e4674b6cff43aae7e191e52964015` |
| vspaero | `0c9e59403d1b8e15e4b7b72b4f24dae4908e0621a1411f714c0110c4215e7c00` |

The archive was retrieved from
`https://openvsp.org/zips/current/mac/OpenVSP-3.55.0-macos-15-ARM64-Python3.13.zip`.
Hashes identify the downloaded files; they are not a publisher signature. The
download page lists no checksums and the executables are ad-hoc signed. The Python
3.11 archive was not examined, so its executables are not claimed by this entry.

## Coefficient baseline

The generated simple aircraft uses Mach 0.1, Reynolds 2,900,000, Vinf 34.03,
Sref 12, bref 10, cref 1.2444444444, Xcg 3, thick set 3 and thin set 4. No audited
value existed for this binary pair, so the first solve recorded the baseline and
later solves were compared with it. Four MCP solves agreed at all recorded decimal
places: two from the released 0.10.0 wheel, one through a configured launcher and
one from this candidate.

| Alpha (degrees) | CLtot | CDtot |
| --- | --- | --- |
| 0 | -0.010563360962 | 0.014862896640 |
| 3 | 0.238537638256 | 0.014837771179 |

This baseline therefore demonstrates repeatability on one computer, not agreement
with an independent reference. The existing absolute tolerance of `1e-8` is
unchanged and baseline selection still requires both executable hashes.

At alpha 3, this build differs from the audited Mac 3.53.1 build by approximately
+2.35% CL and +0.06% CD, and from the audited Ubuntu 3.55.0 build by approximately
-0.32% CL and +1.25% CD. No equivalence between native builds is assumed, and these
differences are not attributed to a specific cause here.

## Verification

- All 296 tests passed with `OPENVSP_NATIVE_TEST_BIN`, `OPENVSP_CAD_TEST_PYTHON` and
  `OPENVSP_CAD_TEST_LIB` set, so none were skipped. This includes native model
  discovery, analytic geometry measurements and CAD exports. FreeCAD 1.1.3 / OCCT
  7.8.1 independently reopened the CAD outputs and matched their expected dimensions.
- The unmodified `run_smoke.py`, `batch_smoke.py` and `smoke_geometry.py` passed over
  MCP stdio. Health reported `tested_version_pair=true` and `tested_native_build=true`.
- The released 0.10.0 wheel was also exercised in an independent environment outside
  the checkout, and passed the same three smokes. Its `run_smoke.py` needed a local
  copy because the released script still expected exactly 16 tools and has no baseline
  for this build; the candidate fixes the former and adds the latter.
- A new test compares the tool list in `run_smoke.py` with the published catalog.
  Hosted CI has no native binaries, so the smoke's own check cannot catch that drift.

The candidate environment used FastAPI 0.162.0 and Starlette 1.10.0; the released
wheel's environment used FastAPI 0.142.2 and Starlette 1.7.0. Both used MCP SDK 1.30.0.

## Reproduce and scope

Set `OPENVSP_BIN` and `VSPAERO_BIN` to the audited binaries, and set
`OPENVSP_NATIVE_TEST_BIN` to the same `vspscript`. FreeCAD reopening additionally
needs `OPENVSP_CAD_TEST_PYTHON` and, on this layout, `OPENVSP_CAD_TEST_LIB` pointing
to the FreeCAD application's `Contents/Resources/bin/python` and `Contents/Resources/lib`.

```sh
python -m pytest
ruff check .
python -m openvsp_mcp --health
python examples/simple_aircraft/run_smoke.py
python examples/simple_aircraft/batch_smoke.py
python scripts/smoke_geometry.py --output smoke_outputs/geometry
```

This validates supported workflow behavior on one computer, not aerodynamic accuracy,
mesh or wake convergence, or aircraft suitability. Existing batches must be resumed
with their original package and native identities. No active runtime or client
configuration is changed by this source update, and it does not create a GitHub Release.
