"""설계자 설명 녹음 → 문장마다 시간이 붙은 글자.

함수: transcribe_file(path, flips) → [{"t", "text", "slide_hint"?}]
  flips: [{"t": 초, "slide": 번호}, ...] 슬라이드가 넘어간 시각 (설명 듣기 모드가 자동으로 만든다)
명령: python ingest/transcribe.py <녹음 파일> [넘김기록.json]  → data/source/speech.json
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import llm  # noqa: E402


def transcribe_file(path, flips=None, hint=None):
    path = Path(path)
    if llm.can("stt"):
        res = llm.transcribe(path.read_bytes(), path.name, with_segments=True)
    else:  # 키가 없으면 이 PC의 Whisper
        from server import local_ai
        res = {"segments": local_ai.stt_segments(path, hint)}   # hint: 로봇마다 부품 이름이 다르다
    segs = []
    for s in res.get("segments", []) or [{"start": 0, "text": res.get("text", "")}]:
        seg = {"t": round(float(s["start"]), 1), "text": s["text"].strip()}
        if flips:
            cur = [f["slide"] for f in flips if f["t"] <= s["start"] + 0.5]
            seg["slide_hint"] = cur[-1] if cur else flips[0]["slide"]
        if seg["text"]:
            segs.append(seg)
    return segs


def main():
    audio = Path(sys.argv[1])
    flips = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")) if len(sys.argv) > 2 else []
    if not llm.available():
        print("LLM_API_KEY 없음: 녹음을 글자로 바꿀 수 없습니다.")
        return
    segs = transcribe_file(audio, flips)
    out = {"note": f"transcribed from {audio.name}", "speaker": "설계자", "segments": segs}
    (ROOT / "data/source/speech.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("segments:", len(segs))


if __name__ == "__main__":
    main()
