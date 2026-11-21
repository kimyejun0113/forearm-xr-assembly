"""두 작업자를 흉내 내서 전체 흐름을 확인한다 (API 키 없이).

사용: .venv\\Scripts\\python tests\\simulate.py
- 작업자 1: 단계를 진행하며 질문, 오래 머묾, 완료 후 한마디
- 기록을 모아(aggregate) 지식을 갱신
- 작업자 2: 같은 질문을 하면 이전 작업자 기록이 답과 힌트에 나오는지 확인
"""
import sys, json, time, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEST_SESS = ROOT / "data/sessions"
for d in ("sim1", "sim2"):
    shutil.rmtree(TEST_SESS / d, ignore_errors=True)
(ROOT / "data/kb/aggregate.json").unlink(missing_ok=True)  # 매번 작업자 0명에서 시작

from fastapi.testclient import TestClient  # noqa: E402
from server import main, session  # noqa: E402

client = TestClient(main.app)


def drain(ws, n=10, wait=0.3):
    out = []
    ws_ = ws
    for _ in range(n):
        try:
            out.append(ws_.receive_json(timeout=wait) if hasattr(ws_, "receive_json") else None)
        except Exception:
            break
    return out


def run(builder, questions_by_step, fast_dwell=False):
    sid = client.post("/api/session", json={"builder": builder}).json()["session_id"]
    s = main.sessions[sid]
    if fast_dwell:
        s.threshold = lambda step_id: 3.0
    print(f"\n===== {builder} =====")
    with client.websocket_connect(f"/ws/{sid}") as ws:
        for step in main.kn.order:
            ws.send_json({"type": "step", "step_id": step, "by": "ui"})
            msg = ws.receive_json()
            while msg["type"] != "step_card":  # 앞 단계에서 늦게 온 알림
                print(f"   (알림:{msg.get('rule')}) {msg.get('text')}")
                msg = ws.receive_json()
            print(f"\n[{step}] {msg['step']['name']}  hints={msg['hints']}")
            # 단계 진입 알림들
            for q in questions_by_step.get(step, []):
                ws.send_json({"type": "ask", "text": q})
                while True:
                    m = ws.receive_json()
                    if m["type"] == "prompt":
                        print(f"   (알림:{m['rule']}) {m['text']}")
                        continue
                    if m["type"] == "answer":
                        print(f"   Q: {q}\n   A: {m['speech']}   [{', '.join(m['sources'])}]")
                        break
                    if m["type"] == "nav":
                        print(f"   Q: {q} → 명령 {m}")
                        break
                    if m["type"] in ("highlight", "quiet", "toast"):
                        print(f"   ({m['type']}) {m.get('part_ids') or m.get('text') or ''}")
                        if m["type"] == "quiet":
                            break
            if fast_dwell and step == "S03":
                time.sleep(4.5)
                print("   ...오래 머묾...")
            s.entered -= 120  # 단계 시간 흉내
        ws.send_json({"type": "finish"})
        m = ws.receive_json()
        while m["type"] != "prompt" or m.get("rule") != "finished":
            print(f"   (알림:{m.get('rule')}) {m.get('text')}")
            m = ws.receive_json()
        print("   (알림:finished)", m["text"])
        ws.send_json({"type": "final_comment", "text": "Part19 클램프 1mm 틈 맞추는 게 제일 어려웠어요. 얇은 카드 끼워서 맞추면 쉬워요."})
        print("  ", ws.receive_json()["text"])


Q1 = {
    "S01": ["커넥터 방향 어느 쪽이야?"],
    "S03": ["Part19 클램프 틈 얼마나 띄워?", "슬라이드 보여줘"],
    "S05": ["볼트 길이 몇 mm야?", "볼트 어떻게 조여?"],
    "S08": ["카본 튜브 자른 면 어떻게 해?"],
    "S10": ["M5는 어디에 넣어?"],
    "S12": ["링크 어떤 거 먼저 걸어?"],
    "S14": ["Part21 방향 어느 쪽이야?", "전원 넣기 전에 뭐 확인해?", "M3 볼트 토크 얼마야?"],
}
Q2 = {
    "S03": ["클램프랑 브래킷 틈 얼마야?", "다들 여기서 어떻게 했어?"],
}

run("sim1", Q1, fast_dwell=True)
print("\n===== aggregate =====")
print(json.dumps(client.post("/api/aggregate").json(), ensure_ascii=False))
run("sim2", Q2)
