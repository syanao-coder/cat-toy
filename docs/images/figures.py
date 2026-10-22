"""docs/hardware.md の説明図（unit.svg・install.svg）を作る。

  python docs/images/figures.py      → docs/images/unit.svg と install.svg を書き出す

配線図は wiring_diagram.py、PNG への書き出しは render.py。
"""

from __future__ import annotations

from pathlib import Path

FONT = "'Hiragino Sans', 'Yu Gothic', Meiryo, 'Noto Sans CJK JP', 'IPAGothic', sans-serif"


class Svg:
    def __init__(self, w: int, h: int):
        self.w, self.h, self.out = w, h, [f'<rect width="{w}" height="{h}" fill="#ffffff"/>']

    def add(self, s: str) -> None:
        self.out.append(s)

    def text(self, x, y, s, size=14, anchor="start", color="#212121", weight="normal"):
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{color}" '
                 f'font-weight="{weight}">{s}</text>')

    def render(self) -> str:
        body = "\n".join(self.out)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" font-family="{FONT}">\n{body}\n</svg>\n')


def unit() -> str:
    """ケースの中身（カメラは固定、動くのはレーザーだけ）。"""
    s = Svg(900, 430)
    s.text(30, 36, "カメラユニットの中身", 20, weight="bold")
    s.text(30, 60, "カメラは動かさずに固定し、パン・チルト台に載せたレーザーだけを動かす", 13, color="#616161")
    s.add('<rect x="30" y="80" width="840" height="300" rx="18" fill="#f5f5f5" stroke="#9e9e9e" stroke-width="3" stroke-dasharray="10 6"/>')
    s.text(50, 106, "ケース（壁の高い所に固定）", 14, color="#616161", weight="bold")

    # 中継基板と ESP32 ボード
    s.add('<rect x="70" y="190" width="250" height="150" rx="6" fill="#c8e6c9" stroke="#66bb6a" stroke-width="2"/>')
    for i in range(10):
        for j in range(5):
            s.add(f'<circle cx="{86 + i * 24}" cy="{206 + j * 28}" r="2.5" fill="#a5d6a7"/>')
    s.add('<rect x="120" y="150" width="150" height="120" rx="8" fill="#263238" stroke="#000" stroke-width="2"/>')
    s.add('<rect x="165" y="160" width="60" height="50" rx="3" fill="#b0bec5"/>')
    s.text(195, 245, "ESP32", 14, "middle", "#eceff1", "bold")
    s.text(220, 362, "ESP32 ボード ＋ 中継基板（電源・配線をまとめる）", 14, "middle", weight="bold")

    # カメラ（固定）とケーブル
    s.add('<path d="M270,190 C340,190 340,150 410,150" fill="none" stroke="#ffb300" stroke-width="10" stroke-linecap="round"/>')
    s.add('<rect x="410" y="122" width="70" height="56" rx="6" fill="#37474f" stroke="#000" stroke-width="2"/>')
    s.add('<circle cx="445" cy="150" r="18" fill="#263238" stroke="#90a4ae" stroke-width="3"/>')
    s.add('<circle cx="445" cy="150" r="8" fill="#4fc3f7"/>')
    s.text(445, 200, "カメラ（固定）", 14, "middle", weight="bold")
    s.text(445, 218, "部屋全体を見渡す", 12, "middle", "#616161")
    s.text(340, 140, "75mm ケーブル", 11, "middle", "#8d6e00")
    s.add('<path d="M480,150 L860,96 M480,150 L860,214" stroke="#4fc3f7" stroke-width="2" stroke-dasharray="6 5"/>')
    s.text(780, 160, "見える範囲", 12, "middle", "#0288d1")

    # パン・チルト台とレーザー
    s.add('<rect x="600" y="300" width="120" height="44" rx="6" fill="#1e88e5" stroke="#0d47a1" stroke-width="2"/>')
    s.text(660, 328, "パン（左右）", 13, "middle", "#ffffff", "bold")
    s.add('<rect x="640" y="250" width="40" height="50" fill="#90a4ae" stroke="#546e7a" stroke-width="2"/>')
    s.add('<rect x="680" y="236" width="76" height="40" rx="6" fill="#1e88e5" stroke="#0d47a1" stroke-width="2"/>')
    s.text(718, 261, "チルト", 13, "middle", "#ffffff", "bold")
    s.add('<rect x="690" y="216" width="60" height="18" rx="4" fill="#d4a017" stroke="#8d6e00" stroke-width="2"/>')
    s.add('<path d="M750,225 L860,290" stroke="#d32f2f" stroke-width="3" stroke-dasharray="8 5"/>')
    s.add('<circle cx="860" cy="290" r="6" fill="#d32f2f"/>')
    s.text(684, 230, "レーザー", 13, "end", weight="bold")
    s.text(660, 364, "パン・チルト台（ここだけ動く）", 14, "middle", weight="bold")
    s.add('<path d="M320,300 C430,300 500,322 600,322" fill="none" stroke="#795548" stroke-width="4"/>')
    s.text(460, 296, "サーボ・レーザーの線", 11, "middle", "#795548")

    # 外へ出る線
    s.add('<path d="M70,300 H10" stroke="#d32f2f" stroke-width="5"/>')
    s.add('<path d="M70,316 H10" stroke="#212121" stroke-width="5"/>')
    s.text(14, 405, "← 5V 電源（後から LAN ケーブルも足せる）", 13, color="#616161")
    return s.render()


def install() -> str:
    """設置のしかた（高い所から斜め下を見下ろす）。"""
    s = Svg(900, 560)
    s.text(30, 36, "設置のしかた", 20, weight="bold")
    s.text(30, 60, "高い壁面（部屋の角が理想）から、部屋の中央に向けて斜め下に取り付ける", 13, color="#616161")
    # 壁と床
    s.add('<rect x="60" y="80" width="26" height="330" fill="#d7ccc8"/>')
    for y in range(86, 410, 18):
        s.add(f'<path d="M60,{y + 12} l26,-12" stroke="#a1887f" stroke-width="2"/>')
    s.add('<rect x="60" y="410" width="800" height="18" fill="#bcaaa4"/>')
    s.text(300, 450, "床（ここで遊ぶ）", 14, "middle", weight="bold")
    # 見える範囲
    s.add('<path d="M118,124 L420,410 L860,410 L860,230 Z" fill="#4fc3f7" fill-opacity="0.18" stroke="#4fc3f7" stroke-width="2" stroke-dasharray="6 5"/>')
    s.text(700, 300, "カメラの見える範囲", 13, "middle", "#0288d1")
    # ケース
    s.add('<path d="M86,104 h40 l14,36 h-54 z" fill="#455a64" stroke="#263238" stroke-width="2"/>')
    s.add('<circle cx="120" cy="128" r="7" fill="#4fc3f7" stroke="#263238" stroke-width="2"/>')
    s.text(150, 108, "ケース", 14, weight="bold")
    s.text(150, 128, "斜め下向き（部屋の中央へ）", 12, color="#616161")
    # 高さ
    s.add('<path d="M40,122 V408 M32,122 h16 M32,408 h16" stroke="#616161" stroke-width="2"/>')
    s.text(18, 262, "2m", 13, "middle", "#616161", "bold")
    s.text(18, 280, "以上", 13, "middle", "#616161", "bold")
    # レーザーと猫
    s.add('<path d="M126,134 L560,404" stroke="#d32f2f" stroke-width="2.5" stroke-dasharray="8 5"/>')
    s.add('<circle cx="560" cy="404" r="6" fill="#d32f2f"/>')
    s.text(560, 448, "レーザーの点", 12, "middle", "#d32f2f", "bold")
    s.add('<ellipse cx="660" cy="388" rx="46" ry="20" fill="#ff8a65"/>')
    s.add('<circle cx="616" cy="366" r="16" fill="#ff8a65"/>')
    s.add('<path d="M604,356 l2,-16 l10,10 z M618,352 l8,-14 l4,14 z" fill="#ff8a65"/>')
    s.add('<path d="M704,384 q30,-10 26,-40" fill="none" stroke="#ff8a65" stroke-width="7" stroke-linecap="round"/>')
    s.add('<circle cx="611" cy="363" r="2.5" fill="#212121"/>')
    s.text(700, 448, "猫の少し前を照らす", 12, "middle", "#bf360c")
    # 注意
    notes = ["・真下に向けない（真下付近はパンの回転が効かず、狙いが不安定になる）",
             "・鏡・窓・テレビ・ガラスに光が当たらない向きに",
             "・猫が登って届く所、ケーブルをかじれる所は避ける"]
    for i, t in enumerate(notes):
        s.text(60, 490 + i * 22, t, 13, color="#424242")
    return s.render()


if __name__ == "__main__":
    here = Path(__file__).parent
    for name, fn in (("unit.svg", unit), ("install.svg", install)):
        (here / name).write_text(fn(), encoding="utf-8")
        print(f"書き出しました: {here / name}")
