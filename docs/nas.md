# NAS（QNAP Container Station）で動かす

猫の認識・遊び方の判断・スマホ用の操作画面を、NAS のコンテナで動かします。
例として QNAP TS-473A（QTS 5.2）＋ Quadro T400 で説明しますが、Docker が動く NAS や PC なら同じように動きます。

**YAML を貼り付けるだけ**で作れます。SSH もファイルの準備も要りません（検出モデルはイメージに入っています）。
その後の初期設定（可動範囲の調整・位置合わせ）も、すべてブラウザの操作画面で行えます。

## 1. 準備（最初の 1 回だけ）

- App Center で **Container Station** をインストールしておく。
- GPU を使う場合: コントロールパネル → ハードウェア → ハードウェアリソース で、GPU の「リソースの使用」を
  **Container Station モード** にして適用する。GPU のドライバは CUDA 12.8 以上に対応している必要がある
  （ドライバ 575 系なら CUDA 12.9 なので問題なし）。

### コンテナイメージを取れるようにする

イメージは GitHub が自動で作り、GitHub Container Registry（`ghcr.io/syanao-coder/cat-toy`）に置きます
（`main` ブランチに変更が入るたびに更新）。リポジトリが非公開なので、イメージも最初は非公開です。次のどちらかを行います。

- **かんたん: イメージを公開にする**（イメージにはプログラムと検出モデルだけが入っていて、Wi-Fi のパスワードや合言葉は入っていません）
  1. GitHub の自分のページ → **Packages** → **cat-toy** を開く
  2. **Package settings** → 一番下の **Change visibility** → **Public**
- **非公開のままにする: NAS に GitHub のアカウントを登録する**
  1. GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** で、
     `read:packages` だけにチェックを入れたトークンを作る
  2. Container Station → **レジストリ**（または「設定」→「レジストリ」）→ 追加 で、
     URL `ghcr.io`、ユーザー名に GitHub のユーザー名、パスワードに作ったトークンを入れる

## 2. アプリケーションを作る

1. Container Station → **アプリケーション** → **作成**
2. アプリケーション名に `cattoy` などを入れ、YAML 欄に [`docker/qnap-app.yml`](../docker/qnap-app.yml) の中身を**そのまま貼り付ける**
3. 次の 2 行だけ書き換える
   ```yaml
   - CATTOY_ESP32_HOST=192.168.1.50   # ← ESP32 の IP アドレス
   - CATTOY_ESP32_KEY=change-me       # ← secrets.h の CATTOY_KEY と同じ合言葉
   ```
   （ESP32 がまだ届いていなくても、仮の値のまま作って構いません。後で書き換えて作り直せます）
4. （任意）**詳細設定 → リソース**で、CPU を 2 コア・メモリを 2GB までなどに制限する。
   Container Station では、CPU・メモリの上限を YAML に書くと「非対応の Docker Compose YAML ファイル」になるため、ここで設定します。
5. **検証**を押して警告が出ないことを確かめ、**作成**を押す。初回はイメージのダウンロード（GPU 版は 3GB ほど）に数分かかります。

> YAML はスペースで字下げしてください（タブを使うと受け付けられません）。貼り付けたものをそのまま使えば大丈夫です。

設定と記録は NAS の `/share/Container/cattoy` に保存されます（アプリケーションを作り直しても消えません）。
共有フォルダ名が `Container` でない場合は、YAML の `volumes:` の行を書き換えてください。

## 3. 操作画面で初期設定する（ESP32 を組み立てて書き込んだ後）

PC かスマホのブラウザで `http://<NAS の IP>:8090/` を開きます。最初は「位置合わせが必要です」と表示されます。
下の方の **「調整（設置したとき・取り付けを動かしたとき）」** を開いて、次の順に進めます。

1. **可動範囲**: 「レーザーを点ける」にチェックを入れ、パン（左右）・チルト（上下）のスライダーでレーザーを動かします。
   床の遊ばせたい範囲の端で「パンの端①に記録」などを押して 4 か所を記録し、**「この可動範囲を保存」**。
   窓・テレビ・人の顔の高さに届かない範囲にしてください。終わったら「調整を終える」。
2. **位置合わせ**: 猫と人がいない状態で **「位置合わせを開始」**。レーザーを 81 か所で点滅させて、
   カメラの映像とサーボの角度を対応付けます（約 3 分）。終わると誤差（目安: 3px 以下）と映像の遅れが表示され、
   「結果の画像を見る」で確認できます。

これで遊び始めます。取り付けを動かしたときは、同じ手順でやり直してください。

## 4. スマホから使う

`http://<NAS の IP>:8090/` をホーム画面に追加すると、アプリのように使えます。

- **iPhone（Safari）**: 共有ボタン → 「ホーム画面に追加」
- **Android（Chrome）**: ︙ メニュー → 「ホーム画面に追加」

| 画面の項目 | 内容 |
| --- | --- |
| ON / OFF ボタン | 遊ばせるかどうか。OFF の間は映像の受信も認識も止まる（NAS の負荷はほぼゼロ） |
| 状態 | 猫を待っています／遊んでいます／休憩中（あと N 分）／動作時間外 |
| 今日遊んだ時間 | 直近 7 日のグラフ付き |
| ライブ映像 | 見ている間だけ配信される。タップすると遊ぶ範囲の座標を記録できる |
| 調整 | 可動範囲の調整と位置合わせ |
| メンテナンス（ESP32） | ESP32 のファームウェアの書き換え・再起動 |

ESP32 の基板の BOOT ボタンでも ON/OFF を切り替えられます。
家の LAN の外からは使えません（外出先から使いたい場合は QNAP の VPN 機能で家の LAN に入ってから開いてください）。

## 5. 設定を変えたいとき

YAML の `environment:` に `CATTOY_<セクション>_<項目>=値` の形で書くと、[cattoy/config.py](../cattoy/config.py) の
どの設定でも変えられます。書き換えたらアプリケーションを作り直します（「編集」→ 更新、または削除して同じ YAML で作成）。

| 例 | 内容 |
| --- | --- |
| `CATTOY_PLAY_ACTIVE_HOURS=08:00-22:00` | 動作する時間帯（`=` の後を空にすると常時） |
| `CATTOY_PLAY_SESSION_MAX_S=900` | 1 回の遊びの長さ（秒） |
| `CATTOY_PLAY_COOLDOWN_S=3600` | 遊んだ後の休憩（秒） |
| `CATTOY_PLAY_FINISH_POINT=[0.5, 0.8]` | 終わりにレーザーを誘導する場所（ライブ映像をタップして得た座標） |
| `CATTOY_PLAY_PLAY_AREA=[[0.1,0.4],[0.9,0.4],[0.95,0.95],[0.05,0.95]]` | 遊ばせる範囲 |
| `CATTOY_CAMERA_VFLIP=true` | 映像が上下逆のとき |

`/share/Container/cattoy/config.toml` を置いて書くこともできます（[config.example.toml](../config.example.toml) 参照）。
優先順位は「操作画面で保存した値（可動範囲）」＞「YAML の環境変数」＞「config.toml」＞ 既定値 です。

## 6. 更新するとき

- **NAS 側**: `main` に変更が入ると、GitHub が自動で新しいイメージを作ります。Container Station でアプリケーションを
  作り直す（または「イメージを更新」）と、新しいイメージで起動します。設定と記録はそのまま残ります。
- **壁の ESP32**: 操作画面の「メンテナンス（ESP32）」から `cattoy_esp32.ino.bin` を選んで書き換えます
  （取り外し不要。詳しくは [firmware/README.md](../firmware/README.md)）。

## NAS の負荷について

| 状態 | 動き |
| --- | --- |
| OFF・動作時間外・休憩中・位置合わせ前 | 映像の受信も認識もしない |
| ON で猫がいない | 1 秒に 1 枚だけ静止画を取って認識する（`CATTOY_RUNTIME_STANDBY_INTERVAL_S`） |
| 猫がいる・遊んでいる | 連続で認識する（GPU なら 1 回数ミリ秒程度の見込み） |

CPU・メモリの上限は、アプリケーションの作成画面の「詳細設定 → リソース」で付けられます（「2. アプリケーションを作る」の 4）。

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| 「非対応の Docker Compose YAML ファイル」と出る | YAML に `cpus:`・`mem_limit:`・`deploy:` の `limits:` を書いていないか（上限は「詳細設定 → リソース」で付ける）。書き換える行の `KEY=` などを消していないか |
| イメージを取れない（denied・unauthorized） | 「1. 準備」のイメージの公開、またはレジストリの登録 |
| イメージが見つからない（not found） | GitHub の Actions でイメージ作成が終わっているか（`main` に入ってから 15 分ほど） |
| 操作画面の「認識」が CPU になっている | GPU が「Container Station モード」になっているか。YAML の `deploy:` 以下を消していないか |
| 「ESP32 とつながっていません」 | ESP32 の電源・Wi-Fi、`CATTOY_ESP32_HOST` の IP アドレス |
| ESP32 の LED が黄色のまま | `CATTOY_ESP32_KEY` が secrets.h の `CATTOY_KEY` と同じか |
| 操作画面が開かない | ポート 8090 が他のアプリと重なっていないか（YAML の `"8090:8080"` の左側を変える） |

ログは Container Station でコンテナを選ぶと見られます。

## 別の方法: SSH で自分でビルドする

イメージを GitHub から取らずに NAS 上で作る場合は、リポジトリを NAS に置いて SSH で次を実行します。

```bash
cd /share/Container/cat-toy
docker compose -f docker/compose.yml up -d --build     # 設定は docker/data/ に置く
```

コマンドで操作したい場合は `docker compose -f docker/compose.yml run --rm cattoy <コマンド>` で
`hw-test`・`calibrate`・`esp32-update` などが使えます（README の「コマンド」）。
その間は本番のコンテナを `docker compose -f docker/compose.yml stop` で止めてください。
