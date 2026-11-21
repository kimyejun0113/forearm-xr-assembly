"""작업 세션: 사건 기록(작업자마다 따로, 추가만) + 예외 규칙(AI가 먼저 말하는 경우)."""
import json, re, time, uuid, statistics
from pathlib import Path
from .textutil import similarity
from .knowledge import Knowledge

ROOT = Path(__file__).resolve().parents[1]
SESS_DIR = ROOT / "data/sessions"
MAX_PROMPTS_PER_STEP = 2
RESUME_GAP_SEC = 30 * 60


class Session:
    def __init__(self, kn: Knowledge, builder: str):
        self.kn, self.builder = kn, builder
        self.id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = SESS_DIR / builder; self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / f"{self.id}.jsonl"
        self.step_id = None
        self.entered = time.time()
        self.prompts_in_step = 0
        self.asked_dwell = False
        self.tools_hint_done = False
        self.questions_in_step: list[str] = []
        self.quiet = False
        self.speaking = False
        self.pending: list[dict] = []   # 말하는 중이라 미룬 알림
        self.log("session_start", builder=builder, kb_reader=kn.kb.get("reader"))

    # ---------- 기록 ----------
    def log(self, type_, **kw):
        ev = {"t": round(time.time(), 2), "type": type_, "session": self.id, "builder": self.builder,
              "step_id": self.step_id, **kw}
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev

    # ---------- 단계 ----------
    def threshold(self, step_id):
        """오래 머묾 기준: (기본 추정 시간과 이전 작업자 중간값을 섞은 값) × 1.5"""
        card = self.kn.steps[step_id]
        st = self.kn.step_stats(step_id)
        n, med = st.get("n_builders", 0), st.get("median_sec")
        expected = card["baseline_sec"] if not med else (card["baseline_sec"] * 2 + med * n) / (2 + n)
        return expected * 1.5

    def enter_step(self, step_id, by="3d") -> list[dict]:
        out = []
        prev = self.step_id
        if prev and prev != step_id:
            dur = time.time() - self.entered
            self.log("step_exit", duration=round(dur, 1), to=step_id)
            card = self.kn.steps[prev]
            moved_forward = self._idx(step_id) > self._idx(prev)
            if moved_forward and "prereq" in card["flags"]:
                tip = re.split(r"(?<=[.요다])\s", card["flags"]["prereq"][0])[0]
                out.append(self._prompt("prereq", "warn", "넘어가기 전에 확인해 주세요. " + tip, tip))
            # 묶음(같은 슬라이드)이 끝날 때만 남은 부품 확인. 단계마다 띄우면 잔소리가 된다
            if moved_forward and card["slides"] != self.kn.steps[step_id]["slides"]:
                out.append(self._prompt("bundle_done", "info", None, f"{card['order']}단계까지 한 묶음 끝. 남은 부품·볼트가 없는지 확인하세요."))
        self.step_id, self.entered = step_id, time.time()
        self.prompts_in_step, self.asked_dwell, self.tools_hint_done = 0, False, False
        self.questions_in_step = []
        self.log("step_enter", by=by, prev=prev)
        card = self.kn.steps[step_id]
        f = card["flags"]
        if "danger" in f:
            out.append(self._prompt("danger", "danger", "안전 주의예요. " + f["danger"][0], f["danger"][0], counts=False))
        if "irreversible" in f:
            out.append(self._prompt("irreversible", "warn", "이 단계는 되돌리기 어려워요. " + f["irreversible"][0], f["irreversible"][0]))
        if "lr" in f:
            out.append(self._prompt("lr", "info", None, "왼쪽·오른쪽 구분: " + f["lr"][0]))
        if self.kn.agg.get("kb_reader") and self.kn.agg.get("kb_reader") != self.kn.kb.get("reader"):
            out.append(self._prompt("design_changed", "info", None, "설명 자료가 이전 작업자 때와 바뀌었어요. 이전 작업자 팁은 참고만 하세요."))
        return [p for p in out if p]

    def _idx(self, sid):
        return self.kn.order.index(sid) if sid in self.kn.order else -1

    # ---------- 질문 ----------
    def on_question(self, q: str) -> list[dict]:
        out = []
        if any(similarity(q, p) >= 0.5 for p in self.questions_in_step):
            out.append(self._prompt("repeat", "info", "말로 설명이 어려우면 슬라이드로 보여 드릴까요? '슬라이드 보여줘'라고 하시면 돼요.", None))
        self.questions_in_step.append(q)
        return [p for p in out if p]

    # ---------- 주기 확인 ----------
    def tick(self) -> list[dict]:
        if not self.step_id:
            return []
        out = []
        el = time.time() - self.entered
        th = self.threshold(self.step_id)
        if not self.asked_dwell and el > th:
            self.asked_dwell = True
            out.append(self._prompt("dwell", "info", "도움이 필요하세요?", None, elapsed=round(el), threshold=round(th)))
        nxt = self._idx(self.step_id) + 1
        if not self.tools_hint_done and el > th / 1.5 * 0.7 and nxt < len(self.kn.order):
            self.tools_hint_done = True
            cur_tools = set(self.kn.steps[self.step_id]["tools"])
            new = [t for t in self.kn.steps[self.kn.order[nxt]]["tools"] if t not in cur_tools]
            if new:
                out.append(self._prompt("next_tools", "info", None, "다음 단계에 필요: " + ", ".join(new)))
        if not self.speaking and self.pending:
            out += self.pending; self.pending = []
        return [p for p in out if p]

    def resume_summary(self):
        """같은 작업자의 지난 세션이 오래전에 멈췄다면 짧게 요약."""
        prev = sorted(p for p in self.dir.glob("*.jsonl") if p != self.file)
        if not prev:
            return None
        evs = [json.loads(l) for l in prev[-1].read_text(encoding="utf-8").splitlines() if l.strip()]
        if any(e["type"] == "session_end" and e.get("completed") for e in evs):
            return None
        last = evs[-1]
        if time.time() - last["t"] < RESUME_GAP_SEC or not last.get("step_id"):
            return None
        card = self.kn.steps.get(last["step_id"])
        if not card:
            return None
        return self._prompt("resume", "info", f"지난번에 {card['order']}단계 {card['name']}까지 하셨어요. 거기서 이어 갈게요.",
                            None, counts=False, step_id=card["step_id"])

    # ---------- 알림 공통 ----------
    def _prompt(self, rule, level, speech, text, counts=True, **kw):
        if self.quiet and level != "danger":
            if speech is None:  # 화면만 쓰는 조용한 알림은 허용
                pass
            else:
                self.log("prompt_suppressed", rule=rule, reason="quiet")
                return None
        if counts and level != "danger" and speech:
            if self.prompts_in_step >= MAX_PROMPTS_PER_STEP:
                self.log("prompt_suppressed", rule=rule, reason="cap")
                return None
            self.prompts_in_step += 1
        p = {"type": "prompt", "rule": rule, "level": level, "speech": speech, "text": text or speech, **kw}
        self.log("prompt", rule=rule, level=level, speech=speech, text=text)
        if self.speaking and level != "danger" and speech:
            self.pending.append(p)
            return None
        return p

    def end(self, completed: bool):
        if self.step_id:
            self.log("step_exit", duration=round(time.time() - self.entered, 1), to=None)
        self.log("session_end", completed=completed)
