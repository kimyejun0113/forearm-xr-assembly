"""질문 → (어디서 찾을지) → 검색 → 답 (음성 짧게 + 화면 + 작은 출처)."""
import re
from . import llm
from .knowledge import Knowledge


NAV = re.compile(r"^\s*(다음|이전|뒤로|다시|처음|(\d+)\s*단계)\s*(단계)?\s*(으로|로)?\s*(가|가줘|가자|넘겨|보여줘)?\s*[.!?]?\s*$")
SHOW_SLIDE = re.compile(r"슬라이드.*(보여|띄워|열어)|PPT.*(보여|띄워)")
QUIET = re.compile(r"조용히|그만 말|말 걸지 마")
ARCHIVE = re.compile(r"(기록|보관함|메모|영상|주의사항).{0,6}(보여|열어|띄워|봐)|보관함|기록 ?창")
SPEC_TERMS = ["토크", "지름", "무게", "전압", "온도", "재질", "공차", "규격"]
BOOKMARK = re.compile(r"잘 ?봐 ?둬|녹화해|기억해 ?둬|표시해 ?둬|찍어 ?둬")
CLOSE = re.compile(r"^(닫아|닫기|창 닫아|보관함 닫아|됐어)")
STUCK = re.compile(r"아 뭐지|왜 안 (들어|돼|맞)|모르겠|헷갈")

SYSTEM = """너는 로봇 조립을 돕는 안경 속 조수다. 작업자는 손이 바쁘다.
주어진 근거(현재 단계 카드, 설계자 슬라이드, 설계자 말, 이전 작업자 기록)만 써서 답한다. 근거에 없으면 지어내지 말고 부드럽게 모른다고 말한다.
"~해도 돼?", "같은 거야?", "구분해야 돼?"는 근거가 "다르다", "안 된다", "같다", "된다" 중 무엇이라 하든 그대로 답한다 (예: 근거가 "왼·오가 다른 부품"이면 "달라요").
근거와 단계 카드 어디에도 그 이야기가 없을 때만 "괜찮다", "구분 없다"처럼 추측해서 허락하지 말고, speech에 "자료엔 없어요"라고 하고 confident를 false로 한다.
작업자는 지금 '현재 단계'를 하고 있다. "이거", "이것", "여기"는 현재 단계의 부품을 뜻한다.
개수·볼트·공구·주의처럼 단계에 딸린 질문은 현재 단계 카드([step])를 먼저 보고 그 단계 값으로 답한다 (전체 합계가 아니라).
"다음 단계", "지금 몇 단계"는 카드의 단계 번호와 다음 단계를 그대로 말한다. 카드에 "확인 필요"가 있으면 함께 알려 준다.
"왜", "~하면 어떻게 돼", "~해도 돼?", "주의할 거", "요령", "안 될 때"처럼 이유·결과·요령을 묻는 질문은
설계자 말(speech)에 있는 이유와 결과(예: "나사산이 뭉개진다", "다 풀어야 한다", "보드를 태운 적 있다")를 반드시 그대로 담아 답한다.
카드의 지시만 되풀이하지 않는다. 질문과 관계없는 다른 단계 이야기는 섞지 않는다.
JSON으로만 답한다:
{"speech": "귀로 들을 핵심 한 문장 (길어도 두 문장, 40자 안팎). 손이 바로 할 것만: 숫자·방향·순서·주의 하나. 말하듯 짧은 존댓말. 이유·출처·인사·'설계자 설명으로는' 같은 머리말은 빼고 화면(text)에 둔다",
 "text": "화면에 보일 자세한 설명 (네 줄 이내): 핵심 + 이유·결과 + 확인 방법",
 "used": ["실제로 쓴 근거 id"],
 "highlight": ["강조할 부품 id (부품 목록에 있는 것만)"],
 "confident": true 또는 false}"""


LR_Q = re.compile(r"왼쪽|오른쪽|좌우|왼오")
# 슬라이드 그림 설명의 "왼쪽에는 …, 오른쪽에는 …"(화면 배치)은 부품 왼·오 이야기가 아니다
LR_EVID = re.compile(r"왼·오|좌우|L\s*/\s*R|거울상|(왼|오른)쪽\s*(부품|과|이랑|하고|·)")


def classify(q: str) -> str:
    if re.search(r"왜|이유|무슨 차이", q):
        return "why"
    if re.search(r"다들|다른 사람|이전|전에 한 사람|선배들", q):
        return "prior"
    if re.search(r"토크|규격|사양|몇 ?mm|몇 ?밀리|지름|길이|몇 개|수량|얼마", q):
        return "spec"
    if re.search(r"어느 쪽|방향|위가|아래가|거꾸로|앞뒤|어디에 ?(넣|끼|꽂|달|박)", q):
        return "orient"
    if re.search(r"뭐야|어느 거|어떤 거|어디|모양", q):
        return "part"
    if re.search(r"어떻게|방법|순서|뭐 해|다음에 뭐", q):
        return "how"
    return "default"


class Agent:
    def __init__(self, kn: Knowledge):
        self.kn = kn

    def route(self, text: str):
        """명령(3D로 보냄)인지 질문인지 먼저 가른다."""
        m = NAV.match(text)
        if m:
            word = m.group(1)
            if m.group(2):
                return {"type": "nav", "to": int(m.group(2))}
            return {"type": "nav", "cmd": {"다음": "next", "이전": "prev", "뒤로": "prev", "다시": "replay", "처음": "first"}[word]}
        if QUIET.search(text):
            return {"type": "quiet"}
        if BOOKMARK.search(text):
            return {"type": "bookmark"}
        if CLOSE.search(text):
            return {"type": "archive_close"}
        if ARCHIVE.search(text):
            return {"type": "archive"}
        return {"type": "question"}

    def answer(self, q: str, step_id: str | None, slide_open: bool = False) -> dict:
        """들리는 말(speech)은 핵심만 짧게, 화면(text)은 자세히. LLM을 안 거친 답도 들리는 말은 줄인다."""
        r = self._answer(q, step_id, slide_open)
        if r.get("speech"):
            if not r.get("text"):
                r["text"] = r["speech"]
            r["speech"] = llm.spoken(r["speech"], 60)
        return r

    def _answer(self, q: str, step_id: str | None, slide_open: bool = False) -> dict:
        intent = classify(q)
        # "5단계에서 …"처럼 단계를 말하면 지금 화면 단계(없을 수도 있음) 대신 그 단계로 답한다
        m = re.search(r"(\d{1,2})\s*(?:번\s*)?단계", q)
        named = next((s for s in self.kn.steps.values() if m and s["order"] == int(m.group(1))), None)
        if named:
            step_id = named["step_id"]
        self._step_id = step_id
        step = self.kn.steps.get(step_id)
        if SHOW_SLIDE.search(q):
            slide = ((step or {}).get("slides") or [None])[0]
            img = next((s["image"] for s in self.kn.kb["slides"] if s["slide_no"] == slide), None)
            return {"speech": "슬라이드를 띄웠어요." if img else "이 단계에 연결된 슬라이드가 없어요.",
                    "text": "", "visual": [{"type": "slide", "slide_no": slide, "image": img}] if img else [],
                    "sources": [f"슬라이드 {slide}"] if img else [], "intent": "show_slide", "confident": bool(img)}

        if re.search(r"다음 뭐|뭐 해야|지금 뭐", q) and step:
            todo = step["instructions"][0] if step["instructions"] else step["name"]
            return {"speech": f"지금 단계는 {step['name']}이에요. 먼저 {todo}.", "text": "\n".join(
                f"{i}. {t}" for i, t in enumerate(step["instructions"], 1)), "visual": [],
                "sources": [f"슬라이드 {step['slides'][0]}"] if step["slides"] else [], "intent": "how", "confident": True}

        hits = self.kn.search(q, step_id, intent)
        parts_q = self.kn.find_parts(q)
        if step and any(p in step["parts"] for p in parts_q):
            parts_q = [p for p in parts_q if p in step["parts"]]  # "카본 튜브"처럼 둘 다 맞으면 지금 단계 것만

        if intent == "prior":
            return self._prior(step_id)
        # 왼쪽/오른쪽을 묻는데 근거(카드·검색 조각) 어디에도 왼·오 이야기가 없으면 LLM이 "같다/다르다"를 지어낸다 → 바로 모른다고
        if LR_Q.search(q):
            evid = self._step_card_text(step) + " " + " ".join(c["text"] for _, c in hits)
            if not LR_EVID.search(evid):
                return {"speech": "왼쪽 오른쪽 구분은 자료엔 없어요.", "text": "설명 자료와 단계 카드에 왼쪽·오른쪽 구분 이야기가 없습니다.",
                        "visual": [], "sources": [], "intent": intent, "confident": False}
        # 키가 있으면 검색 조각이 없어도 현재 단계 카드만으로 답할 수 있다 (예: "어떤 렌치 써?")
        if llm.available() and (hits or step):
            try:
                return self._llm_answer(q, step, hits, intent, parts_q)
            except Exception as e:  # 네트워크 등 실패 → 규칙 답으로
                print("llm error:", e)
        return self._extractive(q, hits, intent, parts_q)

    def _step_card_text(self, step) -> str:
        """현재 단계 카드와 앞뒤 단계를 근거 한 덩어리로 ([step])."""
        if not step:
            return ""
        order, parts = self.kn.order, self.kn.kb["parts"]
        i = order.index(step["step_id"]) if step["step_id"] in order else -1
        name = lambda sid: f"{self.kn.steps[sid]['order']}단계 {self.kn.steps[sid]['name']}"
        lines = [f"현재 단계: {step['order']} / {len(order)}단계 · {step['name']}",
                 "부품: " + ", ".join(f"{p}({parts.get(p, {}).get('name', p)}"
                                    + (f", {self.kn.part_lr[p]}" if p in self.kn.part_lr else "") + ")"
                                    for p in step["parts"])]
        if step.get("instructions"): lines.append("할 일: " + " / ".join(step["instructions"]))
        if step.get("bolts"): lines.append("이 단계 볼트: " + " / ".join(step["bolts"]))
        if step.get("tools"): lines.append("공구: " + ", ".join(step["tools"]))
        if step.get("warnings"): lines.append("주의: " + " / ".join(step["warnings"]))
        if step.get("check"): lines.append("확인 필요: " + step["check"])
        if i > 0: lines.append("이전 단계: " + name(order[i - 1]))
        lines.append("다음 단계: " + (name(order[i + 1]) if 0 <= i < len(order) - 1 else "없음 (마지막 단계)"))
        return "\n".join(lines)

    def _llm_answer(self, q, step, hits, intent, parts_q):
        card = self._step_card_text(step)
        ctx = "\n".join(f"[{c['id']}] ({c['kind']}, {c['source']}) {c['text']}" for _, c in hits)
        user = (f"[step] (현재 단계 카드)\n{card or '현재 단계 모름'}\n\n부품 목록: {', '.join(self.kn.kb['parts'])}\n"
                f"근거:\n{ctx or '(검색된 조각 없음)'}\n\n작업자 질문: {q}")
        r = llm.chat_json(SYSTEM, user)
        ids = r.get("used", [])
        used = [c for _, c in hits if c["id"] in ids]
        sources = {c["source"] for c in used}
        if "step" in ids or not used:
            sources.add(f"단계 카드 {step['order']}단계" if step else "단계 카드")
        hl = [p for p in r.get("highlight", []) if p in self.kn.kb["parts"]] or parts_q
        return {"speech": r.get("speech", ""), "text": r.get("text", ""),
                "visual": [{"type": "highlight", "part_ids": hl}] if hl else [],
                "sources": sorted(sources), "intent": intent,
                "confident": bool(r.get("confident", True))}

    def _prior(self, step_id):
        """'다들 어떻게 했어?' → 이 단계의 이전 작업자 기록을 그대로."""
        st = self.kn.step_stats(step_id)
        tips, faq = st.get("tips", []), st.get("faq", [])
        if not tips and not faq:
            return {"speech": "이 단계는 아직 이전 작업자 기록이 없어요.", "text": "", "visual": [], "sources": [],
                    "intent": "prior", "confident": False}
        lines = [f"한마디: {t['text']}" for t in tips[:2]] + [f"자주 물은 것({q['count']}명): {q['question']}" for q in faq[:2]]
        speech = ("이전 작업자 한마디예요. " + tips[0]["text"]) if tips else f"이전 작업자들은 '{faq[0]['question']}'를 많이 물어봤어요."
        return {"speech": speech, "text": "\n".join(lines), "visual": [],
                "sources": [f"이전 작업자 {st.get('n_builders', 0)}명 기록"], "intent": "prior", "confident": True}

    def _web(self, q, intent):
        """설명 자료에 없을 때만 인터넷 검색. 규격·일반 지식 질문에 한한다 (방향·이유·경험은 검색 안 함)."""
        if intent in ("why", "orient", "prior", "part") or len(q) < 5:
            return None
        from . import local_ai
        rs = [r for r in local_ai.web_search(q, 3) if r["snippet"]]
        if not rs:
            return None
        sn = re.split(r"(?<=[.다요])\s", rs[0]["snippet"].strip())[0][:120]
        return {"speech": "설명 자료엔 없어서 인터넷에서 찾았어요. " + sn,
                "text": "\n".join(f"{r['title']}: {r['snippet'][:140]}" for r in rs[:2]),
                "visual": [], "sources": [f"인터넷: {r['title'][:30]}" for r in rs[:2]],
                "links": [r["url"] for r in rs[:2]], "intent": "web", "confident": False}

    def _extractive(self, q, hits, intent, parts_q):
        """API 없이: 가장 맞는 근거 1~2개를 그대로 읽어 준다."""
        # 수치·규격 질문인데 그 낱말(토크, 지름 …)이 근거 어디에도 없으면 지어내지 않는다
        need = [w for w in SPEC_TERMS if w in q]
        if need and hits and not any(w in c["text"] for _, c in hits[:3] for w in need):
            hits = []
        if not hits or hits[0][0] < 1.5:
            web = self._web(q, intent)
            if web:
                return web
            return {"speech": "설명 자료에서는 딱 맞는 내용을 못 찾았어요. 조금 다르게 물어봐 주시겠어요?",
                    "text": "", "visual": [{"type": "highlight", "part_ids": parts_q}] if parts_q else [],
                    "sources": [], "intent": intent, "confident": False}
        top = [c for _, c in hits[:2]]
        if intent in ("why", "orient", "prior"):
            # 이유·방향·경험 질문은 설계자 말이나 이전 작업자 기록이 충분히 맞으면 그걸 먼저
            pref = {"why": ("speech", "warning"), "orient": ("warning", "speech"), "prior": ("prior_qa", "tip")}[intent]
            alt = next((c for s, c in hits[:4] if c["kind"] in pref and s >= 0.6 * hits[0][0]), None)
            if alt and alt is not top[0]:
                top = [alt, top[0]]
        best = top[0]
        lead = {"speech": "설계자 설명으로는, ", "warning": "주의할 점이에요. ", "prior_qa": "이전 작업자들도 물어본 부분이에요. ",
                "tip": "이전 작업자 팁이에요. "}.get(best["kind"], "")
        txt = re.sub(r"^질문: .*?→ 답: ", "", best["text"])
        txt = re.sub(r"^\d+\.\s*", "", txt)
        txt = re.sub(r"^(설계자 설명으로는, |주의할 점이에요\. |이전 작업자.*?이에요\. )+", "", txt)
        if best["kind"] == "speech":
            # 설계자는 여러 문장에 걸쳐 설명한다: 같은 슬라이드의 바로 다음 문장을 이어 붙인다
            nxt = next((c for c in self.kn.chunks if c["kind"] == "speech" and c.get("slide_no") == best.get("slide_no")
                        and c.get("t", 0) > best.get("t", 0)), None)
            if nxt and len(txt) < 60:
                txt += " " + nxt["text"]
        step = self.kn.steps.get(self._step_id)
        from_chunks = self.kn.find_parts(best["text"])
        if step:  # 지금 단계에 있는 부품만 강조
            from_chunks = [p for p in from_chunks if p in step["parts"]]
        parts = parts_q or from_chunks[:3]
        return {"speech": lead + txt, "text": "\n".join(re.sub(r"^질문: .*?→ 답: ", "", c["text"]) for c in top),
                "visual": [{"type": "highlight", "part_ids": parts}] if parts else [],
                "sources": sorted({c["source"] for c in top}), "intent": intent, "confident": True}
