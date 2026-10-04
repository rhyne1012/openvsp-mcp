"""Native identity and explicitly audited API capabilities, without launching binaries."""

import hashlib
from functools import lru_cache
from pathlib import Path
from shutil import which

# Do not call the broken string-return binding on older/unknown builds.
DOCUMENTED_ANALYSIS_VERSIONS = {"OpenVSP 3.53.0", "OpenVSP 3.53.1"}
TESTED_VERSION_PAIRS = {("3.53.1", "7.2.2")}


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stamp(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=32)
def _cached_digest(path, stamp):
    result = digest_file(path)
    if _stamp(Path(path)) != stamp:
        raise RuntimeError("Native executable changed while hashing")
    return result


def executable_identity(configured, *, refresh=False):
    path = Path(which(configured) or configured).resolve()
    stamp = _stamp(path)
    if refresh:
        result = digest_file(path)
        if _stamp(path) != stamp:
            raise RuntimeError("Native executable changed while hashing")
    else:
        result = _cached_digest(str(path), stamp)
    return {"path": str(path), "sha256": result}


def refresh_identities():
    """A resume boundary always performs full content verification again."""
    _cached_digest.cache_clear()


def native_identity(openvsp, vspaero, *, refresh=False, required=True):
    result = {}
    for name, configured in [("openvsp", openvsp), ("vspaero", vspaero)]:
        try:
            result[name] = executable_identity(configured, refresh=refresh)
        except OSError:
            if required:
                raise
            # The actual process launch still fails if a required binary is absent.
            result[name] = {"path": str(configured), "status": "unavailable"}
    return result
