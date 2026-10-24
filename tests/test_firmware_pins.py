"""ファームウェアのピン番号が、基板の接続表（pcb/check_netlist.py）と同じであること。"""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INO = ROOT / "firmware" / "cattoy_esp32" / "cattoy_esp32.ino"
spec = importlib.util.spec_from_file_location("check_netlist", ROOT / "pcb" / "check_netlist.py")
cn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cn)

# 接続表のネット名の頭 → ファームウェアの #define
NET_TO_DEFINE = {
    "PAN": "PAN_PIN",
    "TILT": "TILT_PIN",
    "レーザー信号": "LASER_PIN",
    "ETH_SCK": "ETH_SCK_PIN",
    "ETH_MOSI": "ETH_MOSI_PIN",
    "ETH_MISO": "ETH_MISO_PIN",
    "ETH_CS": "ETH_CS_PIN",
    "ETH_INT": "ETH_INT_PIN",
}


def defines() -> dict[str, int]:
    """#define の …_PIN（サーボ・レーザー・LAN など）と …_GPIO_NUM（カメラ）。"""
    text = INO.read_text(encoding="utf-8")
    return {m[1]: int(m[2]) for m in re.finditer(r"^#define (\w+_(?:PIN|GPIO_NUM)) (-?\d+)", text, re.M)}


def test_pins_match_pcb_netlist():
    pins = defines()
    gpio_of_net = {}
    for name in cn.expected_nets(set(cn.REQUIRED)):
        m = re.match(r"(.+)（IO(\d+)）$", name)
        if m:
            gpio_of_net[m[1]] = int(m[2])
    assert set(NET_TO_DEFINE) <= set(gpio_of_net)
    for net, define in NET_TO_DEFINE.items():
        assert pins[define] == gpio_of_net[net], f"{define} は IO{gpio_of_net[net]} のはず"


def test_no_pin_is_used_twice():
    """カメラ・サーボ・レーザー・有線 LAN などで、同じ GPIO を二重に使っていないこと（-1 は「使わない」）。"""
    used = [v for v in defines().values() if v >= 0]
    assert len(used) > 20  # カメラのピンも読めている
    assert len(used) == len(set(used))
