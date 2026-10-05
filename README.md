# cat-toy — 猫を見つけて、その前方をレーザーで照らす自動おもちゃ

壁の高い位置や天井付近に取り付けたカメラで猫を見つけ、パン・チルトのサーボで動かすレーザーを
**猫の少し前方**に照らして遊ばせます。Raspberry Pi 1 台で完結し、ネットワークやクラウドは不要です（スタンドアロン動作）。

```
 カメラ ──► Raspberry Pi ───────────────────────────────► PCA9685 ──► パン・チルト ──► レーザー
            ① YOLO で猫・人を検出（約 10 fps）
            ② 猫の位置と速度を推定（検出の合間も外挿）
            ③ 遊び方を決める（50 Hz）: 走る猫の前を逃げる / 止まった猫の前でちょろちょろ動く / …
            ④ 画像上の狙いをサーボの角度に変換（自動キャリブレーションで求めた対応表）
```

## 特徴

- **猫の前方を狙う**: 猫の速度から進む向きを推定し、その先に光点を置く。止まっている猫には、最後に向いていた方向の少し先で誘う。
- **獲物らしい動き**: 前を逃げる・素早く逃げて止まる・ちょろちょろ動く・一瞬隠れて別の場所に現れる、を組み合わせる。
  飛びかかってきたら基本は逃げるが、ときどきわざと捕まえさせる。
- **安全装置**: 猫の体の周り（目を含む）には照射しない／人が写っている間は消灯／遊ぶ範囲を床の指定範囲に限定／
  1 回の遊びは 10 分まで → 30 分休憩／動作時間帯の指定／連続点灯時間の上限。
- **自動キャリブレーション**: レーザーを格子状に点滅させてカメラで位置を測り、「画像の位置 → サーボ角度」を自動で求める。
  取り付けの向きや精度は問わない。
- **ブラウザでプレビュー**: スマホや PC から検出結果と狙いの位置を確認でき、画像をクリックして遊ぶ範囲を設定できる。

## ⚠️ 安全について（必ずお読みください）

- **レーザーは出力 1mW 以下（Class 2 以下）のものを使ってください。** 電子工作用のレーザーモジュールは 5mW 品が多く危険です。
- ソフトウェアで猫の体の周りを避けていますが、検出漏れや遅れはゼロにはできません。
  照射範囲（サーボの可動範囲と `play_area`）は**床だけ**に絞り、人や猫の目の高さに届かないようにしてください。
- 最初の数日は必ず人が見ている所で動かし、猫の反応と動作を確認してから無人運転にしてください。
- 鏡・窓・ガラスなど反射する物に当たらないように設置してください。
- レーザー遊びは「捕まえられない」ことで猫がストレスを溜めることがあると言われます。
  `finish_point` に本物のおもちゃやおやつを置き、最後はそこで「捕まえて」終われるようにするのがおすすめです。

## 必要なもの

詳しくは **[docs/hardware.md](docs/hardware.md)**（部品・配線図・設置方法）を参照してください。

- Raspberry Pi 5（Pi 4 でも可）／ Camera Module 3 Wide
- パン・チルト台 ＋ サーボ MG90S ×2 ／ サーボドライバ PCA9685
- レーザーモジュール（1mW 以下）＋ トランジスタ 2SC1815 ＋ 1kΩ
- Pi 用電源 ＋ サーボ用 5V 2A 電源

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

以降のコマンドは `cat-toy` ディレクトリで実行します（`.venv/bin/cattoy` を `cattoy` と略記）。

### 検出モデルの準備

COCO で学習済みの YOLO（猫と人を検出できる）を ONNX 形式に書き出して使います。PC でも Pi でも作れます。

```bash
pip install ultralytics
yolo export model=yolo11n.pt format=onnx imgsz=320
# できた yolo11n.onnx を cat-toy/models/ に置く
```

> Ultralytics のモデルは AGPL-3.0 ライセンスです。個人で使う分には問題ありません。

## 初回の調整手順

1. **配線の確認**
   ```bash
   cattoy hw-test --laser
   ```
   レーザーが点滅し、サーボが可動範囲の端まで動きます。`cattoy aim --angles 90 60` で好きな角度を照らせるので、
   **レーザーが床の遊ばせたい範囲だけに当たるように** `config.toml` の `pan_min` 〜 `tilt_max` を狭めてください。

2. **キャリブレーション**（猫と人がいない状態で。約 2 分）
   ```bash
   cattoy calibrate --web 8080
   ```
   レーザーを 81 か所で点滅させ、カメラで位置を測ります。終わると `calibration.json` と確認用の `calibration.jpg` ができ、
   誤差が表示されます（目安: 3px 以下）。部屋を明るさの変わらない状態にしておくと失敗しにくいです。

3. **確認**
   ```bash
   cattoy aim --point 0.5 0.7     # 画像の横 50%・縦 70% の位置を照らす
   ```

4. **遊ぶ範囲の設定（任意）**
   ```bash
   cattoy run --web 8080
   ```
   スマホや PC のブラウザで `http://<ラズパイの名前>.local:8080/` を開くと、カメラ映像に検出結果
   （緑=猫、赤=人、黄=照射禁止範囲、赤丸=レーザーの狙い）が重ねて表示されます。
   画像をクリックすると座標が表示されるので、`config.toml` の `play_area` / `finish_point` に貼り付けます。
   指定しない場合は、キャリブレーションでレーザーが写った範囲を少し狭めたものを使います。

5. **自動起動**
   ```bash
   # systemd/cattoy.service の User とパスを自分の環境に合わせてから
   sudo cp systemd/cattoy.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now cattoy
   journalctl -u cattoy -f      # ログを見る
   ```
   電源を入れるだけで起動し、猫が来ると遊び始めます。

## 遊び方の調整

`config.toml` の `[play]` で変えられます（すべての項目と既定値は [cattoy/config.py](cattoy/config.py)）。
距離と速さは**猫の体長を 1 とした倍率**なので、設置の高さやカメラの画角が変わってもそのまま使えます。

| 項目 | 既定値 | 内容 |
| --- | --- | --- |
| `session_max_s` / `cooldown_s` | 600 / 1800 | 1 回の遊びの長さと、その後の休憩（秒） |
| `active_hours` | `"07:00-23:00"` | 動作する時間帯（`""` で常時） |
| `start_delay_s` | 1.5 | 猫がこの秒数見え続けたら開始（誤検出対策） |
| `keepout_margin` | 0.35 | 猫の外接矩形をこれだけ広げた範囲には照射しない |
| `lead_distance` | 1.6 | 走る猫のどれだけ前に置くか |
| `lure_distance` | 2.0 | 止まった猫のどれだけ前で誘うか |
| `creep_speed` / `move_speed` / `dart_speed` | 0.8 / 2.5 / 7.0 | ゆっくり／ふつう／素早い動きの速さ（体長/秒） |
| `flee_probability` | 0.7 | 飛びかかられたとき逃げる確率（残りはわざと捕まえさせる） |
| `person_safety` | true | 人が写っている間は消灯 |
| `play_area` / `finish_point` | 自動 / なし | 遊ぶ範囲（多角形）と、終わりに誘導する場所 |

## 実機なしで試す（PC）

USB カメラや猫の動画ファイルを使って、検出と遊び方の判断をブラウザで確認できます（サーボとレーザーは動かしません）。

```bash
pip install -e '.[desktop]'
cat > config.toml <<'EOF'
[camera]
backend = "opencv"
device = 0              # 動画ファイルなら "cat.mp4"
[servo]
backend = "mock"
[laser]
backend = "mock"
[play]
active_hours = ""
EOF
cattoy run --web 8080      # http://localhost:8080/ を開く
```

## 開発

```bash
pip install -e '.[dev]'
pytest
```

テストでは、レーザー点を追いかける仮想の猫を使って「点灯中は必ず猫から離れた位置・遊ぶ範囲の中を照らしている」ことや、
仮想の部屋（壁掛けのパン・チルト＋広角カメラ）で自動キャリブレーションの精度を確認しています。

| ファイル | 役割 |
| --- | --- |
| `cattoy/app.py` | 本体ループ（映像スレッド＋50 Hz の制御ループ） |
| `cattoy/behavior.py` | 遊び方のステートマシン（狙いの位置・点灯の判断、安全装置） |
| `cattoy/tracker.py` | 猫の追跡（位置・速度の推定と外挿） |
| `cattoy/detector.py` | YOLO（ONNX）による猫・人の検出 |
| `cattoy/calibration.py` | 画像座標 ⇔ サーボ角度の対応付け、レーザー点の検出 |
| `cattoy/calibrate.py` | 自動キャリブレーションの手順 |
| `cattoy/hardware.py` | カメラ・サーボ・レーザーの制御（実機用とモック） |
| `cattoy/preview.py` | ブラウザ用のライブプレビュー |
| `cattoy/cli.py` | コマンド（run / calibrate / hw-test / aim / snapshot） |

## 今後の拡張案

- **Raspberry Pi AI Camera（IMX500）対応**: カメラ側で検出するので、Pi Zero 2 W でも動かせる見込み。
- **閉ループ補正**: 遊んでいる最中もレーザー点をカメラで確かめ、狙いのずれを補正する。
- **遊んだ記録**: 1 日に何分遊んだかをグラフにする、スマホに通知する。
- **多頭飼い対応**: 現在は 1 匹を追いかける（2 匹以上いるときは直前に追っていた猫を優先）。
