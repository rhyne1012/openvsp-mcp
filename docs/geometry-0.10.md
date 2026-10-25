# Geometry discovery and operations in 0.10

OpenVSP 3.55 adds API coverage for geometry relationships and settings, while
3.54 introduced split-and-stitch CAD exports. Version 0.10 exposes the useful
geometry workflows through two new typed tools and additive `openvsp.query`
selectors. There are 18 tools. The other 15 existing input schemas and all
existing output schemas retain their published contracts; `query` validates
selectors explicitly and adds discovery kinds. The XML-only `inspect` tool
continues to report the stored geometry type.

## Native discovery

MCP calls wrap these request objects in `{"request": ...}`. Python and REST use
the request object directly. All discovery kinds below require `geometry_file`;
`offset` and `limit` paginate results (default 100, maximum 200).

| `kind` | Result and selection |
| --- | --- |
| `geometry` | Hierarchy, stored/effective type, Clone original chain, surface types and Behavior/Flip parameters; optional `geom_id`. |
| `containers` | Names, IDs, groups and parameter counts, including settings and Mode containers. Optional `container_id` or `container_name` plus `container_index`. |
| `parameters` | Native values and native bounds for a geometry, container, or explicit `parm_ids`; container selectors are mutually exclusive. Legacy `parm_ids` still takes precedence over `geom_id`. The default still enumerates geometry parameters. |
| `sets` | Set names, indices and geometry membership, including built-in sets. |
| `modes` | Mode names/IDs, normal/degen sets and preset group/setting IDs. |
| `design_variables` | Design-variable membership, type, native parameter limits and separate user design bounds. |

```json
{"request":{"kind":"geometry","geometry_file":"/models/aircraft.vsp3"}}
```

A Clone's stored type stays `Clone`; its effective type and surface information
show what it represents. Unresolved originals are explicit rather than guessed.
Clone APIs are selected after a native version probe so older bindings do not
compile calls to absent functions. These queries do not change `inspect`'s XML
contract or add Clone mutation commands.

Some container IDs, particularly `UserParms`, are recreated on each model load.
Use the returned name and occurrence index for subsequent parameter queries:

```json
{"request":{"kind":"parameters","geometry_file":"/models/aircraft.vsp3","container_name":"VSPAEROSettings","container_index":0}}
```

Design-variable membership is **not saved in `.vsp3`**. Supply
`design_variables_file` with a matching `.xddm` or `.des` file. Loading it applies
its values to the temporary model only. Without that file the response explains
why no design-variable list is available. XDDM user limits are distinct from
native Parm limits. OpenVSP 3.55 DES files can carry a `#LIMITS` section; legacy
files without it label the returned bounds as native defaults. Discovery does not impose
optimization bounds on existing parameter-edit tools.

## CAD export

```json
{"request":{"geometry_file":"/models/aircraft.vsp3","format":"step","export_mode":"split_and_stitch","length_unit":"m","step_representation":"brep","tolerance":0.000001,"geom_set":0}}
```

Call `openvsp.export_geometry` (REST `POST /vsp/export`). `format` is `step` or
`iges`; `export_mode` defaults to `standard`. `length_unit` is required and is
one of `mm`, `cm`, `m`, `in`, `ft`. It declares the unit of existing model
coordinates; it does not rescale the model. STEP tolerance defaults to `1e-6` in
model units. IGES rejects tolerance because it has no equivalent native setting.
`step_representation` applies only to split-and-stitch STEP, and defaults to
`brep`; `shell` is also available.

Standard export produces surface quilts. Split-and-stitch joins surfaces of
each body along feature lines. It does not intersect, trim or fuse different
components into one aircraft solid. A BREP request is not proof of closure.

The response provides `artifacts.cad` with path, byte size and SHA-256, effective
settings, selected set/IDs, and file-structure/unit verification. Runtime
verification does not launch a CAD kernel and reports topology as unassessed.
For independent CAD reopening, use the optional `scripts/validate_cad.py` with
FreeCAD's Python; FreeCAD is not a package dependency:

```sh
/path/to/FreeCAD/AppRun python scripts/validate_cad.py \
  --freecad-lib /path/to/FreeCAD/usr/lib \
  --expected-bbox-mm 10000 1333.333333 1333.333333 \
  --output cad-check.json /runs/geometry.stp
```

The expected dimensions above are for the validation pod, not an arbitrary model.
The report includes importer version, validity, faces/shells/solids, closure and
bounding-box dimensions in millimetres. Its exit status fails on invalid import
or a requested dimension mismatch. Successful import alone is not CFD readiness.

## Geometry measurements

```json
{"request":{"geometry_file":"/models/aircraft.vsp3","analysis":"comp_geom","length_unit":"m","geom_set":0}}
```

```json
{"request":{"geometry_file":"/models/aircraft.vsp3","analysis":"projection","length_unit":"m","projection_direction":"z","geom_set":0}}
```

Call `openvsp.geometry_analysis` (REST `POST /vsp/geometry-analysis`). CompGeom
reports tessellated wetted/theoretical areas and volumes, per-mesh rows and native
mesh diagnostics. Mesh row names need not be unique geometry IDs. Signed volumes
are preserved, including native negative-volume geometry. Open meshes removed
from measurement are reported with warnings.

Projection requires `x`, `y`, `z`, or a finite nonzero three-vector in model
coordinates; vectors are normalized. It reports component silhouette areas and
the union area. Overlapping silhouettes mean component areas need not sum to the
union. The typed workflow uses no projection boundary or convex hull.

`length_unit` is a declaration, with no automatic conversion: areas use its
square and volumes its cube. Raw native Results JSON and CSV remain in the run
directory. `openvsp.read_results` accepts both new operation manifests and
returns their compact metadata, measurements, artifact paths and requested log tail.
These are geometry measurements, not an aerodynamic solve or a
mesh-convergence certificate.

## Execution and compatibility

Both operations accept `geom_set` **or** `mode_id`, never both. Neither means
`SET_ALL` (0). A mode is applied on the private copy before selecting its normal
set. Empty/invalid sets and unknown modes fail before export/measurement.

`output_dir` chooses the parent of a unique run directory; its default is
`openvsp_runs` beside the source. Each run retains an input snapshot, generated
script, native log and manifest with source/executable/package hashes and
queue/preparation/native/validation timings. `timeout_seconds` is a total budget
including worker and CPU admission. Native failures preserve evidence and report
error code/message/phase when OpenVSP supplies them. Client cancellation kills
the native process group through the existing runtime. Neither operation writes
back to the source.

Native validation targets the official OpenVSP 3.55.0 Ubuntu 24.04 build. Existing
VSPAERO workflows and baseline coefficients are unchanged. CAD libraries are not
required for ordinary MCP operation. ParasiteDrag, CFDMesh and FEA execution,
assembly boolean fusion, and typed Clone editing remain outside this change.

## Reproducible validation

Unit and transport tests run without native binaries:

```sh
python -m pytest
ruff check .
```

Enable the geometry regression suites against the selected native executable:

```sh
OPENVSP_NATIVE_TEST_BIN=/opt/OpenVSP/vspscript python -m pytest \
  tests/test_cad.py tests/test_geometry_analysis.py tests/test_query_010.py
```

Native evidence includes analytic sphere area/volume tolerances, scale laws
(area ×4 and volume ×8 when radius doubles), projected-union overlap, source
preservation, set/mode selection, Clone discovery, settings containers and
separate design-variable files. CAD evidence includes reopening all four format/
export-mode combinations with FreeCAD/OCCT and checking declared dimensions.
See the PR validation summary for the exact run results and coverage limits.

For packaged stdio coverage, install the built wheel into a fresh environment and
run `scripts/smoke_geometry.py --output /absolute/validation/path` with that
environment's Python. The child server uses isolated Python mode to avoid loading
the source checkout. `OPENVSP_BIN` selects the native executable.

Recorded Ubuntu 24.04 validation on 2026-10-25:

- FreeCAD **1.1.4 / OCCT 7.8.1** reopened 24 CAD artifacts successfully. All 20
  pod unit/format/export-mode combinations matched their expected dimensions.
  Stitched STEP for the four-component sample aircraft produced four closed
  solids; this is model-specific evidence, not a general closure promise.
- A unit-radius sphere at tessellation 61 measured wetted area
  **12.555229764327139**, volume **4.181122619388631**, and projected area
  **3.136082591287274**. Relative tolerances against 4π, 4π/3 and π were
  0.1%, 0.2% and 0.2%. Radius doubling gave area/projection ×4 and volume ×8
  within 1e-9. No mesh-convergence study was performed.
- Failure checks cover invalid/empty selections, missing executables, corrupt
  results, non-finite measurements, source preservation, worker/CPU admission
  deadlines, and cancellation evidence.

The complete local suite passed **294 tests** with native/CAD options enabled.
Ruff and whitespace checks passed. The installed 0.10 wheel passed isolated stdio
initialization (18 tools), all five new query kinds, settings parameters, four
CAD export variants, both geometry analyses, saved-result round trips, invalid-set
error handling, source-hash preservation, and a subsequent MCP ping. Hosted CI
also checks Python 3.10/3.12/3.13 and installs the built wheel outside the checkout;
those jobs deliberately omit native/CAD integration tests.
