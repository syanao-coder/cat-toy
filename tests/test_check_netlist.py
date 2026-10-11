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
    nets["ETH_MOSI（IO42）"] = {"J2-6", "J7-6"}  # MOSI と MISO を取り違え
    nets["ETH_MISO（IO2）"] = {"J2-5", "J7-5"}
    problems = run(parts, nets)
    assert any("ETH_MOSI" in p for p in problems) and any("ETH_MISO" in p for p in problems)


def test_wrong_header_pin_is_caught():
    parts, nets = good()
    nets["PAN（IO14）"] = {"J1-19", "J4-3"}  # 隣のピン（カメラの IO13）につないでしまった
    problems = run(parts, nets)
    assert any("J1-19（IO13）" in p for p in problems)
    assert any("PAN（IO14）" in p and "J1-20" in p for p in problems)


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
