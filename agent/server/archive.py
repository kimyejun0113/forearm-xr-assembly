"""기록 보관함: "기록 보여줘"라고 하면 뜨는 창의 내용.

단계마다 세 칸으로 나눠 보여 준다.
- 설계자: 슬라이드 주의사항 + 설계자가 말로 강조한 문장 (시간 포함)
- 이전 작업자: 자주 물은 질문과 답, 마지막 한마디(팁), 어려움 점수
- 표시한 장면: 작업자가 ★ 또는 "잘 봐 둬"로 남긴 장면 (사진·영상이 있으면 경로)
"""
import json
from pathlib import Path
from .knowledge import Knowledge

ROOT = Path(__file__).resolve().parents[1]


def bookmarks():
    out = []
    for f in sorted((ROOT / "data/sessions").glob("*/*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e["type"] == "bookmark":
                out.append({"step_id": e.get("step_id"), "builder": e["builder"], "t": e["t"],
                            "note": e.get("note", ""), "media": e.get("media")})
    return out


def build(kn: Knowledge, step_id: str | None = None):
    marks = bookmarks()
    steps = []
    for sid in kn.order:
        if step_id and sid != step_id:
            continue
        card = kn.steps[sid]
        speech = [{"text": c["text"], "source": c["source"]} for c in kn.chunks
                  if c["kind"] == "speech" and (c.get("step_id") == sid or sid in c.get("also", []))]
        st = kn.step_stats(sid)
        steps.append({
            "step_id": sid, "order": card["order"], "name": card["name"],
            "designer": {"warnings": card["warnings"], "speech": speech, "slides": card["slides"]},
            "workers": {"n": st.get("n_builders", 0), "median_sec": st.get("median_sec"),
                        "difficulty": st.get("difficulty"), "faq": st.get("faq", []),
                        "tips": [t["text"] for t in st.get("tips", [])]},
            "marks": [m for m in marks if m["step_id"] == sid],
        })
    return {"type": "archive", "focus": step_id, "steps": steps}
