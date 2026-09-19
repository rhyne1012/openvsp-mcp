"""Reusable build commands and solver preflight in the actual loaded model."""


def simple_aircraft_commands() -> list[str]:
    commands = [
        'SetSetName(3,"Thick_Fuselage"); SetSetName(4,"Thin_Lifting_Surfaces");',
        'string f=AddGeom("POD",""); SetGeomName(f,"Fuselage");',
        'SetParmVal(f,"Length","Design",8); SetParmVal(f,"FineRatio","Design",8);',
        'SetParmVal(f,"Tess_U","Shape",25); SetParmVal(f,"Tess_W","Shape",17);',
        "SetSetFlag(f,3,true); Update();",
    ]
    for name, span, root, tip, x, z, sweep, vertical in [
        ("Main_Wing", 5, 1.6, 0.8, 2.5, 0, 5, False),
        ("Horizontal_Tail", 1.8, 0.9, 0.5, 6.2, 0.2, 10, False),
        ("Vertical_Tail", 1.5, 1.2, 0.5, 6.1, 0, 20, True),
    ]:
        lines = [
            f'{{ string w=AddGeom("WING",""); SetGeomName(w,"{name}");',
            "SetDriverGroup(w,1,SPAN_WSECT_DRIVER,ROOTC_WSECT_DRIVER,TIPC_WSECT_DRIVER);",
        ]
        for parm, group, value in [
            ("Span", "XSec_1", span),
            ("Root_Chord", "XSec_1", root),
            ("Tip_Chord", "XSec_1", tip),
            ("Sweep", "XSec_1", sweep),
            ("X_Rel_Location", "XForm", x),
            ("Z_Rel_Location", "XForm", z),
            ("Tess_W", "Shape", 25),
            ("SectTess_U", "XSec_1", 17),
            ("Camber", "XSecCurve_0", 0),
            ("Camber", "XSecCurve_1", 0),
            ("ThickChord", "XSecCurve_0", 0.12),
            ("ThickChord", "XSecCurve_1", 0.12),
        ]:
            lines.append(f'SetParmVal(w,"{parm}","{group}",{value});')
        if vertical:
            lines += [
                'SetParmVal(w,"Sym_Planar_Flag","Sym",0);',
                'SetParmVal(w,"X_Rel_Rotation","XForm",90);',
            ]
        commands.append("\n".join(lines + ["SetSetFlag(w,4,true); Update(); }"]))
    return commands


def preflight_commands(thick: int, thin: int) -> list[str]:
    return [
        f"""
int thick_set={thick}; int thin_set={thin};
if (thick_set>=GetNumSets() || thin_set>=GetNumSets()) {{
 Print("PREFLIGHT_ERROR: geometry set index does not exist"); return 8;
}}
array<string> thick_ids; array<string> thin_ids;
if(thick_set>=0) thick_ids=GetGeomSetAtIndex(thick_set);
if(thin_set>=0) thin_ids=GetGeomSetAtIndex(thin_set);
if ((thick_set>=0 && thick_ids.length()==0) ||
    (thin_set>=0 && thin_ids.length()==0) ||
    (thick_ids.length()+thin_ids.length()==0)) {{
 Print("PREFLIGHT_ERROR: selected geometry set is empty"); return 8;
}}
for(uint i=0;i<thick_ids.length();i++) {{
 if(thin_ids.find(thick_ids[i])>=0) {{
  Print("PREFLIGHT_ERROR: geometry occurs in both thick and thin sets"); return 8;
 }}
 Print("PREFLIGHT_THICK_ID:"+thick_ids[i]);
}}
for(uint i=0;i<thin_ids.length();i++) Print("PREFLIGHT_THIN_ID:"+thin_ids[i]);
if(CheckErrors()!=0) return 8;
Print("PREFLIGHT_OK");
"""
    ]
