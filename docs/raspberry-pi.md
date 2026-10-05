# Raspberry Pi 1 台で動かす場合（別構成）

標準の構成は「NAS のコンテナ ＋ カメラ付き ESP32」です（[README](../README.md)）。
NAS がなく Raspberry Pi で完結させたい場合はこちらの構成でも動きます。ソフトは共通で、設定の `backend` を切り替えるだけです。

## 部品

| 部品 | おすすめ | メモ |
| --- | --- | --- |
| 本体 | Raspberry Pi 5（4GB 以上） | Pi 4 でも動くが検出が遅くなる（3〜5 fps 程度の見込み） |
| カメラ | Raspberry Pi Camera Module 3 **Wide**（画角 120°） | USB カメラでも可 |
| パン・チルト台＋サーボ ×2 | SG90 / MG90S 用 2 軸ブラケット＋ **MG90S（180°）** | |
| サーボドライバ | PCA9685 16ch（I2C） | GPIO 直結も可（ジッタが出やすい） |
| レーザー | 赤色 650nm **出力 1mW 以下（Class 2 以下）** | [hardware.md](hardware.md) の「レーザーの選び方」参照 |
| トランジスタ | 2SC1815 ＋ 1kΩ | |
| 電源 | Pi 5 用 USB-C 電源（5V 5A）＋ サーボ用 5V 2A 電源 | サーボは別電源にする |

## 配線

```
  Raspberry Pi                PCA9685
  3.3V   (1) ───────────────  VCC
  GPIO2  (3) ───────────────  SDA
  GPIO3  (5) ───────────────  SCL
  GND    (6) ──────┬────────  GND
                   │          V+  ◄──── サーボ用 5V 電源 (+)
                   └──────────────────── サーボ用 5V 電源 (−)   ※GND は必ず共通に
                              ch0 ────── パン用サーボ（左右）
                              ch1 ────── チルト用サーボ（上下）

  GPIO17 (11) ── 1kΩ ──── B ┐
                            │  2SC1815（NPN）
  レーザー (−) ─────────── C ┤  ※足は平らな面を正面にして左から E・C・B
  GND     (9) ──────────── E ┘
  レーザー (+) ── 5V（Pi の 5V ピン (2)、またはモジュールの定格電圧）
```

- 括弧内の数字は物理ピン番号です。PCA9685 の **VCC は 3.3V**、**V+ はサーボ用の 5V** です。
- PCA9685 を使わない場合は、サーボの信号線を GPIO12（ピン 32）・GPIO13（ピン 33）につなぎ `[servo] backend = "gpio"` にします。

## セットアップ（Raspberry Pi OS Bookworm）

```bash
sudo apt update
sudo apt install -y git python3-venv python3-opencv python3-picamera2 python3-gpiozero i2c-tools
sudo raspi-config nonint do_i2c 0        # I2C を有効化（PCA9685 用）

git clone https://github.com/syanao-coder/cat-toy.git
cd cat-toy
python3 -m venv --system-site-packages .venv   # apt で入れた picamera2 / OpenCV を使うため
.venv/bin/pip install -e '.[pi]'
cp config.example.toml config.toml
```

`config.toml` の `backend` を Raspberry Pi 用にします。

```toml
[camera]
backend = "picamera2"
[servo]
backend = "pca9685"
[laser]
backend = "gpio"
gpio = 17
```

検出モデルは [docs/nas.md](nas.md) の「検出モデルの準備」と同じ方法で作り、`models/yolo11n.onnx` に置きます。
その後の手順（hw-test → calibrate → run）は README と同じです。コマンドは `.venv/bin/cattoy` で実行します。

### 自動起動

```bash
# systemd/cattoy.service の User とパスを自分の環境に合わせてから
sudo cp systemd/cattoy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cattoy
journalctl -u cattoy -f      # ログを見る
```
