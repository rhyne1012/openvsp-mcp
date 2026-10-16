# Ubuntu 3.53.1 validation

Validated on 2026-10-16 using only public/generated models. Ubuntu 24.04.5 LTS
x86_64, Python 3.12.3, MCP SDK 1.30.0, OpenVSP 3.53.1 and VSPAERO 7.2.2.
The candidate is based on main `7e5a44028381661b8305635234ec52c6693c2b11`.
It retains package version 0.8.0; its package-content fingerprint distinguishes
the candidate from the released package. No GitHub release is implied.

## Native identity and coefficient baseline

| Build | vspscript SHA-256 | vspaero SHA-256 |
| --- | --- | --- |
| Ubuntu 24.04 amd64 | `f86b1a785eca04d703d0fbd573f6e55d356e5cbb728d693190cd2079b4d92b12` | `b8d62390a5542e3f9ff20d5accd4fd3e01efc21dba6fab945f2b780cb0738d4a` |
| macOS arm64 | `856bf05b14f9ecf56581b071fe0f801165286d22ab0b0f74b30b7f36889969d8` | `7ddca47bf20fecf6db416f8ed531b14afc277927f133fa0995a5c87ba058f169` |

The first Ubuntu smoke failed the macOS coefficient baseline. The same unmodified
source model was then solved with 1, 2, 4 and again 4 threads on each platform.
All four runs on each platform produced exactly the same printed coefficients:

| Build | CLtot | CDtot | Generated mesh node count |
| --- | ---: | ---: | ---: |
| macOS arm64 | 0.233063213237 | 0.014829214640 | 1666 |
| Ubuntu 24.04 x86_64 | 0.233695178980 | 0.014638773132 | 1670 |

Ubuntu differs from this Mac by approximately +0.271% CL and -1.284% CD. Both
geometry headers report 1544 elements, but the generated `.vspgeom` coordinates,
node counts and connectivity differ. The source SHA-256 is identical. This
demonstrates native pipeline differences before the solver; it does not isolate
their compiler/library/algorithm cause or prove which mesh is more accurate.
See [machine-readable comparison](benchmarks/ubuntu-3531-comparison.json).

The new baseline selector requires **both native executable hashes and versions**;
the absolute coefficient tolerance remains **1e-8**. Unknown builds fail instead
of inheriting another build's numbers. Historical version-only values are retained
as documentation. Health keeps `tested_version_pair` for compatibility and adds
`runtime_platform` and `tested_native_build`; the latter also checks Ubuntu
distribution/version or the audited Mac architecture.

## Verification

- The original 126-test suite passed on both hosts. The candidate's 139 tests pass
  on Ubuntu/Python 3.12 and macOS/Python 3.13, with Ruff clean. Added coverage checks
  SSH shell quoting/stdio, unknown binary rejection and conservative platform claims.
- Wheel installation uses an independent permanent runtime outside the checkout;
  `pip check`, isolated package import, health and package fingerprints are checked.
  The source distribution includes the shell launcher and smoke helper.
- Ubuntu native smoke exercises creation, inspection, preview, actual geometry-set
  checks, parameter edits/readback, invalid sets/parameters, source preservation,
  single solve, fixed wake/GMRES settings, saved results and a two-point sweep.
- Native batch smoke covers generated conventional aircraft and the bundled
  rectangular wing, two parallel cases, private edits, queued cancellation,
  explicit resume retaining successes, export, invalid input, and zero-lift forces
  with unavailable efficiency ratios. These native workflows also pass on Mac.
- A Mac client using the installed SSH stdio launcher exercises all 16 tools,
  native solve/sweep, batch cancel/resume/export, and a fresh connection reading
  the saved completed batch.
- Isolated native tests verify a one-second timeout and MCP request cancellation
  kill the active `vspscript` process. During actual batch native work, stdin EOF
  and killing the local SSH process both lead to inactive/cancelled batch status
  after reconnect, with no native child left in those test directories.
- Ubuntu native execution and SVG/STL preview run with `DISPLAY` and
  `WAYLAND_DISPLAY` unset, without Xvfb or X forwarding.

The existing dependency/build deprecation warnings are retained; they do not
change these checks. Hosted CI includes Python 3.12 as well as 3.10/3.13 and still
does not run OpenVSP binaries.

## Limits

This is workflow, transport, cleanup and build-specific repeatability evidence.
It does not establish cross-platform aerodynamic equivalence, mesh convergence,
physical accuracy, interactive Ubuntu GUI operation, silent network-partition
recovery, server crash/power-loss durability, or compatibility with other native
builds. Native artifacts and full request/log evidence are retained on the tested
host; no private aircraft or machine credentials are included in this repository.
