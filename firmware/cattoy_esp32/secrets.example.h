// このファイルを同じフォルダに secrets.h という名前でコピーし、中身を書き換える。
// secrets.h は .gitignore に入っているので、リポジトリには上がらない。

#define WIFI_SSID "あなたのWi-FiのSSID"  // ESP32 は 2.4GHz 帯にしかつながらない
#define WIFI_PASSWORD "パスワード"

// NAS 側 config.toml の [esp32] key と同じ文字列にする（家の LAN 内のいたずら防止）。
// 空文字 "" にすると照合しない。
#define CATTOY_KEY "change-me"

// mDNS 名（cattoy.local でアクセスできる）。複数台使うときは変える
#define CATTOY_HOSTNAME "cattoy"
