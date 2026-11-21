"""새 설명 녹화를 기존 지식에 합친다. 녹화할 때마다 지식을 통째로 갈아엎지 않는다.

슬라이드: 기존 번호는 그대로. 같은 제목이면 더 또렷하게 읽힌 쪽으로 내용만 바꾸고, 처음 보는 슬라이드만 뒤에 붙인다
         (번호가 안 바뀌어야 기존 설계자 말의 슬라이드 연결이 안 깨진다).
설계자 말: 새 녹음 문장을 LLM이 기존 말·단계·슬라이드와 비교해 문장마다 정한다.
         add(새 요령) / replace(기존 문장을 더 구체적으로) / drop(잡담·맞장구·녹화 조작 말·이미 있는 내용).
         음성 인식이 틀린 글자는 부품·단계 이름을 보고 고치고, 어느 단계 이야기인지도 붙인다. 기존 문장은 지우지 않는다.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def merge_slides(old, new):
    """→ (합친 슬라이드, 새 슬라이드 i → 합친 번호, 바뀐 것 설명 목록)"""
    from ingest.pipeline import _same_title, _score
    merged = [dict(s) for s in old]
    mapping, notes = {}, []
    for i, s in enumerate(new):
        j = next((k for k, o in enumerate(merged) if _same_title(o.get("title"), s.get("title"))), None)
        if j is None:
            if not (s.get("step_label") or s.get("instructions") or s.get("warnings")):
                mapping[i] = None              # 내용 없는 처음 보는 장면은 붙이지 않는다
                continue
            merged.append({**s, "slide_no": len(merged) + 1})
            mapping[i] = len(merged)
            notes.append(f"슬라이드 {len(merged)} '{s.get('title')}' 새로 추가")
            continue
        no = merged[j].get("slide_no", j + 1)
        if _score(s) > _score(merged[j]):
            merged[j] = {**s, "slide_no": no}
            notes.append(f"슬라이드 {no} '{s.get('title')}' 더 또렷한 사진으로 다시 읽음")
        mapping[i] = no
    return merged, mapping, notes


SYSTEM = """너는 로봇 조립 설명을 정리하는 편집자다. 설계자(선배)가 슬라이드를 넘기며 말한 녹음을 음성 인식한 새 문장들을,
이미 정리된 설계자 말에 합칠지 문장마다 정한다. 목표: 나중에 조립하는 사람에게 도움이 되는 요령만 남기고, 기존 지식은 지키는 것.

새 문장마다 action을 고른다.
- "add": 조립에 쓸모 있는 새 정보 (순서, 방향, 요령, 주의, 왜 그런지, 확인 방법, 실패 경험). 기존 말에 없는 내용.
- "replace": 기존 문장 하나와 같은 이야기인데 새 문장에 기존에 없던 구체적인 정보(숫자, 방향, 이유, 확인법)가 더 있을 때만. target에 그 기존 번호.
             이때 text는 기존 문장의 정보를 하나도 빼지 않고 새 정보를 더한 한 문장이다. 새 정보가 없으면 replace가 아니라 drop이다.
- "drop": 잡담·맞장구·혼잣말("괜찮아", "그냥 해봐", "네", "음?"), 녹화 조작 말("녹화 끝", "찍어"), 뜻을 알 수 없는 말,
          또는 기존 문장과 같은 내용이라 새로 배울 게 없는 말 (그때 target에 그 기존 번호).
text: 음성 인식이라 글자가 틀릴 수 있다. 부품·단계 이름 목록을 보고 분명한 오류만 고친다
      (예: "링크 이를" → "링크 2를", "전환" → "전완", "한 번은" → "1번은"). 그 밖에는 들은 말 그대로 둔다:
      말을 다듬거나, 들리지 않은 말("소리가 난다" 등)을 보태거나, 뜻을 바꾸지 않는다. drop이면 원문.
기존 설계자 말은 이미 검토한 지식이다. 줄이거나 덮어쓰는 일이 없게 하고, 헷갈리면 add나 drop을 고른다.
slide: 이 문장이 설명하는 슬라이드 번호. 녹음 시각으로 고른 힌트(slide_hint)가 있지만 내용이 다른 슬라이드 이야기면 고친다.
step: 특정 단계 이야기면 그 단계 id(예 "S08"), 여러 단계·전체 이야기면 null.
why: 한 줄 이유.

JSON으로만 답한다: {"items": [{"i": 새 문장 번호, "action": "add|replace|drop", "target": 기존 번호 또는 null, "text": "...", "slide": 번호 또는 null, "step": "S01" 또는 null, "why": "..."}]}"""


def merge_speech(old_segs, new_segs, slides, bid):
    """→ (합친 문장들, 결과 {"added": [...], "updated": [...], "dropped": [...]})"""
    from server import llm
    from ingest import build_kb
    report = {"added": [], "updated": [], "dropped": []}
    if not new_segs:
        return old_segs, report
    steps_doc = json.loads((ROOT / "data/source/steps.json").read_text(encoding="utf-8"))
    steps, parts = steps_doc["steps"], steps_doc["parts"]
    by_order = {st["order"]: st["id"] for st in steps}
    if not llm.available():   # 판단할 LLM이 없으면 기존 지식을 지키고 새 말은 넣지 않는다
        report["dropped"] = [{"text": g["text"], "why": "LLM이 없어 판단 못 함"} for g in new_segs]
        return old_segs, report
    ctx = {
        "단계": [{"id": st["id"], "번호": st["order"], "이름": st["name"]} for st in steps],
        "부품": [p["name"] + (" / " + ", ".join(p.get("aliases", [])) if p.get("aliases") else "") for p in parts.values()],
        "슬라이드": [{"번호": s["slide_no"], "제목": s.get("title"), "단계표시": s.get("step_label"),
                    "단계": build_kb.slide_steps(s, by_order, steps)} for s in slides],
        "기존_설계자_말": [{"번호": k, "slide": g.get("slide_hint"), "text": g["text"]} for k, g in enumerate(old_segs)],
        "새_문장": [{"i": i, "slide_hint": g.get("slide_hint"), "시각": g.get("t"), "text": g["text"]} for i, g in enumerate(new_segs)],
    }
    res = llm.chat_json(SYSTEM, json.dumps(ctx, ensure_ascii=False), temperature=0)
    items = {it.get("i"): it for it in res.get("items", []) if isinstance(it, dict)}
    slide_nos = {s["slide_no"] for s in slides}
    step_ids = {st["id"] for st in steps}
    merged = [dict(g) for g in old_segs]
    adds = []
    for i, g in enumerate(new_segs):
        it = items.get(i) or {"action": "drop", "why": "판단 결과 없음"}
        act, text = it.get("action"), (it.get("text") or g["text"]).strip()
        slide = it.get("slide") if it.get("slide") in slide_nos else g.get("slide_hint")
        step = it.get("step") if it.get("step") in step_ids else None
        tgt = it.get("target") if isinstance(it.get("target"), int) and 0 <= it.get("target") < len(old_segs) else None
        if act == "replace" and tgt is not None:
            report["updated"].append({"old": merged[tgt]["text"], "text": text, "why": it.get("why", "")})
            merged[tgt].setdefault("was", []).append({"text": merged[tgt]["text"], "until": bid})   # 바뀌기 전 문장은 남긴다
            merged[tgt].update({"text": text, "src": bid})
            if step:
                merged[tgt]["step"] = step
        elif act == "add" and text:
            seg = {"t": g.get("t", 0), "text": text, "src": bid}
            if slide:
                seg["slide_hint"] = slide
            if step:
                seg["step"] = step
            adds.append(seg)
            report["added"].append({"text": text, "heard": g["text"], "slide": slide, "step": step, "why": it.get("why", "")})
        else:
            report["dropped"].append({"text": g["text"], "why": it.get("why", ""), "same_as": tgt})
    for seg in adds:   # 같은 슬라이드 문장들 뒤에 끼워 넣는다 (말 순서가 슬라이드 순서를 따르게)
        sl = seg.get("slide_hint")
        pos = max([k + 1 for k, g in enumerate(merged) if sl is not None and (g.get("slide_hint") or 0) <= sl] or [len(merged)])
        merged.insert(pos, seg)
    return merged, report
