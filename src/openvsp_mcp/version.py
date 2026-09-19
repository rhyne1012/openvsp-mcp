"""Package identity, independent of the MCP SDK's version."""

import hashlib
from importlib.metadata import version
from pathlib import Path

__version__ = "0.4.0"


def version_info() -> dict[str, str]:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.suffix in {".py", ".vsp3"}:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return {
        "package_version": __version__,
        "mcp_sdk_version": version("mcp"),
        "package_sha256": digest.hexdigest(),
    }
