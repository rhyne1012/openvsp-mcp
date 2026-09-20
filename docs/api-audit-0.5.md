# OpenVSP API audit for MCP 0.5.0

This is an audit of the interfaces used by this wrapper, not certification of the
entire OpenVSP API. Native validation targets OpenVSP 3.51.3 / VSPAERO 7.2.2 on
macOS arm64. OpenVSP 3.52.0 has been source-compared only; it is not advertised as
native-tested. No installed OpenVSP or active MCP runtime is upgraded by this PR.

## Pinned upstream evidence

- [3.51.3](https://github.com/OpenVSP/OpenVSP/tree/51bdec01d9a50fa4bdbc960b0def21dcd6330f72)
- [3.52.0](https://github.com/OpenVSP/OpenVSP/tree/a0bdbccaa2503860195e9a8fa764a3b1397cba40)
- [3.51.3 API documentation](https://openvsp.org/api_docs/3.51.3/)
- [Python API and process variants](https://openvsp.org/pyapi_docs/3.51.3/)

`src/geom_core/AnalysisMgr.cpp` is identical in these two tags. Its
`VSPAEROSweepAnalysis::SetDefaults` uses values from the current VSPAEROMgr;
"defaults" are not necessarily factory defaults when a model is loaded.
The geometry and sweep analysis accept `UseModeFlag`; explicitly setting it to
zero makes the wrapper's thick/thin set selection authoritative. Setting
`UnsteadyType=STABILITY_OFF (0)` makes the advertised single steady calculation
independent of a saved model's stability/unsteady mode.

## Input contract

| MCP setting | Native mapping | 0.5.0 behavior |
| --- | --- | --- |
| thick/thin sets | GeomSet / ThinGeomSet | Query actual sets; reject empty/overlapping; disable Modes |
| ncpu | NCPU | 1–255; correct previous upper bound 256 |
| wake_iterations | WakeNumIter | 3–255; correct previous 1–1000; keep MCP default 30 |
| fixed_wake | FixedWakeFlag | Explicit false by default; true writes WakeIters=0 |
| wake_nodes | NumWakeNodes | Preserve 4–1024 wrapper resource envelope; this is narrower than the native parameter, not its declared range |
| forward_gmres_tolerance_factor | ForwardGMRESConvergenceFactor | Explicit default 1; allow (0, 1e12]; wrapper excludes native zero |
| Mach / alpha / beta / Re | Start/End/Npts | Exactly one condition; read back solver file and polar |
| reference dimensions / CG / Vinf / rho | Sref, bref, cref, Xcg/Ycg/Zcg, Vinf, Rho | Read back dimensional solver-file fields; no unit conversion |
| steady mode | UnsteadyType | Explicit STABILITY_OFF; extended stability workflows deferred |

The 3.51.3 VSPAEROMgr constructor initializes WakeNumIter to 3, but its Renew()
sets it to 5. The 3.52.0 constructor uses 5. Native discovery on this 3.51.3 build
returns 5 for an empty model. MCP retains its explicit 30-iteration default;
constructor literals must not be mistaken for observed runtime defaults.

The input file verifies 16 named settings including references, conditions,
fixed/relaxed wake, nodes and GMRES. NCPU is not a solver-file field and is not
claimed as solver-file-verified. Not every inherited solver option is overridden:
other model settings remain in the preserved input/output artifacts. Preflight
checks geometry sets, not effective solver-file settings; those exist after the
analysis has run. A setting mismatch fails the operation and retains artifacts.

## Native query and parameter operations

`openvsp.query` provides three read-only modes in one native launch:

- `capabilities`: installed binary version and `ListAnalysis`; no model queries
  are cached. Empty-model capability queries use a five-minute cache keyed by
  binary real path, inode, size and modification time. Report cache age/hit.
- `analysis`: `GetAnalysisInputNames`, type and all INT/DOUBLE/STRING input
  blocks. A supplied model changes the default-value context; the response says
  which context was used. Other native data types have explicit null blocks.
- `parameters`: parameter ID, name, group, container, description, type, limits
  and value; select IDs or a geometry and paginate up to 200 entries per call.

The operation loads a private snapshot and never saves over the source.
`openvsp.set_parameters` applies up to 200 ID/value edits in one load/update/save.
It rejects invalid IDs, out-of-range values and values changed by native driver
constraints, integer rounding, or linked parameters. Final readback is checked
before replacing the source; a source hash guards against concurrent changes.
Commits within one MCP process are serialized. External applications are not
covered by an interprocess file lock.

### Upstream AngelScript registration defect

Both audited `src/geom_core/ScriptMgr.cpp` files register the string-returning
`GetAnalysisInputDoc` against the integer-returning `GetNumAnalysisInputData`.
See [3.51.3 registration](https://github.com/OpenVSP/OpenVSP/blob/51bdec01d9a50fa4bdbc960b0def21dcd6330f72/src/geom_core/ScriptMgr.cpp#L3949)
and [3.52.0 registration](https://github.com/OpenVSP/OpenVSP/blob/a0bdbccaa2503860195e9a8fa764a3b1397cba40/src/geom_core/ScriptMgr.cpp#L4118).
Passing its result to string serialization aborted the real 3.51.3 subprocess.
The wrapper deliberately omits analysis input prose descriptions and reports that
limitation. It does not call this unsafe binding. Parameter descriptions use the
separately verified GetParmDescript binding. No upstream binary is patched.

## Results and coverage boundaries

`openvsp.read_results` reads saved coefficient subsets, verification metadata and
optional bounded log tails without a native launch. It is not a generic executor
for every analysis listed by capability discovery. The existing verified solve,
preview/export and raw trusted AngelScript interfaces remain available.

Malformed polar rows and nonfinite/malformed recognized history rows are errors,
not silently skipped. Unknown history layouts remain explicitly unavailable;
small iteration differences still do not certify numerical convergence.

Batch scheduling, aerodynamic databases, stability derivatives and control
workflows are reserved for 0.6.0. The existing bounded sweep remains supported.
