"""Failed candidate validation must not switch the previous working environment."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX local installer")


@pytest.mark.parametrize("failure", ["install", "health", None])
def test_candidate_is_validated_before_pointer_switch(monkeypatch, tmp_path, failure):
    spec = importlib.util.spec_from_file_location(
        "installer", Path(__file__).parents[1] / "scripts/install_local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "runtime"
    root.mkdir()
    old = root / "old"
    old.mkdir()
    (root / "current").symlink_to(old, target_is_directory=True)

    def create(path):
        (path / "bin").mkdir(parents=True)
        (path / "bin/python").write_text("candidate")

    monkeypatch.setattr(module.venv, "EnvBuilder", lambda **k: SimpleNamespace(create=create))

    def run(args, **kwargs):
        assert (root / "current").resolve() == old
        if (failure == "install" and "install" in args) or (
            failure == "health" and "--health" in args
        ):
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, json.dumps({"status": "ok"}), "")

    monkeypatch.setattr(module.subprocess, "run", run)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            module.install("package.whl", root)
        assert (root / "current").resolve() == old
        assert (
            json.loads(next((root / "releases").glob("*.json")).read_text())["status"] == "failed"
        )
    else:
        selected = module.install("package.whl", root)
        assert selected.is_file() and (root / "current").resolve() != old
    assert old.is_dir()
