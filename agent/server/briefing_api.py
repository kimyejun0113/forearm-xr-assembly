"""설명 듣기 모드 업로드·처리 API.

POST /api/briefing  (multipart)
   frames: 슬라이드 사진들 (안경 카메라가 자동으로 고른 것)
   frame_times: JSON 배열, 사진마다 녹화 시작 후 초
   audio: 설계자 말 녹음 (선택)
   doc: PPT(pptx)·PDF 파일 (선택, 사진보다 정확)
   → {"id": ...}  처리는 뒤에서 돌고, 끝나면 지식을 다시 불러온다
GET  /api/briefing/{id}  → 진행 상태 (stage, ok, log)
GET  /api/briefings      → 지금까지 올린 것 목록
"""
import json, threading, time, uuid
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException

ROOT = Path(__file__).resolve().parents[1]
BDIR = ROOT / "data/briefings"
router = APIRouter()
_on_done = []          # 처리가 끝나면 부를 함수 (지식 다시 불러오기)
_lock = threading.Lock()  # 한 번에 하나씩 처리 (지식 파일을 같이 쓰므로)


def on_done(fn):
    _on_done.append(fn)


def _work(d: Path):
    from ingest import pipeline
    with _lock:
        st = pipeline.run(d)
    if st.get("ok"):
        for fn in _on_done:
            fn()


@router.post("/api/briefing")
async def upload(frames: List[UploadFile] = File(default=[]), frame_times: str = Form(default="[]"),
                 audio: Optional[UploadFile] = File(default=None), doc: Optional[UploadFile] = File(default=None),
                 robot: str = Form(default="forearm")):
    from .knowledge import robots
    if robot not in robots():
        raise HTTPException(400, f"모르는 로봇이에요: {robot}")
    has_audio = audio is not None and bool(audio.filename)
    if robot != "forearm" and not has_audio:   # 안내서로 만든 로봇은 슬라이드가 없어 녹음으로만 배운다
        raise HTTPException(400, f"{robot} 은 녹음으로 배워요. 녹음이 없어요")
    if not frames and not doc and not has_audio:
        raise HTTPException(400, "슬라이드 사진이나 PPT/PDF 파일이 하나는 있어야 합니다")
    bid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = BDIR / bid
    d.mkdir(parents=True)
    times = json.loads(frame_times or "[]")
    meta = {"kind": "doc" if doc else "capture", "frames": [], "audio": None, "doc": None, "robot": robot}
    for i, f in enumerate(frames, 1):
        name = f"f_{i:03d}{Path(f.filename or 'x.jpg').suffix or '.jpg'}"
        (d / name).write_bytes(await f.read())
        meta["frames"].append({"file": name, "t": float(times[i - 1]) if i - 1 < len(times) else 0.0})
    if audio is not None and audio.filename:
        name = "audio" + (Path(audio.filename).suffix or ".webm")
        (d / name).write_bytes(await audio.read())
        meta["audio"] = name
    if doc is not None and doc.filename:
        ext = Path(doc.filename).suffix.lower()
        if ext in (".jpg", ".jpeg", ".png"):  # 사진 한 장을 문서 칸에 올린 경우 → 사진으로 처리
            name = f"f_{len(meta['frames']) + 1:03d}{ext}"
            (d / name).write_bytes(await doc.read())
            meta["frames"].append({"file": name, "t": 0.0}); meta["kind"] = "capture"
        else:
            name = "doc" + ext
            (d / name).write_bytes(await doc.read())
            meta["doc"] = name
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    threading.Thread(target=_work, args=(d,), daemon=True).start()
    return {"id": bid, "frames": len(meta["frames"]), "audio": bool(meta["audio"]), "doc": meta["doc"]}


@router.get("/api/briefing/{bid}")
def status(bid: str):
    f = BDIR / bid / "status.json"
    if not f.exists():
        if (BDIR / bid).exists():
            return {"id": bid, "stage": "queued", "ok": None, "log": []}
        raise HTTPException(404, "없는 기록")
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, PermissionError):  # 쓰는 중 → 다음 확인 때 다시
        return {"id": bid, "stage": "busy", "ok": None, "log": []}


@router.get("/api/briefings")
def listing():
    out = []
    for d in sorted(BDIR.iterdir(), reverse=True):
        f = d / "status.json"
        if d.is_dir() and f.exists():
            s = json.loads(f.read_text(encoding="utf-8"))
            out.append({k: s.get(k) for k in ("id", "stage", "ok", "slides", "segments")})
    return out
