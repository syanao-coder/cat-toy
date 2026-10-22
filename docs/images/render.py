"""docs/images の SVG を PNG（と印刷用の PDF）にする。Chromium と Pillow が必要。

  python docs/images/wiring_diagram.py && python docs/images/figures.py   # SVG を作り直す
  CHROME=/path/to/chrome python docs/images/render.py                     # PNG・PDF を作る

配線図からは、手順書の各節に載せる拡大図（wiring_*.png）も切り出す。
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
SCALE = 2
CHROME = os.environ.get("CHROME", "chromium")

# 配線図から切り出す拡大図（SVG 上の座標: 左, 上, 右, 下）
CROPS = {
    "wiring_power.png": (0, 585, 1010, 910),  # 電源・コンデンサ・サーボ
    "wiring_laser.png": (800, 300, 1400, 910),  # レーザー（R1・R2・Q1）
    "wiring_to92.png": (490, 925, 850, 1075),  # 2SC1815 の足
    "wiring_lan.png": (520, 0, 1400, 340),  # 有線 LAN 用の口（J7）
}


def size_of(svg: Path) -> tuple[int, int]:
    m = re.search(r'width="(\d+)" height="(\d+)"', svg.read_text(encoding="utf-8"))
    return int(m.group(1)), int(m.group(2))


def chrome(args: list[str], html: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", *args,
                        page.as_uri()], check=True, capture_output=True)


def to_png(svg: Path) -> Path:
    w, h = size_of(svg)
    png = svg.with_suffix(".png")
    html = (f'<!doctype html><style>html,body{{margin:0;background:#fff}}</style>'
            f'<img src="{svg.resolve().as_uri()}" style="display:block;width:{w}px;height:{h}px">')
    # 画面の高さは指定より少し小さくなるので、大きめに開いてから切り取る
    chrome([f"--force-device-scale-factor={SCALE}", f"--window-size={w},{h + 300}", f"--screenshot={png}"], html)
    Image.open(png).crop((0, 0, w * SCALE, h * SCALE)).save(png, optimize=True)
    return png


def to_pdf(svg: Path) -> Path:
    w, h = size_of(svg)
    pdf = svg.with_suffix(".pdf")
    html = (f'<!doctype html><style>@page{{size:{w}px {h}px;margin:0}}html,body{{margin:0}}</style>'
            f'<img src="{svg.resolve().as_uri()}" style="display:block;width:{w}px;height:{h}px">')
    chrome(["--no-pdf-header-footer", f"--print-to-pdf={pdf}"], html)
    return pdf


def main() -> None:
    for svg in sorted(HERE.glob("*.svg")):
        print("PNG:", to_png(svg))
    wiring = Image.open(HERE / "wiring.png")
    for name, box in CROPS.items():
        wiring.crop(tuple(v * SCALE for v in box)).save(HERE / name, optimize=True)
        print("切り出し:", HERE / name)
    print("PDF:", to_pdf(HERE / "wiring.svg"))


if __name__ == "__main__":
    main()
