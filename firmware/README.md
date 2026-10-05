# ESP32 ファームウェア（cattoy_esp32）

カメラ付き ESP32-S3 に書き込むプログラムです。ESP32 は「映像を送る」「サーボとレーザーを動かす」だけを行い、
猫の認識や遊び方の判断は NAS 側が行います。

| 機能 | 内容 |
| --- | --- |
| 映像 | `http://<IP>:81/stream`（MJPEG、640×480）／ `http://<IP>/capture`（静止画） |
| 状態 | `http://<IP>/status`（JSON。電波強度・ボタンが押された回数など） |
| 指令 | UDP 4210 番ポート（NAS から 0.1 秒ごとに届く） |
| 安全装置 | 指令が 0.3 秒途絶えたらレーザーを消し、3 秒途絶えたらサーボを止める |
| 遠隔メンテナンス | ネットワーク越しのファームウェア書き換え・再起動・Wi-Fi 設定の変更（下記） |
| ボタン | 基板の BOOT ボタンで ON/OFF を切り替え |
| LED | 赤=レーザー点灯中／緑=NAS とつながっている／黄=NAS からの指令なし／青点滅=Wi-Fi 接続中／白=書き換え中／紫=設定用アクセスポイント |

## 書き込み手順（Arduino IDE）

1. [Arduino IDE](https://www.arduino.cc/en/software) をインストールする。
2. 「ボードマネージャ」で **esp32（by Espressif Systems）の 3.x** をインストールする。
   見つからない場合は、環境設定の「追加のボードマネージャの URL」に
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json` を追加する。
3. `firmware/cattoy_esp32/secrets.example.h` を同じフォルダに `secrets.h` としてコピーし、
   Wi-Fi の SSID・パスワード（**2.4GHz 帯**）と `CATTOY_KEY` を書き換える。
4. `firmware/cattoy_esp32/cattoy_esp32.ino` を開き、「ツール」メニューを次のように設定する。

   | 項目 | 設定 |
   | --- | --- |
   | ボード | ESP32S3 Dev Module |
   | Flash Size | 16MB (128Mb) |
   | PSRAM | **OPI PSRAM** |
   | Partition Scheme | **16M Flash (3MB APP/9.9MB FATFS)**（ネットワーク越しの書き換えに必要。「No OTA」と付くものは選ばない） |
   | USB CDC On Boot | Disabled（「COM」側の USB-C で書き込む場合） |

5. ボードの USB-C（2 つあるうち、USB-シリアル変換チップにつながっている「COM」/「UART」側）を PC につなぎ、書き込む。
   書き込みが始まらない場合は、BOOT ボタンを押したまま RST ボタンを押して離し、もう一度書き込む。
6. シリアルモニタ（115200bps）を開いて RST を押すと、次のように表示される。
   ```
   cat-toy firmware 0.2.0
   Wi-Fi に接続中: xxxx....
   接続しました。IP アドレス: 192.168.1.50（NAS の config.toml の [esp32] host に書く）
   ```

初回だけは USB で書き込みます。**2 回目からは取り外さずにネットワーク越しに書き換えられます**（下記）。

## 動作確認

- PC やスマホのブラウザで `http://<IP>:81/stream` を開き、映像が映ることを確認する。
- `http://<IP>/status` を開くと状態が JSON で表示される。
- **IP アドレスは変わらないように固定してください。** ルーターの設定で、この ESP32 の MAC アドレスに
  決まった IP を割り当てる（「DHCP 固定割り当て」など）のが簡単です。

## 設置した後の更新（取り外さなくてよい）

### ファームウェアを書き換える

1. Arduino IDE で「スケッチ → コンパイルしたバイナリを出力」を実行する。
   スケッチのフォルダの `build/esp32.esp32.esp32s3/` に `cattoy_esp32.ino.bin` ができる
   （`merged.bin` や `bootloader.bin` ではなく、この `.ino.bin` を使う）。
2. 次のどれかで送る。
   - **操作画面**: PC やスマホで `http://<NAS の IP>:8090/` を開き、「メンテナンス（ESP32）」から `.ino.bin` を選んで書き換える。
   - **NAS のコマンド**: ファイルを `docker/data/` に置いてから
     `docker compose -f docker/compose.yml run --rm cattoy esp32-update /data/cattoy_esp32.ino.bin`
   - **Arduino IDE から直接**: 「ツール → ポート」に出るネットワークポート（`cattoy at 192.168.x.x`）を選んで書き込む
     （パスワードは `CATTOY_KEY`）。
3. 書き換え中はレーザーとサーボが止まり（LED が白）、終わると自動で再起動します。遊んでいる最中でも安全に書き換えられます。

**失敗しても元に戻ります。**
- 送ったファイルが壊れていた・途中で途切れた場合は、書き込みの最後の照合（MD5）で失敗し、今の版のまま動き続けます。
- ESP32-S3 用でないファイルや `merged.bin` は、送る前に NAS 側で受け付けません。
- 新しい版が 3 回起動しても確定できない（起動の途中で再起動を繰り返す）場合や、1 分以内に Wi-Fi につながらない場合は、
  自動で前の版に戻ります（Wi-Fi につながった時点で「この版で確定」になります）。
  この仕組みはファームウェア自身が持っているので、このリポジトリのファームウェア（0.2.0 以降）同士の書き換えで働きます。

> `CATTOY_KEY` を空にしていると、家の LAN の誰でも書き換えられてしまいます。必ず設定してください。

### 再起動する

操作画面の「ESP32 を再起動」、または `cattoy esp32-reboot`。

### Wi-Fi を変える（ルーターやパスワードを変えるとき）

- **変える前に**（今の Wi-Fi につながっている間に）: `cattoy esp32-wifi <新しいSSID>` を実行してパスワードを入力する。
  ESP32 が再起動して新しい Wi-Fi につなぎ直します。
- **変えてしまった後**: ESP32 は 3 分つながらないと、設定用のアクセスポイント **`cattoy-setup`** を出します（LED が紫）。
  スマホでこの Wi-Fi につなぎ（パスワードは `CATTOY_KEY`。8 文字未満なら `cattoysetup`）、`http://192.168.4.1/` を開いて
  新しい SSID・パスワードを入力します。10 分間何もしないと再起動して、もう一度元の Wi-Fi につなぎに行きます。

### 状態を見る

`cattoy esp32-status`（ファームウェアの版・ビルド日時・使っている領域・電波強度など）。操作画面の「メンテナンス」にも出ます。

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| LED が赤く点いてすぐ再起動する | カメラの初期化失敗。カメラのケーブルの向き・差し込み、PSRAM の設定（OPI PSRAM） |
| 青点滅のまま | Wi-Fi の SSID・パスワード、2.4GHz 帯か |
| サーボが動くとボードが再起動する | 電源の容量不足。5V 2A 以上の電源と、電解コンデンサを付ける |
| 黄色のまま | NAS から指令が届いていない。`config.toml` の `[esp32] host`、`key` が `CATTOY_KEY` と同じか |
| 映像が上下・左右逆 | `config.toml` の `[camera] vflip` / `hflip` を true にする |
| ネットワーク越しに書き換えられない | Partition Scheme が OTA 対応（16M Flash (3MB APP/9.9MB FATFS)）で書き込まれているか。違う場合は一度だけ USB で書き直す |
| 書き換えたのに版が変わらない | 新しい版が Wi-Fi につながらず前の版に戻った可能性。シリアルモニタか `cattoy esp32-status` で確認する |
