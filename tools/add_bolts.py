"""원본 STEP의 구멍으로 볼트를 찾아, 안내서로 만든 로봇의 assembly.json 에 넣는다 (포어암과 같은 형식).

사용법:
    .venv/Scripts/python tools/add_bolts.py cad/leg_L/leg_L_assembly.step web/assets/leg_L.assembly.json web/assets/leg_L.parts.json

- 구멍 찾기는 plan_disassembly.find_holes 그대로 (반지름 1.2~2.8 mm 원통 구멍, 같은 축끼리 묶기).
- STEP 좌표 → GLB 좌표: 이름이 같은 부품의 중심끼리 맞춘다 (guide_to_robot 과 같은 방법).
- 판-기둥-판처럼 세 부품 이상을 한 줄로 꿰는 구멍은 양쪽에서 박는 짧은 볼트 2개로 나눈다 (안내서: 정강이 기둥 M3 × 4).
- 이미 3D 부품으로 있는 나사(screwA·screwC·screwD 등) 자리는 넣지 않는다.
- 볼트는 그것이 꿰는 부품이 모두 들어온 단계에 붙인다 (그 단계에서 부품과 함께 자기 축으로 들어온다).
- 모터에 박는 볼트(고관절·무릎·발목 모터)는 STEP에 모터 형상이 없어서 찾지 못한다.
guide_to_robot.py 로 assembly.json 을 다시 만들면 이 도구를 다시 돌린다.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import plan_disassembly as pd  # noqa: E402

GAP = pd.GAP_TOL
INTO_POST = 10.0     # mm, 기둥 안으로 들어가는 길이 (안내서 M3 10~12 mm)


def fit_rigid(P, Q):
    pc, qc = P.mean(0), Q.mean(0)
    U, _, Vt = np.linalg.svd((P - pc).T @ (Q - qc))
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    return R, qc - R @ pc


def chains(holes):
    """같은 축 + 이어진 구멍 묶음 → [(축 방향, 축 위 점, [구멍...])]"""
    groups = []
    for h in holes:
        for g in groups:
            if abs(np.dot(g["dir"], h["dir"])) > 0.999 and np.linalg.norm(g["base"] - h["base"]) < pd.AXIS_TOL:
                g["holes"].append(h)
                break
        else:
            groups.append({"dir": h["dir"], "base": h["base"], "holes": [h]})
    out = []
    for g in groups:
        hs = sorted(g["holes"], key=lambda h: h["t0"])
        cur = [hs[0]]
        for h in hs[1:] + [None]:
            if h is not None and h["t0"] <= max(c["t1"] for c in cur) + GAP:
                cur.append(h)
                continue
            out.append((g["dir"], g["base"], cur))
            cur = [h] if h is not None else []
    return out


def bolts_of(chain_dir, base, hs):
    """구멍 묶음 → 볼트 목록 (mm 좌표). 부품 2개면 한 개, 3개 이상이면 양 끝 부품에서 가운데로 하나씩."""
    parts = []
    for h in hs:                                      # 축을 따라 부품 순서 (같은 부품 구멍은 합침)
        if parts and parts[-1]["part"] == h["part"]:
            parts[-1]["t0"] = min(parts[-1]["t0"], h["t0"]); parts[-1]["t1"] = max(parts[-1]["t1"], h["t1"])
            parts[-1]["r"] = max(parts[-1]["r"], h["r"])
        else:
            parts.append(dict(h))
    if len({p["part"] for p in parts}) < 2:
        return []
    at = lambda t: base + chain_dir * t
    size = lambda r: "M3" if r < 1.95 else "M4" if r < 2.45 else "M5"
    r_min = min(p["r"] for p in parts)
    if len(parts) == 2:
        a, b = parts
        head_hi = b["r"] >= a["r"]                     # 머리 쪽 = 더 큰(관통) 구멍
        d = chain_dir if head_hi else -chain_dir
        head, tail = (at(b["t1"]), at(a["t0"])) if head_hi else (at(a["t0"]), at(b["t1"]))
        return [{"head": head, "tail": tail, "dir": d, "r": r_min, "size": size(max(p["r"] for p in parts)),
                 "through": sorted({a["part"], b["part"]})}]
    first, mid, last = parts[0], parts[1:-1], parts[-1]
    m0, m1 = min(p["t0"] for p in mid), max(p["t1"] for p in mid)
    mids = sorted({p["part"] for p in mid})
    return [
        {"head": at(first["t0"]), "tail": at(min(first["t1"] + INTO_POST, m1)), "dir": -chain_dir, "r": r_min,
         "size": size(first["r"]), "through": sorted({first["part"], *mids})},
        {"head": at(last["t1"]), "tail": at(max(last["t0"] - INTO_POST, m0)), "dir": chain_dir, "r": r_min,
         "size": size(last["r"]), "through": sorted({last["part"], *mids})},
    ]


NOT_TARGET = ("pin", "screw", "axle", "sleeve", "ring", "brg", "spacer")   # 볼트가 박히는 상대가 아닌 것 (축·핀·베어링 등)


def guide_specs(assembly_path):
    """안내서 나사 정보(지식 파일의 단계별 bolts 글자) → {단계 번호: [(규격, 길이 mm, 개수)]}. 예: 'M4 12 mm × 3'"""
    import re
    robot = Path(assembly_path).name.split(".")[0]
    kb_path = Path(__file__).resolve().parents[1] / "agent/data/kb/robots" / f"{robot}.json"
    if not kb_path.exists():
        return {}
    out = {}
    for i, s in enumerate(json.loads(kb_path.read_text(encoding="utf-8"))["steps"]):
        for b in s.get("bolts", []):
            m = re.match(r"(M\d)\s+([\d.]+)(?:~[\d.]+)?\s*mm\s*×\s*(\d+)", b)
            if m:
                out.setdefault(i, []).append((m.group(1), float(m.group(2)), int(m.group(3))))
    return out


def motor_bolts(parts, R, t, asm, first_step, assembly_path, start, skipped, have):
    """한 부품에만 있는 구멍인데 바로 앞에 다른 부품(주로 모터)이 붙어 있으면, 그 부품에 박는 볼트.
    STEP 에는 모터 형상이 없어 두 부품을 꿰는 구멍으로는 못 찾는다. GLB 의 모터 표면까지 거리로 판정한다
    (모터 메시는 닫힌 형상이 아니라 '안에 드는지'는 못 쓴다).
    한 부품-상대 사이 구멍이 안내서 개수보다 많으면(예: 지름 4.3 mm 3개 + 4.0 mm 3개) 개수가 맞는 굵기를 고른다.
    안내서가 있으면 그 단계에 그 규격 나사가 남아 있을 때만 넣는다 (나사 없는 단계·이미 채운 개수는 넣지 않음)."""
    import trimesh
    glb = Path(assembly_path).with_name(json.loads(Path(assembly_path).read_text(encoding="utf-8"))["model"].split("/")[-1])
    sc = trimesh.load(glb, force="scene")
    mesh = {}
    for node in sc.graph.nodes_geometry:
        T, g = sc.graph[node]
        m = sc.geometry[g].copy(); m.apply_transform(T); m.apply_scale(1000)
        mesh[node] = m
    targets = {n: m for n, m in mesh.items() if not n.lower().startswith(NOT_TARGET) and n in first_step}
    specs = guide_specs(assembly_path)
    holes = [h for p in parts for h in pd.find_holes(p)]
    groups = {}
    for d, base, hs in chains(holes):
        if len({h["part"] for h in hs}) != 1 or max(h["r"] for h in hs) > 2.45 or hs[0]["part"] not in first_step:
            continue
        part = hs[0]["part"]
        t0, t1 = min(h["t0"] for h in hs), max(h["t1"] for h in hs)
        ends = [(base + d * t0) @ R.T + t, (base + d * t1) @ R.T + t]
        best = None
        for n, m in targets.items():
            if n == part:
                continue
            dist = [float(m.nearest.on_surface([e])[1][0]) for e in ends]
            if min(dist) < 8 and (best is None or min(dist) < best[1]):
                best = (n, min(dist), dist)
        if best is None:
            continue
        n, _, dist = best
        near_i = int(np.argmin(dist))
        tip, head = ends[near_i], ends[1 - near_i]          # 상대 쪽 끝 / 머리 쪽 끝
        r = max(h["r"] for h in hs)
        groups.setdefault((part, n), []).append({"head": head, "tip": tip, "r": r, "size": "M3" if r < 1.95 else "M4"})
    out = []
    for (part, n), hs in groups.items():
        step = max(first_step[part], first_step[n])
        spec = [s for s in specs.get(step, [])]
        by_r = {}
        for h in hs:
            by_r.setdefault(round(h["r"], 2), []).append(h)
        pick = None
        for sz, ln, cnt in spec:
            need = cnt - have.get((step, sz), 0)        # 두 부품을 꿰는 볼트로 이미 채운 만큼 뺀다
            pick = next((v for v in by_r.values() if need > 0 and len(v) == need and v[0]["size"] == sz), None)
            if pick:
                have[(step, sz)] = have.get((step, sz), 0) + len(pick)
                break
        if pick is None:
            if specs:                                  # 안내서와 맞는 묶음이 없으면 넣지 않는다
                skipped.append(f"{part}→{n}: 구멍 {len(hs)}개, {step + 1}단계 안내서 나사와 안 맞아 뺌")
                continue
            pick = by_r[max(by_r)]                     # 안내서가 없으면 가장 굵은(관통) 구멍들
        length = next((ln for sz, ln, cnt in spec if sz == pick[0]["size"]), None)
        for h in pick:
            into = (h["tip"] - h["head"]) / np.linalg.norm(h["tip"] - h["head"])
            L = length or (np.linalg.norm(h["tip"] - h["head"]) + 8)
            tail = h["head"] + into * L
            out.append({"id": f"bolt_{start + len(out)}", "size": h["size"], "diameter": round(2 * min(h["r"], 2.0 if h["size"] == "M4" else 1.5) / 1000, 5),
                        "head": [round(float(v) / 1000, 5) for v in h["head"]], "tail": [round(float(v) / 1000, 5) for v in tail],
                        "dir": [round(float(v), 4) for v in -into], "through": sorted({part, n}), "step": step})
        if len(pick) != len(hs):
            skipped.append(f"{part}→{n}: 구멍 {len(hs)}개 중 안내서와 맞는 {len(pick)}개만")
    return out


def main(step_path, assembly_path, parts_path):
    parts = pd.load_parts(step_path)
    glb = {p["name"]: np.array(p["center_mm"]) for p in json.loads(Path(parts_path).read_text(encoding="utf-8"))["parts"]}
    common = [p for p in parts if p["name"] in glb]
    A = np.array([p["mesh"].bounds.mean(0) * 1000 for p in common])   # STEP 메시는 m, 구멍·GLB 정보는 mm
    B = np.array([glb[p["name"]] for p in common])
    keep = np.ones(len(common), bool)
    for _ in range(3):
        R, t = fit_rigid(A[keep], B[keep])
        err = np.linalg.norm(A @ R.T + t - B, axis=1)
        keep = err < max(3.0, np.median(err) * 3)
    print(f"좌표 맞춤: 부품 {keep.sum()}/{len(common)}개, 오차 중간값 {np.median(err[keep]):.2f} mm")

    holes = [h for p in parts for h in pd.find_holes(p)]
    found = [b for c in chains(holes) for b in bolts_of(*c)]
    screws = {n: c for n, c in glb.items() if n.lower().startswith("screw")}   # 이미 3D 로 있는 나사
    asm = json.loads(Path(assembly_path).read_text(encoding="utf-8"))
    first_step = {}
    for i, s in enumerate(asm["steps"]):
        for n in s.get("parts", []):
            first_step.setdefault(n, i)

    bolts, skipped = [], []
    for b in found:
        head, tail = b["head"] @ R.T + t, b["tail"] @ R.T + t
        mid = (head + tail) / 2
        near = [n for n, c in screws.items() if np.linalg.norm(c - mid) < 15]
        if near:
            skipped.append(f"{b['size']} {'+'.join(b['through'])} (이미 있는 {near[0]})")
            continue
        steps = [first_step.get(n) for n in b["through"]]
        if any(s is None for s in steps):
            skipped.append(f"{b['size']} {'+'.join(b['through'])} (단계에 없는 부품)")
            continue
        bolts.append({"id": f"bolt_{len(bolts) + 1}", "size": b["size"], "diameter": round(2 * b["r"] / 1000, 5),
                      "head": [round(float(v) / 1000, 5) for v in head], "tail": [round(float(v) / 1000, 5) for v in tail],
                      "dir": [round(float(v), 4) for v in R @ b["dir"]], "through": b["through"], "step": max(steps)})
    have = {}
    for b in bolts:
        have[(b["step"], b["size"])] = have.get((b["step"], b["size"]), 0) + 1
    bolts += motor_bolts(parts, R, t, asm, first_step, assembly_path, start=len(bolts) + 1, skipped=skipped, have=have)
    for s in asm["steps"]:
        s.pop("bolts", None)
    for b in bolts:
        asm["steps"][b.pop("step")].setdefault("bolts", []).append(b["id"])
    asm["bolts"] = bolts
    Path(assembly_path).write_text(json.dumps(asm, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"볼트 {len(bolts)}개 넣음")
    for i, s in enumerate(asm["steps"]):
        if s.get("bolts"):
            sz = [next(b["size"] for b in bolts if b["id"] == x) for x in s["bolts"]]
            print(f"  {i + 1}단계 {s['title']}: " + ", ".join(f"{z} × {sz.count(z)}" for z in sorted(set(sz))))
    for x in skipped:
        print("  뺌:", x)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
