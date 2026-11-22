"""에이전트 서버. 실행: .venv\\Scripts\\python -m uvicorn server.main:app --host 0.0.0.0 --port 8000

3D 뷰어(팀원 모듈)와의 약속 (웹소켓 /ws/{session}):
  3D → 서버: {"type":"step", "step_id":"S2", "by":"voice|ui"}
  서버 → 3D: {"type":"highlight", "part_ids":[...]}, {"type":"nav", "cmd":"next|prev|replay|first"} 또는 {"type":"nav","to":2}
"""
import re
import asyncio, json, os
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Body
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import llm, aggregate, archive, local_ai
from .knowledge import Knowledge, robots, ROBOT
from .agent import Agent, STUCK
from .session import Session

ROOT = Path(__file__).resolve().parents[1]
PRIOR_HINTS = os.environ.get("PRIOR_HINTS", "0") == "1"
# 로봇마다 지식·에이전트 하나씩 (포어암 + data/kb/robots/*.json). 작업 세션이 어느 로봇인지 기억한다.
kns = {r: Knowledge(r) for r in robots()}
agents = {r: Agent(k) for r, k in kns.items()}
kn = kns.get(ROBOT, kns["forearm"])       # 로봇을 말하지 않은 요청의 기본


def pick(robot: str | None) -> Knowledge:
    return kns.get(robot or "", kn)


def reload_all():
    for k in kns.values():
        k.reload()


def readers():
    """aggregate.json 에 남길 '이 기록이 쌓일 때의 설명 자료 버전' (로봇마다)"""
    return {r: k.kb.get("reader") for r, k in kns.items()}


sessions: dict[str, Session] = {}
app = FastAPI(title="robot assembly agent")
app.mount("/client", StaticFiles(directory=ROOT / "client"), name="client")
app.mount("/data/captures", StaticFiles(directory=ROOT / "data/captures"), name="captures")
(ROOT / "data/briefings").mkdir(parents=True, exist_ok=True)
app.mount("/data/briefings", StaticFiles(directory=ROOT / "data/briefings"), name="briefings")
# 음성 인식을 이 PC 에서 하면 모델을 미리 불러 둔다 (첫 질문이 10초 넘게 걸리지 않게)
if not llm.can("stt"):
    import threading
    threading.Thread(target=lambda: (local_ai.warm(), print("음성 인식 모델 준비 완료")), daemon=True).start()
# 3D/XR 화면 (저장소 루트의 web/): Quest 에서 http://localhost:8000/xr/xr.html
if (ROOT.parent / "web").is_dir():
    app.mount("/xr", StaticFiles(directory=ROOT.parent / "web", html=True), name="xr")

from . import briefing_api  # noqa: E402
app.include_router(briefing_api.router)
briefing_api.on_done(reload_all)   # 설명을 새로 넣으면 지식을 다시 불러온다 (포어암·안내서 로봇 모두 녹화를 받는다)


def warm_spoken():
    """단계 주의 알림의 '들을 말'(LLM이 짧게 다시 쓴 것)을 미리 만들어 둔다 → 단계에 들어갈 때 안 기다리게"""
    for k in kns.values():
        for st in k.steps.values():
            f = st.get("flags") or {}
            if not isinstance(f, dict):
                continue
            for pre, key in (("안전 주의예요. ", "danger"), ("이 단계는 되돌리기 어려워요. ", "irreversible")):
                for x in (f.get(key) or [])[:1]:
                    try:
                        llm.spoken(pre + x, 50)
                    except Exception:
                        pass


import threading  # noqa: E402
threading.Thread(target=warm_spoken, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(ROOT / "client/index.html")


@app.get("/api/kb")
def get_kb(robot: str | None = None):
    """3D 팀원도 쓰는 단계·부품 목록 (?robot=leg_L 처럼 로봇을 고를 수 있다)"""
    k = pick(robot)
    return {"project": k.kb["project"], "robot": k.robot, "steps": [k.steps[s] for s in k.order], "parts": k.kb["parts"],
            "llm": llm.available()}


@app.get("/api/robots")
def get_robots():
    """처음 화면에서 고를 수 있는 로봇 (기본 로봇이 맨 앞)"""
    order = [kn.robot] + [r for r in kns if r != kn.robot]
    return [{"robot": r, "project": kns[r].kb["project"], "steps": len(kns[r].order)} for r in order]


@app.get("/api/archive")
def get_archive(step_id: str | None = None, robot: str | None = None):
    """기록 보관함 내용 (단계별: 설계자 / 이전 작업자 / 표시한 장면)"""
    return archive.build(pick(robot), step_id)


@app.post("/api/session")
def new_session(body: dict = Body(...)):
    s = Session(pick(body.get("robot")), (body.get("builder") or "worker").strip()[:40])
    sessions[s.id] = s
    resume = s.resume_summary()
    return {"session_id": s.id, "resume": resume}


@app.post("/api/stt")
async def stt(audio: UploadFile = File(...)):
    data, name = await audio.read(), audio.filename or "a.webm"
    try:
        if llm.can("stt"):
            res = await asyncio.to_thread(llm.transcribe, data, name)
            return {"text": res.get("text", "").strip(), "engine": "api"}
        return {"text": await asyncio.to_thread(local_ai.stt, data, name), "engine": "local"}
    except Exception as e:
        return JSONResponse({"error": f"음성 인식 실패: {e}"}, status_code=500)


@app.get("/api/tts")
async def tts(text: str):
    try:
        if llm.can("tts"):
            return Response(await asyncio.to_thread(llm.speak, text), media_type="audio/mpeg")
        return Response(await asyncio.to_thread(local_ai.tts, text), media_type="audio/mpeg")
    except Exception as e:  # 인터넷이 없으면 화면 쪽 speechSynthesis로 대신 읽는다
        print("tts error:", e)
        return Response(status_code=204)


@app.post("/api/aggregate")
def run_aggregate():
    out = aggregate.run(readers())
    reload_all()
    return {"steps": {k: {kk: v[kk] for kk in ("n_builders", "median_sec", "difficulty")} for k, v in out["steps"].items()}}


def refresh_knowledge():
    aggregate.run(readers())
    reload_all()


def comment_steps(text: str, cur: str | None, kn: Knowledge = kn) -> list[str]:
    """끝나고 남긴 한마디가 어느 단계 얘기인지: 말한 단계 번호 > 부품 이름(가장 구체적인 것) > 글 검색."""
    by_order = {st["order"]: sid for sid, st in kn.steps.items()}
    nums = [by_order[int(n)] for n in re.findall(r"(\d{1,2})\s*(?:번|단계)", text) if int(n) in by_order]
    if nums:
        return list(dict.fromkeys(nums))
    t, best = text.lower(), {}
    for pid, p in kn.kb["parts"].items():
        L = max((len(x) for x in [pid, *p["aliases"]] if x.lower() in t), default=0)
        if L:
            best[pid] = L
    if best:
        top = max(best.values())
        pids = [pid for pid, L in best.items() if L == top]            # "메인 카본 튜브"가 "카본 튜브"보다 우선
        place = [sid for sid in kn.order if any(kn.steps[sid]["parts"] == [pid] for pid in pids)]   # 그 부품을 놓는 단계
        anyst = [sid for sid in kn.order if any(pid in kn.steps[sid]["parts"] for pid in pids)]
        if place or anyst:
            return place or anyst
    hits = kn.search(text, None)
    return [hits[0][1].get("step_id") if hits and hits[0][1].get("step_id") else cur or kn.order[-1]]


def card_payload(s: Session):
    kn = s.kn
    c = kn.steps.get(s.step_id)
    if not c:
        return None
    st = kn.step_stats(s.step_id)
    # 이전 작업자 힌트를 단계 시작 때 조용히 띄울지 (3절 A안): 아직 미정이라 .env PRIOR_HINTS=1 일 때만
    hints = [f"이전 작업자 {q['count']}명이 물어본 것: {q['question']}" for q in st.get("faq", [])[:2]] if PRIOR_HINTS else []
    return {"type": "step_card", "step": c, "hints": hints, "index": kn.order.index(s.step_id) + 1, "total": len(kn.order)}


@app.websocket("/ws/{sid}")
async def ws(websocket: WebSocket, sid: str):
    await websocket.accept()
    s = sessions.get(sid)
    if not s:
        await websocket.send_json({"type": "error", "text": "세션이 없습니다"}); await websocket.close(); return
    kn, agent = s.kn, agents[s.kn.robot]       # 이 작업자가 조립하는 로봇

    async def send_all(items):
        for it in items:
            await websocket.send_json(it)

    async def ticker():
        while True:
            await asyncio.sleep(2)
            await send_all(await asyncio.to_thread(s.tick))

    task = asyncio.create_task(ticker())
    try:
        while True:
            m = await websocket.receive_json()
            t = m.get("type")
            if t == "step":
                if m.get("step_id") in kn.steps and m["step_id"] != s.step_id:
                    prompts = await asyncio.to_thread(s.enter_step, m["step_id"], m.get("by", "3d"))   # 알림 말을 줄이느라 LLM을 부를 수 있어 따로
                    await websocket.send_json(card_payload(s))
                    await send_all(prompts)
            elif t == "ask":
                text = (m.get("text") or "").strip()
                if not text:
                    continue
                r = agent.route(text)
                if r["type"] == "nav":
                    s.log("nav", text=text, **{k: v for k, v in r.items() if k != "type"})
                    await websocket.send_json(r)      # 3D(또는 임시 화면)가 단계를 바꾸고 step 신호를 보낸다
                    continue
                if r["type"] == "archive":
                    s.log("archive_open", text=text)
                    payload = archive.build(kn)
                    payload["focus"] = s.step_id
                    await websocket.send_json(payload)
                    continue
                if r["type"] == "archive_close":
                    await websocket.send_json({"type": "archive_close"})
                    continue
                if r["type"] == "bookmark":
                    s.log("bookmark", note=text)
                    await websocket.send_json({"type": "toast", "text": "이 장면을 표시해 뒀어요. 다음 작업자에게 전달돼요"})
                    continue
                if r["type"] == "quiet":
                    s.quiet = True; s.log("quiet", on=True)
                    await websocket.send_json({"type": "quiet", "on": True, "speech": "알겠어요. 안전 경고만 드릴게요."})
                    continue
                if STUCK.search(text) and len(text) < 12:
                    s.log("question", text=text, answer="(막힘 신호)")
                    await websocket.send_json({"type": "prompt", "rule": "stuck", "level": "info",
                                               "speech": "어떤 부분이 막히셨어요? 부품 이름이나 몇 번째 순서인지 말해 주세요.",
                                               "text": "어떤 부분이 막히셨어요?"})
                    continue
                extra = s.on_question(text)
                ans = await asyncio.to_thread(agent.answer, text, s.step_id)
                s.log("question", text=text, answer=ans["speech"], intent=ans["intent"],
                      sources=ans["sources"], confident=ans["confident"])
                for v in ans["visual"]:
                    if v["type"] == "highlight" and v["part_ids"]:
                        await websocket.send_json({"type": "highlight", "part_ids": v["part_ids"]})
                await websocket.send_json({"type": "answer", "question": text, **ans})
                await send_all(extra)
            elif t == "speaking":
                s.speaking = bool(m.get("on"))
            elif t == "quiet":
                s.quiet = bool(m.get("on")); s.log("quiet", on=s.quiet)
            elif t == "bookmark":
                s.log("bookmark", note=m.get("note", ""))
                await websocket.send_json({"type": "toast", "text": "이 장면을 표시해 뒀어요"})
            elif t == "final_comment":
                about = comment_steps(m.get("text", ""), s.step_id, kn)
                s.log("final_comment", text=m.get("text", ""), about_step=about[0], about_steps=about)
                await asyncio.to_thread(refresh_knowledge)
                await websocket.send_json({"type": "toast", "text": "다음 작업자에게 전달할게요. 고맙습니다!"})
            elif t == "finish":
                s.end(completed=True)
                await asyncio.to_thread(refresh_knowledge)   # 끝난 작업자 기록을 바로 다음 사람 지식으로
                await websocket.send_json({"type": "prompt", "rule": "finished", "level": "info",
                                           "speech": "조립 끝! 혹시 막혔던 점이 있으면 한마디 남겨 주세요. 다음 작업자에게 전달돼요.",
                                           "text": "막혔던 점 한마디 남기기", "ask_comment": True})
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
