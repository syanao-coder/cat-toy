"""EasyEDA で描いた中継基板の回路図が docs/pcb.md の接続表どおりかを照合する。

使い方:
  1. EasyEDA で「書き出し → ネットリスト」を選び、形式は「Protel」（「Altium」「Protel2」と表示されることもある）で保存する
  2. python pcb/check_netlist.py <保存したファイル>

部品の記号とピン番号は docs/pcb.md の表が基準。ただし次の違いは自動で読み替える。
- 記号: トランジスタが U2（→ Q1）、W5500 が U3（→ W1）、レーザーの口が H1（→ J6）でもよい。「U3.1」の「.1」も無視する
- W5500 の部品: 「W5500MINI」と「W5500-LITE」で 2 列目の番号の付け方が違うので、部品名で見分ける
- 口（J3・J4・J5・J6）のピンの並び: どのピンが 5V・GND・信号かを、つながっている相手から判断する
  （判断した並びを表示するので、基板の印字に使う）
ESP32 ボードは EasyEDA の部品「ESP32-S3 WROOM N16R8 CAM」（U1、40 ピン。左列が上から 1〜20、
右列が上から 40〜21）の番号。ピンの並びは、届いたボードの印字の写真とも一致している。
"""

from __future__ import annotations

import re
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
# W5500 Lite（W1）。どちらも 1〜6 番が 1 列目（G・G・MO・SCK・CS・INT）で、2 列目の番号の付け方が部品によって違う
W5500_MINI_PINS = {1: "GND", 2: "GND", 3: "MOSI", 4: "SCK", 5: "CS", 6: "INT",
                   7: "MISO", 8: "RST", 9: "NC", 10: "3V3", 11: "3V3", 12: "GND"}  # 「W5500MINI」: 2 列目を逆順に
W5500_LITE_PINS = {1: "GND", 2: "GND", 3: "MOSI", 4: "SCK", 5: "CS", 6: "INT",
                   7: "GND", 8: "3V3", 9: "3V3", 10: "NC", 11: "RST", 12: "MISO"}  # 「W5500-LITE」: 2 列目も同じ向きに
W1_PINS = W5500_MINI_PINS  # 部品名が分からないとき

# 記号の読み替え（正式な記号が無く、別名があるとき）
ALIASES = {"Q1": ("U2",), "W1": ("U3",), "J6": ("H1",)}

REQUIRED = ["U1", "W1", "J3", "J4", "J5", "J6", "Q1", "R1", "R2", "R3", "C1"]
OPTIONAL = ["JP1", "F1", "C2", "C3", "J9", "R4"]

# 口のピンの役目と、照合で使う番号（docs/pcb.md の番号）
CONNECTOR_ROLES = {
    "J3": {"5V": 1, "GND": 2},
    "J4": {"GND": 1, "5V": 2, "信号": 3},
    "J5": {"GND": 1, "5V": 2, "信号": 3},
    "J6": {"＋": 1, "−": 2},
}
CONNECTOR_NAMES = {"J3": "5V 入力", "J4": "パン用サーボ", "J5": "チルト用サーボ", "J6": "レーザー"}
# 向きのない部品（抵抗・セラミックコンデンサ）: どちらの向きに描いてもよい。
# （記号 → この相手と同じネットにあるべきピンの番号, 相手）
UNPOLARIZED = {"R1": (1, "U1-25"), "R2": (2, "U1-21"), "R3": (1, "U1-1"), "R4": (1, "U1-1"), "C2": (1, "U1-1")}


def board_pins(name: str) -> list[str]:
    """ESP32 ボード（U1）の、その信号名（3V3・IO14 など）のピン。"""
    return [f"U1-{n}" for n, s in sorted(U1_PINS.items()) if s == name]


def w5500_pins(name: str, w5500: dict[int, str] = W1_PINS) -> list[str]:
    """W5500（W1）の、その信号名のピン。"""
    return [f"W1-{n}" for n, s in sorted(w5500.items()) if s == name]


def expected_nets(parts: set[str], w5500: dict[int, str] = W1_PINS, laser_supply: str = "5V") -> dict[str, set[str]]:
    """つながっているべきピンの組。キーは説明用の名前で、回路図のネット名と同じでなくてよい。

    laser_supply は、JP1（電圧の切り替え）を使わないときにレーザーの＋をつなぐ先（"5V" か "3V3"）。
    """
    def w(name):
        return w5500_pins(name, w5500)

    nets = {
        "5V": {*board_pins("5V"), "J4-2", "J5-2", "C1-1"},
        "3V3": {*board_pins("3V3"), *w("3V3"), "R3-1"},
        "GND": {*board_pins("GND"), *w("GND"), "J3-2", "J4-1", "J5-1", "C1-2", "Q1-1", "R2-2"},
        "PAN（IO14）": {*board_pins("IO14"), "J4-3"},
        "TILT（IO21）": {*board_pins("IO21"), "J5-3"},
        "レーザー信号（IO47）": {*board_pins("IO47"), "R1-1"},
        "レーザーのベース": {"R1-2", "Q1-3", "R2-1"},
        "レーザー（−）": {"Q1-2", "J6-2"},
        "ETH_SCK（IO2）": {*board_pins("IO2"), *w("SCK")},
        "ETH_MOSI（IO42）": {*board_pins("IO42"), *w("MOSI")},
        "ETH_MISO（IO41）": {*board_pins("IO41"), *w("MISO")},
        "ETH_CS（IO1）": {*board_pins("IO1"), *w("CS"), "R3-2"},
        "ETH_INT（IO3）": {*board_pins("IO3"), *w("INT")},
    }
    if "JP1" in parts:  # レーザーの電圧をジャンパーで選ぶ
        nets["レーザー（＋）"] = {"J6-1", "JP1-2"}
        nets["5V"].add("JP1-1")
        nets["3V3"].add("JP1-3")
    else:  # レーザーの＋を 5V か 3.3V へ直接
        nets[laser_supply].add("J6-1")
    if "F1" in parts:  # ヒューズがあれば、端子台 → F1 → 5V
        nets["5V 入力（ヒューズの前）"] = {"J3-1", "F1-1"}
        nets["5V"].add("F1-2")
    else:
        nets["5V"].add("J3-1")
    for cap in ("C2", "C3"):  # W5500 用のパスコン（任意）
        if cap in parts:
            nets["3V3"].add(f"{cap}-1")
            nets["GND"].add(f"{cap}-2")
    if "R4" in parts:  # W5500 の RST を 3.3V へ（任意）
        nets["W5500 の RST"] = {*w("RST"), "R4-2"}
        nets["3V3"].add("R4-1")
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


def _rename_ref(parts, nets, old: str, new: str) -> None:
    parts[new] = parts.pop(old)
    for pins in nets.values():
        for p in [p for p in pins if p.split("-", 1)[0] == old]:
            pins.discard(p)
            pins.add(new + "-" + p.split("-", 1)[1])


def normalize(parts: dict[str, list[str]], nets: dict[str, set[str]]) -> list[str]:
    """記号の読み替えと、口のピンの並びの判断。表示するためのメモを返す。"""
    notes: list[str] = []
    # 「U3.1」→「U3」
    for ref in list(parts):
        base = re.sub(r"\.\d+$", "", ref)
        if base != ref and base not in parts:
            _rename_ref(parts, nets, ref, base)
    for pins in nets.values():
        for p in list(pins):
            ref, _, num = p.partition("-")
            base = re.sub(r"\.\d+$", "", ref)
            if base != ref:
                pins.discard(p)
                pins.add(f"{base}-{num}")
    # 別名の記号
    for canon, aliases in ALIASES.items():
        if canon in parts:
            continue
        for alias in aliases:
            if alias in parts:
                _rename_ref(parts, nets, alias, canon)
                notes.append(f"{alias} を {canon} として照合しました")
                break

    where = {p: name for name, pins in nets.items() for p in pins}

    def same_net(p, anchor):
        return p in where and where[p] == where.get(anchor)

    # 向きのない部品が逆向きに描かれていたら、1 番と 2 番を入れ替えて読む
    for ref, (num, anchor) in UNPOLARIZED.items():
        other = 3 - num
        if ref in parts and same_net(f"{ref}-{other}", anchor) and not same_net(f"{ref}-{num}", anchor):
            swap = {f"{ref}-1": f"{ref}-2", f"{ref}-2": f"{ref}-1"}
            for pins in nets.values():
                hit = [p for p in pins if p in swap]
                for p in hit:
                    pins.discard(p)
                pins.update(swap[p] for p in hit)
    where = {p: name for name, pins in nets.items() for p in pins}

    # R3（CS）と R4（RST）はどちらも 3.3V への 10k なので、番号が逆でも働きは同じ
    if "R3" in parts and "R4" in parts and same_net("R4-2", "U1-38") and not same_net("R3-2", "U1-38"):
        _rename_ref(parts, nets, "R3", "R3_")
        _rename_ref(parts, nets, "R4", "R3")
        _rename_ref(parts, nets, "R3_", "R4")
        notes.append("R4 を CS の、R3 を RST の引き上げとして照合しました（どちらも 10k なので逆でも問題ありません）")
        where = {p: name for name, pins in nets.items() for p in pins}

    # 口のピンの並び: どのピンが何かを、同じネットにある相手から決める
    anchors = {
        "J3": {"5V": "F1-1" if "F1" in parts else "U1-20", "GND": "U1-21"},
        "J4": {"GND": "U1-21", "5V": "U1-20", "信号": "U1-19"},
        "J5": {"GND": "U1-21", "5V": "U1-20", "信号": "U1-24"},
        "J6": {"−": "Q1-2"},
    }
    for ref, roles in CONNECTOR_ROLES.items():
        mine = sorted({p for p in where if p.split("-", 1)[0] == ref}, key=lambda p: p.split("-", 1)[1])
        if not mine:
            continue
        found: dict[str, str] = {}  # ピン → 役目
        for p in mine:
            for role, anchor in anchors[ref].items():
                if same_net(p, anchor) and role not in found.values():
                    found[p] = role
                    break
        if ref == "J6":  # − でないほうが＋
            rest = [p for p in mine if p not in found]
            if len(rest) == 1:
                found[rest[0]] = "＋"
        taken = {str(roles[r]) for r in found.values()}
        renamed = {}
        for p in mine:
            num = p.split("-", 1)[1]
            if p in found:
                renamed[p] = f"{ref}-{roles[found[p]]}"
            else:  # 役目が分からないピンは、番号がぶつからなければそのまま（ぶつかるなら「?」を付ける）
                renamed[p] = f"{ref}-{num}" if num not in taken else f"{ref}-?{num}"
        for pins in nets.values():
            for p in [p for p in pins if p in renamed]:
                pins.discard(p)
                pins.add(renamed[p])
        if found:
            order = "、".join(f"{p.split('-', 1)[1]} 番＝{found[p]}" for p in mine if p in found)
            notes.append(f"{ref}（{CONNECTOR_NAMES[ref]}）: {order}（基板の印字にこの並びを書く）")
    return notes


def analyze(parts: dict[str, list[str]], nets: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    """（問題点の一覧, メモ）。問題点が空なら接続表どおり。"""
    notes = normalize(parts, nets)
    problems = [f"部品 {ref} がありません" for ref in REQUIRED if ref not in parts]
    unknown = sorted(set(parts) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        problems.append(f"接続表にない部品があります: {', '.join(unknown)}（記号の付け間違いでなければ無視してよい）")

    info = " ".join(parts.get("W1", [])).upper()
    w5500 = W5500_LITE_PINS if "LITE" in info else W5500_MINI_PINS
    if "W1" in parts:
        notes.append(f"W5500 は「{'W5500-LITE' if w5500 is W5500_LITE_PINS else 'W5500MINI'}」の番号で照合しました")

    where = {p: name for name, pins in nets.items() for p in pins}
    laser_supply = "5V"
    if "JP1" not in parts and "J6-1" in where:
        if where["J6-1"] == where.get("U1-1"):
            laser_supply = "3V3"
        notes.append(f"レーザーの＋は {laser_supply.replace('3V3', '3.3V')} へ直接つながっています。"
                     "レーザーの定格電圧がこれと同じか確かめてください（高すぎると明るくなりすぎ、1mW を超えるおそれがある）")

    # 足の形（フットプリント）で気づいたこと
    for ref in ("J4", "J5"):
        if any("HDR-F" in x.upper() for x in parts.get(ref, [])):
            notes.append(f"{ref}（{CONNECTOR_NAMES[ref]}）の足の形がメスのピンヘッダー（HDR-F）です。"
                         "サーボのケーブルの先はメスなので、基板側はオスのピンヘッダー（HDR-M）にしないと挿せません")
    smd = sorted(ref for ref, info in parts.items() if ref[0] in "RC" and any(re.search(r"\b[RC]?0[46]0[23]\b", x) for x in info))
    if smd:
        notes.append(f"{', '.join(smd)} の足の形が小さな表面実装（0603 など、1.6×0.8mm）です。"
                     "手ではんだ付けするなら、穴に差す形（AXIAL-0.4 など）か、少し大きい 0805・1206 のほうが楽です")

    expected = expected_nets(set(parts), w5500, laser_supply)
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
    checked = {"U1": U1_PINS, "W1": w5500}
    expected_pins = set().union(*expected.values())
    for p, name in sorted(where.items()):
        ref, _, num = p.partition("-")
        if ref in checked and p not in expected_pins and len(nets[name]) > 1:
            signal = checked[ref].get(int(num), "?") if num.isdigit() else "?"
            problems.append(f"{p}（{signal}）はつながないピンですが、ネット {name} につながっています")
    # 役目の分からなかった口のピン
    for p in sorted(where):
        if "-?" in p:
            problems.append(f"{p.replace('-?', '-')} が何につながるピンか分かりません（5V・GND・信号のどれにもつながっていない）")
    return problems, notes


def check(parts: dict[str, list[str]], nets: dict[str, set[str]]) -> list[str]:
    """問題点の一覧（日本語）。空なら接続表どおり。"""
    return analyze(parts, nets)[0]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    parts, nets = parse_protel(Path(args[0]).read_text(encoding="utf-8", errors="replace"))
    if not parts and not nets:
        print("ネットリストを読めませんでした。EasyEDA の書き出しで「Protel」形式を選んだか確認してください。")
        return 2
    problems, notes = analyze(parts, nets)
    for n in notes:
        print(f"メモ: {n}")
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
