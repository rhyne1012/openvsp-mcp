"""Install and probe a new local environment before atomically selecting it.

POSIX only. Does not edit Codex config, modify an existing venv, or delete releases.
Point your MCP client at <runtime-root>/current/bin/python -m openvsp_mcp.
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
import venv
from pathlib import Path

if os.name == "posix":
    import fcntl


def install(package: str, runtime_root: Path, requirements: str | None = None) -> Path:
    if os.name != "posix":
        raise RuntimeError("This installer requires POSIX symlinks and flock")
    runtime_root = runtime_root.expanduser().resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    current = runtime_root / "current"
    if current.exists() and not current.is_symlink():
        raise RuntimeError("current must be a symlink; refusing to replace an existing directory")
    with (runtime_root / "install.lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        candidate = runtime_root / "releases" / uuid.uuid4().hex
        candidate.parent.mkdir(exist_ok=True)
        record = candidate.parent / (candidate.name + ".json")
        data = {
            "status": "preparing",
            "package": package,
            "candidate": str(candidate),
            "previous": str(current.resolve()) if current.is_symlink() else None,
        }
        record.write_text(json.dumps(data, indent=2) + "\n")
        try:
            # The candidate stays at its final path; venv shebangs are never relocated.
            venv.EnvBuilder(with_pip=True).create(candidate)
            python = candidate / "bin/python"
            args = [str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check"]
            if requirements:
                args += ["-r", requirements]
            subprocess.run(args + [package], check=True, timeout=600)
            subprocess.run([str(python), "-I", "-m", "pip", "check"], check=True, timeout=30)
            health = subprocess.run(
                [str(python), "-I", "-m", "openvsp_mcp", "--health"],
                check=True,
                capture_output=True,
                text=True,
                timeout=45,
            )
            data["health"] = json.loads(health.stdout)
            if data["health"].get("status") != "ok":
                raise RuntimeError("Candidate health check was not successful")
            link = runtime_root / (".current-" + uuid.uuid4().hex)
            try:
                link.symlink_to(candidate, target_is_directory=True)
                os.replace(link, current)
            finally:
                link.unlink(missing_ok=True)
        except Exception as exc:
            data.update(status="failed", error=str(exc))
            record.write_text(json.dumps(data, indent=2) + "\n")
            raise
        data["status"] = "active"
        record.write_text(json.dumps(data, indent=2) + "\n")
        return current / "bin/python"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package", required=True, help="A pinned wheel, package version or source"
    )
    parser.add_argument(
        "--runtime-root", type=Path, required=True, help="Local, non-cloud directory"
    )
    parser.add_argument("--requirements", help="Optional pinned runtime dependencies")
    args = parser.parse_args()
    print(install(args.package, args.runtime_root, args.requirements))


if __name__ == "__main__":
    if os.name != "posix":
        sys.exit("This installer requires POSIX symlinks and flock")
    main()
