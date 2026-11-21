"""설명 듣기 모드 업로드·처리 시험 (API 키 없이).

1. PPT(pptx) 업로드 → 글자 규칙으로 읽어서 지식이 만들어지는지
2. 안경 사진만 업로드 → 키가 없으면 '키 필요'로 멈추고 기존 지식은 그대로인지
끝나면 원래 지식 파일로 되돌린다.
"""
import sys, json, time, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402
from server import main  # noqa: E402

KEEP = [ROOT / "data/kb/slides_read.json", ROOT / "data/source/speech.json", ROOT / "data/kb/kb.json"]
saved = {f: f.read_bytes() for f in KEEP}
client = TestClient(main.app)


def make_pptx(path):
    """지금 슬라이드 내용으로 실제 pptx 하나를 만든다 (설계자가 준 파일 대역)"""
    sr = json.loads((ROOT / "data/kb/slides_read.json").read_text(encoding="utf-8"))
    sp = json.loads((ROOT / "data/source/speech.json").read_text(encoding="utf-8"))["segments"]
    prs = Presentation()
    for s in sr["slides"]:
        sl = prs.slides.add_slide(prs.slide_layouts[5])
        sl.shapes.title.text = s["title"]
        lines = ([s["step_label"]] if s.get("step_label") else []) + s.get("text", []) + s.get("instructions", [])
        if s.get("warnings"):
            lines += [s.get("warnings_title") or "주의"] + s["warnings"]
        tb = sl.shapes.add_textbox(Inches(0.5), Inches(1.5), Inches(9), Inches(5)).text_frame
        for i, l in enumerate(lines):
            p = tb.paragraphs[0] if i == 0 else tb.add_paragraph()
            p.text = l; p.runs[0].font.size = Pt(14)
        sl.notes_slide.notes_text_frame.text = " ".join(g["text"] for g in sp if g["slide_hint"] == s["slide_no"])
    prs.save(path)


def wait(bid):
    for _ in range(60):
        s = client.get(f"/api/briefing/{bid}").json()
        if s.get("ok") is not None:
            return s
        time.sleep(0.5)
    return s


try:
    p = ROOT / "data/briefings/_test_deck.pptx"
    make_pptx(p)
    r = client.post("/api/briefing", files={"doc": ("forearm.pptx", p.read_bytes())}).json()
    s = wait(r["id"])
    print("[PPTX]", s["stage"], s["ok"], "| slides", s["slides"], "| speech", s["segments"])
    print("   ", "\n    ".join(s["log"]))
    kb = json.loads((ROOT / "data/kb/kb.json").read_text(encoding="utf-8"))
    for c in kb["steps"][:5] + kb["steps"][9:10]:
        print(f"    {c['step_id']} instr={c['instructions']} warn={len(c['warnings'])}")
    print("    지식 다시 불러옴:", main.kn.kb.get("reader"))

    imgs = sorted((ROOT / "data/captures").glob("*.jpg"))[:2]
    files = [("frames", (i.name, i.read_bytes(), "image/jpeg")) for i in imgs]
    before = (ROOT / "data/kb/kb.json").read_bytes()
    r = client.post("/api/briefing", files=files, data={"frame_times": "[1.5, 30.2]"}).json()
    s = wait(r["id"])
    print("[PHOTOS]", s["stage"], s["ok"], "|", s["log"][-1])
    print("    기존 지식 그대로:", before == (ROOT / "data/kb/kb.json").read_bytes())
finally:
    for f, b in saved.items():
        f.write_bytes(b)
    main.kn.reload()
    p.unlink(missing_ok=True)
    print("원래 지식으로 복구:", main.kn.kb.get("reader"))
