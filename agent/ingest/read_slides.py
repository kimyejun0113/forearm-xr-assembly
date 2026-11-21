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
 "clarity": "high(본문 글자까지 또렷) | mid(제목은 읽히고 본문 일부만) | low(제목도 겨우)",
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
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        p = Path(p)
        res = llm.chat_json(SYSTEM, [{"type": "text", "text": "이 사진의 슬라이드를 읽어 줘"}, llm.image_part(p)],
                            model=llm.VISION_MODEL, temperature=0)
        res["image"] = str(p.resolve().relative_to(ROOT)).replace("\\", "/")
        return res
    with ThreadPoolExecutor(max_workers=6) as ex:          # 헤드셋은 2~3초마다 찍으므로 사진이 많다 → 동시에 읽기
        return list(ex.map(one, paths))


TITLE_SYSTEM = ('사진 속 노트북·모니터 화면에 보이는 슬라이드의 제목만 JSON으로 답한다: {"title": "...", "is_slide": true|false}. '
                '보이는 글자 그대로 옮기고, 제목이 안 보이거나 슬라이드가 아니면 title은 "".')


def _screen_crop(p):
    """사진에서 밝은 화면 부분만 잘라 낸다 (못 찾으면 사진 전체). 또렷함 점수도 함께."""
    import numpy as np
    from PIL import Image, ImageFilter, ImageStat
    im = Image.open(p).convert("RGB")
    g = np.asarray(im.convert("L").resize((320, 240)), dtype=np.float32) > 170
    rows, cols = np.where(g.mean(1) > 0.25)[0], np.where(g.mean(0) > 0.25)[0]
    if len(rows) >= 40 and len(cols) >= 40:
        sx, sy = im.width / 320, im.height / 240
        im = im.crop((int(cols[0] * sx), int(rows[0] * sy), int((cols[-1] + 1) * sx), int((rows[-1] + 1) * sy)))
    e = im.convert("L")
    e = e.resize((640, max(1, round(640 * e.height / e.width))))
    return im, ImageStat.Stat(e.filter(ImageFilter.FIND_EDGES)).var[0]


def pick_frames(paths):
    """헤드셋은 같은 슬라이드를 여러 장 찍는다. 화면 부분을 작게(낮은 해상도) 보내 제목만 읽고(사진당 약 280토큰),
    제목이 같은 사진끼리 묶어 가장 또렷한 사진만 고른다. 전부 읽으면 사진당 약 2,300토큰.
    돌려주는 것: 묶음마다 {"best": 사진 번호, "first": 처음 본 사진 번호, "rest": 나머지(또렷한 순)}. 못 읽으면 None."""
    import base64, io
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        im, sharp = _screen_crop(p)
        im.thumbnail((512, 512))
        b = io.BytesIO(); im.save(b, "JPEG", quality=85)
        part = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode(), "detail": "low"}}
        try:
            r = llm.chat_json(TITLE_SYSTEM, [part], model=llm.VISION_MODEL, temperature=0)
        except Exception:
            r = {}
        return (r.get("title") or "").strip() if r.get("is_slide", True) else "", sharp
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(one, paths))
    key = lambda t: "".join(ch for ch in t if ch.isalnum())
    groups = {}
    for i, (title, sharp) in enumerate(res):
        if key(title):
            groups.setdefault(key(title), []).append((sharp, i))
    if not groups:
        return None
    out = []
    for g in groups.values():
        order = [i for _, i in sorted(g, reverse=True)]
        out.append({"best": order[0], "first": min(i for _, i in g), "rest": order[1:]})
    return sorted(out, key=lambda x: x["first"])


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
