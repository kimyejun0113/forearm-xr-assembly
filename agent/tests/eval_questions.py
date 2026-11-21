"""답변 품질 점검: 조립 중 나올 질문을 서버(웹소켓)에 그대로 묻고, 답에 들어가야 할 내용이 있는지 본다.

사용: (서버를 켠 상태에서) python tests/eval_questions.py [general|senior|all]
- general: 단계 카드·부품·볼트·공구 등 일반 질문 25개
- senior : 선배 말(발표자 노트·녹음)에만 있는 이유·결과·요령 질문 14개
           → data/source/briefing_forearm.pptx 를 설명으로 넣은 뒤에 의미가 있다
기준은 정규식 하나라 대충의 자동 채점이다. 결과 줄을 사람이 함께 읽고 판단한다.
"""
import json, re, sys, time
import httpx, websocket

B = "http://localhost:8000"

GENERAL = [  # (단계, 질문, 답에 있어야 할 것)
    ("S01", "이거 어디에 끼우는 거야?", r"액추에이터"),
    ("S01", "액추에이터 커넥터는 어느 방향으로 둬?", r"바깥"),
    ("S01", "액추에이터 1이랑 2 어떻게 구분해?", r"허브|이두|손목|1"),
    ("S02", "클램프는 어디에 끼워?", r"Part19|클램프"),
    ("S03", "브래킷 먼저 놓아야 돼 액추에이터 먼저 놓아야 돼?", r"브래킷|Part18"),
    ("S03", "브래킷이랑 클램프 사이 간격은?", r"1\s*(mm|밀리)"),
    ("S03", "왜 띄워야 돼?", r"비비|마찰|소리|힘"),
    ("S04", "볼트 몇 개 필요해?", r"6"),
    ("S04", "10mm 볼트랑 12.5mm 볼트 어디에 써?", r"위"),
    ("S04", "어떤 렌치 써야 돼?", r"2\.5"),
    ("S04", "볼트 어떤 순서로 조여?", r"대각선"),
    ("S05", "위 하우징 볼트는 몇 mm짜리야?", r"12\.5"),
    ("S06", "액추에이터 2는 어디에 올려?", r"바깥|통째"),
    ("S08", "카본 튜브가 안 들어가는데 어떻게 해?", r"돌리|사포"),
    ("S08", "튜브 어느 쪽으로 끼워?", r"길이|돌리|밀어"),
    ("S10", "M5 볼트는 어디에 넣어?", r"큰"),
    ("S10", "M4 볼트 조임 토크 얼마야?", r"없|확인|모르"),
    ("S11", "메인 튜브랑 두 번째 튜브 차이가 뭐야?", r"손목|이두"),
    ("S13", "구동 링크 2는 어디랑 연결해?", r"핀"),
    ("S14", "핀은 어떻게 끼워?", r"휘|펴|핀"),
    ("S14", "전원 처음 켤 때 주의할 거 있어?", r"극성"),
    ("S14", "XT30 극성 어떻게 확인해?", r"극성"),
    ("S09", "Part21이 뭐야?", r"끝단"),
    ("S12", "끝단은 링크보다 먼저 달아?", r"먼저|걸"),
    ("S09", "다음 단계 뭐야?", r"10|마운트 ↔|볼트"),
    ("S09", "지금 몇 단계야?", r"9"),
]
SENIOR = [  # 선배 말에만 있는 이유·결과·요령
    ("S01", "커넥터 안쪽으로 놓으면 어떻게 돼?", r"풀"),
    ("S03", "브래킷이 안 들어가는데 세게 눌러도 돼?", r"깨|손상"),
    ("S03", "브래킷 제대로 들어갔는지 어떻게 알아?", r"딸깍"),
    ("S04", "볼트 세게 조여도 돼?", r"뭉개|나사산"),
    ("S04", "처음 조일 때 뭐 주의해?", r"빼|길을|길 내"),
    ("S04", "왜 대각선으로 조여야 돼?", r"기울"),
    ("S08", "카본 튜브가 안 들어가", r"돌리"),
    ("S08", "사포로 얼마나 갈아?", r"한 바퀴"),
    ("S10", "볼트 뭐부터 걸어?", r"M5"),
    ("S10", "M5는 어떤 렌치야?", r"4\s*(mm|밀리)"),
    ("S14", "링크 1 먼저 걸면 안 돼?", r"안 들어|다시 풀"),
    ("S14", "전류 제한 몇 암페어로 해?", r"1\s*(A|암페어)"),
    ("S14", "왜 전류 제한 전원 써야 돼?", r"태"),
    ("S14", "다 끝났는데 비비는 소리가 나", r"1\s*(mm|밀리)"),
]


def run(name, tests):
    s = httpx.post(B + "/api/session", json={"builder": f"eval_{name}"}).json()
    ws = websocket.create_connection(f"ws://localhost:8000/ws/{s['session_id']}", timeout=90)

    def drain(sec=1.2):  # 단계에 들어갈 때 오는 카드·알림은 버림
        ws.settimeout(sec)
        try:
            while True:
                ws.recv()
        except Exception:
            pass
        ws.settimeout(90)

    ok, total_t, cur = 0, 0.0, None
    for step, q, key in tests:
        if step != cur:
            ws.send(json.dumps({"type": "step", "step_id": step, "by": "ui"})); drain(); cur = step
        t0 = time.time(); ws.send(json.dumps({"type": "ask", "text": q}))
        while True:
            m = json.loads(ws.recv())
            if m.get("type") == "answer":
                break
        dt = time.time() - t0; total_t += dt
        txt, srcs = m.get("text") or "", m.get("sources") or []
        hit = bool(re.search(key, txt))
        if name == "senior":
            hit = hit and any("설계자 설명" in x for x in srcs)
        ok += hit
        print(f"{'OK ' if hit else 'X  '} [{step}] {q} ({dt:.1f}s)\n     → {txt.replace(chr(10), ' / ')}\n     출처 {srcs}")
    ws.close()
    print(f"== {name}: {ok}/{len(tests)} · 평균 {total_t / len(tests):.1f}s\n")
    return ok, len(tests)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("llm:", httpx.get(B + "/api/kb").json()["llm"])
    if which in ("general", "all"):
        run("general", GENERAL)
    if which in ("senior", "all"):
        run("senior", SENIOR)
