"""지식 저장소: kb.json(설명 자료) + aggregate.json(이전 작업자들에게서 쌓인 것)."""
import json, os, re
from pathlib import Path
from .textutil import BM25, expand

ROOT = Path(__file__).resolve().parents[1]
# 로봇마다 지식 파일이 따로 있다: forearm = 설명 녹화로 만든 data/kb/kb.json,
# 다른 로봇 = tools/guide_to_robot.py 가 만든 data/kb/robots/<robot>.json. 서버 하나가 전부 들고 있다.
ROBOT = os.environ.get("ROBOT", "forearm")      # 로봇을 고르지 않은 화면에 줄 기본 로봇


def kb_file(robot: str) -> Path:
    return ROOT / "data/kb/kb.json" if robot == "forearm" else ROOT / f"data/kb/robots/{robot}.json"


def robots() -> list[str]:
    # <robot>.json 만 지식. <robot>.speech.json 같은 딸린 기록(설명 녹화로 배운 문장)은 로봇이 아니다
    return ["forearm"] + sorted(p.stem for p in (ROOT / "data/kb/robots").glob("*.json") if "." not in p.stem)


AGG_FILE = ROOT / "data/kb/aggregate.json"

# 질문 종류별로 어떤 자료를 더 믿을지
KIND_WEIGHT = {
    "why":      {"speech": 1.6, "warning": 1.3, "instruction": 1.0, "figure": 0.6, "prior_qa": 1.0, "tip": 1.0},
    "part":     {"part": 1.5, "table_row": 1.4, "figure": 1.2, "instruction": 1.0, "speech": 0.8},
    "orient":   {"warning": 1.5, "speech": 1.4, "instruction": 1.2, "figure": 0.8, "prior_qa": 1.3},
    "how":      {"instruction": 1.5, "warning": 1.2, "speech": 1.2, "prior_qa": 1.3, "tip": 1.2},
    "prior":    {"prior_qa": 2.0, "tip": 1.8, "pitfall": 1.8, "speech": 0.8},
    "spec":     {"table_row": 1.3, "warning": 1.2, "speech": 1.0, "instruction": 1.0},
    "default":  {},
}
NUM = re.compile(r"\d+(\.\d+)?\s?(mm|밀리|cm|°|도|N·?m|개|번)")
# 부품 설명 조각은 "이거 뭐야" 류 질문에서만 쓴다 (다른 질문에 끼어들면 답이 엉뚱해진다)
PART_ONLY_FOR = {"part"}


class Knowledge:
    def __init__(self, robot: str = ROBOT):
        self.robot = robot
        self.reload()

    def reload(self):
        self.kb = json.loads(kb_file(self.robot).read_text(encoding="utf-8"))
        self.agg = json.loads(AGG_FILE.read_text(encoding="utf-8")) if AGG_FILE.exists() else {"steps": {}}
        self.steps = {s["step_id"]: s for s in self.kb["steps"]}
        self.order = [s["step_id"] for s in sorted(self.kb["steps"], key=lambda s: s["order"])]
        self.chunks = list(self.kb["chunks"])
        # 부품 목록 조각의 "왼·오 다른/같은 부품" → 부품 id별로 (단계 카드에 붙여서 '왼쪽 오른쪽 같아?'에 바로 답하게)
        self.part_lr = {}
        for c in self.chunks:
            m = c["kind"] == "part" and re.search(r"왼·오 (다른|같은) 부품", c["text"])
            if m:
                for pid in self.kb["parts"]:
                    if re.search(rf"(?<![A-Za-z0-9_]){re.escape(pid)}(?![A-Za-z0-9_])", c["text"]):
                        self.part_lr.setdefault(pid, m.group(0))
        # 이전 작업자 지식도 같은 검색에 넣는다
        for sid, a in self.agg.get("steps", {}).items():
            if sid not in self.steps:      # 다른 로봇의 단계 기록은 섞지 않는다
                continue
            for q in a.get("faq", []):
                self.chunks.append({"id": f"qa-{sid}-{len(self.chunks)}", "kind": "prior_qa", "step_id": sid,
                                    "text": f"질문: {q['question']} → 답: {q['answer']}",
                                    "source": f"이전 작업자 {q['count']}명이 물어봄", "count": q["count"]})
            for t in a.get("tips", []):
                self.chunks.append({"id": f"tip-{sid}-{len(self.chunks)}", "kind": "tip", "step_id": sid,
                                    "text": t["text"], "source": "이전 작업자 한마디"})
        self.index = BM25([c["text"] for c in self.chunks])

    def neighbor_weight(self, chunk_step, cur_step):
        if chunk_step is None:
            return 0.6
        if cur_step is None or chunk_step == cur_step:
            return 1.0
        try:
            d = abs(self.order.index(chunk_step) - self.order.index(cur_step))
        except ValueError:
            return 0.4
        return 0.45 if d == 1 else 0.3

    def search(self, query: str, step_id: str | None, intent: str = "default", k: int = 6):
        w = KIND_WEIGHT.get(intent, {})
        sc = self.index.scores(expand(query))
        ranked = []
        for c, s in zip(self.chunks, sc):
            if s <= 0 or (c["kind"] == "part" and intent not in PART_ONLY_FOR):
                continue
            near = 1.0 if step_id and step_id in c.get("also", []) else self.neighbor_weight(c.get("step_id"), step_id)
            score = s * near * w.get(c["kind"], 1.0)
            if intent == "spec" and NUM.search(c["text"]):
                score *= 1.5  # 수치를 묻는 질문엔 숫자가 있는 근거를 먼저
            ranked.append((score, c))
        ranked.sort(key=lambda x: -x[0])
        return ranked[:k]

    def find_parts(self, text: str) -> list[str]:
        t = text.lower()
        hits = []
        for pid, p in self.kb["parts"].items():
            for a in [pid, *p["aliases"]]:
                if a.lower() in t and pid not in hits:
                    hits.append(pid)
                    break
        return hits

    def step_stats(self, step_id):
        return self.agg.get("steps", {}).get(step_id, {})
