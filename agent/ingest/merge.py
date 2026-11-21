"""새 설명 녹화를 기존 지식에 합친다. 녹화할 때마다 지식을 통째로 갈아엎지 않는다.

슬라이드: 기존 번호는 그대로. 같은 제목이면 더 또렷하게 읽힌 쪽으로 내용만 바꾸고, 처음 보는 슬라이드만 뒤에 붙인다
         (번호가 안 바뀌어야 기존 설계자 말의 슬라이드 연결이 안 깨진다).
설계자 말: 새 녹음 문장을 LLM이 기존 말·단계·슬라이드와 비교해 문장마다 정한다.
         add(새 요령) / replace(기존 문장을 더 구체적으로) / conflict(기존과 반대 — 넣지 않고 확인 요청) / drop(잡담·이미 있는 내용).
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
             기존 숫자·순서를 다른 값으로 바꾸는 것은 replace가 아니라 conflict다.
- "conflict": 기존 문장과 맞지 않는 말 — 순서가 반대이거나("링크 1을 먼저" vs 기존 "링크 2를 먼저"), 숫자·규격·방향·개수가 다를 때.
             지식에 넣지 않고 사람이 확인하게 따로 모은다. target에 부딪히는 기존 번호, why에 무엇이 다른지.
             음성 인식이 틀려서 반대로 들렸을 수도 있으니, 고쳐서 기존과 맞게 되는 분명한 오류가 아니면 conflict다.
             (새 문장 일부만 부딪혀도 conflict. 부딪히지 않는 새 정보가 따로 있으면 그 부분만 add로 따로 써도 안 된다 — 한 문장은 한 action)
- "drop": 잡담·맞장구·혼잣말("괜찮아", "그냥 해봐", "네", "음?"), 녹화 조작 말("녹화 끝", "찍어"), 뜻을 알 수 없는 말,
          또는 기존 문장과 같은 내용이라 새로 배울 게 없는 말 (그때 target에 그 기존 번호).
text: 음성 인식(헤드셋 옆에서 말한 작은 소리)이라 글자가 자주 틀린다. 앞뒤 문장, 그 슬라이드가 맡은 단계, 부품 목록을 보고
      소리가 비슷한 오류는 고친다. 자주 나오는 것:
      - 숫자+번: "이번/이 번"→"2번", "일번/한 번은"→"1번은", "삼번"→"3번", "사번"→"4번", "오번"→"5번", "육번"→"6번", "칠번/이쪽"→"7번" 등.
        그 슬라이드 단계 중 말한 부품과 맞는 번호일 때만 (예: 슬라이드 2(1~5단계)에서 "이번 클램프는 파트 19" → 2단계가 클램프(Part19)이므로 "2번 클램프").
      - 부품·재료: "커본"→"카본", "전환"→"전완", "링크 이를"/"그 2가"(링크 이야기 중)→"링크 2를"/"링크 2가", "P/피"(핀 이야기)→"핀" 등 부품 목록의 이름.
      - 동사: "바퀴면"(가시 이야기)→"박히면", "헛더라"→"헛돌더라", "뚫을 때"(볼트)→"돌릴 때"처럼 문맥상 분명한 것.
      확신이 없으면 들은 말 그대로 둔다. 말을 다듬거나, 들리지 않은 내용을 보태거나, 뜻을 바꾸지 않는다. drop이면 원문.
      한 생각이 두 문장으로 끊겨 들렸으면 (예: "카본 튜브 넣을 때 장갑 꼭 껴야 돼" + "카본 가시가 박히면 잘 안 빠져")
      앞 문장을 add하면서 text에 둘을 이어 쓰고, 뒤 문장은 drop(target null, why "앞 문장에 합침")으로 해도 된다.
기존 설계자 말은 이미 검토한 지식이다. 줄이거나 덮어쓰는 일이 없게 하고, 헷갈리면 add나 drop을 고른다.
slide: 이 문장이 설명하는 슬라이드 번호. 녹음 시각으로 고른 힌트(slide_hint)가 있지만 내용이 다른 슬라이드 이야기면 고친다.
step: 특정 단계 이야기면 그 단계 id(예 "S08"), 여러 단계·전체 이야기면 null.
why: 한 줄 이유.

JSON으로만 답한다: {"items": [{"i": 새 문장 번호, "action": "add|replace|conflict|drop", "target": 기존 번호 또는 null, "text": "...", "slide": 번호 또는 null, "step": "S01" 또는 null, "why": "..."}]}"""


def merge_speech(old_segs, new_segs, slides, bid, steps=None, parts=None):
    """→ (합친 문장들, 결과 {"added": [...], "updated": [...], "dropped": [...]})"""
    from server import llm
    from ingest import build_kb
    report = {"added": [], "updated": [], "conflicts": [], "dropped": []}
    if not new_segs:
        return old_segs, report
    if steps is None:   # 포어암: data/source/steps.json. 다른 로봇은 그 지식의 단계·부품을 넘겨받는다
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
                    "단계": build_kb.slide_steps(s, by_order, steps)} for s in slides] or "없음 (이 로봇은 안내서로 만든 지식이라 슬라이드가 없다. slide는 null)",
        "기존_설계자_말_단계": [g.get("step") for g in old_segs] if not slides else None,
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
        elif act == "conflict":   # 기존 지식과 반대되는 말: 넣지 않고 사람에게 보여 준다
            report["conflicts"].append({"text": text, "heard": g["text"], "old": merged[tgt]["text"] if tgt is not None else None,
                                        "why": it.get("why", "")})
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
