"""EasyEDA で描いた中継基板の回路図が docs/pcb.md の接続表どおりかを照合する。

使い方:
  1. EasyEDA で「書き出し → ネットリスト」を選び、形式は「Protel」（「Altium」「Protel2」と表示されることもある）で保存する
  2. python pcb/check_netlist.py <保存したファイル>

部品の記号（J1・R1 など）とピン番号は docs/pcb.md の表のとおりに付けてあることが前提。
ESP32 ボードのピンの並び（HEADER_LEFT / HEADER_RIGHT）は、届いたボード（ESP32-S3 WROOM N16R8 CAM、
片側 20 本）の印字の写真から読み取ったもの。
"""

from __future__ import annotations

import sys
from pathlib import Path

# ESP32 ボードのピンヘッダー。1 番はアンテナ側（USB-C と反対の端）
HEADER_LEFT = [  # J1
    "3V3", "EN", "IO4", "IO5", "IO6", "IO7", "IO15", "IO16", "IO17", "IO18",
    "IO8", "IO3", "IO46", "IO9", "IO10", "IO11", "IO12", "IO13", "IO14", "5V",
]
HEADER_RIGHT = [  # J2
    "IO43", "IO44", "IO1", "IO2", "IO42", "IO41", "IO40", "IO39", "IO38", "IO37",
    "IO36", "IO35", "IO0", "IO45", "IO48", "IO47", "IO21", "IO20", "IO19", "GND",
]
# W5500 Lite を挿す口（6 本 × 2 列）。1 番は GND の端
W5500_ROW1 = ["GND", "GND", "MOSI", "SCK", "CS", "INT"]  # J7
W5500_ROW2 = ["GND", "3V3", "3V3", "NC", "RST", "MISO"]  # J8
# つないではいけないピンを調べる口（ピンの取り違えを見つける）
CHECKED_HEADERS = {"J1": HEADER_LEFT, "J2": HEADER_RIGHT, "J7": W5500_ROW1, "J8": W5500_ROW2}

REQUIRED = ["J1", "J2", "J3", "J4", "J5", "J6", "J7", "J8", "JP1", "Q1", "R1", "R2", "R3", "C1"]
OPTIONAL = ["F1", "C2", "C3", "J9"]


def board_pins(name: str) -> list[str]:
    """ESP32 ボードの信号名（3V3・IO14 など）が出ているヘッダーのピン。"""
    pins = [f"J1-{i}" for i, n in enumerate(HEADER_LEFT, 1) if n == name]
    pins += [f"J2-{i}" for i, n in enumerate(HEADER_RIGHT, 1) if n == name]
    return pins


def expected_nets(parts: set[str]) -> dict[str, set[str]]:
    """つながっているべきピンの組。キーは説明用の名前で、回路図のネット名と同じでなくてよい。"""
    nets = {
        "5V": {*board_pins("5V"), "J4-2", "J5-2", "C1-1", "JP1-1"},
        "3V3": {*board_pins("3V3"), "J8-2", "J8-3", "R3-1", "JP1-3"},
        "GND": {*board_pins("GND"), "J3-2", "J4-1", "J5-1", "C1-2", "J7-1", "J7-2", "J8-1", "Q1-1", "R2-2"},
        "PAN（IO14）": {*board_pins("IO14"), "J4-3"},
        "TILT（IO21）": {*board_pins("IO21"), "J5-3"},
        "レーザー信号（IO47）": {*board_pins("IO47"), "R1-1"},
        "レーザーのベース": {"R1-2", "Q1-3", "R2-1"},
        "レーザー（−）": {"Q1-2", "J6-2"},
        "レーザー（＋）": {"J6-1", "JP1-2"},
        "ETH_SCK（IO2）": {*board_pins("IO2"), "J7-4"},
        "ETH_MOSI（IO42）": {*board_pins("IO42"), "J7-3"},
        "ETH_MISO（IO41）": {*board_pins("IO41"), "J8-6"},
        "ETH_CS（IO1）": {*board_pins("IO1"), "J7-5", "R3-2"},
        "ETH_INT（IO3）": {*board_pins("IO3"), "J7-6"},
    }
    if "F1" in parts:  # ヒューズがあれば、端子台 → F1 → 5V
        nets["5V 入力（ヒューズの前）"] = {"J3-1", "F1-1"}
        nets["5V"].add("F1-2")
    else:
        nets["5V"].add("J3-1")
    for cap in ("C2", "C3"):  # W5500 用のパスコン（任意）
        if cap in parts:
            nets["3V3"].add(f"{cap}-1")
            nets["GND"].add(f"{cap}-2")
    if "J9" in parts:  # 外付けの ON/OFF ボタン（任意）
        nets["ボタン（IO0）"] = {*board_pins("IO0"), "J9-1"}
        nets["GND"].add("J9-2")
    return nets


def parse_protel(text: str) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """Protel 形式のネットリストを読む。戻り値は（部品の記号 → 情報の行, ネット名 → ピンの集合）。"""
    parts: dict[str, list[str]] = {}
    nets: dict[str, set[str]] = {}
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        if lines[i] in ("[", "("):
            close = "]" if lines[i] == "[" else ")"
            j = i + 1
            body = []
            while j < len(lines) and lines[j] != close:
                if lines[j]:
                    body.append(lines[j])
                j += 1
            if body:
                if close == "]":
                    parts[body[0]] = body[1:]
                else:
                    nets[body[0]] = {p.replace(" ", "") for p in body[1:]}
            i = j
        i += 1
    return parts, nets


def check(parts: dict[str, list[str]], nets: dict[str, set[str]]) -> list[str]:
    """問題点の一覧（日本語）。空なら接続表どおり。"""
    problems = [f"部品 {ref} がありません" for ref in REQUIRED if ref not in parts]
    unknown = sorted(set(parts) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        problems.append(f"接続表にない部品があります: {', '.join(unknown)}（記号の付け間違いでなければ無視してよい）")

    where = {}  # ピン → ネット名
    for name, pins in nets.items():
        for p in pins:
            where[p] = name

    expected = expected_nets(set(parts))
    used_nets: dict[str, str] = {}  # 回路図のネット名 → 期待した組の名前
    shorted: set[frozenset[str]] = set()
    for label, pins in expected.items():
        found = {where[p] for p in pins if p in where}
        missing = sorted(p for p in pins if p not in where)
        if missing:
            problems.append(f"{label}: どこにもつながっていないピンがあります: {', '.join(missing)}")
        if len(found) > 1:
            groups = "; ".join(f"{n}: {', '.join(sorted(p for p in pins if where.get(p) == n))}" for n in sorted(found))
            problems.append(f"{label}: つながっているべきピンが別々のネットに分かれています（{groups}）")
        for n in found:
            pair = frozenset((used_nets.get(n, label), label))
            if len(pair) == 2 and pair not in shorted:
                shorted.add(pair)
                problems.append(f"{used_nets[n]} と {label} がつながってしまっています（ネット {n}）")
            used_nets.setdefault(n, label)

    # ESP32 ボードと W5500 の、使わないはずのピンが何かにつながっていないか（ピンの取り違えを見つける）
    expected_pins = set().union(*expected.values())
    for p, name in sorted(where.items()):
        ref, _, num = p.partition("-")
        if ref in CHECKED_HEADERS and p not in expected_pins and len(nets[name]) > 1:
            header = CHECKED_HEADERS[ref]
            signal = header[int(num) - 1] if num.isdigit() and 0 < int(num) <= len(header) else "?"
            problems.append(f"{p}（{signal}）はつながないピンですが、ネット {name} につながっています")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    parts, nets = parse_protel(Path(args[0]).read_text(encoding="utf-8", errors="replace"))
    if not parts and not nets:
        print("ネットリストを読めませんでした。EasyEDA の書き出しで「Protel」形式を選んだか確認してください。")
        return 2
    problems = check(parts, nets)
    if problems:
        print(f"接続表と違う所が {len(problems)} 件あります:")
        for p in problems:
            print(f"  - {p}")
        return 1
    optional = [ref for ref in OPTIONAL if ref in parts]
    print(f"接続表どおりです（部品 {len(parts)} 個、ネット {len(nets)} 本。任意の部品: {', '.join(optional) or 'なし'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
