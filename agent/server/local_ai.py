"""API 키 없이 쓰는 AI 기능 (이 PC에서 직접, 또는 키 없는 무료 서비스).

- 음성 인식: faster-whisper (PC에서, 처음 쓸 때 small 모델 약 500 MB 내려받음)
- 음성 합성: edge-tts (마이크로소프트 무료 음성, 인터넷 필요, 키 없음)
- 글자 인식: Windows 기본 OCR (한국어, 추가 다운로드 없음)
- 인터넷 검색: DuckDuckGo (ddgs, 키 없음)
"""
import asyncio, io, os, re, tempfile
from pathlib import Path

WHISPER_SIZE = os.environ.get("WHISPER_SIZE", "small")
# 조립 현장 단어를 미리 알려 주면 잘못 알아듣는 일이 줄어든다 (예: 브래킷 → 브레키든)
STT_HINT = os.environ.get("STT_HINT", "자비스, 다음, 이전, 다시, 처음부터, 단계, 전체 보기, 모두 펼치기, 모두 조립, 기본 배치, 마지막, 브래킷, 액추에이터, 카본 튜브, 클램프, "
                          "마운트, 하우징, 볼트, 렌치, 구동 링크, 끝단, 커넥터, XT30, M3, M4, M5")
TTS_VOICE_KO = os.environ.get("EDGE_TTS_VOICE", "ko-KR-SunHiNeural")
_whisper = None


# ---------- 음성 인식 ----------
# 짧은 말(단계 명령)은 빠른 base, 긴 말(질문)은 정확한 small. CPU 코어를 전부 쓴다.
# 측정 (Core Ultra 5 125H): base 약 0.9초, small 약 2.7초 (예전 설정 small 3~3.8초)
FAST_SIZE = os.environ.get("WHISPER_FAST_SIZE", "base")
SHORT_SEC = float(os.environ.get("STT_SHORT_SEC", "1.6"))
_models = {}


def _model(size: str | None = None):
    size = size or WHISPER_SIZE
    if size not in _models:
        from faster_whisper import WhisperModel
        _models[size] = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    return _models[size]


def warm():
    """서버 시작 때 미리 불러 두기 (첫 질문이 느리지 않게)"""
    import numpy as np
    for size in {FAST_SIZE, WHISPER_SIZE}:
        list(_model(size).transcribe(np.zeros(16000, dtype="float32"), language="ko", beam_size=1)[0])


def stt(audio: bytes, filename: str = "q.webm") -> str:
    """짧은 질문 녹음 → 글자"""
    from faster_whisper.audio import decode_audio
    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".webm", delete=False) as f:
        f.write(audio); p = f.name
    try:
        wav = decode_audio(p)                                  # 16 kHz
        size = FAST_SIZE if len(wav) / 16000 <= SHORT_SEC else WHISPER_SIZE
        # 말 앞뒤는 화면 쪽에서 이미 잘라서 보내므로 VAD 는 끔 (더 빠름)
        segs, _ = _model(size).transcribe(wav, language="ko", beam_size=1, vad_filter=False, initial_prompt=STT_HINT,
                                          without_timestamps=True, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()
    finally:
        os.unlink(p)


def stt_segments(path) -> list[dict]:
    """긴 설명 녹음 → 문장마다 시작 시간.
    쉬지 않고 말하면 Whisper가 몇십 초를 한 덩어리로 주므로, 단어 시각을 받아 문장 끝(. ? !)에서 나눈다
    (그래야 문장마다 그때 보던 슬라이드가 붙는다)."""
    segs, _ = _model().transcribe(str(path), language="ko", vad_filter=True, initial_prompt=STT_HINT, word_timestamps=True)
    out = []
    for s in segs:
        cur, t0 = "", None
        for w in (s.words or []):
            if t0 is None:
                t0 = w.start
            cur += w.word
            if w.word.strip().endswith((".", "?", "!", "。")):
                if cur.strip():
                    out.append({"start": round(float(t0), 1), "text": cur.strip()})
                cur, t0 = "", None
        if cur.strip():
            out.append({"start": round(float(t0 if t0 is not None else s.start), 1), "text": cur.strip()})
        elif not s.words and s.text.strip():
            out.append({"start": round(float(s.start), 1), "text": s.text.strip()})
    return out


# ---------- 음성 합성 ----------
def tts(text: str) -> bytes:
    import edge_tts

    async def run():
        buf = io.BytesIO()
        async for ch in edge_tts.Communicate(text, TTS_VOICE_KO).stream():
            if ch["type"] == "audio":
                buf.write(ch["data"])
        return buf.getvalue()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run())
    # 이미 이벤트 루프 안이면 (서버) 새 스레드 루프에서
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(run())).result()


# ---------- 글자 인식 (Windows OCR) ----------
def ocr_lines(path) -> list[dict]:
    """사진 → 줄 목록 [{text, x, y, h}] (위에서 아래 순서)"""
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.storage import StorageFile, FileAccessMode
    from winrt.windows.graphics.imaging import BitmapDecoder

    async def run():
        f = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
        stream = await f.open_async(FileAccessMode.READ)
        bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
        res = await OcrEngine.try_create_from_language(Language("ko")).recognize_async(bmp)
        out = []
        for ln in res.lines:
            ws = list(ln.words)
            r = [w.bounding_rect for w in ws]
            out.append({"text": ln.text, "x": min(b.x for b in r), "y": min(b.y for b in r),
                        "h": max(b.height for b in r)})
        return sorted(out, key=lambda l: (round(l["y"] / 8), l["x"]))
    return asyncio.run(run())


def read_slide(path) -> dict:
    """OCR 줄 → 슬라이드 데이터 (vision 읽기와 같은 모양). 그림 설명은 못 한다."""
    raw = [l for l in ocr_lines(path) if l["text"].strip()]
    # 번호("01")가 따로 한 줄로 잡히면 같은 높이의 오른쪽 줄 앞에 붙인다
    nums = [l for l in raw if re.fullmatch(r"\d{1,2}", l["text"].strip())]
    lines = [l for l in raw if l not in nums]
    for n in nums:
        cand = [l for l in lines if abs(l["y"] - n["y"]) < 14 and l["x"] > n["x"]]
        if cand:
            t = min(cand, key=lambda l: l["x"] - n["x"])
            t["text"] = n["text"].strip() + " " + t["text"]
    if not lines:
        return {"is_slide": False}
    bottom = max(l["y"] for l in lines)
    feet = [l for l in lines if l["y"] >= bottom - 20 and bottom > 400]  # 맨 아래 줄 = 쪽 번호·꼬리말
    lines = [l for l in lines if l not in feet]
    big = max(lines, key=lambda l: l["h"])
    label = next((l["text"] for l in lines if re.match(r"^\s*STEP", l["text"], re.I)), None)
    instr, warns, text, in_warn = [], [], [], False
    foot = " · ".join(l["text"] for l in sorted(feet, key=lambda l: l["x"])) or None
    for l in lines:
        t = l["text"].strip()
        if l is big or t == label:
            continue
        if re.match(r"^(주의|전원 넣기 전)", t):
            in_warn = True; continue
        if re.search(r"\d+\s*/\s*\d+$", t) and l["y"] > big["y"] + 300:
            foot = t; continue
        if in_warn:
            warns.append(re.sub(r"^[·•\-]\s*", "", t).strip())
        elif re.match(r"^\d{1,2}[.\s]", t):
            instr.append(t)
        else:
            text.append(t)
    return {"is_slide": True, "kind": "step" if label else "overview", "step_label": label, "title": big["text"].strip(),
            "text": text, "table": [], "instructions": instr, "warnings": warns, "figure": "",
            "highlighted_parts": [], "footer": foot, "reader": "windows-ocr"}


# ---------- 인터넷 검색 ----------
def web_search(query: str, n: int = 3) -> list[dict]:
    from ddgs import DDGS
    rs = []
    for attempt in range(2):  # 무료 검색은 가끔 빈 결과를 줘서 한 번 더 시도
        try:
            rs = DDGS().text(query, region="kr-kr", max_results=n)
            if rs:
                break
        except Exception as e:  # 막히거나 인터넷이 없으면 빈 결과
            print("search error:", e)
    if not rs:
        return []
    return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in rs]
