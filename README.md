# openvsp-mcp — OpenVSP and VSPAERO through MCP

A maintained fork of [Three-Little-Birds/openvsp-mcp](https://github.com/Three-Little-Birds/openvsp-mcp).
Use it to inspect a model, apply AngelScript geometry edits, and run a single steady
subsonic VSPAERO condition. The original MIT license and history are retained.

The first maintenance release is **0.3.0**. See [migration notes](docs/maintenance.md)
for behavior changes and [the aircraft regression](examples/simple_aircraft/run_smoke.py)
for a complete, executable example.

## Install

Python 3.10+ and a separate OpenVSP installation are required. The real integration
case was verified on macOS Apple Silicon with **OpenVSP 3.51.3 / VSPAERO 7.2.2**.
Other OpenVSP releases and platforms have not been integration-tested by this fork.
The new pipeline uses the VSPAERO 7 thick/thin geometry-set interface; older releases
are not claimed to be compatible. OpenVSP/VSPAERO binaries are not included.

```sh
git clone https://github.com/rhyne1012/openvsp-mcp.git
cd openvsp-mcp
# While the first maintenance PR is under review:
git checkout fix/reliable-vspaero
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

# Example paths for the macOS application bundle; adjust for your installation.
export OPENVSP_BIN=/Applications/OpenVSP.app/Contents/Resources/vspscript
export VSPAERO_BIN=/Applications/OpenVSP.app/Contents/Resources/vspaero
python -m openvsp_mcp --describe
```

`OPENVSP_BIN` should identify `vspscript`, or a `vsp` executable that accepts
`-script`. `VSPAERO_BIN` identifies the solver installation; OpenVSP invokes it
through its Analysis API. It is not called with a `.vsp3` filename as solver input.
The MCP SDK is constrained to `>=1.20,<2` to retain the FastMCP 1.x interface.

## MCP tools

Start the server with `openvsp-mcp` or `python -m openvsp_mcp` (stdio by default).
Configure the client with the absolute path to that executable and the binary
paths above. Each tool accepts a nested `request` object.

| Tool | Behavior |
| --- | --- |
| `openvsp.inspect` | Reads `.vsp3` XML metadata without starting OpenVSP or saving the file. |
| `openvsp.modify` | Applies commands, validates the generated model, then replaces the input file. |
| `openvsp.run_vspaero` | Applies commands to a copy, prepares aerodynamic geometry, solves, and validates results. Leaves the input file unchanged. |

Example inspection arguments:

```json
{"request": {"geometry_file": "/absolute/path/aircraft.vsp3"}}
```

Example solve arguments for the bundled four-component aircraft regression:

```json
{
  "request": {
    "geometry_file": "/absolute/path/simple_aircraft.vsp3",
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
      "reynolds": 2900000.0
    }
  }
}
```

Set 3 contains the fuselage and set 4 contains the three lifting surfaces **in this
example only**. Supply sets and reference dimensions appropriate to your own model.
Defaults are all geometry as thin surfaces, no thick surfaces, unit reference
area/span/chord, Mach 0.1 and alpha 3 degrees. Angles are in degrees. Geometry,
reference dimensions, speed and density must use consistent units. Mach, speed,
density and Reynolds number are independent inputs; the wrapper does not derive
atmospheric consistency for you. This version runs exactly one flight condition.

To edit a model, use `set_commands`, for example:

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

`set_commands` contains trusted AngelScript with the server process's privileges.
Use the server locally with trusted clients. Inspection reports XML metadata; it
does not certify geometric validity or solver compatibility.

## Results and failures

Each modify/solve gets a new directory under `output_dir`, or `openvsp_runs` beside
the source model. It contains an input snapshot, `automation.vspscript`, the output
model, `openvsp.log`, and `manifest.json`. Solver runs also retain `solver.log`,
`.vspgeom`, `.vspaero`, `.adb`, `.history`, `.polar`, and `history.csv`.
Files are retained on success and failure; remove old run directories when no
longer needed. Archived scripts read their own input snapshot. Re-running one can
overwrite artifacts in that archived run, so copy the run first if preserving it.

The response preserves `script_path` and `result_path` and adds `geometry_path`,
`run_directory`, `log_path`, `manifest_path`, `artifacts`, `coefficients`, and
`analysis_inputs`. All returned paths are absolute and exist on successful return.
`analysis_inputs` records the explicitly applied settings; `openvsp.log` includes
OpenVSP's analysis-input dump. Flight conditions are checked against the polar.

Success requires zero exit status, a unique script completion marker, a nonempty
model, fresh nonempty solver artifacts, and a finite single-row polar matching
Mach, alpha, beta, and Reynolds number. API errors cause an explicit failure.
A failure returns the run directory and log path. POSIX timeouts terminate the
process group, including the solver. Windows child-process cleanup has not been
integration-verified.

## Verification

```sh
python -m pytest
ruff check .
# Requires the real OpenVSP and VSPAERO binaries configured above:
python examples/simple_aircraft/run_smoke.py
```

The smoke builds a fuselage, main wing, horizontal tail and vertical tail using
`build.vspscript`, then uses a real MCP stdio connection to inspect, rename a wing,
reject an invalid parameter edit, and solve the single-point case. It verifies
source preservation and persistent results. `smoke_outputs/smoke_result.json`
contains the full response. Set `OPENVSP_SMOKE_OUTPUT` to change the output folder.
Observed smoke values are approximately CL 0.233343 and CD 0.00959482; these verify
the workflow, not aerodynamic accuracy, convergence or design suitability.

Unit/transport tests require no OpenVSP binaries and run in GitHub Actions. The real
solver smoke is opt-in and is not part of the hosted CI job.

## Other interfaces

The same Python API remains available:

```python
from openvsp_mcp import OpenVSPRequest, VSPAeroSettings, execute_openvsp

response = execute_openvsp(OpenVSPRequest(
    geometry_file="/absolute/path/wing.vsp3",
    analysis=VSPAeroSettings(sref=12, bref=10, cref=1.2),
))
print(response.coefficients)
```

HTTP MCP and REST are available for locally controlled clients:

```sh
python -m openvsp_mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
python -m uvicorn openvsp_mcp.fastapi_app:create_app --factory --host 127.0.0.1 --port 8002
```

REST endpoints are `POST /vsp/inspect`, `/vsp/modify`, and `/vsp/run`. Their JSON
body is the request object directly, without the MCP `request` wrapper.

## Maintenance

Keep `upstream` pointing to the original project and `origin` to this fork. Use a
small branch per reproducible issue, add a regression, and keep a verified version
available before switching a daily MCP client. Track follow-up work in
[the maintenance notes](docs/maintenance.md). See [LICENSE](LICENSE).
