"""Read native capability, analysis-input and parameter metadata in one process."""

import copy
import hashlib
import json
import tempfile
import time
import uuid
from pathlib import Path
from shutil import copy2, which
from threading import Lock

from . import core
from .describe import describe_geometry
from .models import OpenVSPRequest, ParameterEditRequest, QueryRequest
from .runtime import check_cancelled

_cache = {}
_cache_lock = Lock()

# JSON is emitted as one nonce-prefixed line, separate from OpenVSP diagnostics.
_JSON = r"""
string J(string s) {
 string r="\""; string hex="0123456789abcdef";
 for(uint i=0;i<s.length();i++) {
  uint c=s[i];
  if(c==34) r+="\\\"";
  else if(c==92) r+="\\\\";
  else if(c<32) r+="\\u00"+hex.substr(c/16,1)+hex.substr(c%16,1);
  else r+=s.substr(i,1);
 }
 return r+"\"";
}
string N(double v) { return formatFloat(v,"",0,17); }
string Strings(array<string>@ v) {
 string s="["; for(uint i=0;i<v.length();i++) { if(i>0)s+=","; s+=J(v[i]); }
 return s+"]";
}
string Doubles(array<double>@ v) {
 string s="["; for(uint i=0;i<v.length();i++) { if(i>0)s+=","; s+=N(v[i]); }
 return s+"]";
}
string Ints(array<int>@ v) {
 string s="["; for(uint i=0;i<v.length();i++) { if(i>0)s+=","; s+=formatInt(v[i]); }
 return s+"]";
}
"""


def _script(request: QueryRequest, source: Path | None, token: str) -> str:
    q = core._quote
    lines = [_JSON, "int main() { ClearVSPModel();"]
    if source:
        lines.append(f"ReadVSPFile({q(source)});")
    lines += ['string result="{\\"openvsp_version\\":"+J(GetVSPVersion());']
    if request.kind == "capabilities":
        lines += ['result+=",\\"analyses\\":"+Strings(ListAnalysis());']
    elif request.kind == "analysis":
        name = q(request.analysis_name)
        lines += [
            f"array<string> analyses=ListAnalysis(); if(analyses.find({name})<0) return 2;",
            f"SetAnalysisInputDefaults({name});",
            f"array<string> names=GetAnalysisInputNames({name});",
            'result+=",\\"inputs\\":[";',
            'for(uint i=0;i<names.length();i++) { if(i>0) result+=",";',
            f"string key=names[i]; int type=GetAnalysisInputType({name},key);",
            f"int count=GetNumAnalysisInputData({name},key);",
            'result+="{\\"name\\":"+J(key)+",\\"type_code\\":"+formatInt(type);',
            'result+=",\\"blocks\\":[";',
            'for(int j=0;j<count;j++) { if(j>0) result+=",";',
            f"if(type==INT_DATA) result+=Ints(GetIntAnalysisInput({name},key,j));",
            f"else if(type==DOUBLE_DATA) result+=Doubles(GetDoubleAnalysisInput({name},key,j));",
            f"else if(type==STRING_DATA) result+=Strings(GetStringAnalysisInput({name},key,j));",
            'else result+="null";',
            '} result+="]}"; } result+="]";',
        ]
    else:
        if request.parm_ids:
            lines.append("array<string> ids={" + ",".join(q(p) for p in request.parm_ids) + "};")
        else:
            lines += ["array<string> geoms=FindGeoms(); array<string> ids;"]
            if request.geom_id:
                lines += [
                    f"if(geoms.find({q(request.geom_id)})<0) return 3;",
                    f"geoms={{{q(request.geom_id)}}};",
                ]
            lines += [
                "for(uint i=0;i<geoms.length();i++) {",
                "array<string> p=GetGeomParmIDs(geoms[i]);",
                "for(uint j=0;j<p.length();j++) ids.insertLast(p[j]); }",
            ]
        lines += [
            'result+=",\\"total\\":"+formatUInt(ids.length())+",\\"parameters\\":[";',
            f"uint first={request.offset}; uint end=first+{request.limit};",
            "if(end>ids.length()) end=ids.length();",
            'for(uint i=first;i<end;i++) { if(i>first) result+=",";',
            "string id=ids[i]; if(!ValidParm(id)) return 4;",
            'result+="{\\"id\\":"+J(id)+",\\"name\\":"+J(GetParmName(id));',
            'result+=",\\"group\\":"+J(GetParmGroupName(id));',
            'result+=",\\"container_id\\":"+J(GetParmContainer(id));',
            'result+=",\\"description\\":"+J(GetParmDescript(id));',
            'result+=",\\"type_code\\":"+formatInt(GetParmType(id));',
            'result+=",\\"value\\":"+N(GetParmVal(id));',
            'result+=",\\"lower\\":"+N(GetParmLowerLimit(id));',
            'result+=",\\"upper\\":"+N(GetParmUpperLimit(id))+"}";',
            '} result+="]";',
        ]
    lines += [
        'result+="}";',
        "if(GetNumTotalErrors()!=0) { while(GetNumTotalErrors()>0) {",
        "ErrorObj e=PopLastError(); Print(e.GetErrorString()); } return 5; }",
        f"Print({q('MCP_QUERY_' + token + ':')}+result); return 0; }}",
    ]
    return "\n".join(lines)


def query_model(request: QueryRequest) -> dict:
    """Only capability enumeration without a model is cached (five-minute TTL)."""
    check_cancelled()
    started = time.monotonic()
    cache_key = None
    if request.kind == "capabilities" and request.geometry_file is None:
        binary = Path(which(core.OPENVSP_BIN) or core.OPENVSP_BIN).resolve()
        try:
            stat = binary.stat()
            cache_key = (str(binary), stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except OSError:
            pass
        with _cache_lock:
            cached = _cache.get(cache_key) if cache_key else None
            if cached and started - cached[0] < 300:
                result = copy.deepcopy(cached[1])
                result.update(
                    cache_hit=True,
                    cache_age_seconds=started - cached[0],
                    elapsed_seconds=time.monotonic() - started,
                )
                return result
    with tempfile.TemporaryDirectory(prefix="openvsp-query-") as tmp:
        run = Path(tmp)
        snapshot = None
        source_hash = None
        if request.geometry_file:
            original = Path(request.geometry_file).expanduser().resolve()
            snapshot = run / "source.vsp3"
            copy2(original, snapshot)
            describe_geometry(str(snapshot))
            source_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        token = uuid.uuid4().hex
        script, log = run / "query.vspscript", run / "query.log"
        script.write_text(_script(request, snapshot, token), encoding="utf-8")
        rc = core._run_script(script, log, request.timeout_seconds)
        output = log.read_text(errors="replace")
        prefix = "MCP_QUERY_" + token + ":"
        rows = [
            s.strip()[len(prefix) :] for s in output.splitlines() if s.strip().startswith(prefix)
        ]
        if rc != 0 or len(rows) != 1:
            raise RuntimeError(f"Native {request.kind} query failed (exit {rc}): {output[-4000:]}")
        try:
            result = json.loads(
                rows[0],
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"Non-finite query value: {value}")
                ),
            )
        except ValueError as exc:
            raise RuntimeError(f"Invalid native query output: {exc}") from exc
    result.update(
        kind=request.kind,
        cache_hit=False,
        cache_age_seconds=0,
        elapsed_seconds=time.monotonic() - started,
    )
    if source_hash:
        result["input_sha256"] = source_hash
    if request.kind == "analysis":
        result["analysis_name"] = request.analysis_name
        result["description_status"] = (
            "omitted: unsafe audited AngelScript GetAnalysisInputDoc binding"
        )
        result["defaults_source"] = "loaded model" if snapshot else "empty OpenVSP model"
        result["scope"] = "INT/DOUBLE/STRING values; unsupported data types have null blocks."
    if request.kind == "parameters":
        result.update(offset=request.offset, limit=request.limit)
    if cache_key:
        with _cache_lock:
            _cache.clear()
            _cache[cache_key] = (time.monotonic(), copy.deepcopy(result))
    return result


def set_parameters(request: ParameterEditRequest):
    return core.execute_openvsp(
        OpenVSPRequest(
            geometry_file=request.geometry_file,
            output_dir=request.output_dir,
            timeout_seconds=request.timeout_seconds,
            run_vspaero=False,
            case_name="parameters",
            parameter_edits=request.edits,
        )
    )
