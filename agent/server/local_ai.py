"""API 키 없이 쓰는 AI 기능 (이 PC에서 직접, 또는 키 없는 무료 서비스).

- 음성 인식: faster-whisper (PC에서, 처음 쓸 때 small 모델 약 500 MB 내려받음)
- 음성 합성: edge-tts (마이크로소프트 무료 음성, 인터넷 필요, 키 없음)
- 글자 인식: Windows 기본 OCR (한국어, 추가 다운로드 없음)
- 인터넷 검색: DuckDuckGo (ddgs, 키 없음)
"""
import asyncio, io, os, re, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

WHISPER_SIZE = os.environ.get("WHISPER_SIZE", "small")
# 조립 현장 단어를 미리 알려 주면 잘못 알아듣는 일이 줄어든다 (예: 브래킷 → 브레키든)
STT_HINT = os.environ.get("STT_HINT", "자비스, 다음, 이전, 다시, 처음부터, 단계, 전체 보기, 모두 펼치기, 모두 조립, 기본 배치, 마지막, 브래킷, 액추에이터, 카본 튜브, 클램프, "
                          "마운트, 하우징, 볼트, 렌치, 구동 링크, 끝단, 커넥터, XT30, M3, M4, M5")
# 한국어 신경망 음성 중 가장 자연스러운 것(현수 Multilingual). 조금 빠르게 (손이 바쁜 사람에게 짧게)
TTS_VOICE_KO = os.environ.get("EDGE_TTS_VOICE", "ko-KR-HyunsuMultilingualNeural")
TTS_RATE = os.environ.get("EDGE_TTS_RATE", "+8%")
_whisper = None


# ---------- 음성 인식 ----------
# 짧은 말(단계 명령)은 빠른 base, 긴 말(질문)은 정확한 small. CPU 코어를 전부 쓴다.
# 측정 (Core Ultra 5 125H): base 약 0.9초, small 약 2.7초 (예전 설정 small 3~3.8초)
FAST_SIZE = os.environ.get("WHISPER_FAST_SIZE", "base")
# 1.6초 이하(“다음”, “7단계” 같은 짧은 명령)만 base. 질문까지 base로 하면 헤드셋 마이크 소리에서 크게 틀린다
# (합성 음성 시험에선 비슷했지만 실제로는 '자비스, 시판, 이거 어떡해'처럼 들음)
SHORT_SEC = float(os.environ.get("STT_SHORT_SEC", "1.6"))
_models = {}
import threading as _thr  # noqa: E402
_stt_lock = _thr.Lock()   # 음성 인식은 한 번에 하나: 여러 개를 같이 돌리면 CPU를 나눠 써서 모두 느려진다 (THINKING에서 멈춘 듯 보임)


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
        sec = len(wav) / 16000
        size = FAST_SIZE if sec <= SHORT_SEC else WHISPER_SIZE
        t_in = time.time()
        # 말 앞뒤는 화면 쪽에서 이미 잘라서 보내므로 VAD 는 끔 (더 빠름)
        with _stt_lock:
            # 앞 요청에 밀려 오래 기다린 말은 헤드셋이 이미 포기했다(20초) → 돌리지 않고 넘겨서 줄이 더 밀리지 않게
            if time.time() - t_in > 12:
                print(f"[음성 인식] {time.time() - t_in:.0f}초 밀려서 건너뜀", flush=True)
                return ""
            # temperature 하나만: 기본값은 결과가 이상하면(되풀이 등) 온도를 바꿔 최대 6번 다시 받아 적어서
            # 2초짜리 잡음에 30초가 걸렸다 ('1, 2, 3, … 20'). 글자 수도 말 길이에 맞게 묶는다.
            segs, _ = _model(size).transcribe(wav, language="ko", beam_size=1, vad_filter=False, initial_prompt=STT_HINT,
                                              without_timestamps=True, condition_on_previous_text=False,
                                              temperature=0.0, max_new_tokens=int(20 + 15 * sec))
            text = " ".join(s.text.strip() for s in segs).strip()
        # 잡음을 'M5, M5, M5, …'·'전체 보기, 전체 보기, …'처럼 되풀이해 받아 적은 건 말이 아니다 (명령으로 실행되지 않게 버림)
        words = re.findall(r"\w+", text)
        if words and max(words.count(w) for w in set(words)) >= 4 and max(words.count(w) for w in set(words)) / len(words) >= 0.4:
            print(f"[음성 인식] 되풀이라 버림 → {text[:60]}", flush=True)
            return ""
        return text
    finally:
        os.unlink(p)


def _level(path):
    """소리 크기 맞추기. 헤드셋 마이크는 쓴 사람 입 쪽 소리만 크게 잡고 옆에서 설명하는 사람 말은 40분의 1쯤으로 작게 잡는다.
    그대로 두면 무음 거르기(VAD)가 설명을 통째로 잘라 버려서, 0.5초마다 작은 말은 키운다 (최대 60배, 완전한 무음은 그대로)."""
    import numpy as np
    from faster_whisper.audio import decode_audio
    w = decode_audio(str(path))
    win, out = 8000, np.zeros_like(w)
    for i in range(0, len(w), win):
        seg = w[i:i + win]
        r = float(np.sqrt((seg ** 2).mean())) + 1e-6
        g = min(0.05 / r, 60.0) if r > 0.00005 else 1.0
        out[i:i + win] = np.clip(seg * g, -1, 1)
    return out


def _sentences(segs) -> list[dict]:
    """Whisper 조각 → 문장마다 시작 시간. 쉬지 않고 말하면 몇십 초를 한 덩어리로 주므로 단어 시각으로 문장 끝(. ? !)에서 나눈다."""
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


def _looping(sents) -> bool:
    """같은 말 되풀이(힌트 단어 '하우징, 하우징, 하우징' 같은 환각)"""
    import re
    for x in sents:
        words = re.findall(r"\w+", x["text"])
        if len(words) >= 3 and max(words.count(w) for w in set(words)) / len(words) >= 0.5:
            return True
    texts = [x["text"] for x in sents]
    return len(texts) >= 3 and len(set(texts)) <= len(texts) / 2


def stt_segments(path, hint: str | None = None) -> list[dict]:
    """긴 설명 녹음 → 문장마다 시작 시간 (그래야 문장마다 그때 보던 슬라이드가 붙는다).
    작은 소리를 키운 녹음은 Whisper가 실행할 때마다 다르게, 가끔은 힌트 단어만 되풀이하는 엉터리로 듣는다.
    그래서 두 번 듣는다: 늘 같은 결과가 나오는 설정(힌트 없음·온도 0)과 부품 이름 힌트를 준 설정.
    힌트 쪽이 되풀이 없이 비슷한 양 이상을 들었으면 그걸(부품 이름이 더 정확), 아니면 고정 설정 쪽을 쓴다.
    남은 글자 오류는 합칠 때 LLM이 문맥으로 고친다."""
    w, m = _level(path), _model()
    base = _sentences(m.transcribe(w, language="ko", vad_filter=True, word_timestamps=True,
                                   condition_on_previous_text=False, temperature=0.0, beam_size=5)[0])
    hint = _sentences(m.transcribe(w, language="ko", vad_filter=True, word_timestamps=True, initial_prompt=hint or STT_HINT)[0])
    size = lambda xs: sum(len(x["text"]) for x in xs)
    pick = hint if hint and not _looping(hint) and size(hint) >= 0.8 * size(base) else base
    print(f"[전사] 고정 {len(base)}문장 {size(base)}자 · 힌트 {len(hint)}문장 {size(hint)}자 → {'힌트' if pick is hint else '고정'}", flush=True)
    return pick


# ---------- 음성 합성 ----------
# 기본은 Supertonic(노트북에서 도는 신경망 음성, 한국어 자연스러움). 안 되면 Edge 신경망 음성.
TTS_ENGINE = os.environ.get("TTS_ENGINE", "supertonic")
SUPERTONIC_VOICE = os.environ.get("SUPERTONIC_VOICE", "F1")     # F1~F5, M1~M5
SUPERTONIC_SPEED = float(os.environ.get("SUPERTONIC_SPEED", "1.1"))
SUPERTONIC_STEPS = int(os.environ.get("SUPERTONIC_STEPS", "5"))
# ONNX 스레드: 기본(논리 코어 18개 전부)은 성능·효율 코어가 섞여 서로 기다려 느리다. 6개면 한 문장 2.65초 → 1.3초
SUPERTONIC_THREADS = int(os.environ.get("SUPERTONIC_THREADS", "6"))   # 품질 단계 (기본 8). 5도 또렷하고 20% 빠름, 4 이하는 단어가 뭉개짐
_st = {}
import threading as _th  # noqa: E402
_st_lock = _th.Lock()


def _supertonic():
    if "tts" not in _st:
        from supertonic import TTS
        t = TTS(intra_op_num_threads=SUPERTONIC_THREADS)
        _st.update(tts=t, style=t.get_voice_style(SUPERTONIC_VOICE))
    return _st["tts"], _st["style"]


# 대화형 AI 음성 (키가 있으면 먼저): Gemini TTS → OpenAI gpt-4o-mini-tts. 랩 프록시에는 음성 모델이 없어서 각자 키가 필요하다.
# 무료 Gemini 키는 한 문장에 5~27초, 모델마다 1분 3번이라 대화에 못 쓴다 → TTS_USE_GEMINI=1 일 때만 (결제를 켜 빨라지면)
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "") if os.environ.get("TTS_USE_GEMINI") == "1" else ""
# 무료 키는 모델마다 1분 3번 → 한도가 찬 모델은 잠깐 쉬고 다음 모델로 (세 모델이면 1분 9번)
GEMINI_TTS_MODELS = [m for m in os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts,gemini-3.1-flash-tts-preview").split(",") if m]
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "Kore")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_VOICE = os.environ.get("OPENAI_VOICE", "marin")
# 말투 지시는 영어로 "Say ...:" 꼴로 앞에 붙여야 지시문을 소리 내어 읽지 않는다 (한국어로 쓰면 그대로 읽었다)
VOICE_STYLE = os.environ.get("VOICE_STYLE", "Say in a warm, calm, natural Korean assistant voice, a little brisk")
_cooldown = {}                          # 모델 → 다시 써도 되는 시각
TTS_CACHE = ROOT / "data/tts_cache"     # 같은 말은 한 번만 만든다 (단계 주의 안내처럼 되풀이되는 말)


def _wav(pcm: bytes, rate: int = 24000) -> bytes:
    """헤더 없는 16비트 모노 PCM → WAV"""
    import struct
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _gemini_tts(text: str) -> tuple[bytes, str]:
    import base64, httpx, time
    last = None
    for model in GEMINI_TTS_MODELS:
        if _cooldown.get(model, 0) > time.time():
            continue
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       headers={"x-goog-api-key": GEMINI_KEY}, timeout=float(os.environ.get("GEMINI_TTS_TIMEOUT", "8")),
                       json={"contents": [{"parts": [{"text": f"{VOICE_STYLE}: {text}"}]}],
                             "generationConfig": {"responseModalities": ["AUDIO"],
                                                  "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}}}})
        if r.status_code in (404, 429, 503):   # 없는 모델·한도 참·바쁨 → 다음 모델 (한도는 1분 쉬기)
            if r.status_code != 404:
                _cooldown[model] = time.time() + (60 if r.status_code == 429 else 10)
            last = f"{model} {r.status_code}"
            continue
        r.raise_for_status()
        part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
        data = base64.b64decode(part["data"])
        if data[:4] == b"RIFF":
            return data, "audio/wav"
        m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
        return _wav(data, int(m.group(1)) if m else 24000), "audio/wav"
    raise RuntimeError(f"Gemini 음성을 지금 못 씀 ({last})")


def _openai_tts(text: str) -> tuple[bytes, str]:
    import httpx
    r = httpx.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {OPENAI_KEY}"}, timeout=30,
                   json={"model": OPENAI_TTS_MODEL, "voice": OPENAI_VOICE, "input": text, "instructions": VOICE_STYLE,
                         "response_format": "mp3"})
    r.raise_for_status()
    return r.content, "audio/mpeg"


def tts_engine() -> str:
    return "gemini" if GEMINI_KEY else "openai" if OPENAI_KEY else TTS_ENGINE


def tts_audio(text: str) -> tuple[bytes, str]:
    """글 → (소리 바이트, 형식). 키가 있으면 Gemini/GPT 음성(만든 것은 저장해 두고 다시 씀),
    없거나 실패하면 Supertonic, 그다음 Edge. Supertonic은 숫자를 한글로 바꿔 읽힌다 ('4번'을 '세 번'으로 읽는 문제)."""
    import hashlib
    for name, fn, on, voice in (("Gemini", _gemini_tts, bool(GEMINI_KEY), GEMINI_VOICE), ("OpenAI", _openai_tts, bool(OPENAI_KEY), OPENAI_VOICE)):
        if not on:
            continue
        key = hashlib.sha1(f"{name}|{voice}|{VOICE_STYLE}|{text}".encode("utf-8")).hexdigest()
        for ext, mime in ((".wav", "audio/wav"), (".mp3", "audio/mpeg")):
            f = TTS_CACHE / (key + ext)
            if f.exists():
                return f.read_bytes(), mime
        try:
            audio, mime = fn(text)
            TTS_CACHE.mkdir(parents=True, exist_ok=True)
            (TTS_CACHE / (key + (".wav" if mime == "audio/wav" else ".mp3"))).write_bytes(audio)
            return audio, mime
        except Exception as e:
            print(f"{name} 음성 실패, 다음 음성으로:", str(e)[:200], flush=True)
    if TTS_ENGINE == "supertonic":
        try:
            import soundfile as sf
            from .ko_num import ko_numbers
            with _st_lock:   # 한 모델을 여러 요청이 동시에 쓰지 않게
                t, style = _supertonic()
                wav, _ = t.synthesize(ko_numbers(text), voice_style=style, lang="ko", speed=SUPERTONIC_SPEED, total_steps=SUPERTONIC_STEPS)
            buf = io.BytesIO()
            sf.write(buf, wav.squeeze(), t.sample_rate, format="WAV", subtype="PCM_16")
            return buf.getvalue(), "audio/wav"
        except Exception as e:
            print("supertonic 실패, Edge 음성으로:", e, flush=True)
    return tts(text), "audio/mpeg"


def warm_tts():
    """서버 켤 때 모델을 미리 올려 둔다 (첫 말이 늦지 않게)"""
    print("음성:", tts_engine(), flush=True)
    if TTS_ENGINE == "supertonic":
        try:
            tts_audio("준비됐어요.")
        except Exception:
            pass


def tts(text: str) -> bytes:
    """Edge 신경망 음성 (인터넷 필요)"""
    import edge_tts

    async def run():
        buf = io.BytesIO()
        async for ch in edge_tts.Communicate(text, TTS_VOICE_KO, rate=TTS_RATE).stream():
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
