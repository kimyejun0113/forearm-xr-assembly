"""작업자별 기록 → 단계별로 쌓인 지식 (data/kb/aggregate.json).

- 단계 시간: 작업자별 합계 → 중간값
- 자주 묻는 질문: 같은 단계의 비슷한 질문 묶음, 몇 명이 물었는지, 가장 좋았던 답
  (좋았던 답 = 그 답 뒤에 같은 질문이 다시 나오지 않은 답)
- 어려움 점수: 1인당 질문 수 + 기준 초과 + 뒤로 가기
- 팁: 조립을 마치며 남긴 한마디
"""
import json, re, statistics
from collections import defaultdict
from pathlib import Path
from .textutil import similarity

ROOT = Path(__file__).resolve().parents[1]


def run(kb_reader=None):
    sessions = []
    for f in (ROOT / "data/sessions").glob("*/*.jsonl"):
        evs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if evs:
            sessions.append(evs)
    dur = defaultdict(lambda: defaultdict(float))          # step -> builder -> sec
    qa = defaultdict(list)                                  # step -> [(builder, q, answer, repeated)]
    back = defaultdict(int); dwell = defaultdict(int); tips = []
    for evs in sessions:
        for i, e in enumerate(evs):
            s = e.get("step_id")
            if e["type"] == "step_exit" and s:
                dur[s][e["builder"]] += e.get("duration", 0)
            elif (e["type"] == "question" and s and e.get("confident", True)   # 못 찾은 답은 남기지 않는다
                  and e.get("intent") not in ("show_slide",)):                 # 명령은 질문이 아니다
                later = [x for x in evs[i + 1:] if x["type"] == "question" and x.get("step_id") == s]
                repeated = any(similarity(e["text"], x["text"]) >= 0.5 for x in later)
                ans = re.sub(r"^(설계자 설명으로는, |주의할 점이에요\. |이전 작업자.*?이에요\. )+", "", e.get("answer", ""))
                qa[s].append((e["builder"], e["text"], ans, repeated))
            elif e["type"] == "step_enter" and e.get("prev") and s:
                pass
            elif e["type"] == "nav" and e.get("cmd") == "prev" and s:
                back[s] += 1
            elif e["type"] == "prompt" and e.get("rule") == "dwell" and s:
                dwell[s] += 1
            elif e["type"] == "final_comment":
                for sid in e.get("about_steps") or [e.get("about_step") or e.get("step_id")]:   # 두 튜브처럼 여러 단계에 해당할 수 있음
                    tips.append({"text": e["text"], "step_id": sid, "builder": e["builder"]})

    out = {"kb_reader": kb_reader, "steps": {}}
    for s in set(dur) | set(qa):
        builders = set(dur[s]) | {b for b, *_ in qa[s]}
        clusters = []
        for b, q, a, rep in qa[s]:
            for c in clusters:
                if similarity(q, c["question"]) >= 0.45:
                    c["builders"].add(b)
                    if not rep and a and not c["answer"]:
                        c["answer"] = a
                    break
            else:
                clusters.append({"question": q, "answer": "" if rep else a, "builders": {b}})
        faq = sorted(({"question": c["question"], "answer": c["answer"], "count": len(c["builders"])}
                      for c in clusters if c["answer"]), key=lambda x: -x["count"])
        n = max(len(builders), 1)
        out["steps"][s] = {
            "n_builders": len(dur[s]),
            "median_sec": round(statistics.median(dur[s].values()), 1) if dur[s] else None,
            "faq": faq[:8],
            "difficulty": round(len(qa[s]) / n + dwell[s] / n + back[s] / n, 2),
            "tips": [t for t in tips if t["step_id"] == s],
        }
    (ROOT / "data/kb/aggregate.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
