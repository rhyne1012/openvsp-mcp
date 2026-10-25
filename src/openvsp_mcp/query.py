"""Read native capability, analysis-input and parameter metadata in one process."""

import copy
import hashlib
import json
import math
import re
import tempfile
import time
import uuid
from pathlib import Path
from shutil import copy2, which
from threading import Lock
from xml.etree import ElementTree as ET

from . import core
from .describe import describe_geometry
from .models import OpenVSPRequest, ParameterEditRequest, QueryRequest
from .native import DOCUMENTED_ANALYSIS_VERSIONS
from .runtime import check_cancelled, cpu_allocation, deadline_scope

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


class NativeQueryError(RuntimeError):
    """A failed native query with machine-readable diagnostics where available."""

    def __init__(self, message, *, native_errors=None, phase="query", exit_code=None):
        super().__init__(message)
        self.native_errors = native_errors or []
        self.phase = phase
        self.exit_code = exit_code


_PARM_JSON = r"""
string ParmJSON(string id) {
 return "{\"id\":"+J(id)+",\"name\":"+J(GetParmName(id))
 +",\"group\":"+J(GetParmGroupName(id))+",\"container_id\":"+J(GetParmContainer(id))
 +",\"description\":"+J(GetParmDescript(id))+",\"type_code\":"+formatInt(GetParmType(id))
 +",\"value\":"+N(GetParmVal(id))+",\"lower\":"+N(GetParmLowerLimit(id))
 +",\"upper\":"+N(GetParmUpperLimit(id))+"}";
}
"""


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Non-finite query value: {value}")
    return number


def _page(request, field, size="ids.length()"):
    return [
        f'result+=",\\"total\\":"+formatUInt({size})+",\\"{field}\\":[";',
        # Clamp before adding the limit: offsets may exceed AngelScript uint.
        f"uint first={min(request.offset, 4294967295)}; if(first>{size}) first={size};",
        f"uint end={size}; if({size}-first>{request.limit}) end=first+{request.limit};",
        'for(uint i=first;i<end;i++) { if(i>first) result+=",";',
    ]


def _containers():
    # Modes have Parm containers but are omitted from FindContainers' linkable list.
    return [
        "array<string> containers=FindContainers(); array<string> modeContainers=GetAllModes();",
        "for(uint k=0;k<modeContainers.length();k++) if(containers.find(modeContainers[k])<0) containers.insertLast(modeContainers[k]);",
    ]


def _select_container(request):
    q = core._quote
    if request.container_id:
        return [
            f"string selected={q(request.container_id)};",
            'if(containers.find(selected)<0) return Fail("Unknown container ID","select_container");',
        ]
    return [
        'string selected=""; uint occurrence=0;',
        "for(uint k=0;k<containers.length();k++) {",
        f"if(GetContainerName(containers[k])!={q(request.container_name)}) continue;",
        f"if(occurrence=={min(request.container_index, 4294967295)}) {{ selected=containers[k]; break; }} occurrence++; }}",
        'if(selected.length()==0) return Fail("Unknown container name/index","select_container");',
    ]


def _script(
    request: QueryRequest,
    source: Path | None,
    token: str,
    *,
    native_version: str | None = None,
    design_variables: Path | None = None,
    design_ids: list[str] | None = None,
) -> str:
    q = core._quote
    errors = q("MCP_NATIVE_ERRORS_" + token + ":")
    lines = [
        _JSON,
        _PARM_JSON,
        f"""
bool Errors(string phase) {{
 if(GetNumTotalErrors()==0) return false;
 string rows="["; bool first=true;
 while(GetNumTotalErrors()>0) {{
  ErrorObj e=PopLastError(); if(!first) rows+=","; first=false;
  rows+="{{\\\"code\\\":"+formatInt(e.GetErrorCode())+",\\\"message\\\":"+J(e.GetErrorString())+",\\\"phase\\\":"+J(phase)+"}}";
 }}
 Print({errors}+rows+"]"); return true;
}}
int Fail(string message, string phase) {{
 Print({errors}+"[{{\\\"code\\\":null,\\\"message\\\":"+J(message)+",\\\"phase\\\":"+J(phase)+"}}]"); return 3;
}}
""",
        "int main() { ClearVSPModel();",
    ]
    if source:
        lines += [f"ReadVSPFile({q(source)});", 'if(Errors("load_model")) return 5;']
    if design_variables:
        loader = "ReadApplyXDDMFile" if design_variables.suffix == ".xddm" else "ReadApplyDESFile"
        lines += [
            "array<string> expectedVars={" + ",".join(q(p) for p in (design_ids or [])) + "};",
            'for(uint j=0;j<expectedVars.length();j++) if(!ValidParm(expectedVars[j])) return Fail("Unknown design variable parameter: "+expectedVars[j],"load_design_variables");',
            f"{loader}({q(design_variables)});",
            'if(Errors("load_design_variables")) return 5;',
            'if(uint(GetNumDesignVars())!=expectedVars.length()) return Fail("Imported design-variable count does not match the input","load_design_variables");',
            'for(int j=0;j<GetNumDesignVars();j++) if(expectedVars.find(GetDesignVar(j))<0) return Fail("Imported design-variable membership does not match the input","load_design_variables");',
        ]
    lines += ['string result="{\\"openvsp_version\\":"+J(GetVSPVersion());']
    if request.kind == "capabilities":
        lines += ['result+=",\\"analyses\\":"+Strings(ListAnalysis());']
    elif request.kind == "analysis":
        name = q(request.analysis_name)
        safe_versions = " || ".join(
            f"GetVSPVersion()=={q(version)}" for version in sorted(DOCUMENTED_ANALYSIS_VERSIONS)
        )
        lines += [
            f"bool docsAvailable=({safe_versions});",
            f"array<string> analyses=ListAnalysis(); if(analyses.find({name})<0) return 2;",
            f"SetAnalysisInputDefaults({name});",
            f"array<string> names=GetAnalysisInputNames({name});",
            'result+=",\\"inputs\\":[";',
            'for(uint i=0;i<names.length();i++) { if(i>0) result+=",";',
            f"string key=names[i]; int type=GetAnalysisInputType({name},key);",
            f"int count=GetNumAnalysisInputData({name},key);",
            'result+="{\\"name\\":"+J(key)+",\\"type_code\\":"+formatInt(type);',
            (
                f'if(docsAvailable) result+=",\\"description\\":"+J(GetAnalysisInputDoc({name},key));'
                ' else result+=",\\"description\\":null";'
            ),
            'result+=",\\"blocks\\":[";',
            'for(int j=0;j<count;j++) { if(j>0) result+=",";',
            f"if(type==INT_DATA) result+=Ints(GetIntAnalysisInput({name},key,j));",
            f"else if(type==DOUBLE_DATA) result+=Doubles(GetDoubleAnalysisInput({name},key,j));",
            f"else if(type==STRING_DATA) result+=Strings(GetStringAnalysisInput({name},key,j));",
            'else result+="null";',
            '} result+="]}"; } result+="]";',
        ]

    elif request.kind == "parameters":
        if request.parm_ids:
            lines.append("array<string> ids={" + ",".join(q(p) for p in request.parm_ids) + "};")
        elif request.container_id or request.container_name:
            lines += [
                *_containers(),
                *_select_container(request),
                "array<string> ids=FindContainerParmIDs(selected);",
            ]
        else:
            lines += ["array<string> geoms=FindGeoms(); array<string> ids;"]
            if request.geom_id:
                lines += [
                    f'if(geoms.find({q(request.geom_id)})<0) return Fail("Unknown geometry ID","select_geometry");',
                    f"geoms={{{q(request.geom_id)}}};",
                ]
            lines += [
                "for(uint i=0;i<geoms.length();i++) {",
                "array<string> p=GetGeomParmIDs(geoms[i]);",
                "for(uint j=0;j<p.length();j++) ids.insertLast(p[j]); }",
            ]
        # Validate the whole selection even when pagination would hide a bad ID.
        lines += [
            'for(uint j=0;j<ids.length();j++) if(!ValidParm(ids[j])) return Fail("Unknown parameter ID: "+ids[j],"select_parameters");',
            *_page(request, "parameters"),
            'result+=ParmJSON(ids[i]); } result+="]";',
        ]
    elif request.kind == "containers":
        lines += [*_containers(), "array<string> ids=containers;"]
        if request.container_id or request.container_name:
            lines += [*_select_container(request), "ids={selected};"]
        lines += [
            *_page(request, "containers"),
            "string id=ids[i]; array<string> parms=FindContainerParmIDs(id); uint nameIndex=0;",
            "for(uint k=0;k<containers.length();k++) { if(containers[k]==id) break; if(GetContainerName(containers[k])==GetContainerName(id)) nameIndex++; }",
            'result+="{\\"name_index\\":"+formatUInt(nameIndex)+",";',
            'result+="\\"id\\":"+J(id)+",\\"name\\":"+J(GetContainerName(id));',
            'result+=",\\"groups\\":"+Strings(FindContainerGroupNames(id));',
            'result+=",\\"parameter_count\\":"+formatUInt(parms.length())+"}";',
            '} result+="]";',
        ]
    elif request.kind == "sets":
        lines += [
            "uint count=GetNumSets();",
            *_page(request, "sets", "count"),
            'result+="{\\"index\\":"+formatUInt(i)+",\\"name\\":"+J(GetSetName(i));',
            'result+=",\\"builtin\\":"+(i<uint(SET_FIRST_USER)?"true":"false");',
            'result+=",\\"geom_ids\\":"+Strings(GetGeomSetAtIndex(i))+"}";',
            '} result+="]";',
            'result+=",\\"none_set_index\\":"+formatInt(SET_NONE);',
        ]
    elif request.kind == "modes":
        lines += [
            "array<string> ids=GetAllModes();",
            *_page(request, "modes"),
            "string id=ids[i];",
            'result+="{\\"id\\":"+J(id)+",\\"name\\":"+J(GetContainerName(id));',
            'result+=",\\"normal_set\\":"+N(GetParmVal(FindParm(id,"NormalSet","Sets")));',
            'result+=",\\"degen_set\\":"+N(GetParmVal(FindParm(id,"DegenSet","Sets")));',
            'result+=",\\"group_ids\\":"+Strings(ModeGetAllGroups(id));',
            'result+=",\\"setting_ids\\":"+Strings(ModeGetAllSettings(id))+"}";',
            '} result+="]";',
        ]
    elif request.kind == "design_variables":
        lines += [
            "uint count=GetNumDesignVars();",
            *_page(request, "design_variables", "count"),
            "string id=GetDesignVar(i);",
            'if(!ValidParm(id)) return Fail("Unknown design variable parameter: "+id,"design_variables");',
            'result+="{\\"index\\":"+formatUInt(i)+",\\"parm_id\\":"+J(id);',
            'result+=",\\"type_code\\":"+formatInt(GetDesignVarType(i));',
            'result+=",\\"parameter\\":"+ParmJSON(id)+"}";',
            '} result+="]";',
        ]
        if source:
            lines.append(f"WriteXDDMFile({q(source.parent / 'design_variables.xddm')});")
    elif request.kind == "geometry":
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", native_version or "")
        clone_api = bool(match and tuple(map(int, match.groups())) >= (3, 55, 0))
        lines += ["array<string> all=FindGeoms(); array<string> ids=all;"]
        if request.geom_id:
            lines += [
                f'if(ids.find({q(request.geom_id)})<0) return Fail("Unknown geometry ID","select_geometry");',
                f"ids={{{q(request.geom_id)}}};",
            ]
        lines += [
            *_page(request, "geometries"),
            "string id=ids[i]; string kind=GetGeomTypeName(id);",
            'string parent=GetGeomParent(id); string original=""; string effective=kind;',
            'array<string> chain; string resolution="not_clone";',
        ]
        if clone_api:
            lines += [
                'if(kind=="Clone" || kind=="CLONE") {',
                'original=GetGeomCloneOriginal(id); string current=original; effective=""; resolution="empty";',
                "while(current.length()>0) {",
                'if(current==id || chain.find(current)>=0) { resolution="cycle"; break; }',
                'chain.insertLast(current); if(all.find(current)<0) { resolution="missing"; break; }',
                "string currentType=GetGeomTypeName(current);",
                'if(currentType!="Clone" && currentType!="CLONE") { effective=currentType; resolution="resolved"; break; }',
                "current=GetGeomCloneOriginal(current); } }",
            ]
        else:
            lines += [
                'if(kind=="Clone" || kind=="CLONE") { resolution="unavailable"; effective=""; }',
            ]
        lines += [
            'result+="{\\"id\\":"+J(id)+",\\"name\\":"+J(GetGeomName(id));',
            'result+=",\\"type\\":"+J(kind)+",\\"effective_type\\":"+(effective.length()>0?J(effective):"null");',
            'result+=",\\"parent_id\\":"+(parent=="NONE"||parent.length()==0?"null":J(parent));',
            'result+=",\\"children_ids\\":"+Strings(GetGeomChildren(id));',
            'result+=",\\"clone_original_id\\":"+(original.length()>0?J(original):"null");',
            'result+=",\\"clone_chain\\":"+Strings(chain)+",\\"clone_resolution\\":"+J(resolution);',
            'result+=",\\"main_surface_count\\":"+formatInt(GetNumMainSurfs(id));',
            'result+=",\\"total_surface_count\\":"+formatInt(GetTotalNumSurfs(id));',
            'result+=",\\"surfaces\\":[";',
            'for(int j=0;j<GetNumMainSurfs(id);j++) { if(j>0) result+=",";',
            'result+="{\\"main_index\\":"+formatInt(j)+",\\"surface_type_code\\":"+formatInt(GetGeomVSPSurfType(id,j));',
            'result+=",\\"cfd_type_code\\":"+formatInt(GetGeomVSPSurfCfdType(id,j))+"}"; }',
            'result+="],\\"behavior_parameters\\":["; bool firstParm=true; string flip="null"; string flipMask="null";',
            "array<string> parms=GetGeomParmIDs(id); for(uint j=0;j<parms.length();j++) {",
            'if(GetParmName(parms[j])=="Flip_Flag") { flip=GetParmVal(parms[j])!=0?"true":"false"; flipMask=N(GetParmVal(parms[j])); }',
            'if(GetParmGroupName(parms[j])!="Behavior" && GetParmName(parms[j])!="Flip_Flag") continue;',
            'if(!firstParm) result+=","; firstParm=false; result+=ParmJSON(parms[j]); }',
            'result+="],\\"flip\\":"+flip+",\\"flip_mask\\":"+flipMask+"}"; } result+="]";',
            'result+=",\\"clone_api_available\\":' + ("true" if clone_api else "false") + '";',
        ]
    lines += [
        'result+="}";',
        'if(Errors("query")) return 5;',
        f"Print({q('MCP_QUERY_' + token + ':')}+result); return 0; }}",
    ]
    return "\n".join(lines)


def _preflight_design_variables(path: Path) -> tuple[list[str], dict[str, str]]:
    """Reject malformed or ambiguous files before calling the permissive native readers."""
    identifiers = []
    bounds_sources = {}

    def numeric(value, field):
        if value is None:
            raise ValueError(f"Missing {field}")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"Non-finite {field}")
        return result

    def add(identifier, value, lower=None, upper=None):
        if not identifier or not identifier.strip():
            raise ValueError("Design variable is missing its parameter ID")
        if identifier in identifiers:
            raise ValueError(f"Duplicate design variable: {identifier}")
        numeric(value, "design variable value")
        low = numeric(lower, "lower limit") if lower is not None else None
        high = numeric(upper, "upper limit") if upper is not None else None
        if low is not None and high is not None and low > high:
            raise ValueError("Design-variable lower limit exceeds upper limit")
        identifiers.append(identifier)
        format_name = path.suffix[1:].upper()
        bounds_sources[identifier] = (
            f"{format_name} limits"
            if lower is not None or upper is not None
            else f"native defaults after {format_name} import"
        )

    try:
        if path.suffix == ".xddm":
            root = ET.parse(path).getroot()
            if root.tag != "Model":
                raise ValueError("XDDM root must be Model")
            for entry in root:
                if entry.tag in {"Variable", "Constant"}:
                    add(entry.get("VSPID"), entry.get("Value"), entry.get("Min"), entry.get("Max"))
        else:
            lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if not lines or not re.fullmatch(r"\d+", lines[0]):
                raise ValueError("DES must start with a nonnegative variable count")
            count = int(lines[0])
            values, limits = [], []
            in_limits = False
            for line in lines[1:]:
                if line == "#LIMITS":
                    if in_limits:
                        raise ValueError("Duplicate DES limits section")
                    in_limits = True
                elif not line.startswith("#"):
                    (limits if in_limits else values).append(line)
            if len(values) != count:
                raise ValueError("DES variable count does not match its value records")

            def des_record(line, expected):
                label, separator, data = line.rpartition(":")
                fields = data.split()
                if not separator or label.count(":") < 3 or len(fields) != expected:
                    raise ValueError("Malformed DES variable record")
                return label.split(":", 1)[0], fields

            for line in values:
                identifier, values = des_record(line, 1)
                add(identifier, values[0])
            seen_limits = set()
            for line in limits:
                identifier, values = des_record(line, 2)
                if identifier not in identifiers or identifier in seen_limits:
                    raise ValueError("Unknown or duplicate DES limit parameter")
                low, high = (numeric(value, "DES limit") for value in values)
                if low > high:
                    raise ValueError("Design-variable lower limit exceeds upper limit")
                seen_limits.add(identifier)
                bounds_sources[identifier] = "DES limits"
    except (OSError, UnicodeError, ET.ParseError, ValueError) as exc:
        raise NativeQueryError(
            f"Invalid design-variable input: {exc}", phase="validate_design_variables"
        ) from exc
    return identifiers, bounds_sources


def _design_variable_bounds(path: Path, result: dict):
    """XDDM is the native API's only exported representation of user limits."""
    try:
        root = ET.parse(path).getroot()
        bounds = {}
        for element in root:
            if element.tag not in {"Variable", "Constant"}:
                continue
            limits = {}
            for name, attribute in (("lower", "Min"), ("upper", "Max")):
                value = element.get(attribute)
                number = float(value) if value is not None else None
                if number is not None and not math.isfinite(number):
                    raise ValueError("Non-finite design variable limit")
                limits[name] = number
            bounds[element.get("VSPID")] = limits
        for item in result["design_variables"]:
            item["design_bounds"] = bounds.get(item["parm_id"], {"lower": None, "upper": None})
    except (OSError, ET.ParseError, ValueError) as exc:
        raise NativeQueryError(
            f"Invalid native design-variable bounds: {exc}", phase="read_bounds"
        ) from exc


def query_model(request: QueryRequest) -> dict:
    """Only capability enumeration without a model is cached (five-minute TTL)."""
    check_cancelled()
    started = time.monotonic()
    with deadline_scope(started + request.timeout_seconds):
        return _query_model(request, started)


def _query_model(request: QueryRequest, started: float) -> dict:
    cache_key = None
    if request.kind == "capabilities" and request.geometry_file is None:
        binary = Path(which(core.OPENVSP_BIN) or core.OPENVSP_BIN).resolve()
        try:
            stat = binary.stat()
            cache_key = (
                str(binary),
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
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
    native_version = None
    if request.kind == "geometry":
        # A runtime if cannot guard missing AngelScript names: probe before compiling.
        native_version = query_model(QueryRequest(timeout_seconds=request.timeout_seconds)).get(
            "openvsp_version"
        )
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
        design_snapshot = None
        design_hash = None
        design_ids = None
        bounds_sources = {}
        if request.design_variables_file:
            original_design = Path(request.design_variables_file).expanduser().resolve()
            design_snapshot = run / ("input_design_variables" + original_design.suffix.lower())
            copy2(original_design, design_snapshot)
            design_hash = hashlib.sha256(design_snapshot.read_bytes()).hexdigest()
            design_ids, bounds_sources = _preflight_design_variables(design_snapshot)
        check_cancelled()
        token = uuid.uuid4().hex
        script, log = run / "query.vspscript", run / "query.log"
        script.write_text(
            _script(
                request,
                snapshot,
                token,
                native_version=native_version,
                design_variables=design_snapshot,
                design_ids=design_ids,
            ),
            encoding="utf-8",
        )
        with deadline_scope(started + request.timeout_seconds), cpu_allocation(1):
            rc = core._run_script(script, log, request.timeout_seconds)
        output = log.read_text(errors="replace")
        prefix = "MCP_QUERY_" + token + ":"
        rows = [
            s.strip()[len(prefix) :] for s in output.splitlines() if s.strip().startswith(prefix)
        ]
        if rc != 0 or len(rows) != 1:
            native_errors = []
            error_prefix = "MCP_NATIVE_ERRORS_" + token + ":"
            for line in output.splitlines():
                if line.strip().startswith(error_prefix):
                    try:
                        errors = json.loads(line.strip()[len(error_prefix) :])
                        if isinstance(errors, list):
                            native_errors.extend(
                                error for error in errors if isinstance(error, dict)
                            )
                    except (ValueError, TypeError):
                        pass
            phase = native_errors[0].get("phase", "query") if native_errors else "query"
            details = json.dumps(native_errors) if native_errors else output[-4000:]
            raise NativeQueryError(
                f"Native {request.kind} query failed (exit {rc}, phase {phase}): {details}",
                native_errors=native_errors,
                phase=phase,
                exit_code=rc,
            )
        try:
            result = json.loads(
                rows[0],
                parse_float=_finite_float,
                parse_constant=_finite_float,
            )
        except ValueError as exc:
            raise NativeQueryError(f"Invalid native query output: {exc}", phase="decode") from exc
        if request.kind == "design_variables":
            _design_variable_bounds(run / "design_variables.xddm", result)
            for item in result["design_variables"]:
                item["design_bounds_source"] = bounds_sources[item["parm_id"]]
        if request.kind == "containers":
            stored_ids = {node.text for node in ET.parse(snapshot).iterfind(".//ParmContainer/ID")}
            for item in result["containers"]:
                item["id_persisted_in_source"] = item["id"] in stored_ids
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
            "available"
            if result.get("openvsp_version") in DOCUMENTED_ANALYSIS_VERSIONS
            else "omitted: unaudited or unsafe AngelScript GetAnalysisInputDoc binding"
        )
        result["defaults_source"] = "loaded model" if snapshot else "empty OpenVSP model"
        result["scope"] = "INT/DOUBLE/STRING values; unsupported data types have null blocks."
    if request.kind not in {"capabilities", "analysis"}:
        result.update(offset=request.offset, limit=request.limit)
    if request.kind == "design_variables":
        result["membership_source"] = (
            "separate design-variable file applied to the isolated model"
            if design_hash
            else "not stored in .vsp3; supply design_variables_file"
        )
        result["bounds_scope"] = (
            "design_bounds are optimization bounds exported by OpenVSP; "
            "parameter.lower/upper are native Parm limits"
        )
        if design_hash:
            result["design_variables_input_sha256"] = design_hash
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
