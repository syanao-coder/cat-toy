# NAS（QNAP Container Station）で動かす

猫の認識・遊び方の判断・スマホ用の操作画面を、NAS のコンテナで動かします。
例として QNAP TS-473A（QTS 5.2）＋ Quadro T400 で説明しますが、Docker が動く NAS や PC なら同じ手順で動きます。

## 1. 準備

- App Center で **Container Station** をインストールしておく。
- GPU を使う場合: コントロールパネル → ハードウェア → ハードウェアリソース で、GPU の「リソースの使用」を
  **Container Station モード** にして適用する。
- コントロールパネル → ネットワークとファイルサービス → Telnet / SSH で **SSH を有効** にする。
- GPU のドライバが CUDA 12.8 以上に対応していることを確認する（SSH で `nvidia-smi` を実行し、右上の
  「CUDA Version」が 12.8 以上なら OK。ドライバ 575 系なら 12.9 なので問題なし）。

## 2. ファイルを NAS に置く

GitHub の「Code → Download ZIP」でダウンロードし、File Station で共有フォルダ（例: `Container`）に展開します。
以下では `/share/Container/cat-toy` に置いたものとして説明します。

SSH でログインし、設定ファイルとモデル用のフォルダを作ります。

```bash
cd /share/Container/cat-toy
mkdir -p docker/data/models
cp config.example.toml docker/data/config.toml
vi docker/data/config.toml      # [esp32] host と key を書き換える（File Station のテキストエディタでも可）
```

## 3. 検出モデルの準備

COCO で学習済みの YOLO11n（猫と人を検出できる）を ONNX 形式に書き出します。
Ultralytics の公式コンテナを使えば NAS の上だけで作れます（初回はイメージのダウンロードに時間がかかります）。

```bash
cd /share/Container/cat-toy/docker/data/models
docker run --rm -v "$PWD":/models -w /models ultralytics/ultralytics:latest-cpu \
  yolo export model=yolo11n.pt format=onnx imgsz=320
ls    # yolo11n.onnx ができていれば OK
docker rmi ultralytics/ultralytics:latest-cpu    # 大きいので、終わったら消してよい
```

> Ultralytics のモデルは AGPL-3.0 ライセンスです。個人で使う分には問題ありません。

## 4. コンテナを作る

```bash
cd /share/Container/cat-toy
docker compose -f docker/compose.yml build
```

GPU を使わない場合は、`docker/compose.yml` の `dockerfile:` を `docker/Dockerfile.cpu` にし、
`deploy:` の `reservations:` 以下を消してください。

## 5. 調整（ESP32 を組み立てて書き込んだ後）

すべて SSH で `cd /share/Container/cat-toy` してから実行します。
後からやり直すときは、先に `docker compose -f docker/compose.yml stop` で本番のコンテナを止めてください
（2 つのプログラムが同時に ESP32 へ指令を送らないようにするため）。

1. **配線の確認**: レーザーが点滅し、サーボが可動範囲の端まで動きます。
   ```bash
   docker compose -f docker/compose.yml run --rm cattoy hw-test --laser
   ```
   好きな角度を照らして、床の遊ばせたい範囲だけに当たるように `config.toml` の `pan_min`〜`tilt_max` を狭めます。
   ```bash
   docker compose -f docker/compose.yml run --rm cattoy aim --angles 90 60
   ```

2. **キャリブレーション**（猫と人がいない状態で。約 2〜3 分）
   ```bash
   docker compose -f docker/compose.yml run --rm -p 8091:8091 cattoy calibrate -y --web 8091
   ```
   `http://<NAS の IP>:8091/` で様子を見られます。終わると `docker/data/calibration.json` と確認用の
   `calibration.jpg` ができ、誤差（目安: 3px 以下）と映像の遅れが表示されます。

3. **起動**
   ```bash
   docker compose -f docker/compose.yml up -d
   docker compose -f docker/compose.yml logs -f      # ログを見る（Ctrl+C で抜ける）
   ```
   ログに `検出モデルを読み込みました（GPU で実行）` と出れば GPU で認識しています。
   NAS を再起動しても自動で立ち上がります（`restart: unless-stopped`）。

## 6. スマホから使う

スマホのブラウザで `http://<NAS の IP>:8090/` を開きます。

- **iPhone（Safari）**: 共有ボタン → 「ホーム画面に追加」
- **Android（Chrome）**: ︙ メニュー → 「ホーム画面に追加」

アイコンから開くと、アプリのように使えます。

| 画面の項目 | 内容 |
| --- | --- |
| ON / OFF ボタン | 遊ばせるかどうか。OFF の間は映像の受信も認識も止まる（NAS の負荷はほぼゼロ） |
| 状態 | 猫を待っています／遊んでいます／休憩中（あと N 分）／動作時間外 |
| 今日遊んだ時間 | 直近 7 日のグラフ付き |
| ライブ映像 | 見ている間だけ配信される。タップすると遊ぶ範囲の座標を記録できる |

ESP32 の基板の BOOT ボタンでも ON/OFF を切り替えられます。
家の LAN の外からは使えません（外出先から使いたい場合は QNAP の VPN 機能で家の LAN に入ってから開いてください）。

## NAS の負荷について

| 状態 | 動き |
| --- | --- |
| OFF・動作時間外・休憩中 | 映像の受信も認識もしない |
| ON で猫がいない | 1 秒に 1 枚だけ静止画を取って認識する（`standby_interval_s`） |
| 猫がいる・遊んでいる | 連続で認識する（GPU なら 1 回数ミリ秒程度の見込み） |

念のため `docker/compose.yml` でコンテナの CPU を 2 コア分・メモリを 2GB までに制限しています。

## 更新するとき

**NAS 側のプログラム**: 新しいファイルで上書きしてから（`docker/data` は消さないこと）、作り直します。

```bash
docker compose -f docker/compose.yml up -d --build
```

**壁の ESP32**: 取り外さずにネットワーク越しに書き換えられます。操作画面の「メンテナンス（ESP32）」から
`cattoy_esp32.ino.bin` を選ぶか、次のコマンドで行います（詳しくは [firmware/README.md](../firmware/README.md)）。

```bash
cp ~/cattoy_esp32.ino.bin docker/data/
docker compose -f docker/compose.yml run --rm cattoy esp32-update /data/cattoy_esp32.ino.bin
docker compose -f docker/compose.yml run --rm cattoy esp32-status     # 版を確認
```

本番のコンテナは動かしたままで構いません（書き換え中は ESP32 が指令を受け付けず、再起動後に自動でつなぎ直します）。

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| ログに `CPU で実行` と出る | GPU が「Container Station モード」になっているか。`docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi` で GPU が見えるか |
| `ESP32 の映像を受信できません` | `config.toml` の `[esp32] host`。ブラウザで `http://<ESP32 の IP>:81/stream` が映るか |
| 操作画面に「ESP32 とつながっていません」 | ESP32 の電源・Wi-Fi。IP アドレスが変わっていないか |
| `calibration.json がありません` | 先に手順 5-2 のキャリブレーションを行う |
| `docker compose` が見つからない | Container Station が古い場合は `docker-compose`（ハイフンあり）で同じように実行する |
