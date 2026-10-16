"""Select an audited smoke baseline by both native executable identities."""

import json
from pathlib import Path


def select_baseline(health):
    data = json.loads(Path(__file__).with_name("baselines.json").read_text())
    for build in data["native_builds"]:
        if all(
            health["checks"][name]["version"] == build[name + "_version"]
            and health["native_identity"][name].get("sha256") == build[name + "_sha256"]
            for name in ("openvsp", "vspaero")
        ):
            return build["coefficients"], data["absolute_tolerance"]
    raise RuntimeError(
        "No audited coefficient baseline for this native binary pair. "
        "Record both hashes and validate repeatability before adding a build; "
        "do not widen the tolerance or infer equivalence from version strings."
    )
