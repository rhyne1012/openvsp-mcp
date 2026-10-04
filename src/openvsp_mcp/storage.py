"""Batch v2: immutable specification/details and an atomic compact status index."""

import copy
import hashlib
import json
import os
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path

from .models import BatchRequest
from .native import digest_file

DETAIL_FIELDS = {"response", "artifact_hashes", "response_sha256", "attempts"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def nonfinite(value):
    raise ValueError(f"Non-finite manifest value: {value}")


def read_json(path, limit=64 * 1024 * 1024):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Batch record exceeds its size limit")
    return json.loads(raw, parse_constant=nonfinite)


def atomic_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".batch-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _stamp(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


@lru_cache(maxsize=16)
def _specification(path, stamp, expected):
    spec = read_json(path)
    if fingerprint(spec) != expected or _stamp(Path(path)) != stamp:
        raise ValueError("Batch specification fingerprint mismatch")
    request = BatchRequest.model_validate(spec["request"])
    return spec, tuple(c.case_id for c in request.cases)


def specification(directory, expected, *, refresh=False):
    path = (directory / "spec.json").resolve()
    if not path.is_relative_to(directory):
        raise ValueError("Batch specification escaped its directory")
    if refresh:
        spec, ids = _specification.__wrapped__(str(path), _stamp(path), expected)
    else:
        spec, ids = _specification(str(path), _stamp(path), expected)
    return copy.deepcopy(spec), ids


def compact_index(data):
    if data["schema_version"] == 1:
        return data
    index = {key: value for key, value in data.items() if key not in {"spec", "cases"}}
    index["cases"] = [
        {key: value for key, value in row.items() if key not in DETAIL_FIELDS}
        | {"attempt_count": len(row.get("attempts", []))}
        for row in data["cases"]
    ]
    return index


def save_case(directory, row):
    parent = (directory / "cases" / row["case_id"]).resolve()
    if not parent.is_relative_to(directory):
        raise ValueError("Case detail escaped its directory")
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / ("record-" + uuid.uuid4().hex + ".json")
    detail = {key: row[key] for key in DETAIL_FIELDS if key in row}
    detail["case_id"] = row["case_id"]
    atomic_json(path, detail)
    row.update(detail_file=str(path.relative_to(directory)), detail_sha256=digest_file(path))


def load_case(directory, row):
    if not row.get("detail_file"):
        if row["status"] == "success" or row.get("attempt_count", 0):
            raise ValueError("Case is missing its committed detail record")
        return row | {"attempts": []}
    path = (directory / row["detail_file"]).resolve()
    if not path.is_relative_to(directory):
        raise ValueError("Case detail integrity mismatch")
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != row.get("detail_sha256"):
        raise ValueError("Case detail integrity mismatch")
    detail = json.loads(raw, parse_constant=nonfinite)
    if not isinstance(detail, dict) or not isinstance(detail.get("attempts"), list):
        raise TypeError("Invalid case detail record")
    if detail.get("case_id") != row["case_id"] or set(detail) - DETAIL_FIELDS - {"case_id"}:
        raise ValueError("Case detail does not match index")
    if len(detail.get("attempts", [])) != row.get("attempt_count", 0):
        raise ValueError("Case attempt count does not match index")
    return row | detail
