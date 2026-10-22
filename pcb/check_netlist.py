"""EasyEDA で描いた中継基板の回路図が docs/pcb.md の接続表どおりかを照合する。

使い方:
  1. EasyEDA で「書き出し → ネットリスト」を選び、形式は「Protel」（「Altium」「Protel2」と表示されることもある）で保存する
  2. python pcb/check_netlist.py <保存したファイル>

部品の記号（U1・R1 など）とピン番号は docs/pcb.md の表のとおりに付けてあることが前提。
ESP32 ボードは EasyEDA の部品「ESP32-S3 WROOM N16R8 CAM」（U1、40 ピン。左列が上から 1〜20、
右列が上から 40〜21）、W5500 は「W5500MINI」（W1、12 ピン）の番号に合わせてある。
ピンの並びは、届いたボードの印字の写真とも一致している。
"""

from __future__ import annotations

import sys
from pathlib import Path

# ESP32 ボードのピンヘッダー（アンテナ側から順に）
HEADER_LEFT = [  # U1 の 1〜20 番
    "3V3", "EN", "IO4", "IO5", "IO6", "IO7", "IO15", "IO16", "IO17", "IO18",
    "IO8", "IO3", "IO46", "IO9", "IO10", "IO11", "IO12", "IO13", "IO14", "5V",
]
HEADER_RIGHT = [  # U1 の 40〜21 番
    "IO43", "IO44", "IO1", "IO2", "IO42", "IO41", "IO40", "IO39", "IO38", "IO37",
    "IO36", "IO35", "IO0", "IO45", "IO48", "IO47", "IO21", "IO20", "IO19", "GND",
]
U1_PINS = {i: n for i, n in enumerate(HEADER_LEFT, 1)} | {41 - k: n for k, n in enumerate(HEADER_RIGHT, 1)}
# W5500 Lite（W1）。1〜6 番が 1 列目（G・G・MO・SCK・CS・INT）、7〜12 番が 2 列目を逆順に（MI・RST・NC・V・V・G）
W1_PINS = {1: "GND", 2: "GND", 3: "MOSI", 4: "SCK", 5: "CS", 6: "INT",
           7: "MISO", 8: "RST", 9: "NC", 10: "3V3", 11: "3V3", 12: "GND"}
# つないではいけないピンを調べる部品（ピンの取り違えを見つける）
CHECKED_PARTS = {"U1": U1_PINS, "W1": W1_PINS}
# トランジスタは Q1 と呼ぶ（EasyEDA で U2 になっていてもよい）
TRANSISTOR_ALIASES = ("U2",)

REQUIRED = ["U1", "W1", "J3", "J4", "J5", "J6", "JP1", "Q1", "R1", "R2", "R3", "C1"]
OPTIONAL = ["F1", "C2", "C3", "J9"]


def board_pins(name: str) -> list[str]:
    """ESP32 ボード（U1）の、その信号名（3V3・IO14 など）のピン。"""
    return [f"U1-{n}" for n, s in sorted(U1_PINS.items()) if s == name]


def w5500_pins(name: str) -> list[str]:
    """W5500（W1）の、その信号名のピン。"""
    return [f"W1-{n}" for n, s in sorted(W1_PINS.items()) if s == name]


def expected_nets(parts: set[str]) -> dict[str, set[str]]:
    """つながっているべきピンの組。キーは説明用の名前で、回路図のネット名と同じでなくてよい。"""
    nets = {
        "5V": {*board_pins("5V"), "J4-2", "J5-2", "C1-1", "JP1-1"},
        "3V3": {*board_pins("3V3"), *w5500_pins("3V3"), "R3-1", "JP1-3"},
        "GND": {*board_pins("GND"), *w5500_pins("GND"), "J3-2", "J4-1", "J5-1", "C1-2", "Q1-1", "R2-2"},
        "PAN（IO14）": {*board_pins("IO14"), "J4-3"},
        "TILT（IO21）": {*board_pins("IO21"), "J5-3"},
        "レーザー信号（IO47）": {*board_pins("IO47"), "R1-1"},
        "レーザーのベース": {"R1-2", "Q1-3", "R2-1"},
        "レーザー（−）": {"Q1-2", "J6-2"},
        "レーザー（＋）": {"J6-1", "JP1-2"},
        "ETH_SCK（IO2）": {*board_pins("IO2"), *w5500_pins("SCK")},
        "ETH_MOSI（IO42）": {*board_pins("IO42"), *w5500_pins("MOSI")},
        "ETH_MISO（IO41）": {*board_pins("IO41"), *w5500_pins("MISO")},
        "ETH_CS（IO1）": {*board_pins("IO1"), *w5500_pins("CS"), "R3-2"},
        "ETH_INT（IO3）": {*board_pins("IO3"), *w5500_pins("INT")},
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


def normalize(parts: dict[str, list[str]], nets: dict[str, set[str]]) -> None:
    """トランジスタの記号が Q1 でなく U2 などなら、Q1 として扱う。"""
    if "Q1" in parts:
        return
    for alias in TRANSISTOR_ALIASES:
        if alias in parts:
            parts["Q1"] = parts.pop(alias)
            for pins in nets.values():
                for p in [p for p in pins if p.startswith(alias + "-")]:
                    pins.discard(p)
                    pins.add("Q1-" + p.split("-", 1)[1])
            return


def check(parts: dict[str, list[str]], nets: dict[str, set[str]]) -> list[str]:
    """問題点の一覧（日本語）。空なら接続表どおり。"""
    normalize(parts, nets)
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
        if ref in CHECKED_PARTS and p not in expected_pins and len(nets[name]) > 1:
            signal = CHECKED_PARTS[ref].get(int(num), "?") if num.isdigit() else "?"
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
