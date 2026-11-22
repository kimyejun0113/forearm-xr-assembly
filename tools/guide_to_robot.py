"""설계자 조립 안내서(HTML) + 원본 CAD(GLB) → 우리 형식(GLB · assembly.json · kb.json).

사용법:
    .venv/Scripts/python tools/step_to_glb.py <s08_full_zero.step> <full.glb>      # 원본 CAD는 늘 하던 대로 GLB 로
    .venv/Scripts/python tools/guide_to_robot.py <안내서.html> <full.glb> leg_L

- 3D 는 원본 CAD 그대로 쓴다. 안내서에서는 조립 순서·설명·주의·나사 수·부품이 들어오는 방향만 가져온다.
- 원본 CAD 에 없는 베어링만 안내서 수치(위치·바깥지름·폭)로 만들어 넣는다.
- 끝에서 설명 녹화 기록(<robot>.speech.json)을 지식에 다시 붙이고, cad/<robot>/<robot>_assembly.step 이 있으면
  tools/add_bolts.py 로 볼트도 다시 넣는다 (다시 돌려도 녹화로 배운 말과 볼트가 사라지지 않게).
- 결과:
    web/assets/<robot>.glb, web/assets/<robot>.assembly.json   (XR 화면용, 포어암과 같은 형식)
    agent/data/kb/robots/<robot>.json                         (에이전트 지식, kb.json 과 같은 형식)
- 안내서 단계 중 부품을 더하지 않는 단계(시작 전에·손으로 확인·오른다리)는 3D 단계로 만들지 않고 지식으로만 넣는다.
"""
import base64
import json
import re
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]

# 안내서 id → 원본 CAD 이름 (왼다리). 나머지는 id + '_L'
CAD_NAME = {"hip": "hip_roll_yaw_pitch_modules", "knee_gim": "knee_GIM8115", "ankle_rs": "ankle_RS02",
            "foot": "old_foot_and_shoe", "shoe": "old_foot_and_shoe"}
SAME_MESH = {"shoe": "foot"}          # 원본 CAD 에서는 발과 신발이 한 덩어리


# ---------------------------------------------------------------- 안내서 읽기
def js_to_json(src: str) -> str:
    """안내서 스크립트의 JS 객체 글자 → JSON (문자열 밖에서만 키에 따옴표, X(d) → [d,0,0])."""
    out, i, n = [], 0, len(src)
    seg = []

    def flush():
        s = "".join(seg)
        s = re.sub(r"\bX\((-?[\d.]+)\)", r"[\1,0,0]", s)
        s = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', s)
        s = re.sub(r",\s*([}\]])", r"\1", s)
        out.append(s)
        seg.clear()

    while i < n:
        c = src[i]
        if c in "'\"":
            flush()
            j, buf = i + 1, []
            while src[j] != c:
                if src[j] == "\\":
                    buf.append(src[j + 1]); j += 2; continue
                buf.append(src[j]); j += 1
            out.append(json.dumps("".join(buf), ensure_ascii=False))
            i = j + 1
            continue
        seg.append(c)
        i += 1
    flush()
    return "".join(out)


def read_guide(path):
    html = Path(path).read_text(encoding="utf-8")
    guide = json.loads(re.search(r"window\.GUIDE=(\{.*?\});\s*</script>", html, re.S).group(1))
    code = html[html.index("// ---------- 부품 이름·색"):]

    def const(name, end):
        m = re.search(rf"const {name}=(.*?){end}", code, re.S)
        return m.group(1)

    links = json.loads(js_to_json(const("LINKS", ";")))
    steps_src = const("STEPS", r";\nconst addedAt").replace("focus:LINKS", "focus:" + json.dumps(links))
    steps = json.loads(js_to_json(steps_src))
    ko = json.loads(js_to_json(const("KO", ";\nconst colOf")))
    col = json.loads(js_to_json(const("COL", ";\nconst KO")))
    intro = re.sub(r"<[^>]+>", " ", html[:html.index("<script")])
    return guide, steps, ko, col, intro


def guide_mesh(g):
    pos = np.frombuffer(base64.b64decode(g["p"]), dtype=np.int16).astype(np.float64).reshape(-1, 3) / 10
    idx = np.frombuffer(base64.b64decode(g["t"]), dtype=np.uint16 if g["w"] == 2 else np.uint32).reshape(-1, 3)
    return trimesh.Trimesh(pos, idx, process=False)


def bearing(center_yz, x0, od, w):
    """안내서와 같은 베어링: 안지름 8, 바깥지름 od, X 축으로 x0 ~ x0+w (mm, 안내서 좌표)"""
    m = trimesh.creation.annulus(r_min=4, r_max=od / 2, height=w, sections=28)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    m.apply_translation([x0 + w / 2, center_yz[0], center_yz[1]])
    return m


# ---------------------------------------------------------------- 좌표 맞추기
def fit_rigid(P, Q):
    """P(안내서 mm) → Q(CAD mm) 회전 R, 이동 t (Kabsch)"""
    pc, qc = P.mean(0), Q.mean(0)
    U, _, Vt = np.linalg.svd((P - pc).T @ (Q - qc))
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    return R, qc - R @ pc


def main(guide_path, full_glb, robot):
    side = robot.split("_")[-1]                       # leg_L → L
    guide, gsteps, ko, col, intro = read_guide(guide_path)
    P = guide["points"]

    full = trimesh.load(full_glb, force="scene")
    cad = {}                                          # CAD 이름 → 세계 좌표 메시 (mm)
    for node in full.graph.nodes_geometry:
        T, gname = full.graph[node]
        m = full.geometry[gname].copy()
        m.apply_transform(T)
        m.apply_scale(1000)
        cad[node] = m

    def cad_of(gid):
        return f"{CAD_NAME.get(gid, gid)}_{side}"

    # 안내서에 나오는 3D 항목 (출력 부품 · 핀·나사 · 그대로 쓰는 것)
    gmesh = {}
    for grp in ("parts", "pins", "team"):
        for k, g in guide[grp].items():
            if g.get("only_lr"):
                continue
            gmesh[k] = guide_mesh(g)

    # 안내서 좌표 → CAD 좌표 (같은 부품의 중심끼리 맞춤, 크게 어긋나는 것은 빼고 다시)
    pairs = [k for k in gmesh if k not in SAME_MESH and k != "foot" and cad_of(k) in cad]
    missing = [k for k in gmesh if cad_of(k) not in cad]
    if missing:
        print("원본 CAD 에 없는 안내서 항목:", missing)
    A = np.array([gmesh[k].bounds.mean(0) for k in pairs])
    B = np.array([cad[cad_of(k)].bounds.mean(0) for k in pairs])
    keep = np.ones(len(pairs), bool)
    for _ in range(3):
        R, t = fit_rigid(A[keep], B[keep])
        err = np.linalg.norm(A @ R.T + t - B, axis=1)
        keep = err < max(3.0, np.median(err) * 3)
    print(f"좌표 맞춤: 부품 {keep.sum()}/{len(pairs)}개, 오차 중간값 {np.median(err[keep]):.2f} mm, 최대 {err[keep].max():.2f} mm")
    for k, e in zip(pairs, err):
        if e > 3:
            print(f"  어긋남 {k}: {e:.1f} mm (CAD 위치를 씁니다)")
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = t

    # 단계에 쓰이는 항목만 GLB 로
    BRG = {"brg_K1": ("K", 118, 22, 7), "brg_K2": ("K", 131, 22, 7), "brg_A1": ("A", 118.1, 16, 5), "brg_A2": ("A", 133.5, 16, 5),
           "brg_AIN": ("AIN0", 171.5, 22, 7), "brg_B": ("B0", 171.5, 22, 7), "brg_D": ("D0", 141.5, 22, 7), "brg_C": ("C0", 144.5, 22, 7)}
    used = []
    for s in gsteps:
        for gid, _ in s["add"]:
            gid = SAME_MESH.get(gid, gid)
            if gid not in used:
                used.append(gid)
    scene, report = trimesh.Scene(), []
    for gid in used:
        if gid in BRG:
            c, x0, od, w = BRG[gid]
            m = bearing(P[c], x0, od, w)
            m.apply_transform(M)
        elif cad_of(gid) in cad:
            m = cad[cad_of(gid)].copy()
        else:
            print(f"  3D 없음, 건너뜀: {gid}")
            continue
        m.apply_scale(0.001)
        scene.add_geometry(m, node_name=gid, geom_name=gid)
        lo, hi = m.bounds
        report.append({"name": gid, "triangles": int(len(m.faces)),
                       "size_mm": [round(float(v) * 1000, 1) for v in hi - lo],
                       "center_mm": [round(float(v) * 1000, 1) for v in (lo + hi) / 2]})
    have = {r["name"]: None for r in report}     # 단계 순서를 지키는 집합 (다시 돌려도 파일 내용 순서가 같게)

    out_web = ROOT / "web/assets"
    glb = out_web / f"{robot}.glb"
    scene.export(glb)
    (out_web / f"{robot}.parts.json").write_text(json.dumps({"source": [str(guide_path), str(full_glb)], "parts": report},
                                                             ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{glb.name}: 부품 {len(report)}개, 삼각형 {sum(r['triangles'] for r in report):,}개")

    # 위쪽: 고관절 → 발 방향의 반대, 가장 가까운 축으로
    up = cad[cad_of("hip")].bounds.mean(0) - cad[cad_of("foot")].bounds.mean(0)
    ax = int(np.argmax(np.abs(up))); up = [0, 0, 0]; up[ax] = 1 if (cad[cad_of("hip")].bounds.mean(0) - cad[cad_of("foot")].bounds.mean(0))[ax] > 0 else -1

    # ------------------------------------------------ 단계
    def ids(s):
        r = []
        for gid, _ in s["add"]:
            gid = SAME_MESH.get(gid, gid)
            if gid in have and gid not in r:
                r.append(gid)
        return r

    def cad_dir(off):
        v = R @ np.array(off, float)
        return [round(float(x), 4) for x in v / (np.linalg.norm(v) or 1)]

    build = [s for s in gsteps if s["add"]]
    extra = {i: s for i, s in enumerate(gsteps) if not s["add"]}
    a_steps = []
    for s in build:
        pd = {}
        for gid, off in s["add"]:
            gid = SAME_MESH.get(gid, gid)
            if gid in have and any(off) and gid not in pd:
                pd[gid] = cad_dir(off)
        a_steps.append({"title": s["t"], "parts": ids(s), "desc": s["do"][0],
                        "dir": next(iter(pd.values()), None), "part_dirs": pd})
    colors = {k: v for k, v in col.items() if k in have}
    for k in have:
        if k.startswith("brg_") or k in guide["pins"]:
            colors.setdefault(k, "#b8bec6")
        elif re.match(r"^(ring_|sleeve_|spacer_)", k):
            colors.setdefault(k, "#d8c9a0")
        else:
            colors.setdefault(k, "#9aa1a6")
    assembly = {"model": f"assets/{robot}.glb", "label": "LEG " + side, "up": up, "colors": colors,
                "status": "안내서(S08 다리 조립 안내서) 순서. 3D 는 원본 CAD s08_full_zero.step 의 왼다리.",
                "metal": sorted(k for k in have if k.startswith("brg_") or k in guide["pins"]
                                or k in ("hip", "knee_gim", "ankle_rs")),
                "steps": a_steps}
    (out_web / f"{robot}.assembly.json").write_text(json.dumps(assembly, ensure_ascii=False, indent=1), encoding="utf-8")

    # ------------------------------------------------ 지식 (kb.json 과 같은 형식)
    names = {}
    for k in have:
        g = guide["parts"].get(k)
        long = g["name"] if g else (guide["pins"][k]["spec"] if k in guide["pins"] else "")
        short = ko.get(k, k)
        if k == "foot":
            short = "발과 신발 (그대로)"
        names[k] = {"name": short, "aliases": [a for a in dict.fromkeys([short, long, k]) if a]}

    def tools_of(bolts):
        t = []
        txt = " ".join(b[0] for b in bolts)
        if "M6" in txt: t.append("M6 십자 드라이버 (축이 긴 것)")
        if re.search(r"M[34]", txt): t.append("M4·M3 육각 볼렌치")
        if re.search(r"608|688", txt): t.append("바이스 또는 클램프")
        return t

    def bolt_str(b):
        name, n = b
        m = re.match(r"(M\d+)×([\d~.]+)\s*(.*)", name)
        return f"{m.group(1)} {m.group(2)} mm × {n}" + (f" {m.group(3)}" if m.group(3) else "") if m else f"{name} × {n}"

    selftap = re.search(r"셀프탭 구멍\.\s*(.*?)\s*꼭|셀프탭 구멍\.\s*([^\n]*)", intro)
    selftap = (selftap.group(1) or selftap.group(2)).strip() if selftap else None
    pre = "L"
    k_steps, chunks = [], []

    def chunk(kind, text, step_id, source, **kw):
        chunks.append({"id": f"c{len(chunks)}", "kind": kind, "text": text, "step_id": step_id, "source": source, **kw})

    gi = {id(s): i for i, s in enumerate(gsteps)}
    for n, s in enumerate(build, 1):
        sid = f"{pre}{n:02d}"
        inst = list(s["do"])
        warns = [s["warn"]] if s.get("warn") else []
        nxt = extra.get(gi[id(s)] + 1)
        if nxt and nxt.get("focus"):                  # '손으로 움직여 확인' 은 링크를 다 건 단계 끝에 붙인다
            inst += ["확인: " + d for d in nxt["do"]]
            if nxt.get("warn"):
                warns.append(nxt["warn"])
        bolts = s.get("bolts", [])
        nscrew = sum(b[1] for b in bolts if b[0].startswith("M"))
        nbrg = sum(b[1] for b in bolts if re.search(r"608|688", b[0]))
        flags = {}
        if selftap and "셀프탭" in " ".join(s["do"]):
            flags["irreversible"] = [selftap]
        k_steps.append({"step_id": sid, "order": n, "name": s["t"], "parts": ids(s), "tools": tools_of(bolts),
                        "bolts": [bolt_str(b) for b in bolts], "instructions": inst, "warnings": warns,
                        "slides": [], "flags": flags, "check": None,
                        "baseline_sec": 60 + 20 * nscrew + 40 * nbrg})   # 안내서에 시간이 없어 나사·베어링 수로 어림
        src = f"S08 안내서 {gi[id(s)]}단계"
        for d in inst:
            chunk("instruction", d, sid, src)
        for w in warns:
            chunk("warning", w, sid, src, also=[])
        if s.get("tip"):
            chunk("speech", s["tip"], sid, src + " 팁", slide_no=f"tip-{sid}", t=0, also=[])
        for b in bolts:
            chunk("table_row", f"{s['t']} · {b[0]} · {b[1]}개", sid, src)
    # 부품을 더하지 않는 단계: 시작 전에 · 오른다리 → 전체 지식
    guide_ui = re.compile(r"마우스|버튼|탭에서|탭의|화면 위")      # 안내서 화면 조작 이야기는 XR 에서 맞지 않는다
    n_lr = sum(1 for g in guide["parts"].values() if g.get("same") is False)
    intro = re.sub(r"그중\s+개만", f"그중 {n_lr}개만", intro)
    for i, s in extra.items():
        if s.get("focus"):
            continue
        for d in s["do"]:
            if guide_ui.search(d):
                continue
            chunk("instruction", d, None, f"S08 안내서 {i}단계 ({s['t']})")
        if s.get("tip"):
            chunk("speech", s["tip"], None, f"S08 안내서 {i}단계 팁", slide_no=f"tip-x{i}", t=0, also=[])
    for line in [l.strip() for l in intro.split("\n") if l.strip()]:
        if (any(w in line for w in ("셀프탭", "드라이버", "볼렌치", "바이스", "구분", "거울", "네임펜")) and len(line) > 15
                and not line.startswith("꼭 구분할") and "3D로" not in line):
            chunk("speech", re.sub(r"\s+", " ", line), None, "S08 안내서 첫 화면", slide_no="intro", t=len(chunks), also=[])
    first_step = {}
    for st in k_steps:
        for p in st["parts"]:
            first_step.setdefault(p, st["step_id"])
    for k, v in names.items():
        g = guide["parts"].get(k)
        extra_txt = ""
        if g:
            extra_txt = f" · 출력: {g['print']}" + (" · 왼·오 다른 부품" if g.get("same") is False else " · 왼·오 같은 부품")
        chunk("part", f"{v['name']} — 다른 이름: {', '.join(v['aliases'][1:])}{extra_txt}", first_step.get(k), "부품 목록", part_id=k)

    # 설명 녹화로 배운 설계자 말 (agent/data/kb/robots/<robot>.speech.json) 을 다시 붙인다.
    # 녹화 순서대로: updated 는 같은 글자의 speech 를 바꾸고(이전 글자는 was), added 는 id 만 새로 매겨 붙인다. conflicts 는 넣지 않는다.
    sp_file = ROOT / "agent/data/kb/robots" / f"{robot}.speech.json"
    if sp_file.exists():
        n_add = n_upd = 0
        for rec in json.loads(sp_file.read_text(encoding="utf-8")).get("recordings", []):
            for u in rec.get("updated", []):
                c = next((c for c in chunks if c["kind"] == "speech" and c["text"] == u["old"]), None)
                if c:
                    c["was"] = [*c.get("was", []), u["old"]]
                    c["text"] = u["text"]
                    n_upd += 1
                else:
                    print(f"  녹화 {rec.get('briefing')}: 바꿀 문장을 못 찾음 → {u['old'][:40]}")
            for a in rec.get("added", []):
                chunks.append({**a, "id": f"c{len(chunks)}"})
                n_add += 1
        print(f"설명 녹화 다시 붙임: 새 문장 {n_add}개, 바뀐 문장 {n_upd}개 ({sp_file.name})")

    kb = {"project": f"{robot} v2", "robot": robot, "reader": f"guide:{Path(guide_path).name}",
          "steps": k_steps, "parts": names, "slides": [], "chunks": chunks}
    out_kb = ROOT / "agent/data/kb/robots" / f"{robot}.json"
    out_kb.parent.mkdir(parents=True, exist_ok=True)
    out_kb.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"단계 {len(a_steps)}개 → {out_kb.relative_to(ROOT)}, 지식 조각 {len(chunks)}개, 위쪽 {up}")

    # assembly.json 을 새로 썼으니 볼트도 다시 넣는다 (그 로봇만의 STEP 이 cad/<robot>/<robot>_assembly.step 에 있을 때)
    bolt_step = ROOT / "cad" / robot / f"{robot}_assembly.step"
    if bolt_step.exists():
        import add_bolts
        add_bolts.main(str(bolt_step), str(out_web / f"{robot}.assembly.json"), str(out_web / f"{robot}.parts.json"))
    else:
        print(f"볼트 건너뜀: {bolt_step.relative_to(ROOT)} 없음")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
