"""docs/hardware.md の配線図（wiring.svg）を作る。

  python docs/images/wiring_diagram.py      → docs/images/wiring.svg を書き出す

ESP32 ボードのピンの並びは pcb/check_netlist.py と同じ（ESP32-S3-DevKitC-1 互換の想定）。
届いたボードの並びが違ったら、LEFT / RIGHT を直して作り直す（配線の道筋も合わせて直す必要がある）。
"""

from __future__ import annotations

from pathlib import Path

W, H = 1400, 1110

LEFT = ["3V3", "3V3", "RST", "IO4", "IO5", "IO6", "IO7", "IO15", "IO16", "IO17", "IO18",
        "IO8", "IO3", "IO46", "IO9", "IO10", "IO11", "IO12", "IO13", "IO14", "5V", "GND"]
RIGHT = ["GND", "TX", "RX", "IO1", "IO2", "IO42", "IO41", "IO40", "IO39", "IO38", "IO37",
         "IO36", "IO35", "IO0", "IO45", "IO48", "IO47", "IO21", "IO20", "IO19", "GND", "GND"]

# 線の色
C5V, CGND, C33 = "#d32f2f", "#212121", "#ef6c00"
CSERVO, CLASER, CLAN = "#2e7d32", "#7b1fa2", "#1565c0"
BROWN, ORANGE = "#795548", "#fb8c00"

# ESP32 ボード
BX0, BX1, BY0, BY1 = 560, 840, 150, 740
PX_L, PX_R = 588, 812
def row_y(n: int) -> int:  # 1 始まり
    return 180 + 24 * (n - 1)

USED_L = {1: C33, 13: CLAN, 20: CSERVO, 21: C5V, 22: CGND}
USED_R = {4: CLAN, 5: CLAN, 6: CLAN, 7: CLAN, 17: CLASER, 18: CSERVO}

Y_33, Y_GND, Y_5V = 28, 820, 860  # 3.3V・GND・5V の線（レール）
HOP = 6

out: list[str] = []


def add(s: str) -> None:
    out.append(s)


def text(x, y, s, size=13, anchor="start", color="#212121", weight="normal", **kw):
    extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in kw.items())
    add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{color}" '
        f'font-weight="{weight}"{extra}>{s}</text>')


def wire(points, color, width=3, hops=()):
    """折れ線。hops は線上の点で、そこでは半円で飛び越える（交差しているだけでつながっていない）。"""
    d = f"M{points[0][0]},{points[0][1]}"
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        on = [h for h in hops if (x0 == x1 == h[0] and min(y0, y1) < h[1] < max(y0, y1))
              or (y0 == y1 == h[1] and min(x0, x1) < h[0] < max(x0, x1))]
        if x0 == x1:  # 縦の線: 右へふくらむ
            sgn = 1 if y1 > y0 else -1
            for hx, hy in sorted(on, key=lambda h: sgn * h[1]):
                d += f" L{hx},{hy - sgn * HOP} A{HOP},{HOP} 0 0 {1 if sgn > 0 else 0} {hx},{hy + sgn * HOP}"
        else:  # 横の線: 上へふくらむ
            sgn = 1 if x1 > x0 else -1
            for hx, hy in sorted(on, key=lambda h: sgn * h[0]):
                d += f" L{hx - sgn * HOP},{hy} A{HOP},{HOP} 0 0 {1 if sgn > 0 else 0} {hx + sgn * HOP},{hy}"
        d += f" L{x1},{y1}"
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
        f'stroke-linejoin="round" stroke-linecap="round"/>')


def dot(x, y, color):
    add(f'<circle cx="{x}" cy="{y}" r="5" fill="{color}"/>')


def resistor_h(x0, x1, y, label, color):
    add(f'<rect x="{x0}" y="{y - 8}" width="{x1 - x0}" height="16" rx="4" fill="#f5e6c8" stroke="#8d6e63" stroke-width="1.5"/>')
    for i, c in enumerate(["#795548", "#212121", "#d32f2f"]):
        add(f'<rect x="{x0 + 10 + i * 8}" y="{y - 8}" width="4" height="16" fill="{c}"/>')
    text((x0 + x1) / 2, y - 14, label, 12, "middle", color)


def resistor_v(x, y0, y1, label, color, side="left"):
    add(f'<rect x="{x - 8}" y="{y0}" width="16" height="{y1 - y0}" rx="4" fill="#f5e6c8" stroke="#8d6e63" stroke-width="1.5"/>')
    for i, c in enumerate(["#795548", "#212121", "#ff9800"]):
        add(f'<rect x="{x - 8}" y="{y0 + 10 + i * 8}" width="16" height="4" fill="{c}"/>')
    if side == "left":
        text(x - 14, (y0 + y1) / 2 + 4, label, 12, "end", color)
    else:
        text(x + 14, (y0 + y1) / 2 + 4, label, 12, "start", color)


def servo(x0, y0, title, pins_x, header_y, colors, horn_dx=30):
    """サーボの本体と、基板に付ける 3 ピンの口（ケーブルを挿す）。"""
    add(f'<rect x="{x0}" y="{y0}" width="110" height="64" rx="6" fill="#1e88e5" stroke="#0d47a1" stroke-width="2"/>')
    add(f'<ellipse cx="{x0 + horn_dx}" cy="{y0 - 6}" rx="22" ry="7" fill="#fafafa" stroke="#9e9e9e"/>')
    text(x0 + 55, y0 + 28, title, 13, "middle", "#ffffff", "bold")
    text(x0 + 55, y0 + 46, "MG90S", 12, "middle", "#e3f2fd")
    for px, c in zip(pins_x, colors):  # ケーブル
        wire([(px, y0 + 64), (px, header_y - 8)], c, 3)
    add(f'<rect x="{min(pins_x) - 12}" y="{header_y - 8}" width="{max(pins_x) - min(pins_x) + 24}" height="16" rx="2" fill="#263238"/>')
    for px in pins_x:
        add(f'<circle cx="{px}" cy="{header_y}" r="3.5" fill="#ffd54f"/>')


# ------------------------------------------------------------------ 背景と見出し
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
text(40, 44, "カメラユニットの配線図（手配線用）", 22, weight="bold")
text(40, 68, "ESP32 ボードを上から見た図。線は基板の裏で配線してよい", 13, color="#616161")

# ------------------------------------------------------------------ ESP32 ボード
add(f'<rect x="{BX0}" y="{BY0}" width="{BX1 - BX0}" height="{BY1 - BY0}" rx="10" fill="#263238" stroke="#000" stroke-width="2"/>')
add(f'<rect x="660" y="166" width="80" height="150" rx="3" fill="#b0bec5" stroke="#78909c"/>')
add('<rect x="660" y="166" width="80" height="34" fill="#546e7a"/>')
for i in range(4):
    add(f'<path d="M668,{172 + i * 7} h64" stroke="#cfd8dc" stroke-width="1.5"/>')
text(700, 260, "ESP32-S3", 11, "middle", "#37474f", "bold")
text(700, 276, "モジュール", 11, "middle", "#37474f")
text(700, 140, "アンテナ側", 13, "middle", "#455a64")
text(700, 470, "ESP32-S3 CAM", 15, "middle", "#eceff1", "bold")
text(700, 490, "（部品面）", 12, "middle", "#b0bec5")
for ux in (640, 722):
    add(f'<rect x="{ux}" y="728" width="38" height="20" rx="6" fill="#9e9e9e" stroke="#616161"/>')
text(700, 776, "USB-C 側（ふさがない）", 13, "middle", "#455a64")

for i, name in enumerate(LEFT, 1):
    y = row_y(i)
    c = USED_L.get(i)
    add(f'<circle cx="{PX_L}" cy="{y}" r="6" fill="{c or "#455a64"}" stroke="{c or "#90a4ae"}" stroke-width="1.5"/>')
    text(PX_L + 14, y + 4, name, 12, color="#ffffff" if c else "#78909c", weight="bold" if c else "normal")
for i, name in enumerate(RIGHT, 1):
    y = row_y(i)
    c = USED_R.get(i)
    add(f'<circle cx="{PX_R}" cy="{y}" r="6" fill="{c or "#455a64"}" stroke="{c or "#90a4ae"}" stroke-width="1.5"/>')
    text(PX_R - 14, y + 4, name, 12, "end", color="#ffffff" if c else "#78909c", weight="bold" if c else "normal")

# ------------------------------------------------------------------ 5V・GND の線（レール）と入力端子
wire([(60, Y_GND), (1350, Y_GND)], CGND, 5)
wire([(60, Y_5V), (1330, Y_5V)], C5V, 5)
text(70, Y_GND - 8, "GND の線（黒）", 12, color=CGND, weight="bold")
text(70, Y_5V - 8, "5V の線（赤）", 12, color=C5V, weight="bold")
add('<rect x="18" y="802" width="40" height="76" rx="4" fill="#2e7d32" stroke="#1b5e20" stroke-width="2"/>')
for y, s in ((Y_GND, "−"), (Y_5V, "＋")):
    add(f'<circle cx="38" cy="{y}" r="9" fill="#cfd8dc" stroke="#455a64" stroke-width="2"/>')
    add(f'<path d="M32,{y} h12" stroke="#455a64" stroke-width="2"/>')
    text(8, y + 5, s, 15, "middle", weight="bold")
text(18, 795, "J3 5V 入力（端子台）", 12, weight="bold")
text(18, 900, "↑ AC アダプター（5V 2A 以上）か PoE スプリッターの 5V をつなぐ", 12, color="#616161")

# ボードの 5V・GND
wire([(PX_L, row_y(21)), (500, row_y(21)), (500, Y_5V)], C5V, hops=[(500, Y_GND)])
dot(500, Y_5V, C5V)
wire([(PX_L, row_y(22)), (520, row_y(22)), (520, Y_GND)], CGND)
dot(520, Y_GND, CGND)

# C1
add('<rect x="212" y="700" width="40" height="70" rx="6" fill="#283593" stroke="#1a237e" stroke-width="2"/>')
add('<rect x="212" y="700" width="12" height="70" fill="#9fa8da"/>')
text(218, 740, "−", 14, "middle", "#1a237e", "bold")
wire([(222, 770), (222, Y_GND)], CGND)
dot(222, Y_GND, CGND)
wire([(244, 770), (244, Y_5V)], C5V, hops=[(244, Y_GND)])
dot(244, Y_5V, C5V)
text(232, 672, "C1 電解コンデンサ", 12, "middle", weight="bold")
text(232, 690, "470〜1000µF（帯の側が −）", 12, "middle")

# ------------------------------------------------------------------ パン用サーボ（J4）
servo(350, 606, "パン用サーボ", (380, 404, 428), 720, (BROWN, C5V, ORANGE))
text(362, 724, "J4", 13, "end", weight="bold")
text(380, 746, "茶", 11, "middle", BROWN)
text(404, 746, "赤", 11, "middle", C5V)
text(428, 746, "橙", 11, "middle", ORANGE)
wire([(380, 728), (380, 740)], CGND)
wire([(380, 752), (380, Y_GND)], CGND)
dot(380, Y_GND, CGND)
wire([(404, 728), (404, 740)], C5V)
wire([(404, 752), (404, Y_5V)], C5V, hops=[(404, Y_GND)])
dot(404, Y_5V, C5V)
wire([(428, 728), (428, 740)], CSERVO)
wire([(428, 752), (428, 770), (470, 770), (470, row_y(20)), (PX_L, row_y(20))], CSERVO)
text(476, row_y(20) - 8, "IO14 → パン", 12, color=CSERVO, weight="bold")

# ------------------------------------------------------------------ チルト用サーボ（J5）
servo(900, 616, "チルト用サーボ", (930, 954, 978), 720, (ORANGE, C5V, BROWN), horn_dx=80)
text(996, 724, "J5", 13, weight="bold")
text(930, 746, "橙", 11, "middle", ORANGE)
text(954, 746, "赤", 11, "middle", C5V)
text(978, 746, "茶", 11, "middle", BROWN)
wire([(930, 728), (930, 740)], CSERVO)
wire([(930, 752), (930, 770), (860, 770), (860, row_y(18)), (PX_R, row_y(18))], CSERVO)
text(866, row_y(18) + 20, "IO21 → チルト", 12, color=CSERVO, weight="bold")
wire([(954, 728), (954, 740)], C5V)
wire([(954, 752), (954, Y_5V)], C5V, hops=[(954, Y_GND)])
dot(954, Y_5V, C5V)
wire([(978, 728), (978, 740)], CGND)
wire([(978, 752), (978, Y_GND)], CGND)
dot(978, Y_GND, CGND)

# ------------------------------------------------------------------ レーザー（Q1・R1・R2）
YL = row_y(17)
wire([(PX_R, YL), (1010, YL)], CLASER)
text(826, YL - 8, "IO47 → レーザー", 12, color=CLASER, weight="bold")
resistor_h(1010, 1058, YL, "R1 1kΩ", CLASER)
wire([(1058, YL), (1132, YL)], CLASER)
dot(1090, YL, CLASER)
resistor_v(1090, 620, 676, "R2 10kΩ", CGND)
wire([(1090, YL), (1090, 620)], CLASER)
wire([(1090, 676), (1090, Y_GND)], CGND)
dot(1090, Y_GND, CGND)
# トランジスタの記号
add(f'<circle cx="1152" cy="{YL}" r="26" fill="#fafafa" stroke="#424242" stroke-width="2"/>')
add(f'<path d="M1138,{YL - 14} v28" stroke="#212121" stroke-width="3"/>')
add(f'<path d="M1138,{YL - 6} L1162,{YL - 22} M1138,{YL + 6} L1162,{YL + 22}" stroke="#212121" stroke-width="2.5"/>')
add(f'<path d="M1162,{YL + 22} l-10,-1 l5,-7 z" fill="#212121"/>')
wire([(1162, YL - 22), (1162, 486)], CGND)
wire([(1162, YL + 22), (1162, Y_GND)], CGND)
dot(1162, Y_GND, CGND)
text(1128, YL - 10, "B", 12, "end", "#424242", "bold")
text(1170, YL - 30, "C", 12, color="#424242", weight="bold")
text(1170, YL + 40, "E", 12, color="#424242", weight="bold")
text(1186, YL + 6, "Q1 2SC1815", 13, weight="bold")
# レーザーのモジュール
add('<rect x="1216" y="372" width="36" height="84" rx="5" fill="#d4a017" stroke="#8d6e00" stroke-width="2"/>')
add('<rect x="1220" y="366" width="28" height="10" rx="2" fill="#bdbdbd"/>')
add(f'<path d="M1234,362 V310" stroke="{C5V}" stroke-width="3" stroke-dasharray="6 5"/>')
text(1262, 400, "レーザー", 13, weight="bold")
text(1262, 418, "（1mW 以下）", 12)
wire([(1228, 456), (1228, 486), (1162, 486)], CGND)
text(1206, 500, "−（黒）", 11, "end", CGND)
wire([(1242, 456), (1242, 470), (1310, 470), (1310, Y_5V)], C5V, hops=[(1310, Y_GND)])
dot(1310, Y_5V, C5V)
text(1264, 466, "＋（赤）", 11, color=C5V)

# ------------------------------------------------------------------ 3.3V と有線 LAN 用の口（J7）
J7X = {8: 900, 7: 940, 6: 980, 5: 1020, 4: 1060, 3: 1100, 2: 1140, 1: 1180}
J7N = {1: "GND", 2: "3V3", 3: "5V", 4: "SCK", 5: "MOSI", 6: "MISO", 7: "CS", 8: "INT"}
JT, JB = 96, 130
wire([(PX_L, row_y(1)), (PX_L, Y_33), (J7X[2], Y_33), (J7X[2], JT)], C33)
text(600, Y_33 - 8, "3.3V の線（橙）", 12, color=C33, weight="bold")
# INT（IO3）は上を回る
wire([(PX_L, row_y(13)), (540, row_y(13)), (540, 50), (J7X[8], 50), (J7X[8], JT)], CLAN, hops=[(PX_L, 50)])
text(640, 45, "IO3 → INT", 12, color=CLAN, weight="bold")
# R3（CS を 3.3V へ）
resistor_v(J7X[7], 40, 84, "", C33)
wire([(J7X[7], Y_33), (J7X[7], 40)], C33)
wire([(J7X[7], 84), (J7X[7], JT)], CLAN)
dot(J7X[7], Y_33, C33)
text(J7X[7] + 14, 66, "R3 10kΩ", 12, color=C33)
# SPI の 4 本
for n, row in ((7, 4), (6, 5), (5, 6), (4, 7)):
    wire([(PX_R, row_y(row)), (J7X[n], row_y(row)), (J7X[n], JB)], CLAN)
# J7 の 5V・GND は右端を回ってレールへ
wire([(J7X[3], JB), (J7X[3], 200), (1330, 200), (1330, Y_5V)], C5V, hops=[(1330, Y_GND)])
wire([(J7X[1], JB), (J7X[1], 176), (1350, 176), (1350, Y_GND)], CGND)
add(f'<rect x="876" y="{JT}" width="328" height="{JB - JT}" rx="3" fill="#212121"/>')
for n, x in J7X.items():
    text(x, JT + 14, str(n), 10, "middle", "#bdbdbd")
    text(x, JT + 28, J7N[n], 10, "middle", "#ffffff", "bold")
text(1214, 112, "J7 有線 LAN 用の口", 13, weight="bold")
text(1214, 130, "（1×8 メス。W5500 用）", 11, color="#616161")

# ------------------------------------------------------------------ 凡例・注意
add('<line x1="20" y1="918" x2="1380" y2="918" stroke="#e0e0e0" stroke-width="2"/>')
text(40, 946, "線の色", 14, weight="bold")
legend = [(C5V, "5V"), (CGND, "GND"), (C33, "3.3V"), (CSERVO, "サーボの信号"), (CLASER, "レーザーの信号"), (CLAN, "有線 LAN の信号")]
for i, (c, s) in enumerate(legend):
    x, y = 40 + (i % 2) * 190, 970 + (i // 2) * 26
    wire([(x, y), (x + 36, y)], c, 4)
    text(x + 46, y + 5, s, 13)
dot(48, 1054, CGND)
text(62, 1059, "つながっている所", 13)
wire([(210, 1054), (232, 1054)], CGND, 3, hops=[(221, 1054)])
add('<path d="M221,1044 v20" stroke="#9e9e9e" stroke-width="3"/>')
text(242, 1059, "交差しているだけ（つながない）", 13)

# 2SC1815 の足
text(500, 946, "2SC1815 の足", 14, weight="bold")
add('<path d="M520,1000 v-28 a28,28 0 0 1 56,0 v28 z" fill="#424242"/>')
text(548, 990, "C1815", 10, "middle", "#e0e0e0")
for i, s in enumerate(("E", "C", "B")):
    x = 530 + i * 18
    add(f'<rect x="{x - 1.5}" y="1000" width="3" height="34" fill="#9e9e9e"/>')
    text(x, 1050, s, 13, "middle", weight="bold")
text(600, 976, "文字の書いてある平らな面を", 12)
text(600, 994, "手前に、足を下にして見たとき", 12)
text(600, 1012, "左から E・C・B", 12, weight="bold")
text(600, 1036, "E＝GND、C＝レーザーの −、", 12)
text(600, 1054, "B＝R1 と R2", 12)

# 注意
notes = [
    ("ボードのピンの並びは想定（ESP32-S3-DevKitC-1 互換）です。", True),
    ("届いたボードの印字と必ず見比べてください。", True),
    ("灰色のピンには何もつなぎません。", False),
    ("J7 は W5500 を後から挿すための口です。今は付けておくだけで構いません。", False),
    ("レーザーの＋は定格に合わせて 5V か 3.3V へ（3V 用なら 3.3V）。", False),
    ("サーボの電源をボードの 3V3 や USB-C から取らないこと。", False),
    ("書き込み（USB）のときは 5V 入力を外す。", False),
]
text(860, 946, "注意", 14, weight="bold")
for i, (s, strong) in enumerate(notes):
    text(860, 972 + i * 19, s, 12, color="#b71c1c" if strong else "#212121", weight="bold" if strong else "normal")


def render() -> str:
    body = "\n".join(out)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'font-family="\'Hiragino Sans\', \'Yu Gothic\', Meiryo, \'Noto Sans CJK JP\', \'IPAGothic\', sans-serif">\n'
            f'{body}\n</svg>\n')


if __name__ == "__main__":
    path = Path(__file__).with_name("wiring.svg")
    path.write_text(render(), encoding="utf-8")
    print(f"書き出しました: {path}")
