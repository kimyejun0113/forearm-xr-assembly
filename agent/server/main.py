"""에이전트 서버. 실행: .venv\\Scripts\\python -m uvicorn server.main:app --host 0.0.0.0 --port 8000

3D 뷰어(팀원 모듈)와의 약속 (웹소켓 /ws/{session}):
  3D → 서버: {"type":"step", "step_id":"S2", "by":"voice|ui"}
  서버 → 3D: {"type":"highlight", "part_ids":[...]}, {"type":"nav", "cmd":"next|prev|replay|first"} 또는 {"type":"nav","to":2}
"""
import asyncio, json, os
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Body
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import llm, aggregate, archive, local_ai
from .knowledge import Knowledge
from .agent import Agent, STUCK
from .session import Session

ROOT = Path(__file__).resolve().parents[1]
PRIOR_HINTS = os.environ.get("PRIOR_HINTS", "0") == "1"
kn = Knowledge()
agent = Agent(kn)
sessions: dict[str, Session] = {}
app = FastAPI(title="forearm assembly agent")
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
briefing_api.on_done(kn.reload)   # 설명을 새로 넣으면 지식을 다시 불러온다


@app.get("/")
def index():
    return FileResponse(ROOT / "client/index.html")


@app.get("/api/kb")
def get_kb():
    """3D 팀원도 쓰는 단계·부품 목록"""
    return {"project": kn.kb["project"], "steps": [kn.steps[s] for s in kn.order], "parts": kn.kb["parts"],
            "llm": llm.available()}


@app.get("/api/archive")
def get_archive(step_id: str | None = None):
    """기록 보관함 내용 (단계별: 설계자 / 이전 작업자 / 표시한 장면)"""
    return archive.build(kn, step_id)


@app.post("/api/session")
def new_session(body: dict = Body(...)):
    s = Session(kn, (body.get("builder") or "worker").strip()[:40])
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
    out = aggregate.run(kn.kb.get("reader"))
    kn.reload()
    return {"steps": {k: {kk: v[kk] for kk in ("n_builders", "median_sec", "difficulty")} for k, v in out["steps"].items()}}


def refresh_knowledge():
    aggregate.run(kn.kb.get("reader"))
    kn.reload()


def card_payload(s: Session):
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

    async def send_all(items):
        for it in items:
            await websocket.send_json(it)

    async def ticker():
        while True:
            await asyncio.sleep(2)
            await send_all(s.tick())

    task = asyncio.create_task(ticker())
    try:
        while True:
            m = await websocket.receive_json()
            t = m.get("type")
            if t == "step":
                if m.get("step_id") in kn.steps and m["step_id"] != s.step_id:
                    prompts = s.enter_step(m["step_id"], m.get("by", "3d"))
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
                hits = kn.search(m.get("text", ""), None)
                step = hits[0][1].get("step_id") if hits else s.step_id
                s.log("final_comment", text=m.get("text", ""), about_step=step)
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
