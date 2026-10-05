# ESP32 ファームウェア（cattoy_esp32）

カメラ付き ESP32-S3 に書き込むプログラムです。ESP32 は「映像を送る」「サーボとレーザーを動かす」だけを行い、
猫の認識や遊び方の判断は NAS 側が行います。

| 機能 | 内容 |
| --- | --- |
| 映像 | `http://<IP>:81/stream`（MJPEG、640×480）／ `http://<IP>/capture`（静止画） |
| 状態 | `http://<IP>/status`（JSON。電波強度・ボタンが押された回数など） |
| 指令 | UDP 4210 番ポート（NAS から 0.1 秒ごとに届く） |
| 安全装置 | 指令が 0.3 秒途絶えたらレーザーを消し、3 秒途絶えたらサーボを止める |
| ボタン | 基板の BOOT ボタンで ON/OFF を切り替え |
| LED | 赤=レーザー点灯中／緑=NAS とつながっている／黄=NAS からの指令なし／青点滅=Wi-Fi 接続中 |

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
   | Partition Scheme | Huge APP (3MB No OTA/1MB SPIFFS) |
   | USB CDC On Boot | Disabled（「COM」側の USB-C で書き込む場合） |

5. ボードの USB-C（2 つあるうち、USB-シリアル変換チップにつながっている「COM」/「UART」側）を PC につなぎ、書き込む。
   書き込みが始まらない場合は、BOOT ボタンを押したまま RST ボタンを押して離し、もう一度書き込む。
6. シリアルモニタ（115200bps）を開いて RST を押すと、次のように表示される。
   ```
   cat-toy firmware 0.1.0
   Wi-Fi に接続中: xxxx....
   接続しました。IP アドレス: 192.168.1.50（NAS の config.toml の [esp32] host に書く）
   ```

## 動作確認

- PC やスマホのブラウザで `http://<IP>:81/stream` を開き、映像が映ることを確認する。
- `http://<IP>/status` を開くと状態が JSON で表示される。
- **IP アドレスは変わらないように固定してください。** ルーターの設定で、この ESP32 の MAC アドレスに
  決まった IP を割り当てる（「DHCP 固定割り当て」など）のが簡単です。

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| LED が赤く点いてすぐ再起動する | カメラの初期化失敗。カメラのケーブルの向き・差し込み、PSRAM の設定（OPI PSRAM） |
| 青点滅のまま | Wi-Fi の SSID・パスワード、2.4GHz 帯か |
| サーボが動くとボードが再起動する | 電源の容量不足。5V 2A 以上の電源と、電解コンデンサを付ける |
| 黄色のまま | NAS から指令が届いていない。`config.toml` の `[esp32] host`、`key` が `CATTOY_KEY` と同じか |
| 映像が上下・左右逆 | `config.toml` の `[camera] vflip` / `hflip` を true にする |
