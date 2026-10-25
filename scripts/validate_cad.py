#!/usr/bin/env python3
"""Independently reopen CAD exports with FreeCAD/OCCT and report measured topology.

Run using FreeCAD's bundled Python (for an extracted AppImage, AppRun python),
optionally supplying --freecad-lib /path/to/usr/lib. The MCP does not execute
this script automatically or treat format checks as proof of watertightness.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


def inspect_cad(path, freecad, part, expected_bbox=None, relative_tolerance=1e-6):
    shape = part.Shape()
    shape.read(str(path))
    if shape.isNull() or not shape.Faces:
        raise ValueError("CAD importer produced no faces")
    bbox = shape.BoundBox
    dimensions = [bbox.XLength, bbox.YLength, bbox.ZLength]
    if not all(math.isfinite(value) and value >= 0 for value in dimensions) or max(dimensions) == 0:
        raise ValueError("CAD importer produced an invalid bounding box")
    matches = None
    if expected_bbox is not None:
        matches = all(
            math.isclose(actual, expected, rel_tol=relative_tolerance, abs_tol=1e-7)
            for actual, expected in zip(dimensions, expected_bbox)
        )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "freecad_version": ".".join(freecad.Version()[:3]),
        "occt_version": part.OCC_VERSION,
        "read_status": "passed",
        "valid": shape.isValid(),
        "closed": shape.isClosed(),
        "faces": len(shape.Faces),
        "shells": len(shape.Shells),
        "closed_shells": sum(shell.isClosed() for shell in shape.Shells),
        "solids": len(shape.Solids),
        "bbox_dimensions_mm": dimensions,
        "expected_bbox_mm": expected_bbox,
        "dimensions_match": matches,
        "scope": "OCCT import and topology checks; no assembly fusion or CFD suitability claim.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--freecad-lib", type=Path)
    parser.add_argument("--expected-bbox-mm", nargs=3, type=float, metavar=("X", "Y", "Z"))
    parser.add_argument("--relative-tolerance", type=float, default=1e-6)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.relative_tolerance) or args.relative_tolerance <= 0:
        parser.error("relative tolerance must be finite and positive")
    if args.expected_bbox_mm is not None and not all(
        math.isfinite(value) and value >= 0 for value in args.expected_bbox_mm
    ):
        parser.error("expected bounding-box dimensions must be finite and nonnegative")
    if args.freecad_lib:
        sys.path.insert(0, str(args.freecad_lib.resolve()))
    import FreeCAD
    import Part

    reports = []
    for path in args.files:
        try:
            reports.append(
                inspect_cad(
                    path.resolve(), FreeCAD, Part, args.expected_bbox_mm, args.relative_tolerance
                )
            )
        except (OSError, ValueError, RuntimeError, Part.OCCError) as exc:
            reports.append(
                {"path": str(path.resolve()), "read_status": "failed", "error": str(exc)}
            )
    payload = json.dumps(reports, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return int(
        any(
            row["read_status"] != "passed"
            or not row.get("valid")
            or row.get("dimensions_match") is False
            for row in reports
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
