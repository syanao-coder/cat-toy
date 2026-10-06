"""コマンドライン: `cattoy <サブコマンド> [--config config.toml]`"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .config import Config, load_full_config


def _cmd_run(cfg: Config, args: argparse.Namespace) -> None:
    from .app import run

    run(cfg, args.web)


def _cmd_calibrate(cfg: Config, args: argparse.Namespace) -> None:
    from .calibrate import run_calibration

    print("レーザーが部屋中を照らします。猫と人がいないことを確認してください。")
    if not args.yes and input("開始しますか? [y/N] ").strip().lower() != "y":
        return
    run_calibration(cfg, args.web)


def _cmd_hw_test(cfg: Config, args: argparse.Namespace) -> None:
    """配線確認: レーザーの点滅と、サーボを可動範囲の端まで動かす。"""
    from .hardware import make_laser, make_pantilt

    laser = make_laser(cfg.laser, cfg.esp32)
    pantilt = make_pantilt(cfg.servo, cfg.esp32)
    s = cfg.servo
    try:
        link = getattr(pantilt, "link", None)
        if link is not None:
            st = link.status()
            if st is None:
                print(f"ESP32（{cfg.esp32.host}）から応答がありません。電源・Wi-Fi・IP アドレスを確認してください")
            else:
                print(f"ESP32 に接続しました: {st}")
        print("レーザーを 3 回点滅します")
        for _ in range(3):
            laser.on()
            time.sleep(0.3)
            laser.off()
            time.sleep(0.3)
        steps = [
            ("ホーム", s.pan_home, s.tilt_home),
            ("パン最小", s.pan_min, s.tilt_home),
            ("パン最大", s.pan_max, s.tilt_home),
            ("ホーム", s.pan_home, s.tilt_home),
            ("チルト最小", s.pan_home, s.tilt_min),
            ("チルト最大", s.pan_home, s.tilt_max),
            ("ホーム", s.pan_home, s.tilt_home),
        ]
        for name, pan, tilt in steps:
            print(f"{name}: pan={pan:.0f} tilt={tilt:.0f}")
            pantilt.move(pan, tilt)
            laser.set(args.laser)
            time.sleep(1.5)
    finally:
        laser.close()
        pantilt.close()


def _cmd_aim(cfg: Config, args: argparse.Namespace) -> None:
    """指定した角度・または画像上の位置を照らす（可動範囲の確認やキャリブレーションの検証用）。"""
    from .hardware import make_laser, make_pantilt

    if args.point:
        from .app import load_calibration

        cal = load_calibration(cfg, (cfg.camera.width, cfg.camera.height))
        x, y = args.point
        pan, tilt = cal.pixel_to_angles(x * cfg.camera.width, y * cfg.camera.height)
    elif args.angles:
        pan, tilt = args.angles
    else:
        pan, tilt = cfg.servo.pan_home, cfg.servo.tilt_home
    laser = make_laser(cfg.laser, cfg.esp32)
    pantilt = make_pantilt(cfg.servo, cfg.esp32)
    try:
        pantilt.move(pan, tilt)
        print(f"pan={pantilt.pan:.1f} tilt={pantilt.tilt:.1f} を {args.seconds:.0f} 秒照らします")
        time.sleep(0.5)
        laser.on()
        time.sleep(args.seconds)
    finally:
        laser.close()
        pantilt.close()


def _cmd_snapshot(cfg: Config, args: argparse.Namespace) -> None:
    """カメラ画像に 0.1 刻みの目盛りを描いて保存する（play_area の座標を読み取る用）。"""
    import cv2

    from .hardware import make_camera

    camera = make_camera(cfg.camera, cfg.esp32)
    try:
        img = camera.read_fresh().copy()
    finally:
        camera.close()
    h, w = img.shape[:2]
    for k in range(1, 10):
        x, y = int(w * k / 10), int(h * k / 10)
        cv2.line(img, (x, 0), (x, h), (200, 200, 200), 1)
        cv2.line(img, (0, y), (w, y), (200, 200, 200), 1)
        cv2.putText(img, f"{k / 10:.1f}", (x + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
        cv2.putText(img, f"{k / 10:.1f}", (2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
    cv2.imwrite(args.output, img)
    print(f"保存しました: {args.output}")


def _esp32_link(cfg: Config):
    from .esp32 import Esp32Link

    if not cfg.esp32.host:
        raise SystemExit("config.toml の [esp32] host に ESP32 の IP アドレスを書いてください")
    return Esp32Link(cfg.esp32)


def _print_status(st: dict | None) -> None:
    if st is None:
        print("ESP32 から応答がありません")
        return
    print(f"  ファームウェア {st.get('fw')}（{st.get('built')}・{st.get('partition')}）")
    print(f"  Wi-Fi {st.get('ssid')}  電波 {st.get('rssi')} dBm  IP {st.get('ip')}  MAC {st.get('mac', '?')}  起動から {st.get('uptime_s')} 秒")


def _cmd_esp32_status(cfg: Config, args: argparse.Namespace) -> None:
    link = _esp32_link(cfg)
    try:
        _print_status(link.status())
    finally:
        link.close()


def _cmd_esp32_update(cfg: Config, args: argparse.Namespace) -> None:
    """ファームウェアをネットワーク越しに書き換える（ESP32 を取り外さなくてよい）。"""
    from . import firmware

    data = Path(args.file).read_bytes()
    info = firmware.inspect_image(data, allow_foreign=args.force)
    link = _esp32_link(cfg)
    try:
        print("今の ESP32:")
        _print_status(link.status())
        print(f"書き込むファイル: {args.file}（版 {info['fw']}・{info['built']} にビルド・{info['size']:,} バイト）")
        print("送信中です。終わるまで電源を切らないでください…")
        firmware.upload_firmware(cfg.esp32, data, allow_foreign=args.force)
        print("書き換えました。再起動を待っています…")
        st = firmware.wait_until_back(link.status)
        if st is None:
            print("ESP32 が戻ってきません。1 分ほど待って `cattoy esp32-status` で確認してください。")
            print("（新しい版で Wi-Fi につながらなかった場合は、自動で前の版に戻ります）")
        else:
            print("再起動しました:")
            _print_status(st)
    finally:
        link.close()


def _cmd_esp32_reboot(cfg: Config, args: argparse.Namespace) -> None:
    from . import firmware

    link = _esp32_link(cfg)
    try:
        firmware.reboot(cfg.esp32)
        print("再起動しています…")
        _print_status(firmware.wait_until_back(link.status))
    finally:
        link.close()


def _cmd_esp32_wifi(cfg: Config, args: argparse.Namespace) -> None:
    """ESP32 がつなぐ Wi-Fi を変える（ルーターを替える前に、今の Wi-Fi につながっている間に行う）。"""
    import getpass

    from . import firmware

    password = args.password if args.password is not None else getpass.getpass("新しい Wi-Fi のパスワード: ")
    print(firmware.set_wifi(cfg.esp32, args.ssid, password))
    print("ESP32 は新しい Wi-Fi につなぎ直します。IP アドレスが変わったら config.toml の [esp32] host を直してください。")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="cattoy", description="猫を見つけてレーザーで遊ぶ自動おもちゃ")
    p.add_argument("-c", "--config", default="config.toml", help="設定ファイル（既定: config.toml）")
    p.add_argument("-v", "--verbose", action="store_true", help="詳細なログを出す")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("run", help="遊ばせる（本番）")
    sp.add_argument("--web", type=int, default=None, metavar="PORT", help="ブラウザでプレビューを見る（例: 8080）")
    sp.set_defaults(func=_cmd_run)

    sp = sub.add_parser("calibrate", help="カメラとサーボの対応付けを自動で測る")
    sp.add_argument("--web", type=int, default=None, metavar="PORT")
    sp.add_argument("-y", "--yes", action="store_true", help="確認せずに開始する")
    sp.set_defaults(func=_cmd_calibrate)

    sp = sub.add_parser("hw-test", help="レーザーとサーボの配線確認")
    sp.add_argument("--laser", action="store_true", help="サーボを動かす間レーザーを点灯する")
    sp.set_defaults(func=_cmd_hw_test)

    sp = sub.add_parser("aim", help="指定した場所を照らす")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--angles", type=float, nargs=2, metavar=("PAN", "TILT"), help="サーボ角度（度）")
    g.add_argument("--point", type=float, nargs=2, metavar=("X", "Y"), help="画像上の位置（0〜1）。要キャリブレーション")
    sp.add_argument("--seconds", type=float, default=5.0)
    sp.set_defaults(func=_cmd_aim)

    sp = sub.add_parser("snapshot", help="目盛り付きのカメラ画像を保存する")
    sp.add_argument("-o", "--output", default="snapshot.jpg")
    sp.set_defaults(func=_cmd_snapshot)

    sp = sub.add_parser("esp32-status", help="ESP32 の状態（ファームウェアの版・電波など）を見る")
    sp.set_defaults(func=_cmd_esp32_status)

    sp = sub.add_parser("esp32-update", help="ESP32 のファームウェアをネットワーク越しに書き換える")
    sp.add_argument("file", help="Arduino IDE で出力した cattoy_esp32.ino.bin")
    sp.add_argument("--force", action="store_true", help="cat-toy 以外のファームウェアも書き込む（遠隔で戻せなくなるので注意）")
    sp.set_defaults(func=_cmd_esp32_update)

    sp = sub.add_parser("esp32-reboot", help="ESP32 を再起動する")
    sp.set_defaults(func=_cmd_esp32_reboot)

    sp = sub.add_parser("esp32-wifi", help="ESP32 がつなぐ Wi-Fi を変える")
    sp.add_argument("ssid")
    sp.add_argument("--password", default=None, help="省略すると入力を求める")
    sp.set_defaults(func=_cmd_esp32_wifi)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not Path(args.config).exists():
        logging.info("%s がないため、既定値と環境変数の設定で動かします", args.config)
    cfg = load_full_config(args.config)
    args.func(cfg, args)


if __name__ == "__main__":
    main()
