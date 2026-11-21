"""슬라이드 HTML을 이미지로 렌더링하고, 안경 카메라로 찍은 것처럼 변형한다.

사용: python ingest/render_slides.py <deck 폴더(project/ 를 담은 곳)> <이미지 폴더>
- <deck>/project/deck.css, <deck>/project/slides/*.html, <deck>/project/deck.json 을 읽는다.
- /_blob/<id> 이미지는 <이미지 폴더>/<id>.png 로 바꿔 끼운다.
결과: data/source/slides_html/<id>.html, data/captures/<n>_<id>.png (카메라 느낌), data/captures/clean/<n>_<id>.png
"""
import sys, json, re, subprocess, shutil
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance

ROOT = Path(__file__).resolve().parents[1]
CHROME = next(p for p in [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
] if Path(p).exists())

FONT = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@500&display=swap">'


def build_html(deck: Path, img_dir: Path, sid: str) -> str:
    css = (deck / "project/deck.css").read_text(encoding="utf-8")
    sec = (deck / f"project/slides/{sid}.html").read_text(encoding="utf-8")
    sec = re.sub(r"<aside>.*?</aside>", "", sec, flags=re.S)  # 발표자 노트는 화면에 안 보인다
    sec = re.sub(r'/_blob/([0-9a-f]{32})', lambda m: (img_dir / f"{m.group(1)}.png").as_uri(), sec)
    return f"""<!doctype html><html><head><meta charset="utf-8">{FONT}
<style>html,body{{margin:0;width:1920px;height:1080px;overflow:hidden}}
section{{position:relative;width:1920px;height:1080px}}
{css}</style></head><body>{sec}</body></html>"""


def camera_look(src: Path, dst: Path, n: int):
    """안경 카메라 느낌: 약간 기울어진 원근, 살짝 흐림, 밝기 차이, 1280px."""
    im = Image.open(src).convert("RGB")
    w, h = im.size
    k = 0.035 + 0.01 * (n % 3)
    quad = (int(w * k), int(h * k * 0.6), int(w * (k * 0.4)), int(h * (1 - k)),
            int(w * (1 - k * 0.5)), int(h * (1 - k * 0.8)), int(w * (1 - k)), int(h * k))
    im = im.transform((w, h), Image.QUAD, quad, Image.BICUBIC)
    im = ImageEnhance.Brightness(im).enhance(0.92 + 0.03 * (n % 2))
    im = im.filter(ImageFilter.GaussianBlur(0.8)).resize((1280, 720), Image.LANCZOS)
    im.save(dst, quality=85)


def main():
    deck, img_dir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    order = json.loads((deck / "project/deck.json").read_text(encoding="utf-8"))["order"]
    out_html = ROOT / "data/source/slides_html"
    cap = ROOT / "data/captures"; (cap / "clean").mkdir(parents=True, exist_ok=True)
    for n, sid in enumerate(order, 1):
        f = out_html / f"{sid}.html"
        f.write_text(build_html(deck, img_dir, sid), encoding="utf-8")
        clean = cap / "clean" / f"{n:02d}_{sid}.png"
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--virtual-time-budget=4000", "--window-size=1920,1080",
                        f"--screenshot={clean}", f.as_uri()], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        camera_look(clean, cap / f"{n:02d}_{sid}.jpg", n)
        print("captured", n, sid)


if __name__ == "__main__":
    main()
