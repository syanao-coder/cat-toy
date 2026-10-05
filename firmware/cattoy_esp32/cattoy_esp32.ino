// cat-toy ESP32-S3 ファームウェア
//
// 役割は「目」と「手」だけ。猫の認識や遊び方の判断は NAS 側（cattoy run）が行う。
//   映像   :81/stream   MJPEG（NAS が猫を探している間だけ接続される）
//          :80/capture  静止画 1 枚（猫がいない間はこちらを 1 秒ごとに取りに来る）
//   状態   :80/status   JSON（電波強度・ボタンが押された回数など）
//   指令   UDP :4210    "CT1 <key> <seq> <パンのパルス幅us> <チルトのパルス幅us> <サーボ有効0/1> <レーザー0/1>"
//
// 遠隔メンテナンス（設置後に取り外さずに済むように）
//   :80/update  POST   新しいファームウェア（.bin）を書き込んで再起動する（ヘッダー X-Cattoy-Key が必要）
//   :80/reboot  POST   再起動する
//   :80/wifi    POST   Wi-Fi の SSID・パスワードを変えて再起動する（ssid=...&pass=...）
//   ArduinoOTA         Arduino IDE の「ネットワークポート」から直接書き込むこともできる
//   新しいファームウェアが起動に失敗したら（1 分以内に Wi-Fi につながらない・3 回起動しても確定できない）、自動で前の版に戻る。
//   Wi-Fi に 3 分つながらなければ、設定用のアクセスポイント「cattoy-setup」を出す。
//
// 安全装置: 指令が 0.3 秒届かなければレーザーを消し、3 秒届かなければサーボを止める。
//           Wi-Fi が切れても NAS のプログラムが止まっても、書き換え中も、レーザーが点いたままにはならない。
//
// 対象ボード: ESP32-S3-WROOM-1 N16R8 のカメラ付きボード（Freenove ESP32-S3-WROOM CAM 互換の配置）
// 必要なもの: Arduino IDE ＋ esp32 ボードパッケージ 3.x（書き込み方法は firmware/README.md）

#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <Update.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "secrets.h"  // secrets.example.h をコピーして作る（Wi-Fi の SSID・パスワードなど）

#define FW_VERSION "0.2.0"

// ---------------------------------------------------------------- カメラのピン（ESP32-S3-EYE と同じ配置）
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 15
#define SIOD_GPIO_NUM 4
#define SIOC_GPIO_NUM 5
#define Y9_GPIO_NUM 16
#define Y8_GPIO_NUM 17
#define Y7_GPIO_NUM 18
#define Y6_GPIO_NUM 12
#define Y5_GPIO_NUM 10
#define Y4_GPIO_NUM 8
#define Y3_GPIO_NUM 9
#define Y2_GPIO_NUM 11
#define VSYNC_GPIO_NUM 6
#define HREF_GPIO_NUM 7
#define PCLK_GPIO_NUM 13

// ---------------------------------------------------------------- サーボ・レーザー・ボタン
#define PAN_PIN 14
#define TILT_PIN 21
#define LASER_PIN 47
#define BUTTON_PIN 0   // 基板の BOOT ボタン（起動後は普通のボタンとして使える）
#define RGB_LED_PIN 48 // 基板のフルカラー LED（状態表示）

// GPIO35〜37 は N16R8 では PSRAM が使っているので使わないこと

#define SERVO_FREQ_HZ 50
#define SERVO_RES_BITS 14
#define PAN_LEDC_CH 4  // カメラのクロックが LEDC チャンネル 0（タイマー 0）を使うので、別のチャンネル・タイマーにする
#define TILT_LEDC_CH 5
#define PULSE_MIN_US 500
#define PULSE_MAX_US 2500

#define UDP_PORT 4210
#define LASER_TIMEOUT_MS 300
#define SERVO_TIMEOUT_MS 3000

#define WIFI_TIMEOUT_MS (3UL * 60 * 1000)          // これだけつながらなければ設定用アクセスポイントを出す
#define WIFI_TIMEOUT_NEW_FW_MS (60UL * 1000)       // 書き換え直後の初回起動でこれだけつながらなければ前の版に戻す
#define SETUP_AP_SSID "cattoy-setup"
#define SETUP_AP_TIMEOUT_MS (10UL * 60 * 1000)     // 設定されなければ再起動して、もう一度つなぎに行く

#ifndef CATTOY_KEY
#define CATTOY_KEY ""
#endif
#ifndef CATTOY_HOSTNAME
#define CATTOY_HOSTNAME "cattoy"
#endif

// ---------------------------------------------------------------- 状態
static WiFiUDP udp;
static httpd_handle_t ctrlHttpd = NULL;
static httpd_handle_t streamHttpd = NULL;

static volatile bool laserOn = false;
static volatile bool servoOn = false;
static volatile int panUs = 1500;
static volatile int tiltUs = 1500;
static volatile unsigned long lastCmdMs = 0;
static volatile unsigned long lastSeq = 0;
static volatile unsigned long cmdCount = 0;
static volatile unsigned long buttonCount = 0;
static volatile int streamClients = 0;
static volatile bool updating = false;       // 書き換え中は指令を受け付けない
static volatile unsigned long rebootAt = 0;  // 0 以外ならこの時刻に再起動する
static bool setupMode = false;               // 設定用アクセスポイントを出している
static bool newFirmware = false;             // 書き換え直後の試運転中（Wi-Fi につながるまで仮の状態）
static bool idfPendingVerify = false;        // ブートローダーの自動ロールバックが有効な環境での「確認待ち」状態
static Preferences prefs;
static String wifiSsid, wifiPass;

// ---------------------------------------------------------------- 出力
static uint32_t pulseToDuty(int us) {
  const uint32_t maxDuty = (1UL << SERVO_RES_BITS) - 1;
  return (uint32_t)((uint64_t)us * maxDuty / (1000000UL / SERVO_FREQ_HZ));
}

static void setLaser(bool on) {
  laserOn = on;
  digitalWrite(LASER_PIN, on ? HIGH : LOW);
}

static void setServos(bool on, int pan, int tilt) {
  pan = constrain(pan, PULSE_MIN_US, PULSE_MAX_US);
  tilt = constrain(tilt, PULSE_MIN_US, PULSE_MAX_US);
  if (on) {
    if (!servoOn || pan != panUs) ledcWrite(PAN_PIN, pulseToDuty(pan));
    if (!servoOn || tilt != tiltUs) ledcWrite(TILT_PIN, pulseToDuty(tilt));
  } else if (servoOn) {
    ledcWrite(PAN_PIN, 0);  // パルスを止めると保持をやめる（静止中のジッタ音・発熱を防ぐ）
    ledcWrite(TILT_PIN, 0);
  }
  servoOn = on;
  panUs = pan;
  tiltUs = tilt;
}

static void setLed(uint8_t r, uint8_t g, uint8_t b) {
  rgbLedWrite(RGB_LED_PIN, r, g, b);
}

// 書き換え・再起動の前に、レーザーとサーボを確実に止めて指令を受け付けなくする
static void enterSafeState() {
  updating = true;
  setLaser(false);
  setServos(false, panUs, tiltUs);
}

// ---------------------------------------------------------------- 指令（UDP）
static void handleCommand(const char *line) {
  char key[48];
  unsigned long seq;
  int pan, tilt, servo, laser;
  if (updating) return;
  if (sscanf(line, "CT1 %47s %lu %d %d %d %d", key, &seq, &pan, &tilt, &servo, &laser) != 6) return;
  const char *expected = strlen(CATTOY_KEY) ? CATTOY_KEY : "-";
  if (strcmp(key, expected) != 0) return;
  lastCmdMs = millis();
  lastSeq = seq;
  cmdCount++;
  setServos(servo != 0, pan, tilt);
  setLaser(laser != 0);
}

static void pollUdp() {
  char buf[128];
  int size;
  while ((size = udp.parsePacket()) > 0) {
    int n = udp.read(buf, sizeof(buf) - 1);
    if (n <= 0) continue;
    buf[n] = 0;
    handleCommand(buf);
  }
}

// ---------------------------------------------------------------- HTTP
static esp_err_t captureHandler(httpd_req_t *req) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

static esp_err_t statusHandler(httpd_req_t *req) {
  char json[448];
  const esp_partition_t *running = esp_ota_get_running_partition();
  snprintf(json, sizeof(json),
           "{\"fw\":\"%s\",\"built\":\"%s %s\",\"partition\":\"%s\",\"uptime_s\":%lu,\"rssi\":%d,"
           "\"ssid\":\"%s\",\"ip\":\"%s\",\"laser\":%d,\"servo\":%d,\"pan_us\":%d,\"tilt_us\":%d,"
           "\"cmd_age_ms\":%lu,\"cmd_count\":%lu,\"button\":%lu,\"stream_clients\":%d,\"updating\":%d,\"trial\":%d,"
           "\"free_heap\":%u,\"free_psram\":%u}",
           FW_VERSION, __DATE__, __TIME__, running ? running->label : "?", millis() / 1000, WiFi.RSSI(),
           wifiSsid.c_str(), WiFi.localIP().toString().c_str(), laserOn ? 1 : 0, servoOn ? 1 : 0, panUs, tiltUs,
           millis() - lastCmdMs, cmdCount, buttonCount, streamClients, updating ? 1 : 0, newFirmware ? 1 : 0,
           (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getFreePsram());
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t streamHandler(httpd_req_t *req) {
  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  streamClients++;
  char part[96];
  esp_err_t res = ESP_OK;
  int failures = 0;
  while (res == ESP_OK) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      if (++failures > 100) break;  // カメラが応答しない: 接続を切って NAS に再接続させる
      delay(10);
      continue;
    }
    failures = 0;
    int n = snprintf(part, sizeof(part), "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                     (unsigned)fb->len);
    res = httpd_resp_send_chunk(req, part, n);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, "\r\n", 2);
    esp_camera_fb_return(fb);
  }
  streamClients--;
  return res;
}

// ---------------------------------------------------------------- 遠隔メンテナンス
static bool authorized(httpd_req_t *req) {
  if (setupMode || strlen(CATTOY_KEY) == 0) return true;  // 設定用アクセスポイントでは、その接続パスワードで守る
  char key[64];
  if (httpd_req_get_hdr_value_str(req, "X-Cattoy-Key", key, sizeof(key)) != ESP_OK) return false;
  return strcmp(key, CATTOY_KEY) == 0;
}

static esp_err_t reply(httpd_req_t *req, const char *status, const char *text) {
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "text/plain; charset=utf-8");
  return httpd_resp_sendstr(req, text);
}

// 書き換えたら「試運転」の印を付ける。新しい版は起動のたびに数え、Wi-Fi につながったら印を消す。
// 3 回起動しても確定できない・1 分以内に Wi-Fi につながらない場合は、前の版に戻す。
// （ブートローダーの自動ロールバックが無効な Arduino 環境でも戻せるよう、自前でも行う）
static void armTrial() {
  const esp_partition_t *running = esp_ota_get_running_partition();
  prefs.putString("prev", running ? running->label : "");
  prefs.putUChar("trial", 1);
  prefs.putUChar("boots", 0);
}

static void revertToPrevious(const char *reason) {
  Serial.printf("前の版に戻します: %s\n", reason);
  setLaser(false);
  prefs.putUChar("trial", 0);
  if (idfPendingVerify) esp_ota_mark_app_invalid_rollback_and_reboot();  // 戻らなければ下で自前で戻す
  String label = prefs.getString("prev", "");
  const esp_partition_t *prev =
      esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, label.c_str());
  if (prev && prev != esp_ota_get_running_partition()) esp_ota_set_boot_partition(prev);
  delay(200);
  ESP.restart();
}

static void confirmFirmware() {
  if (idfPendingVerify) esp_ota_mark_app_valid_cancel_rollback();
  if (newFirmware) {
    prefs.putUChar("trial", 0);
    Serial.println("この版で確定しました");
  }
  newFirmware = false;
}

static esp_err_t updateHandler(httpd_req_t *req) {
  if (!authorized(req)) return reply(req, "403 Forbidden", "key が違います");
  if (req->content_len <= 0) return reply(req, "400 Bad Request", "ファームウェアが空です");
  enterSafeState();
  Serial.printf("ファームウェアの書き換えを始めます（%u バイト）\n", (unsigned)req->content_len);
  if (!Update.begin(req->content_len, U_FLASH)) {
    updating = false;
    return reply(req, "500 Internal Server Error", Update.errorString());
  }
  char md5[40];
  if (httpd_req_get_hdr_value_str(req, "X-Cattoy-MD5", md5, sizeof(md5)) == ESP_OK) Update.setMD5(md5);
  static uint8_t buf[4096];
  size_t remaining = req->content_len;
  while (remaining > 0) {
    int r = httpd_req_recv(req, (char *)buf, remaining < sizeof(buf) ? remaining : sizeof(buf));
    if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
    if (r <= 0 || Update.write(buf, r) != (size_t)r) {
      Update.abort();
      updating = false;
      return reply(req, "500 Internal Server Error", "受信または書き込みに失敗しました");
    }
    remaining -= r;
  }
  if (!Update.end(true)) {  // MD5 が合わない・壊れたイメージならここで失敗し、今の版のまま動き続ける
    updating = false;
    return reply(req, "500 Internal Server Error", Update.errorString());
  }
  armTrial();
  Serial.println("書き換えました。再起動します");
  rebootAt = millis() + 500;
  return reply(req, "200 OK", "OK");
}

static esp_err_t rebootHandler(httpd_req_t *req) {
  if (!authorized(req)) return reply(req, "403 Forbidden", "key が違います");
  enterSafeState();
  rebootAt = millis() + 500;
  return reply(req, "200 OK", "OK");
}

// "a%20b+c" のような URL エンコードを戻す
static String urlDecode(const String &s) {
  String out;
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '+') {
      out += ' ';
    } else if (c == '%' && i + 2 < s.length()) {
      char hex[3] = {s[i + 1], s[i + 2], 0};
      out += (char)strtol(hex, NULL, 16);
      i += 2;
    } else {
      out += c;
    }
  }
  return out;
}

static String formValue(const String &body, const char *name) {
  String key = String(name) + "=";
  int start = body.startsWith(key) ? 0 : body.indexOf("&" + key);
  if (start < 0) return "";
  if (start > 0) start += 1;
  start += key.length();
  int end = body.indexOf('&', start);
  return urlDecode(end < 0 ? body.substring(start) : body.substring(start, end));
}

static esp_err_t wifiHandler(httpd_req_t *req) {
  if (!authorized(req)) return reply(req, "403 Forbidden", "key が違います");
  if (req->content_len <= 0 || req->content_len > 512) return reply(req, "400 Bad Request", "ssid=...&pass=... を送ってください");
  char body[513];
  size_t got = 0;
  while (got < req->content_len) {
    int r = httpd_req_recv(req, body + got, req->content_len - got);
    if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
    if (r <= 0) return reply(req, "400 Bad Request", "受信に失敗しました");
    got += r;
  }
  body[got] = 0;
  String ssid = formValue(String(body), "ssid");
  String pass = formValue(String(body), "pass");
  if (ssid.length() == 0) return reply(req, "400 Bad Request", "ssid が空です");
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  Serial.printf("Wi-Fi の設定を変更しました: %s（再起動します）\n", ssid.c_str());
  enterSafeState();
  rebootAt = millis() + 1000;
  return reply(req, "200 OK", "保存しました。再起動して新しい Wi-Fi につなぎます");
}

static const char SETUP_PAGE[] =
    "<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>cattoy の Wi-Fi 設定</title>"
    "<style>body{font-family:sans-serif;margin:24px;max-width:420px}input,button{width:100%;padding:10px;"
    "margin:6px 0 14px;font-size:16px;box-sizing:border-box}</style></head><body>"
    "<h2>cattoy の Wi-Fi 設定</h2><p>つなぐ Wi-Fi（2.4GHz 帯）を入力してください。</p>"
    "<form method=\"post\" action=\"/wifi\">SSID<input name=\"ssid\" required>"
    "パスワード<input name=\"pass\" type=\"password\"><button>保存して再起動</button></form></body></html>";

static esp_err_t setupPageHandler(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_sendstr(req, SETUP_PAGE);
}

static void registerUri(httpd_handle_t server, const char *path, httpd_method_t method,
                        esp_err_t (*handler)(httpd_req_t *)) {
  httpd_uri_t uri = {};
  uri.uri = path;
  uri.method = method;
  uri.handler = handler;
  httpd_register_uri_handler(server, &uri);
}

static void registerGet(httpd_handle_t server, const char *path, esp_err_t (*handler)(httpd_req_t *)) {
  registerUri(server, path, HTTP_GET, handler);
}

static void startHttp() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.ctrl_port = 32768;
  config.max_uri_handlers = 12;
  config.recv_wait_timeout = 15;  // ファームウェアの受信が途切れたときの待ち時間（秒）
  if (httpd_start(&ctrlHttpd, &config) == ESP_OK) {
    registerGet(ctrlHttpd, "/capture", captureHandler);
    registerGet(ctrlHttpd, "/status", statusHandler);
    registerUri(ctrlHttpd, "/update", HTTP_POST, updateHandler);
    registerUri(ctrlHttpd, "/reboot", HTTP_POST, rebootHandler);
    registerUri(ctrlHttpd, "/wifi", HTTP_POST, wifiHandler);
  }
  // 映像は 1 本の接続が送り続けるので、静止画・状態とは別のサーバーにする
  config.server_port = 81;
  config.ctrl_port = 32769;
  if (httpd_start(&streamHttpd, &config) == ESP_OK) {
    registerGet(streamHttpd, "/stream", streamHandler);
  }
}

// ---------------------------------------------------------------- 初期化
static bool startCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA;  // 640x480（NAS 側の camera.width / height と合わせる）
  c.jpeg_quality = 12;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;  // 常に最新の 1 枚を渡す（遅れを溜めない）
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("カメラの初期化に失敗しました: 0x%x\n", err);
    return false;
  }
  return true;
}

// Arduino の既定では起動するとすぐ「この版で確定」になる。確定を Wi-Fi につながった後まで遅らせる
extern "C" bool verifyRollbackLater() { return true; }

static bool isNewFirmware() {
  const esp_partition_t *running = esp_ota_get_running_partition();
  esp_ota_img_states_t state;
  return running && esp_ota_get_state_partition(running, &state) == ESP_OK && state == ESP_OTA_IMG_PENDING_VERIFY;
}

static bool connectWifi(unsigned long timeoutMs) {
  wifiSsid = prefs.getString("ssid", WIFI_SSID);  // /wifi で変えた設定があればそちらを使う
  wifiPass = prefs.getString("pass", WIFI_PASSWORD);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(CATTOY_HOSTNAME);
  WiFi.setSleep(false);  // 省電力モードは遅延が大きくなるので切る
  WiFi.setAutoReconnect(true);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  Serial.printf("Wi-Fi に接続中: %s", wifiSsid.c_str());
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > timeoutMs) {
      Serial.println("\nWi-Fi につながりませんでした");
      return false;
    }
    setLed(0, 0, 20);
    delay(250);
    setLed(0, 0, 0);
    delay(250);
    Serial.print(".");
  }
  Serial.printf("\n接続しました。IP アドレス: %s（NAS の config.toml の [esp32] host に書く）\n",
                WiFi.localIP().toString().c_str());
  return true;
}

// Wi-Fi につながらないとき: 設定用アクセスポイントを出し、スマホから SSID・パスワードを入れ直せるようにする
static void startSetupAp() {
  setupMode = true;
  WiFi.disconnect(true);
  WiFi.mode(WIFI_AP);
  const char *pass = strlen(CATTOY_KEY) >= 8 ? CATTOY_KEY : "cattoysetup";
  WiFi.softAP(SETUP_AP_SSID, pass);
  Serial.printf("設定用アクセスポイント %s（パスワード: CATTOY_KEY、8 文字未満なら cattoysetup）を出しました。"
                "スマホでつないで http://192.168.4.1/ を開いてください\n", SETUP_AP_SSID);
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  if (httpd_start(&ctrlHttpd, &config) == ESP_OK) {
    registerGet(ctrlHttpd, "/", setupPageHandler);
    registerGet(ctrlHttpd, "/status", statusHandler);
    registerUri(ctrlHttpd, "/wifi", HTTP_POST, wifiHandler);
  }
}

static void startOta() {
  ArduinoOTA.setHostname(CATTOY_HOSTNAME);
  if (strlen(CATTOY_KEY)) ArduinoOTA.setPassword(CATTOY_KEY);
  ArduinoOTA.onStart([]() { enterSafeState(); });
  ArduinoOTA.onEnd([]() { armTrial(); });
  ArduinoOTA.onError([](ota_error_t) { updating = false; });
  ArduinoOTA.begin();
}

void setup() {
  // 起動直後から確実に消灯・停止しておく
  pinMode(LASER_PIN, OUTPUT);
  digitalWrite(LASER_PIN, LOW);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  delay(200);
  Serial.printf("\ncat-toy firmware %s\n", FW_VERSION);

  prefs.begin("cattoy", false);
  idfPendingVerify = isNewFirmware();
  newFirmware = idfPendingVerify || prefs.getUChar("trial", 0) != 0;
  if (newFirmware) {
    uint8_t boots = prefs.getUChar("boots", 0) + 1;
    prefs.putUChar("boots", boots);
    Serial.printf("書き換え後の試運転中です（%u 回目の起動）。Wi-Fi につながったらこの版で確定します\n", boots);
    if (boots > 3) revertToPrevious("3 回起動しても確定できませんでした");
  }

  ledcAttachChannel(PAN_PIN, SERVO_FREQ_HZ, SERVO_RES_BITS, PAN_LEDC_CH);
  ledcAttachChannel(TILT_PIN, SERVO_FREQ_HZ, SERVO_RES_BITS, TILT_LEDC_CH);
  ledcWrite(PAN_PIN, 0);
  ledcWrite(TILT_PIN, 0);

  if (!psramFound()) Serial.println("警告: PSRAM が見つかりません（ボード設定の PSRAM を OPI PSRAM にしてください）");
  if (!startCamera()) {
    setLed(30, 0, 0);
    delay(5000);
    ESP.restart();
  }
  if (!connectWifi(newFirmware ? WIFI_TIMEOUT_NEW_FW_MS : WIFI_TIMEOUT_MS)) {
    if (newFirmware) revertToPrevious("新しい版で Wi-Fi につながりません");
    startSetupAp();
    return;
  }
  startOta();
  MDNS.addService("http", "tcp", 80);
  udp.begin(UDP_PORT);
  startHttp();
  confirmFirmware();
  setLed(0, 10, 0);
}

void loop() {
  if (rebootAt && (long)(millis() - rebootAt) >= 0) {
    setLaser(false);
    ESP.restart();
  }
  if (setupMode) {
    setLed(20, 0, 20);  // 紫=設定用アクセスポイントを出している
    if (millis() > SETUP_AP_TIMEOUT_MS) ESP.restart();
    delay(20);
    return;
  }
  ArduinoOTA.handle();
  pollUdp();

  // 見張り役: NAS からの指令が途絶えたら止める
  unsigned long age = millis() - lastCmdMs;
  if (laserOn && age > LASER_TIMEOUT_MS) setLaser(false);
  if (servoOn && age > SERVO_TIMEOUT_MS) setServos(false, panUs, tiltUs);

  // BOOT ボタン: 押された回数を数える（NAS が /status を見て ON/OFF を切り替える）
  static bool lastPressed = false;
  static unsigned long changedAt = 0;
  bool pressed = digitalRead(BUTTON_PIN) == LOW;
  if (pressed != lastPressed && millis() - changedAt > 50) {
    changedAt = millis();
    lastPressed = pressed;
    if (pressed) buttonCount++;
  }

  // LED: 赤=レーザー点灯中 / 緑=NAS とつながっている / 黄=NAS からの指令なし / 青点滅=Wi-Fi 接続中 / 白=書き換え中
  static unsigned long ledAt = 0;
  if (millis() - ledAt > 200) {
    ledAt = millis();
    if (updating) setLed(15, 15, 15);
    else if (WiFi.status() != WL_CONNECTED) setLed(0, 0, (millis() / 250) % 2 ? 20 : 0);
    else if (laserOn) setLed(25, 0, 0);
    else if (age < 2000) setLed(0, 10, 0);
    else setLed(10, 8, 0);
  }
  delay(2);
}
