"""안경 카메라로 찍은 슬라이드 사진을 비전 AI로 읽어 슬라이드 데이터로 바꾼다.

함수: read_images(paths) → [slide dict]  (서버의 설명 듣기 모드가 부른다)
명령: python ingest/read_slides.py [캡처 폴더=data/captures]  → data/kb/slides_read.json
API 키가 없으면 RuntimeError (명령으로 돌리면 기존 파일을 그대로 둔다).
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import llm  # noqa: E402

SYSTEM = """너는 로봇 조립 설명 슬라이드를 안경 카메라 사진으로 읽는다.
사진은 기울어지거나 흐릴 수 있고, 슬라이드가 아닌 장면(사람, 벽)일 수도 있다. 보이는 글자만 정확히 옮기고 추측한 내용은 넣지 않는다.
노트북·모니터·TV 화면 안에 슬라이드가 보이면 화면 바깥(책상, 손, 물건)은 무시하고 그 슬라이드를 읽는다 (is_slide true). 글자가 작아 못 읽는 줄은 빼고 읽히는 것만 옮긴다.
번호 목록 앞의 두 자리 번호(예: 04)와 주의 항목 앞 번호(예: "04·05 ·")는 지우지 말고 그대로 둔다.
JSON으로만 답한다:
{"is_slide": true 또는 false,
 "kind": "overview|step|other",
 "step_label": "STEP 01–05 같은 위쪽 작은 글씨, 없으면 null",
 "title": "큰 제목",
 "text": ["본문 문장"],
 "table": [["머리행"], ["행"]] 또는 [],
 "instructions": ["번호 목록 항목을 순서대로"],
 "warnings_title": "주의 상자 제목 또는 null",
 "warnings": ["주의 상자 항목"],
 "figure": "그림이 무엇을 보여주는지 한두 문장 (색으로 강조된 부품 포함)",
 "highlighted_parts": ["그림에서 색으로 강조된 부품 이름"],
 "footer": "아래쪽 작은 글씨"}"""


def read_images(paths):
    if not llm.can("vision"):  # 비전 서버가 없으면 Windows OCR로 글자만 읽는다 (그림 설명은 빈칸)
        from server import local_ai
        out = []
        for p in paths:
            s = local_ai.read_slide(p)
            s["image"] = str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/")
            out.append(s)
        return out
    slides = []
    for p in paths:
        p = Path(p)
        res = llm.chat_json(SYSTEM, [{"type": "text", "text": "이 사진의 슬라이드를 읽어 줘"}, llm.image_part(p)],
                            model=llm.VISION_MODEL, temperature=0)
        res["image"] = str(p.resolve().relative_to(ROOT)).replace("\\", "/")
        slides.append(res)
    return slides


def main():
    cap = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/captures"
    if not llm.available():
        print("LLM_API_KEY 없음: 비전 읽기를 건너뜁니다 (기존 data/kb/slides_read.json 사용).")
        return
    paths = sorted(p for p in cap.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    slides = [s for s in read_images(paths) if s.get("is_slide", True)]
    for n, s in enumerate(slides, 1):
        s["slide_no"] = n
        print("read", n, s.get("title"))
    out = {"source": str(cap), "reader": f"vision:{llm.VISION_MODEL}", "slides": slides}
    (ROOT / "data/kb/slides_read.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
