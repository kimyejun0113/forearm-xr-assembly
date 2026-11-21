"""구조 정리 문서의 기능을 하나씩 확인한다 (API 키 없이). 사용: .venv\\Scripts\\python tests\\feature_check.py"""
import sys, time, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
shutil.rmtree(ROOT / "data/sessions/check", ignore_errors=True)
(ROOT / "data/kb/aggregate.json").unlink(missing_ok=True)

from fastapi.testclient import TestClient  # noqa: E402
from server import main  # noqa: E402

c = TestClient(main.app)
results = []
def ok(name, cond, info=""):
    results.append((name, bool(cond), info))

kn = main.kn
ok("PPT 읽기 결과가 단계에 연결됨", all(kn.steps[s]["slides"] for s in kn.order), f"{len(kn.order)}단계")
ok("설계자 말이 단계에 연결됨", sum(1 for ch in kn.chunks if ch["kind"] == "speech" and ch.get("step_id")) > 0)

sid = c.post("/api/session", json={"builder": "check"}).json()["session_id"]
s = main.sessions[sid]
def recv_until(ws, types, n=12):
    got = []
    for _ in range(n):
        m = ws.receive_json(); got.append(m)
        if m["type"] in types:
            break
    return got

with c.websocket_connect(f"/ws/{sid}") as ws:
    first, last = kn.order[0], kn.order[-1]
    ws.send_json({"type": "step", "step_id": first})
    got = recv_until(ws, {"step_card"})
    ok("단계 이동 → 단계 카드", got[-1]["type"] == "step_card")
    ws.send_json({"type": "ask", "text": "다음"})
    m = ws.receive_json(); ok("음성 명령 '다음' → nav", m.get("type") == "nav" and m.get("cmd") == "next")
    ws.send_json({"type": "ask", "text": "5단계로 가"})
    m = ws.receive_json(); ok("음성 명령 'N단계' → nav", m.get("to") == 5)
    ws.send_json({"type": "ask", "text": "커넥터 방향 어느 쪽이야?"})
    got = recv_until(ws, {"answer"}); a = got[-1]
    ok("질문 → 근거 답 + 출처", a["type"] == "answer" and a["sources"], a.get("speech", "")[:50])
    ws.send_json({"type": "ask", "text": "슬라이드 보여줘"})
    a = recv_until(ws, {"answer"})[-1]
    ok("슬라이드는 요청 때만", any(v["type"] == "slide" for v in a["visual"]))
    ws.send_json({"type": "ask", "text": "커넥터 방향 어느 쪽이야?"})
    got = recv_until(ws, {"prompt", "answer"}, 6)
    got += [ws.receive_json()] if got[-1]["type"] == "answer" else []
    ok("예외: 같은 질문 반복", any(g.get("rule") == "repeat" for g in got))
    ws.send_json({"type": "ask", "text": "토크 얼마야?"})
    a = recv_until(ws, {"answer"})[-1]
    ok("모르면 지어내지 않음", a["confident"] is False or "못 찾" in a["speech"], a["speech"][:40])
    ws.send_json({"type": "ask", "text": "아 뭐지"})
    m = ws.receive_json(); ok("예외: 막힌 혼잣말", m.get("rule") == "stuck")
    ws.send_json({"type": "ask", "text": "여기 잘 봐 둬"})
    m = ws.receive_json(); ok("잘 봐 둬 → 장면 표시", m.get("type") == "toast")
    ws.send_json({"type": "ask", "text": "기록 보여줘"})
    m = ws.receive_json(); ok("기록 보관함 열기", m.get("type") == "archive" and len(m["steps"]) == len(kn.order))
    ws.send_json({"type": "ask", "text": "닫아"})
    m = ws.receive_json(); ok("기록 보관함 닫기", m.get("type") == "archive_close")
    # 단계별 진입 알림
    rules = set()
    for st in kn.order[1:]:
        ws.send_json({"type": "step", "step_id": st})
        for g in recv_until(ws, {"step_card"}):
            pass
        time.sleep(0.05)
    ok("단계 14개 끝까지 이동", s.step_id == last)
    s.threshold = lambda _: 1.0
    time.sleep(3.0)
    got = []
    for _ in range(8):
        got.append(ws.receive_json())
        if got[-1].get("rule") == "dwell":
            break
    ok("예외: 오래 머물면 한 번 묻기", any(g.get("rule") == "dwell" for g in got))
    ws.send_json({"type": "ask", "text": "조용히 해 줘"})
    got = recv_until(ws, {"quiet"}, 6); ok("조용히 모드", got[-1].get("type") == "quiet")
    ws.send_json({"type": "finish"})
    m = recv_until(ws, {"prompt"})[-1]; ok("완료 → 한마디 요청", m.get("ask_comment"))
    ws.send_json({"type": "final_comment", "text": "M3 볼트 10mm랑 12.5mm 섞여서 헷갈렸어요. 봉지 나눠 두세요."})
    ws.receive_json()

log = (ROOT / "data/sessions/check").glob("*.jsonl")
evs = [l for f in log for l in f.read_text(encoding="utf-8").splitlines()]
kinds = {__import__("json").loads(l)["type"] for l in evs}
ok("기록: 단계·질문·알림·표시·한마디 따로 저장", {"step_enter", "step_exit", "question", "prompt", "bookmark", "final_comment"} <= kinds)
agg = c.post("/api/aggregate").json()
ok("기록 모아 정리 (단계 시간·어려움)", len(agg["steps"]) > 0)
arch = c.get("/api/archive").json()
ok("보관함에 이전 작업자 한마디·표시 장면", any(s_["workers"]["tips"] for s_ in arch["steps"]) and any(s_["marks"] for s_ in arch["steps"]))

r = c.get("/api/tts", params={"text": "커넥터 방향 어느 쪽이야?"})
ok("음성 합성 (서버)", r.status_code == 200 and len(r.content) > 1000, f"{len(r.content)} bytes")
r = c.post("/api/stt", files={"audio": ("q.mp3", r.content, "audio/mpeg")}).json()
ok("음성 인식 (서버)", "커넥터" in r.get("text", ""), f"{r.get('engine')}: {r.get('text')}")
sys.path.insert(0, str(ROOT / "ingest"))
import read_slides  # noqa: E402
sl = read_slides.read_images([sorted((ROOT / "data/captures").glob("*.jpg"))[1]])[0]
ok("PPT 사진 읽기 (키 없이 OCR)", len(sl.get("instructions", [])) >= 3, sl.get("title"))
a = main.agent.answer("M3 볼트 조임 토크 얼마야?", kn.order[3])
ok("자료에 없으면 인터넷 검색 + 출처", a["intent"] == "web" and a["sources"], a["sources"][0] if a["sources"] else "")

shutil.rmtree(ROOT / "data/sessions/check", ignore_errors=True)
(ROOT / "data/kb/aggregate.json").unlink(missing_ok=True)
for n, r, i in results:
    print(("OK  " if r else "FAIL"), n, ("· " + i) if i else "")
print(f"\n{sum(r for _, r, _ in results)}/{len(results)} 통과")
