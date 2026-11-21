"""설명 듣기 결과 한 건(data/briefings/<id>/)을 지식으로 만든다.

들어오는 것 (meta.json)
  {"kind": "capture" | "doc", "frames": [{"file": "f_001.jpg", "t": 12.3}], "audio": "audio.webm" | null, "doc": "deck.pptx" | null}
순서
  1. 슬라이드: 문서면 read_doc, 사진이면 비전으로 읽고 같은 슬라이드를 연달아 찍은 건 하나로 합친다
  2. 설계자 말: 녹음이 있으면 음성 인식(사진 시각으로 슬라이드 연결), 없고 문서 노트가 있으면 노트를 말로 쓴다
  3. 기존 지식 파일을 data/kb/history/에 백업하고 새 slides_read.json·speech.json을 쓴 뒤 build_kb
상태는 status.json에 단계별로 남긴다.
"""
import json, shutil, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Job:
    def __init__(self, d: Path):
        self.d = d
        self.status = {"id": d.name, "stage": "queued", "ok": None, "log": [], "slides": 0, "segments": 0}
        self.save()

    def save(self):
        # 화면이 읽는 도중 반쯤 쓴 파일을 보지 않게 임시 파일에 쓰고 바꿔치기
        # (Windows는 다른 쪽이 읽는 중이면 바꿔치기가 막히므로 잠깐 기다렸다 다시)
        tmp = self.d / "status.tmp"
        tmp.write_text(json.dumps(self.status, ensure_ascii=False, indent=2), encoding="utf-8")
        for _ in range(20):
            try:
                tmp.replace(self.d / "status.json")
                return
            except PermissionError:
                time.sleep(0.05)
        (self.d / "status.json").write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")

    def step(self, stage, msg):
        self.status["stage"] = stage
        self.status["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        self.save()


def _same(a, b):
    """연달아 찍힌 두 사진이 같은 슬라이드인가 (비전 결과 비교)"""
    if not a.get("title") or a.get("title") != b.get("title"):
        return False
    return (a.get("step_label") == b.get("step_label")) and len(a.get("instructions") or []) == len(b.get("instructions") or [])


def run(d: Path):
    from ingest import read_slides, read_docs, transcribe, build_kb
    job = Job(d)
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        slides, notes, flips = [], [], []
        # 1) 슬라이드
        if meta.get("doc"):
            job.step("slides", f"문서 읽는 중: {meta['doc']}")
            slides, notes = read_docs.read_doc(d / meta["doc"], d / "pages")
        if meta.get("frames"):
            job.step("slides", f"사진 {len(meta['frames'])}장 비전으로 읽는 중")
            read = read_slides.read_images([d / f["file"] for f in meta["frames"]])
            merged = []
            for f, s in zip(meta["frames"], read):
                if not s.get("is_slide", True):
                    continue
                if merged and _same(merged[-1]["s"], s):
                    merged[-1]["dup"] += 1
                    continue
                merged.append({"s": s, "t": f["t"], "dup": 0})
            if not meta.get("doc"):
                slides = [m["s"] for m in merged]
            flips = [{"t": m["t"], "slide": i} for i, m in enumerate(merged, 1)]
            job.step("slides", f"슬라이드 {len(merged)}장으로 정리 (겹친 사진 {sum(m['dup'] for m in merged)}장 합침)")
        if not slides:
            raise RuntimeError("읽은 슬라이드가 없습니다")
        for n, s in enumerate(slides, 1):
            s["slide_no"] = n
            s.setdefault("image", None)
        job.status["slides"] = len(slides)

        # 2) 설계자 말
        segs = []
        if meta.get("audio"):
            job.step("speech", "녹음 글자로 바꾸는 중")
            segs = transcribe.transcribe_file(d / meta["audio"], flips)
        elif any(notes):
            job.step("speech", "녹음이 없어 발표자 노트를 설계자 말로 사용")
            t = 0
            for n, note in enumerate(notes, 1):
                for sent in [x.strip() for x in note.replace("\n", " ").split(". ") if x.strip()]:
                    segs.append({"t": t, "slide_hint": n, "text": sent if sent.endswith((".", "요", "다")) else sent + "."})
                    t += 6
        job.status["segments"] = len(segs)

        # 3) 백업 후 지식 만들기
        job.step("build", "기존 지식 백업 후 새 지식 만드는 중")
        hist = ROOT / "data/kb/history" / time.strftime("%Y%m%d-%H%M%S")
        hist.mkdir(parents=True, exist_ok=True)
        for f in [ROOT / "data/kb/slides_read.json", ROOT / "data/source/speech.json", ROOT / "data/kb/kb.json"]:
            if f.exists():
                shutil.copy2(f, hist / f.name)
        reader = "doc" if meta.get("doc") else "vision"
        (ROOT / "data/kb/slides_read.json").write_text(json.dumps(
            {"source": f"briefing {d.name}", "reader": f"{reader}:{d.name}", "slides": slides}, ensure_ascii=False, indent=2), encoding="utf-8")
        if segs or not (ROOT / "data/source/speech.json").exists():
            (ROOT / "data/source/speech.json").write_text(json.dumps(
                {"note": f"briefing {d.name}", "speaker": "설계자", "segments": segs}, ensure_ascii=False, indent=2), encoding="utf-8")
        else:   # 녹음 없이 슬라이드만 새로 읽음: 설계자 말은 그대로 두고, 슬라이드 번호가 안 맞으면 내용으로 다시 맞춘다
            sp_file = ROOT / "data/source/speech.json"
            sp = json.loads(sp_file.read_text(encoding="utf-8"))
            old_n = max([g.get("slide_hint", 0) for g in sp.get("segments", [])] or [0])
            if old_n != len(slides):
                for g in sp.get("segments", []):
                    g.pop("slide_hint", None)
                sp_file.write_text(json.dumps(sp, ensure_ascii=False, indent=2), encoding="utf-8")
            job.status["segments"] = len(sp.get("segments", []))
            job.step("speech", f"설계자 말 {len(sp.get('segments', []))}문장은 그대로 유지")
        build_kb.main()
        job.status["ok"] = True
        job.step("done", f"완료: 슬라이드 {len(slides)}장, 설계자 말 {job.status['segments']}문장")
    except Exception as e:
        job.status["ok"] = False
        job.step("error", f"실패: {e}")
        (d / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    return job.status
