"""A matching version alone must not imply a validated native build."""

import copy
import importlib.util
from pathlib import Path

import pytest

from openvsp_mcp import validation

spec = importlib.util.spec_from_file_location(
    "baseline", Path(__file__).parents[1] / "examples/simple_aircraft/baseline.py"
)
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def fixture(build):
    return {
        "checks": {
            name: {"status": "ok", "version": build[name + "_version"]}
            for name in ("openvsp", "vspaero")
        },
        "native_identity": {
            name: {"sha256": build[name + "_sha256"]} for name in ("openvsp", "vspaero")
        },
    }


@pytest.mark.parametrize("build", validation.VALIDATED_BUILDS)
def test_audited_build_and_strict_coefficient_baseline(monkeypatch, build):
    current = {
        key: build[key]
        for key in ("system", "machine", "distribution_id", "distribution_version")
        if key in build
    }
    monkeypatch.setattr(validation, "platform_info", lambda: current)
    data = fixture(build)
    result = validation.validation_metadata(data["checks"], data["native_identity"])
    assert result["tested_native_build"]
    values, tolerance = baseline.select_baseline(data)
    assert tolerance == 1e-8 and set(values) == {"CLtot", "CDtot"}
    for name in ("openvsp", "vspaero"):
        changed = copy.deepcopy(data)
        changed["native_identity"][name]["sha256"] = "0" * 64
        assert not validation.validation_metadata(changed["checks"], changed["native_identity"])[
            "tested_native_build"
        ]
        with pytest.raises(RuntimeError, match="No audited"):
            baseline.select_baseline(changed)


@pytest.mark.parametrize(
    "changes",
    [
        {"machine": "aarch64"},
        {"distribution_version": "26.04"},
        {"distribution_id": "debian"},
        {"distribution_id": None},
    ],
)
def test_other_linux_platforms_are_not_claimed(monkeypatch, changes):
    build = validation.VALIDATED_BUILDS[1]
    current = {
        key: build[key] for key in ("system", "machine", "distribution_id", "distribution_version")
    }
    current.update(changes)
    monkeypatch.setattr(validation, "platform_info", lambda: current)
    data = fixture(build)
    assert not validation.validation_metadata(data["checks"], data["native_identity"])[
        "tested_native_build"
    ]


def test_missing_os_release_is_conservative(monkeypatch):
    monkeypatch.setattr(validation.platform, "system", lambda: "Linux")
    monkeypatch.setattr(validation.platform, "machine", lambda: "x86_64")

    def missing():
        raise OSError("unavailable")

    monkeypatch.setattr(validation.platform, "freedesktop_os_release", missing)
    assert validation.platform_info()["distribution_id"] is None
