"""경험 전달 시험 (켜져 있는 서버로, 실제 시간으로): 앞사람이 막힌 곳이 다음 사람에게 전달되는가.

사용: (서버를 켠 상태, .env 에 PRIOR_HINTS=1) python tests/transfer_test.py
- 작업자 A: 4단계에서 오래 머물며 질문 2개, 5단계에서 다시 4단계로 돌아감, 8단계 튜브에서 막힘, 끝나고 한마디
- 작업자 C: 4단계에서 비슷한 질문, 끝나고 튜브 요령 한마디
- 작업자 B (새 사람): 4단계에 들어가면 앞사람들 질문이 카드에 뜨는지, "다들 어떻게 했어?"에 무엇이 나오는지
data/sessions/ 의 다른 기록도 함께 모이므로, 깨끗하게 보려면 시험 전에 그 폴더를 비워 둔다.
"""
import json, time
import httpx, websocket

B = "http://localhost:8000"
ORDER = [f"S{i:02d}" for i in range(1, 15)]


class Worker:
    def __init__(self, name):
        self.name = name
        s = httpx.post(B + "/api/session", json={"builder": name}).json()
        self.ws = websocket.create_connection(f"ws://localhost:8000/ws/{s['session_id']}", timeout=90)
        self.cards = {}

    def _read(self, until=None, sec=1.5):
        out = []
        self.ws.settimeout(sec)
        try:
            while True:
                m = json.loads(self.ws.recv()); out.append(m)
                if m.get("type") == "step_card":
                    self.cards[m["step"]["step_id"]] = m
                if until and m.get("type") in until:
                    break
        except Exception:
            pass
        self.ws.settimeout(90)
        return out

    def step(self, sid):
        self.ws.send(json.dumps({"type": "step", "step_id": sid, "by": "ui"}))
        return self._read()

    def ask(self, q):
        self.ws.send(json.dumps({"type": "ask", "text": q}))
        msgs = self._read(until={"answer", "nav", "prompt"}, sec=30)
        last = msgs[-1] if msgs else {}
        print(f"   [{self.name}] Q {q}\n        → {(last.get('text') or last.get('speech') or last.get('cmd') or '').replace(chr(10), ' / ')}"
              + (f"\n        출처 {last.get('sources')}" if last.get("sources") else ""))
        return last

    def comment_and_finish(self, comment):
        self.ws.send(json.dumps({"type": "finish"})); self._read(sec=3)
        self.ws.send(json.dumps({"type": "final_comment", "text": comment})); self._read(sec=4)
        self.ws.close()


def walk(w, plan):
    """plan: 단계 → (머무는 초, [질문]). 없는 단계는 1초."""
    for sid in ORDER:
        stay, qs = plan.get(sid, (1, []))
        w.step(sid)
        for q in qs:
            w.ask(q)
        time.sleep(stay)


print("== 작업자 A (첫 사람)")
a = Worker("작업자A")
walk(a, {"S04": (25, ["10밀리랑 12.5밀리 볼트 어떻게 구분해?", "볼트 세게 조여도 돼?"]),
         "S05": (2, []), "S08": (12, ["카본 튜브가 안 들어가"])})
a.step("S04"); a.ask("이전"); time.sleep(5); a.step("S14")   # 다 하고 4단계로 돌아가 다시 확인
a.comment_and_finish("4번에서 10mm랑 12.5mm 볼트가 섞여서 위쪽을 다시 풀었어요. 봉지를 따로 두세요.")

print("\n== 작업자 C (두 번째 사람)")
c = Worker("작업자C")
walk(c, {"S04": (15, ["10mm 볼트랑 12.5mm 볼트가 헷갈려"])})
c.comment_and_finish("카본 튜브는 돌리면서 넣으니까 금방 들어갔어요.")

time.sleep(2)
agg = json.loads(open("data/kb/aggregate.json", encoding="utf-8").read())
print("\n== 쌓인 지식 (aggregate.json)")
for sid in ("S04", "S05", "S08"):
    st = agg["steps"].get(sid, {})
    print(f"   {sid}: 작업자 {st.get('n_builders')}명 · 시간 중간값 {st.get('median_sec')}초 · 어려움 {st.get('difficulty')}")
    for f in st.get("faq", []):
        print(f"        자주 물은 것 ({f['count']}명): {f['question']}  → {f['answer'][:60]}")
    for t in st.get("tips", []):
        print(f"        한마디 ({t['builder']}): {t['text']}")

print("\n== 작업자 B (새 사람)")
b = Worker("작업자B")
for sid in ORDER[:4]:
    b.step(sid)
card = b.cards.get("S04", {})
print("   4단계 카드에 뜬 앞사람 경험:", card.get("hints") or "(없음)")
b.ask("다들 여기서 어떻게 했어?")
b.ask("10밀리랑 12.5밀리 어떻게 구분해?")
for sid in ORDER[4:8]:
    b.step(sid)
b.ask("다들 여기서 어떻게 했어?")
b.ws.close()
