"""읽은 슬라이드 + 설계자 말 + 단계 목록 → data/kb/kb.json (단계 카드와 지식 조각).

사용: python ingest/build_kb.py
입력: data/kb/slides_read.json, data/source/speech.json(없어도 됨), data/source/steps.json

단계 연결 규칙
- 슬라이드 머리글 "STEP 01–05" → 그 슬라이드가 맡는 단계 범위 (하나면 그 단계)
- 순서 항목 "04 M3 …" → 앞 번호가 단계 번호
- 주의 항목 "04·05 · …" → 앞 번호들의 단계에 모두 붙인다. 번호가 없으면 슬라이드의 마지막 단계
- 설계자 말 → "4번", "12번 13번"처럼 말한 번호가 있으면 그 단계, 없으면 슬라이드 안에서 가장 비슷한 단계
"""
import sys, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.textutil import similarity  # noqa: E402

FLAG_RULES = {
    "danger": r"전원|배터리|전류|극성|화상|날카|감전",
    "irreversible": r"접착|압입|셀프탭|본드|눌러 넣",
    "prereq": r"먼저|전에|아직 .{0,10}않|다음에|STEP ?\d ?에서",
    "lr": r"왼쪽|오른쪽|L/R|좌우|왼·오",
}
# 슬라이드의 단계 번호는 두 자리: 순서 "04 M3 …", 주의 "04·05 · 10 mm …" (본문 숫자와 헷갈리지 않게)
LEAD_WARN = re.compile(r"^\s*(\d{2}(?:·\d{2})*)\s+·\s+")
LEAD_INSTR = re.compile(r"^\s*(\d{2})[.)]?\s+")


def mmss(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def slide_text(s):
    parts = [s.get("title") or "", *(s.get("text") or []), *(s.get("instructions") or []),
             *(s.get("warnings") or []), s.get("figure") or ""]
    parts += [" ".join(r) for r in (s.get("table") or [])]
    return " ".join(parts)


def slide_steps(s, by_order, steps):
    """슬라이드가 맡는 단계 id 목록"""
    lab = s.get("step_label") or ""
    m = re.search(r"STEP\s*(\d+)\s*(?:[–\-~]\s*(\d+))?", lab, re.I)
    if m:
        a = int(m.group(1)); b = int(m.group(2) or a)
        return [by_order[n] for n in range(a, b + 1) if n in by_order]
    if s.get("kind") == "overview":
        return []
    best = max(steps, key=lambda st: similarity(slide_text(s), st["name"]))
    return [best["id"]]


def lead_numbers(text):
    # OCR은 가운뎃점(·)을 •, ∙, ・ 로 읽기도 한다
    text = re.sub(r"[•∙・‧]", "·", text)
    m = LEAD_WARN.match(text) or LEAD_INSTR.match(text)
    if not m:
        return [], text
    return [int(x) for x in m.group(1).split("·")], text[m.end():]


def align_speech(segs, slides):
    """slide_hint가 없으면 순서를 지키는 최적 정렬(DP)로 문장마다 슬라이드를 고른다."""
    if all("slide_hint" in g for g in segs):
        return [g["slide_hint"] for g in segs]
    texts = [slide_text(s) for s in slides]
    S = [[similarity(g["text"], t) for t in texts] for g in segs]
    n, m = len(segs), len(slides)
    dp = [[-1e9] * m for _ in range(n)]
    bk = [[0] * m for _ in range(n)]
    for j in range(m):
        dp[0][j] = S[0][j] - 0.02 * j
    for i in range(1, n):
        for j in range(m):
            k = max(range(j + 1), key=lambda k: dp[i - 1][k])
            dp[i][j] = dp[i - 1][k] + S[i][j]
            bk[i][j] = k
    j = max(range(m), key=lambda j: dp[n - 1][j])
    path = [j]
    for i in range(n - 1, 0, -1):
        j = bk[i][j]
        path.append(j)
    return [slides[j]["slide_no"] for j in reversed(path)]


def speech_step(text, sids, by_order, instr_of, prev):
    """말한 문장이 어느 단계 이야기인지 → (단계, 같이 말한 단계들)
    번호를 말했으면 그 단계들. 없으면 같은 슬라이드에서 바로 앞 문장의 단계(말은 이어진다),
    그것도 없으면 내용이 가장 비슷한 단계."""
    if not sids:
        return None, []
    rng = {k for k, v in by_order.items() if v in sids}
    said = list(dict.fromkeys(int(x) for x in re.findall(r"(\d{1,2})번", text) if int(x) in rng))
    if said and not re.search(r"\d+번부터", text):
        ids = [by_order[x] for x in said]
        return ids[0], ids[1:]
    if prev in sids:
        return prev, []
    best = max(sids, key=lambda sid: similarity(text, " ".join(instr_of.get(sid, []))))
    return best, []


def main():
    sr = json.loads((ROOT / "data/kb/slides_read.json").read_text(encoding="utf-8"))
    steps_doc = json.loads((ROOT / "data/source/steps.json").read_text(encoding="utf-8"))
    steps, parts = steps_doc["steps"], steps_doc["parts"]
    by_order = {st["order"]: st["id"] for st in steps}
    sp_file = ROOT / "data/source/speech.json"
    segs = json.loads(sp_file.read_text(encoding="utf-8"))["segments"] if sp_file.exists() else []
    slides = sr["slides"]

    s_steps = {s["slide_no"]: slide_steps(s, by_order, steps) for s in slides}
    seg_slide = align_speech(segs, slides) if segs else []

    chunks, instr_of, warn_of, slides_of = [], {}, {}, {}
    def add(kind, text, step, source, **kw):
        chunks.append({"id": f"c{len(chunks)}", "kind": kind, "text": text, "step_id": step, "source": source, **kw})

    for s in slides:
        n, sids = s["slide_no"], s_steps[s["slide_no"]]
        src = f"슬라이드 {n}"
        first = sids[0] if sids else None
        for sid in sids:
            slides_of.setdefault(sid, []).append(n)
        if s.get("title"):
            add("slide_title", f"{s['title']} ({s.get('step_label') or ''})", first, src, slide_no=n)
        for t in s.get("text") or []:
            add("slide_text", t, first, src, slide_no=n)
        for row in (s.get("table") or [])[1:]:
            add("table_row", " · ".join(row), first, src, slide_no=n)
        for i, t in enumerate(s.get("instructions") or [], 1):
            nums, body = lead_numbers(t)
            sid = by_order.get(nums[0]) if nums else (sids[i - 1] if i - 1 < len(sids) else first)
            instr_of.setdefault(sid, []).append(body)
            add("instruction", body, sid, src, slide_no=n)
        for t in s.get("warnings") or []:
            nums, body = lead_numbers(t)
            targets = [by_order[x] for x in nums if x in by_order and by_order[x] in sids] or ([sids[-1]] if sids else [None])
            for sid in targets:
                warn_of.setdefault(sid, []).append(body)
            add("warning", body, targets[0], src, slide_no=n, also=targets[1:])
        if s.get("figure"):
            add("figure", s["figure"], first, src, slide_no=n)
    prev, prev_slide = None, None
    for g, sn in zip(segs, seg_slide):
        if sn != prev_slide:
            prev = None
        sid, also = speech_step(g["text"], s_steps.get(sn, []), by_order, instr_of, prev)
        add("speech", g["text"], sid, f"설계자 설명 {mmss(g['t'])}", slide_no=sn, t=g["t"], also=also)
        prev, prev_slide = (also[-1] if also else sid), sn
    for pid, p in parts.items():
        owner = next((st["id"] for st in steps if pid in st["parts"]), None)
        add("part", f"{p['name']} — 다른 이름: {', '.join(p['aliases'])}", owner, "부품 목록", part_id=pid)

    cards = []
    for st in steps:
        sid = st["id"]
        speech = [c["text"] for c in chunks if c["kind"] == "speech" and (c["step_id"] == sid or sid in c.get("also", []))]
        instr, warns = instr_of.get(sid, []), warn_of.get(sid, [])
        bolts = [f"{b[0]} {b[1]} × {b[2]}" for b in st.get("bolts", [])]
        flags = {}
        for name, rx in FLAG_RULES.items():
            # 선행 조건은 슬라이드에 적힌 것만 믿는다 (말은 "먼저 걸고" 같은 순서 설명이 많아 잘못 잡힘)
            pool = warns + instr if name == "prereq" else warns + instr + speech
            hits = [t for t in pool if re.search(rx, t)]
            if hits:
                flags[name] = hits[:3]
        cards.append({
            "step_id": sid, "order": st["order"], "name": st["name"],
            "parts": st["parts"], "tools": st.get("tools", []), "bolts": bolts,
            "instructions": instr or [st["name"]], "warnings": warns,
            "slides": slides_of.get(sid, []), "flags": flags, "check": st.get("check"),
            "baseline_sec": 40 + 25 * len(st["parts"]) + 20 * sum(b[2] for b in st.get("bolts", [])),
        })

    kb = {"project": steps_doc["project"], "reader": sr.get("reader"), "steps": cards,
          "parts": parts, "slides": [{"slide_no": s["slide_no"], "image": s["image"], "title": s.get("title"),
                                      "step_ids": s_steps[s["slide_no"]]} for s in slides],
          "chunks": chunks}
    (ROOT / "data/kb/kb.json").write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"steps {len(cards)}, chunks {len(chunks)}")
    for c in cards:
        sp = sum(1 for x in chunks if x["kind"] == "speech" and (x["step_id"] == c["step_id"] or c["step_id"] in x.get("also", [])))
        print(c["step_id"], c["name"], "| slide", c["slides"], "| warn", len(c["warnings"]), "| speech", sp,
              "| flags", list(c["flags"]), "| base", c["baseline_sec"])


if __name__ == "__main__":
    main()
