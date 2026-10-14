import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pin_order_matches_netlist_checker():
    wd = load("wiring_diagram", "docs/images/wiring_diagram.py")
    cn = load("check_netlist", "pcb/check_netlist.py")
    alias = {"TX": "IO43", "RX": "IO44"}
    assert [alias.get(n, n) for n in wd.LEFT] == cn.HEADER_LEFT
    assert [alias.get(n, n) for n in wd.RIGHT] == cn.HEADER_RIGHT


def test_committed_svg_is_up_to_date():
    wd = load("wiring_diagram", "docs/images/wiring_diagram.py")
    committed = (ROOT / "docs/images/wiring.svg").read_text(encoding="utf-8")
    assert committed == wd.render(), "python docs/images/wiring_diagram.py で作り直してください"
