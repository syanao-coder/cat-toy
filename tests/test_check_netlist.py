import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_netlist", ROOT / "pcb" / "check_netlist.py")
cn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cn)


def make_netlist(parts, nets):
    """EasyEDA が書き出す Protel 形式と同じ形の文字列を作る。"""
    out = []
    for ref in parts:
        out += ["[", ref, "FOOTPRINT", "VALUE", "]"]
    for name, pins in nets.items():
        out += ["(", name, *sorted(pins), ")"]
    return "\r\n".join(out) + "\r\n"


def good(parts=None):
    parts = list(parts or cn.REQUIRED)
    return parts, {name: set(pins) for name, pins in cn.expected_nets(set(parts)).items()}


def run(parts, nets):
    return cn.check(*cn.parse_protel(make_netlist(parts, nets)))


def test_correct_netlist_passes():
    assert run(*good()) == []
    assert run(*good(cn.REQUIRED + cn.OPTIONAL)) == []  # ヒューズ・パスコン・ボタン付き


def test_parse_protel_format():
    text = "[\r\nJ1\r\nHDR-F-2.54_1X22\r\n\r\n]\r\n(\r\nGND\r\nJ1-22\r\nC1-2\r\n)\r\n"
    parts, nets = cn.parse_protel(text)
    assert parts == {"J1": ["HDR-F-2.54_1X22"]}
    assert nets == {"GND": {"J1-22", "C1-2"}}


def test_swapped_spi_lines_are_caught():
    parts, nets = good()
    nets["ETH_MOSI（IO42）"] = {"U1-36", "W1-7"}  # MOSI と MISO を取り違え
    nets["ETH_MISO（IO41）"] = {"U1-35", "W1-3"}
    problems = run(parts, nets)
    assert any("ETH_MOSI" in p for p in problems) and any("ETH_MISO" in p for p in problems)


def test_wrong_header_pin_is_caught():
    parts, nets = good()
    nets["PAN（IO14）"] = {"U1-18", "J4-3"}  # 隣のピン（カメラの IO13）につないでしまった
    problems = run(parts, nets)
    assert any("U1-18（IO13）" in p for p in problems)
    assert any("PAN（IO14）" in p and "U1-19" in p for p in problems)


def test_w5500_unused_pin_is_caught():
    parts, nets = good()
    nets["3V3"].add("W1-9")  # W5500 の NC ピンに 3.3V をつないでしまった
    assert any("W1-9（NC）" in p for p in run(parts, nets))


def test_easyeda_part_numbers():
    """EasyEDA の部品の番号（右列は上から 40〜21）で、主なピンが正しい所にあること。"""
    assert {n: cn.U1_PINS[n] for n in (1, 12, 19, 20, 21, 24, 25, 28, 35, 36, 37, 38)} == {
        1: "3V3", 12: "IO3", 19: "IO14", 20: "5V", 21: "GND", 24: "IO21", 25: "IO47",
        28: "IO0", 35: "IO41", 36: "IO42", 37: "IO2", 38: "IO1"}


def test_transistor_named_u2_is_accepted():
    parts, nets = good()
    parts[parts.index("Q1")] = "U2"
    for pins in nets.values():
        for p in [p for p in pins if p.startswith("Q1-")]:
            pins.discard(p)
            pins.add("U2-" + p.split("-")[1])
    assert run(parts, nets) == []


def test_short_between_nets_is_caught():
    parts, nets = good()
    nets["5V"] |= nets.pop("GND")
    assert any("つながってしまっています" in p for p in run(parts, nets))


def test_split_net_is_caught():
    parts, nets = good()
    nets["5V"].discard("J4-2")
    nets["5V_SERVO"] = {"J4-2", "J5-2"}
    nets["5V"].discard("J5-2")
    assert any("別々のネット" in p for p in run(parts, nets))


def test_missing_part_and_fuse_wiring():
    parts, nets = good()
    parts.remove("R2")
    for pins in nets.values():
        pins -= {"R2-1", "R2-2"}
    assert any("部品 R2" in p for p in run(parts, nets))
    # F1 を置いたのに、端子台から直接 5V へつないでいる
    parts, nets = good(cn.REQUIRED + ["F1"])
    nets["5V"] |= {"J3-1", "F1-1"}
    nets.pop("5V 入力（ヒューズの前）")
    assert run(parts, nets)


def test_cli(tmp_path, capsys):
    f = tmp_path / "board.net"
    f.write_text(make_netlist(*good()), encoding="utf-8")
    assert cn.main([str(f)]) == 0
    assert "接続表どおり" in capsys.readouterr().out
    f.write_text("not a netlist", encoding="utf-8")
    assert cn.main([str(f)]) == 2


def user_schematic(tweak=None):
    """届いた回路図（2026-10-22 のスクリーンショット）と同じ記号・番号のネットリスト。"""
    parts = [p for p in cn.REQUIRED]  # JP1 なし、レーザーの＋は 5V へ直接
    nets = {name: set(pins) for name, pins in cn.expected_nets(set(parts)).items()}
    mini_to_lite = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 12, 8: 11, 9: 10, 10: 8, 11: 9, 12: 7}
    rename = {f"W1-{m}": f"U3-{l}" for m, l in mini_to_lite.items()}
    rename |= {f"Q1-{n}": f"U2-{n}" for n in (1, 2, 3)}
    rename |= {"J6-1": "H1-1", "J6-2": "H1-2"}  # VCC・GND の順
    rename |= {"J4-1": "J4-3", "J4-2": "J4-2", "J4-3": "J4-1"}  # PWM・VCC・GND の順
    rename |= {"J5-1": "J5-3", "J5-2": "J5-2", "J5-3": "J5-1"}
    rename |= {"J3-1": "J3-2", "J3-2": "J3-1"}  # GND・5V の順
    nets = {name: {rename.get(p, p) for p in pins} for name, pins in nets.items()}
    parts = [{"Q1": "U2", "W1": "U3", "J6": "H1"}.get(p, p) for p in parts]
    if tweak:
        tweak(nets)
    text = make_netlist(parts, nets).replace("[\r\nU3\r\nFOOTPRINT\r\nVALUE", "[\r\nU3\r\nW5500-LITE\r\nW5500-LITE")
    return cn.parse_protel(text)


def test_user_schematic_passes_with_notes():
    problems, notes = cn.analyze(*user_schematic())
    assert problems == []
    joined = "\n".join(notes)
    assert "W5500-LITE" in joined and "U2 を Q1" in joined and "H1 を J6" in joined
    assert "J4（パン用サーボ）: 1 番＝信号、2 番＝5V、3 番＝GND" in joined
    assert "J3（5V 入力）: 1 番＝GND、2 番＝5V" in joined
    assert "レーザーの＋は 5V" in joined


def test_user_schematic_earlier_mistakes_are_caught():
    def unwire(nets):
        for pins in nets.values():
            pins.discard("U3-7")  # W5500 の 7 番（GND）のつなぎ忘れ
            pins.discard("U3-9")  # 9 番（3.3V）の線が届いていない
    problems = cn.check(*user_schematic(unwire))
    assert any("W1-7" in p for p in problems) and any("W1-9" in p for p in problems)


def test_laser_on_3v3_and_rst_pullup():
    parts, nets = good(cn.REQUIRED + ["R4"])
    nets["5V"].discard("J6-1")
    nets["3V3"].add("J6-1")
    problems, notes = cn.analyze(*cn.parse_protel(make_netlist(parts, nets)))
    assert problems == [] and any("3.3V" in n for n in notes)


REAL = ROOT / "tests" / "data" / "sheet1_2026-10-22.net"


def test_real_easyeda_netlist_passes():
    """EasyEDA Std が書き出した実物のネットリスト（R2・R3・R4 は逆向きに描かれている）。"""
    problems, notes = cn.analyze(*cn.parse_protel(REAL.read_text(encoding="utf-8")))
    assert problems == []
    joined = "\n".join(notes)
    assert "J4（パン用サーボ）: 1 番＝信号、2 番＝5V、3 番＝GND" in joined
    assert "HDR-F" in joined and "0603" in joined  # 足の形の注意


def test_real_netlist_with_mistakes_is_caught():
    parts, nets = cn.parse_protel(REAL.read_text(encoding="utf-8"))
    nets["U1_36"].discard("U3-3")  # MOSI を SCLK へつないでしまった
    nets["U1_37"].add("U3-3")
    nets["GND"].discard("R2-1")  # R2 を GND でなく 5V へ
    nets["U1_20"].add("R2-1")
    problems = cn.check(parts, nets)
    assert any("ETH_MOSI" in p for p in problems)
    assert any("GND" in p and "R2" in p for p in problems)


REAL_V2 = ROOT / "tests" / "data" / "sheet1_2026-10-22_v2.net"


def test_real_netlist_v2_with_r3_r4_swapped_passes():
    """2 回目のネットリスト: サーボの口をオスに、抵抗をリード付きに直し、R3 と R4 の番号が入れ替わっている。"""
    problems, notes = cn.analyze(*cn.parse_protel(REAL_V2.read_text(encoding="utf-8")))
    assert problems == []
    joined = "\n".join(notes)
    assert "R4 を CS" in joined
    assert "HDR-F" not in joined and "0603" not in joined  # 足の形の注意は消えた
