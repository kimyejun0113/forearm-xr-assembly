"""PPT(pptx)·PDF 파일을 슬라이드 데이터로 바꾼다. 카메라보다 정확하고 API 키 없이도 된다.

read_doc(path, img_dir) → (slides, notes)
- slides: read_slides.py와 같은 형식
- notes: 슬라이드별 발표자 노트 (녹음이 없을 때 설계자 말 대신 쓴다)
글자 → 구조(제목·순서·주의) 정리는 키가 있으면 LLM, 없으면 규칙으로 한다.
"""
import re, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import llm  # noqa: E402

STEP_LABEL = re.compile(r"STEP\s*\d", re.I)
INSTR = re.compile(r"^\d{2}[.)]?\s+\S")          # "04 M3 …"
WARN_NUM = re.compile(r"^\d{2}(?:·\d{2})*\s+·\s+")  # "04·05 · …"
WARN_HEAD = re.compile(r"^(주의|주의사항|경고|확인|전원 넣기 전 확인|CAUTION|WARNING)\b", re.I)
BULLET = re.compile(r"^[•·\-–▪▸]\s*")

STRUCT_SYSTEM = """너는 로봇 조립 설명 슬라이드의 글자를 구조로 정리한다. 없는 내용은 만들지 않는다.
번호 목록 앞 두 자리 번호와 주의 항목 앞 번호는 지우지 않는다.
JSON으로만: {"kind":"overview|step|other","step_label":null 또는 문자열,"title":"","text":[],"table":[],"instructions":[],"warnings_title":null,"warnings":[],"figure":null,"footer":null}"""


def structure(lines, title=None):
    """글자 줄 목록 → 슬라이드 구조"""
    lines = [l.strip() for l in lines if l and l.strip()]
    if llm.available():
        try:
            r = llm.chat_json(STRUCT_SYSTEM, "제목: " + (title or "") + "\n" + "\n".join(lines))
            r.setdefault("title", title)
            return r
        except Exception as e:  # 네트워크 실패 → 규칙
            print("structure llm error:", e)
    s = {"kind": "other", "step_label": None, "title": title, "text": [], "table": [],
         "instructions": [], "warnings_title": None, "warnings": [], "figure": None, "footer": None}
    in_warn = False
    for l in lines:
        if l == title:
            continue
        if STEP_LABEL.search(l) and not s["step_label"] and len(l) < 60:
            s["step_label"] = l; s["kind"] = "step"; continue
        if WARN_HEAD.match(l):
            s["warnings_title"] = l; in_warn = True; continue
        if WARN_NUM.match(l) or (in_warn and (BULLET.match(l) or not INSTR.match(l))):
            s["warnings"].append(BULLET.sub("", l)); continue
        if INSTR.match(l):
            s["instructions"].append(l); in_warn = False; continue
        if re.fullmatch(r"\d+\s*/\s*\d+", l) or "·" in l and len(l) < 30 and not s["text"] and s["title"] is None:
            s["footer"] = l; continue
        if s["title"] is None:
            s["title"] = l; continue
        s["text"].append(l)
    if s["instructions"]:
        s["kind"] = "step"
    elif s["kind"] == "other" and len(s["text"]) >= 1:
        s["kind"] = "overview"
    return s


def read_pptx(path):
    from pptx import Presentation
    prs = Presentation(str(path))
    slides, notes = [], []
    for slide in prs.slides:
        title, lines = None, []
        for sh in slide.shapes:
            if sh.has_text_frame:
                for p in sh.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs).strip()
                    if t:
                        lines.append(t)
                if sh == getattr(slide.shapes, "title", None) and sh.text_frame.text.strip():
                    title = sh.text_frame.text.strip()
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    lines.append(" · ".join(c.text.strip() for c in row.cells))
        slides.append(structure(lines, title))
        notes.append(slide.notes_slide.notes_text_frame.text.strip() if slide.has_notes_slide else "")
    return slides, notes


def read_pdf(path, img_dir):
    import pymupdf
    doc = pymupdf.open(str(path))
    img_dir.mkdir(parents=True, exist_ok=True)
    slides = []
    for i, page in enumerate(doc, 1):
        lines = page.get_text("text").splitlines()
        s = structure(lines)
        img = img_dir / f"page_{i:02d}.png"
        page.get_pixmap(dpi=96).save(str(img))
        s["image"] = str(img.resolve().relative_to(ROOT)).replace("\\", "/")
        slides.append(s)
    return slides, ["" for _ in slides]


def read_doc(path, img_dir):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pptx":
        return read_pptx(path)
    if ext == ".pdf":
        return read_pdf(path, img_dir)
    raise ValueError(f"지원하지 않는 형식: {ext} (pptx, pdf, 사진만)")


if __name__ == "__main__":
    sl, nt = read_doc(sys.argv[1], ROOT / "data/briefings/_cli")
    print(json.dumps({"slides": sl, "notes": nt}, ensure_ascii=False, indent=2)[:3000])
