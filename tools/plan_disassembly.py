"""CAD 형상으로 볼트 위치와 분해 순서를 계산한다.

사용법:
    .venv/Scripts/python tools/plan_disassembly.py cad/forearm.STEP web/assets/forearm.assembly.json

하는 일
1. 볼트 찾기: 여러 부품에 걸쳐 같은 축으로 이어진 작은 원통 구멍(반지름 1.2~2.8 mm)을 볼트로 본다.
2. 막힘 계산: 부품(또는 한 덩어리 묶음)을 ±X ±Y ±Z 로 밀었을 때 다른 부품과 부딪히는지 광선으로 검사한다.
3. 분해 순서: 볼트를 먼저 풀고, 부딪히지 않고 빠지는 것부터 하나씩 뺀다. 그 역순이 조립 순서.
4. 기존 assembly.json 의 title/desc/unit 은 유지하고 steps 와 bolts 를 다시 쓴다.
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import trimesh
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

sys.path.insert(0, str(Path(__file__).parent))
import step_to_glb as s2g  # noqa: E402

HOLE_R_MIN, HOLE_R_MAX = 1.2, 2.8      # mm (M2.5~M5 탭·관통 구멍)
AXIS_TOL = 0.3                          # mm, 같은 축으로 보는 거리
GAP_TOL = 1.5                           # mm, 구멍 사이 틈이 이 이하면 한 볼트
TOUCH = 0.0004                          # m, 이 이하 거리의 충돌은 '맞닿음'으로 무시
DIRS = {"+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0), "-Y": (0, -1, 0), "+Z": (0, 0, 1), "-Z": (0, 0, -1)}

_cast = getattr(TopoDS, "Face_s", None) or TopoDS.Face


def load_parts(step_path):
    """step_to_glb 와 같은 이름 규칙으로 부품 이름, OCP 형상, 메시를 읽는다."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(step_path) != IFSelect_RetDone:
        sys.exit("STEP 읽기 실패")
    reader.Transfer(doc)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    roots = s2g.TDF_LabelSequence()
    tool.GetFreeShapes(roots)
    raw = []
    for i in range(1, roots.Length() + 1):
        s2g.collect_parts(tool, roots.Value(i), TopLoc_Location(), raw, [])
    used, parts = {}, []
    for p in raw:
        mesh = s2g.mesh_shape(p["shape"])
        if mesh is None or len(mesh.faces) == 0:
            continue
        base = s2g.clean_name(p["raw"])
        used[base] = used.get(base, 0) + 1
        name = base if used[base] == 1 else f"{base}#{used[base]}"
        parts.append({"name": name, "shape": p["shape"], "mesh": mesh})
    return parts


# ---------------------------------------------------------------- 볼트 찾기
def find_holes(part):
    holes = []
    exp = TopExp_Explorer(part["shape"], TopAbs_FACE)
    while exp.More():
        face = _cast(exp.Current())
        exp.Next()
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Cylinder:
            continue
        cyl = surf.Cylinder()
        r = cyl.Radius()
        if not (HOLE_R_MIN <= r <= HOLE_R_MAX):
            continue
        if surf.LastUParameter() - surf.FirstUParameter() < 2.5:   # 반 바퀴 이상 돌아야 구멍
            continue
        ax3 = cyl.Position()
        inward = (face.Orientation() == TopAbs_REVERSED) == ax3.Direct()
        if not inward:                                             # 바깥으로 볼록한 원통(보스)은 제외
            continue
        loc, d = ax3.Location(), ax3.Direction()
        p0 = np.array([loc.X(), loc.Y(), loc.Z()])
        dv = np.array([d.X(), d.Y(), d.Z()])
        # 축 방향 부호 통일
        k = int(np.argmax(np.abs(dv)))
        if dv[k] < 0:
            dv = -dv
            v0, v1 = -surf.LastVParameter(), -surf.FirstVParameter()
        else:
            v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
        base = p0 - np.dot(p0, dv) * dv                             # 원점에서 가장 가까운 축 위 점
        t0 = np.dot(p0, dv) + v0
        t1 = np.dot(p0, dv) + v1
        holes.append({"part": part["name"], "r": r, "dir": dv, "base": base, "t0": min(t0, t1), "t1": max(t0, t1)})
    return holes


def find_bolts(parts, body_of):
    holes = [h for p in parts for h in find_holes(p)]
    # 같은 축끼리 묶기
    groups = []
    for h in holes:
        for g in groups:
            if abs(np.dot(g["dir"], h["dir"])) > 0.999 and np.linalg.norm(g["base"] - h["base"]) < AXIS_TOL:
                g["holes"].append(h)
                break
        else:
            groups.append({"dir": h["dir"], "base": h["base"], "holes": [h]})
    bolts = []
    for g in groups:
        hs = sorted(g["holes"], key=lambda h: h["t0"])
        chain = [hs[0]]
        for h in hs[1:] + [None]:
            if h is not None and h["t0"] <= max(c["t1"] for c in chain) + GAP_TOL:
                chain.append(h)
                continue
            bodies = {body_of[c["part"]] for c in chain}
            if len(bodies) >= 2:                      # 서로 다른 몸체 2개 이상을 꿰면 볼트
                t0 = min(c["t0"] for c in chain)
                t1 = max(c["t1"] for c in chain)
                r_min = min(c["r"] for c in chain)
                r_max = max(c["r"] for c in chain)
                # 머리 쪽: 더 큰 구멍(관통 구멍)이 있는 끝
                lo = min(chain, key=lambda c: c["t0"])
                hi = max(chain, key=lambda c: c["t1"])
                head_hi = hi["r"] >= lo["r"]
                d = g["dir"] if head_hi else -g["dir"]
                tail_t, head_t = (t0, t1) if head_hi else (t1, t0)
                bolts.append({
                    "tail": (g["base"] + g["dir"] * tail_t) / 1000.0,
                    "head": (g["base"] + g["dir"] * head_t) / 1000.0,
                    "dir": d,
                    "diameter": round(2 * r_min / 1000.0, 5),
                    "size": "M3" if r_max < 1.95 else "M4" if r_max < 2.45 else "M5",
                    "through": sorted({c["part"] for c in chain}),
                    "bodies": sorted(bodies),
                })
            chain = [h] if h is not None else []
    return bolts, len(holes)


def bolt_mesh(b):
    length = float(np.linalg.norm(b["head"] - b["tail"]))
    r = b["diameter"] / 2
    shaft = trimesh.creation.cylinder(radius=r, height=length, sections=12)
    head = trimesh.creation.cylinder(radius=r * 1.8, height=r * 1.6, sections=12)
    head.apply_translation([0, 0, length / 2 + r * 0.8])
    m = trimesh.util.concatenate([shaft, head])
    T = trimesh.geometry.align_vectors([0, 0, 1], b["dir"])
    m.apply_transform(T)
    m.apply_translation((b["head"] + b["tail"]) / 2)
    return m


# ---------------------------------------------------------------- 막힘 검사
INSET = 0.0005   # m, 표면에서 안쪽으로 들인 점으로 검사 (맞닿은 벽을 스치는 가짜 충돌 방지)


def sample_points(mesh, n=500):
    v, nrm = mesh.vertices, mesh.vertex_normals
    idx = np.arange(len(v)) if len(v) <= n else np.random.default_rng(0).choice(len(v), n, replace=False)
    return v[idx] - nrm[idx] * INSET


def blocked_by(moving, others, d):
    """moving 을 d 로 밀 때 부딪히는 몸체 이름 집합. others = {이름: mesh}"""
    if not others:
        return set()
    names = list(others)
    combo = trimesh.util.concatenate([others[n] for n in names])
    owner = np.concatenate([[i] * len(others[n].faces) for i, n in enumerate(names)])
    d = np.asarray(d, float)
    hit = set()
    # 1) 움직이는 쪽 꼭짓점에서 d 방향으로
    pts = sample_points(moving)
    loc, ray_i, tri = combo.ray.intersects_location(pts + d * 1e-5, np.tile(d, (len(pts), 1)))
    if len(loc):
        dist = np.einsum("ij,ij->i", loc - pts[ray_i], np.tile(d, (len(loc), 1)))
        for t, f in zip(dist, tri):
            if t > TOUCH:
                hit.add(names[owner[f]])
    # 2) 다른 몸체 꼭짓점에서 -d 방향으로 (구멍에 꽂힌 축 같은 경우). 몸체별로 따로 쏴서 주인을 바로 앎
    for n in names:
        if n in hit:
            continue
        opts = sample_points(others[n], 250)
        loc, ray_i, _ = moving.ray.intersects_location(opts - d * 1e-5, np.tile(-d, (len(opts), 1)))
        if len(loc):
            dist = np.einsum("ij,ij->i", opts[ray_i] - loc, np.tile(d, (len(loc), 1)))
            if (dist > TOUCH).any():
                hit.add(n)
    return hit


DIRS_ALL = {}


def candidate_dirs(body, mesh, hint, bolts):
    """시험할 방향들: 축 6개 + 설계자 힌트 + 이 몸체를 꿰는 볼트 축 + 몸체 길이 방향."""
    dirs = {k: np.array(v, float) for k, v in DIRS.items()}
    if hint is not None:
        h = np.array(hint, float)
        dirs["hint:+"] = h / np.linalg.norm(h)
    for b in bolts:
        if body in b["bodies"]:
            # 볼트 머리가 이 몸체 쪽에 있으면 머리 방향으로, 아니면 반대로 빠지는 게 자연스러움
            dirs["bolt:+"] = np.array(b["dir"], float)
            dirs["bolt:-"] = -np.array(b["dir"], float)
            break
    # 몸체 길이 방향 (주성분): 비스듬한 튜브는 이 방향으로만 빠짐
    v = mesh.vertices - mesh.vertices.mean(axis=0)
    _, sv, vt = np.linalg.svd(v, full_matrices=False)
    if sv[0] > 1.5 * sv[1]:                                    # 확실히 길쭉할 때만
        long_axis = vt[0] / np.linalg.norm(vt[0])
        if max(abs(long_axis)) < 0.995:                        # 축과 이미 같으면 생략
            dirs["long:+"] = long_axis
            dirs["long:-"] = -long_axis
    return dirs


# ---------------------------------------------------------------- 메인
def main(step_path, assembly_path, out_path=None):
    out_path = out_path or assembly_path
    old = json.loads(Path(assembly_path).read_text(encoding="utf-8"))
    # 설계자가 적어 둔 방향 힌트 (몸체 이름 → dir)
    hint_by_title = {s["title"]: s["dir"] for s in old["steps"] if s.get("dir")}
    parts = load_parts(step_path)
    names = [p["name"] for p in parts]

    # 몸체 = 한 덩어리 묶음(unit) 또는 낱개 부품
    import fnmatch

    def match(pattern, name):
        if "#" not in pattern and "#" in name:
            return False
        return fnmatch.fnmatchcase(name, pattern)

    body_of, body_title, body_desc = {}, {}, {}
    for s in old["steps"]:
        if not s.get("unit"):
            continue
        for n in names:
            if any(match(pat, n) for pat in s["parts"]):
                body_of[n] = s["title"]
        body_title[s["title"]] = s["title"]
        body_desc[s["title"]] = s.get("desc", "")
    for n in names:
        body_of.setdefault(n, n)
    for s in old["steps"]:                       # 낱개 부품 단계의 제목·설명 기억
        if s.get("unit"):
            continue
        for n in names:
            if any(match(pat, n) for pat in s["parts"]):
                body_title.setdefault(n, s["title"] if len(s["parts"]) == 1 else n)
                body_desc.setdefault(n, s.get("desc", ""))

    members = defaultdict(list)
    for n in names:
        members[body_of[n]].append(n)
    hint = {}
    for b in members:
        t = body_title.get(b, b)
        if t in hint_by_title:
            hint[b] = hint_by_title[t]
    mesh_of = {n: p["mesh"] for n, p in zip(names, parts)}
    body_mesh = {b: trimesh.util.concatenate([mesh_of[n] for n in ms]) for b, ms in members.items()}

    bolts, n_holes = find_bolts(parts, body_of)
    print(f"원통 구멍 {n_holes}개 → 볼트 {len(bolts)}개")
    for i, b in enumerate(bolts):
        b["id"] = f"bolt_{i + 1}"
        print(f"  {b['id']}: {b['size']} 길이 {np.linalg.norm(b['head'] - b['tail']) * 1000:.1f}mm  {' + '.join(b['bodies'])}")

    # 분해: 볼트 → 빠지는 몸체 순서로
    remaining = set(members)
    bolts_left = {b["id"]: b for b in bolts}
    disassembly = []
    removed = set()        # 먼저 빠진 몸체들

    def record_blockers(body, dkey):
        """모든 부품이 다 있을 때 이 방향을 막는 몸체 중 먼저 빠진 것 + 이 몸체를 꿰는 볼트."""
        everyone = {r: body_mesh[r] for r in members if r != body}
        blk = blocked_by(body_mesh[body], everyone, DIRS_ALL[dkey]) & removed
        return sorted(blk), [b["id"] for b in bolts if body in b["bodies"]]
    bolt_free_cache = {}

    def bolt_free(bid):
        """볼트 머리 쪽 길이 남은 몸체에 막히지 않았는지 (자기가 꿰는 몸체는 제외)."""
        b = bolts_left[bid]
        key = (bid, frozenset(remaining))
        if key not in bolt_free_cache:
            others = {r: body_mesh[r] for r in remaining if r not in b["bodies"]}
            bolt_free_cache[key] = not blocked_by(bolt_mesh(b), others, b["dir"])
        return bolt_free_cache[key]

    def release_bolts(body):
        """이 몸체를 잡고 있는 볼트를 빼기 직전에 푼다 (같은 방향끼리 한 단계)."""
        groups = defaultdict(list)
        for bid in [i for i, b in bolts_left.items() if body in b["bodies"]]:
            b = bolts_left.pop(bid)
            groups[tuple(np.round(b["dir"], 2))].append(bid)
        for ids in groups.values():
            disassembly.append({"kind": "bolts", "bolts": ids})

    while remaining:
        # 볼트는 몸체를 빼기 직전에 푼다 → 조립할 때는 몸체를 놓자마자 볼트를 조이는 순서가 됨
        candidates = []
        for body in sorted(remaining):
            mine = [i for i, b in bolts_left.items() if body in b["bodies"]]
            if not all(bolt_free(i) for i in mine):
                continue
            others = {r: body_mesh[r] for r in remaining if r != body}
            dirs = candidate_dirs(body, body_mesh[body], hint.get(body), bolts)
            free = [k for k, d in dirs.items() if not blocked_by(body_mesh[body], others, d)]
            if free:
                # 우선순위: 설계자가 적은 방향 > 볼트 축 > 몸체 길이 방향 > 모델 바깥쪽
                c = body_mesh[body].centroid - np.mean([body_mesh[r].centroid for r in remaining], axis=0)
                rank = {"hint": 3, "bolt": 2, "long": 1}
                best = max(free, key=lambda k: (rank.get(k.split(":")[0], 0), np.dot(dirs[k], c)))
                DIRS_ALL[best + "@" + body] = dirs[best]
                candidates.append((body, best + "@" + body, free, len(members[body])))
        if not candidates:
            stuck = sorted(remaining)
            print("더 이상 뺄 수 있는 몸체가 없어요 (서로 맞물림):", stuck)
            DIRS_ALL["-Y@" + stuck[0]] = np.array(DIRS["-Y"], float)
            blk, blk_bolts = record_blockers(stuck[0], "-Y@" + stuck[0])
            release_bolts(stuck[0])
            disassembly.append({"kind": "body", "body": stuck[0], "dir": "-Y@" + stuck[0], "free": [], "forced": True,
                                "blk": blk, "blk_bolts": blk_bolts})
            remaining.discard(stuck[0])
            removed.add(stuck[0])
            continue
        # 작은 것(부품 수 적은 것)부터, 이름순
        body, best, free, _ = sorted(candidates, key=lambda c: (c[3], c[0]))[0]
        blk, blk_bolts = record_blockers(body, best)
        release_bolts(body)
        disassembly.append({"kind": "body", "body": body, "dir": best, "free": free, "blk": blk, "blk_bolts": blk_bolts})
        remaining.discard(body)
        removed.add(body)
        print(f"  빼기: {body:<32} 방향 {best}  (가능: {', '.join(free)})")

    # 조립 순서 = 분해의 역순
    steps = []
    for d in reversed(disassembly):
        if d["kind"] == "bolts":
            bs = [b for b in bolts if b["id"] in d["bolts"]]
            target = sorted({bd for b in bs for bd in b["bodies"]})
            steps.append({
                "title": f"볼트 {len(bs)}개 조이기 ({bs[0]['size']})",
                "bolts": d["bolts"],
                "parts": [],
                "desc": "고정: " + ", ".join(body_title.get(t, t) for t in target),
            })
        else:
            body = d["body"]
            unit = len(members[body]) > 1 and body in body_title and body_of[members[body][0]] == body
            steps.append({
                "title": body_title.get(body, body),
                **({"unit": True} if unit else {}),
                "dir": [round(float(v), 4) for v in DIRS_ALL[d["dir"]]],
                "parts": members[body],
                "desc": body_desc.get(body, ""),
                "free_dirs": [f.split("@")[0] for f in d["free"]],
                **({"warn": "형상 계산으로 빠지는 방향을 못 찾았어요. 실제 순서를 확인해 주세요."} if d.get("forced") else {}),
                "_blk": d["blk"], "_blk_bolts": d["blk_bolts"],
            })

    # 막는 것들을 단계 번호로 (화면에서 '먼저 빼야 할 것' 판단에 사용)
    step_of_body = {s["title"] if s.get("unit") else s["parts"][0]: i for i, s in enumerate(steps) if s.get("parts")}
    for i, s in enumerate(steps):
        if s.get("parts"):
            step_of_body[body_of[s["parts"][0]]] = i
    step_of_bolt = {bid: i for i, s in enumerate(steps) for bid in s.get("bolts", [])}
    for s in steps:
        blk = s.pop("_blk", [])
        blk_bolts = s.pop("_blk_bolts", [])
        if "bolts" in s and not s.get("parts"):
            s["blockers"] = []
            continue
        s["blockers"] = sorted({step_of_body[b] for b in blk if b in step_of_body} |
                               {step_of_bolt[b] for b in blk_bolts if b in step_of_bolt})

    out = {
        "model": old["model"],
        "status": "CAD 형상으로 계산한 순서입니다 (볼트는 구멍 위치로 추정). 설계자가 확인해 주세요.",
        "notes": old.get("notes", ""),
        "bolts": [{
            "id": b["id"], "size": b["size"], "diameter": b["diameter"],
            "head": [round(float(v), 5) for v in b["head"]],
            "tail": [round(float(v), 5) for v in b["tail"]],
            "dir": [round(float(v), 4) for v in b["dir"]],
            "through": b["through"],
        } for b in bolts],
        "steps": steps,
    }
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"조립 {len(steps)}단계를 {out_path} 에 저장했어요.")


if __name__ == "__main__":
    # plan_disassembly.py <STEP> <제목·묶음·방향 힌트가 있는 json> [저장할 json]
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
