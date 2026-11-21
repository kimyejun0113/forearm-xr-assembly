"""키 없는 AI 기능 시험: 음성 합성 → 그 소리를 음성 인식 → OCR → 검색"""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import local_ai as L  # noqa: E402

t = time.time(); mp3 = L.tts("커넥터 방향 어느 쪽이야?")
print(f"[합성] {len(mp3)} bytes, {time.time()-t:.1f}s")
t = time.time(); txt = L.stt(mp3, "q.mp3")
print(f"[인식] '{txt}' {time.time()-t:.1f}s (첫 실행은 모델 내려받기 포함)")
t = time.time(); txt = L.stt(mp3, "q.mp3")
print(f"[인식 2회째] '{txt}' {time.time()-t:.1f}s")
cap = sorted((ROOT / "data/captures").glob("*.jpg"))[1]
t = time.time(); s = L.read_slide(cap)
print(f"[OCR] {cap.name} {time.time()-t:.1f}s\n  제목: {s.get('title')}\n  라벨: {s.get('step_label')}\n  순서: {s.get('instructions')[:3]}\n  주의: {s.get('warnings')[:2]}")
t = time.time(); r = L.web_search("M3 볼트 권장 조임 토크")
print(f"[검색] {len(r)}건 {time.time()-t:.1f}s"); [print("  -", x["title"][:50], x["url"][:60]) for x in r]
