"""STEP 어셈블리를 부품별 노드가 있는 GLB로 변환한다.

사용법:
    .venv/Scripts/python tools/step_to_glb.py cad/forearm.STEP web/assets/forearm.glb

- 부품 이름과 조립 위치를 유지한다 (노드 이름 = 부품 이름).
- 단위는 미터로 맞춘다 (WebXR 기준).
- 같은 부품이 여러 번 쓰이면 이름 뒤에 _2, _3 을 붙인다.
- 부품 목록과 크기를 parts.json 으로 함께 저장한다 (분해 순서표 작성용).
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import trimesh
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
try:
    from OCP.TDF import TDF_LabelSequence
except ImportError:  # OCP 7.8+ 에서는 collections 모듈로 옮겨짐
    from OCP.collections import Sequence_TDF_Label as TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.XCAFDoc import XCAFDoc_DocumentTool

LINEAR_DEFLECTION_MM = 0.3   # 작을수록 곱고 무거움
ANGULAR_DEFLECTION = 0.35    # 라디안


def clean_name(raw: str) -> str:
    """SolidWorks 이름 정리: 구성 이름 '_기본', '.step', '^어셈블리명' 제거."""
    name = raw
    name = re.sub(r"_기본$", "", name)
    name = re.sub(r"\.step$", "", name, flags=re.IGNORECASE)
    name = name.split("^")[0]
    name = re.sub(r"<\d+>$", "", name)
    return name.strip() or "part"


def label_name(label: TDF_Label) -> str:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return attr.Get().ToExtString()
    return ""


def mesh_shape(shape) -> trimesh.Trimesh | None:
    BRepMesh_IncrementalMesh(shape, LINEAR_DEFLECTION_MM, False, ANGULAR_DEFLECTION, True)
    verts, faces, offset = [], [], 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        cast = getattr(TopoDS, "Face_s", None) or TopoDS.Face
        face = cast(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            n = tri.NbNodes()
            pts = np.empty((n, 3))
            for i in range(1, n + 1):
                p = tri.Node(i).Transformed(trsf)
                pts[i - 1] = (p.X(), p.Y(), p.Z())
            idx = np.empty((tri.NbTriangles(), 3), dtype=np.int64)
            reverse = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                idx[i - 1] = (a, c, b) if reverse else (a, b, c)
            verts.append(pts)
            faces.append(idx - 1 + offset)
            offset += n
        exp.Next()
    if not verts:
        return None
    m = trimesh.Trimesh(np.vstack(verts), np.vstack(faces), process=True)
    m.apply_scale(0.001)  # OCP 는 mm 로 읽음 → m
    return m


def collect_parts(shape_tool, label, loc, out, path):
    """어셈블리 트리를 내려가며 최종 부품(leaf)과 누적 위치를 모은다."""
    name = label_name(label)
    if shape_tool.IsReference_s(label):
        ref = TDF_Label()
        shape_tool.GetReferredShape_s(label, ref)
        loc = loc.Multiplied(shape_tool.GetLocation_s(label))
        collect_parts(shape_tool, ref, loc, out, path + [name])
        return
    if shape_tool.IsAssembly_s(label):
        comps = TDF_LabelSequence()
        shape_tool.GetComponents_s(label, comps)
        for i in range(1, comps.Length() + 1):
            collect_parts(shape_tool, comps.Value(i), loc, out, path + [name])
        return
    shape = shape_tool.GetShape_s(label)
    out.append({"raw": name or (path[-1] if path else "part"), "path": path, "shape": shape.Moved(loc)})


def main(src: str, dst: str):
    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(src) != IFSelect_RetDone:
        sys.exit(f"STEP 읽기 실패: {src}")
    reader.Transfer(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)

    parts = []
    for i in range(1, roots.Length() + 1):
        collect_parts(shape_tool, roots.Value(i), TopLoc_Location(), parts, [])

    scene = trimesh.Scene()
    used, report = {}, []
    for p in parts:
        mesh = mesh_shape(p["shape"])
        if mesh is None or len(mesh.faces) == 0:
            continue
        base = clean_name(p["raw"])
        used[base] = used.get(base, 0) + 1
        # 같은 부품을 두 번째로 쓸 때는 '#2'를 붙인다 ('_2'는 원래 이름과 겹칠 수 있음)
        node = base if used[base] == 1 else f"{base}#{used[base]}"
        scene.add_geometry(mesh, node_name=node, geom_name=node)
        lo, hi = mesh.bounds
        report.append({
            "name": node,
            "triangles": int(len(mesh.faces)),
            "size_mm": [round(float(v) * 1000, 1) for v in (hi - lo)],
            "center_mm": [round(float(v) * 1000, 1) for v in (lo + hi) / 2],
        })

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    scene.export(dst)
    lo, hi = scene.bounds
    summary = {
        "source": src,
        "parts": report,
        "total_triangles": sum(r["triangles"] for r in report),
        "overall_size_mm": [round(float(v) * 1000, 1) for v in (hi - lo)],
    }
    Path(dst).with_suffix(".parts.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"부품 {len(report)}개, 삼각형 {summary['total_triangles']:,}개, 전체 크기(mm) {summary['overall_size_mm']}")
    for r in report:
        print(f"  {r['name']:<45} {r['triangles']:>7,}  {r['size_mm']}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
