"""OpenAI 호환 API 클라이언트 (해커톤에서 받는 키를 .env에 넣으면 켜진다).

키가 없으면 available() == False 이고, 나머지 코드는 규칙·검색만으로 동작한다.
"""
import base64, json, os
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def _load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
BASE = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
VISION_MODEL = os.environ.get("VISION_MODEL", MODEL)
STT_MODEL = os.environ.get("STT_MODEL", "whisper-1")
TTS_MODEL = os.environ.get("TTS_MODEL", "tts-1")
TTS_VOICE = os.environ.get("TTS_VOICE", "alloy")
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "30"))


# 이 서버로 보낼 기능 (나머지는 local_ai로). 예: 로컬 Ollama는 글 생성만 → LLM_FEATURES=chat
FEATURES = {f.strip() for f in os.environ.get("LLM_FEATURES", "chat,vision,stt,tts").split(",") if f.strip()}


def available() -> bool:
    """글 생성(답변 정리)을 이 서버로 할 수 있나"""
    return bool(KEY) and "chat" in FEATURES


def can(feature: str) -> bool:
    """chat / vision / stt / tts 중 이 서버로 할 수 있는 것"""
    return bool(KEY) and feature in FEATURES


def _headers():
    return {"Authorization": f"Bearer {KEY}"}


def chat_json(system: str, user: str | list, model: str | None = None, temperature: float = 0.2) -> dict:
    """JSON 객체로 답을 받는다. user는 문자열 또는 멀티모달 content 리스트."""
    body = {
        "model": model or MODEL,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    r = httpx.post(f"{BASE}/chat/completions", headers=_headers(), json=body, timeout=TIMEOUT)
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    return json.loads(txt)


def image_part(path: Path) -> dict:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def transcribe(audio: bytes, filename: str = "audio.webm", with_segments: bool = False) -> dict:
    from .local_ai import STT_HINT          # 부품 이름 힌트 (잘못 알아듣는 일 줄이기)
    data = {"model": STT_MODEL, "language": "ko", "prompt": STT_HINT}
    if with_segments:
        data["response_format"] = "verbose_json"
    r = httpx.post(f"{BASE}/audio/transcriptions", headers=_headers(), data=data,
                   files={"file": (filename, audio)}, timeout=TIMEOUT * 4)
    r.raise_for_status()
    return r.json()


def speak(text: str) -> bytes:
    r = httpx.post(f"{BASE}/audio/speech", headers=_headers(), timeout=TIMEOUT,
                   json={"model": TTS_MODEL, "voice": TTS_VOICE, "input": text, "response_format": "mp3"})
    r.raise_for_status()
    return r.content
