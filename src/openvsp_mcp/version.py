"""Package identity, independent of the MCP SDK's version."""

import hashlib
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

__version__ = "0.5.0"


@lru_cache(maxsize=1)
def _version_info() -> tuple[str, str]:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.suffix in {".py", ".vsp3"}:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return version("mcp"), digest.hexdigest()


def version_info() -> dict[str, str]:
    sdk, digest = _version_info()
    return {
        "package_version": __version__,
        "mcp_sdk_version": sdk,
        "package_sha256": digest,
    }
