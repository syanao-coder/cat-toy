// cat-toy ESP32-S3 ファームウェア
//
// 役割は「目」と「手」だけ。猫の認識や遊び方の判断は NAS 側（cattoy run）が行う。
//   映像   :81/stream   MJPEG（NAS が猫を探している間だけ接続される）
//          :80/capture  静止画 1 枚（猫がいない間はこちらを 1 秒ごとに取りに来る）
//   状態   :80/status   JSON（電波強度・ボタンが押された回数など）
//   指令   UDP :4210    "CT1 <key> <seq> <パンのパルス幅us> <チルトのパルス幅us> <サーボ有効0/1> <レーザー0/1>"
//
// 安全装置: 指令が 0.3 秒届かなければレーザーを消し、3 秒届かなければサーボを止める。
//           Wi-Fi が切れても NAS のプログラムが止まっても、レーザーが点いたままにはならない。
//
// 対象ボード: ESP32-S3-WROOM-1 N16R8 のカメラ付きボード（Freenove ESP32-S3-WROOM CAM 互換の配置）
// 必要なもの: Arduino IDE ＋ esp32 ボードパッケージ 3.x（書き込み方法は firmware/README.md）

#include <ESPmDNS.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#include "esp_camera.h"
#include "esp_http_server.h"
#include "secrets.h"  // secrets.example.h をコピーして作る（Wi-Fi の SSID・パスワードなど）

#define FW_VERSION "0.1.0"

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

// ---------------------------------------------------------------- 指令（UDP）
static void handleCommand(const char *line) {
  char key[48];
  unsigned long seq;
  int pan, tilt, servo, laser;
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
  char json[320];
  snprintf(json, sizeof(json),
           "{\"fw\":\"%s\",\"uptime_s\":%lu,\"rssi\":%d,\"ip\":\"%s\",\"laser\":%d,\"servo\":%d,"
           "\"pan_us\":%d,\"tilt_us\":%d,\"cmd_age_ms\":%lu,\"cmd_count\":%lu,\"button\":%lu,"
           "\"stream_clients\":%d,\"free_heap\":%u,\"free_psram\":%u}",
           FW_VERSION, millis() / 1000, WiFi.RSSI(), WiFi.localIP().toString().c_str(), laserOn ? 1 : 0,
           servoOn ? 1 : 0, panUs, tiltUs, millis() - lastCmdMs, cmdCount, buttonCount, streamClients,
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

static void registerGet(httpd_handle_t server, const char *path, esp_err_t (*handler)(httpd_req_t *)) {
  httpd_uri_t uri = {};
  uri.uri = path;
  uri.method = HTTP_GET;
  uri.handler = handler;
  httpd_register_uri_handler(server, &uri);
}

static void startHttp() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.ctrl_port = 32768;
  if (httpd_start(&ctrlHttpd, &config) == ESP_OK) {
    registerGet(ctrlHttpd, "/capture", captureHandler);
    registerGet(ctrlHttpd, "/status", statusHandler);
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

static void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(CATTOY_HOSTNAME);
  WiFi.setSleep(false);  // 省電力モードは遅延が大きくなるので切る
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("Wi-Fi に接続中: %s", WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    setLed(0, 0, 20);
    delay(250);
    setLed(0, 0, 0);
    delay(250);
    Serial.print(".");
  }
  Serial.printf("\n接続しました。IP アドレス: %s（NAS の config.toml の [esp32] host に書く）\n",
                WiFi.localIP().toString().c_str());
}

void setup() {
  // 起動直後から確実に消灯・停止しておく
  pinMode(LASER_PIN, OUTPUT);
  digitalWrite(LASER_PIN, LOW);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  delay(200);
  Serial.printf("\ncat-toy firmware %s\n", FW_VERSION);

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
  connectWifi();
  if (MDNS.begin(CATTOY_HOSTNAME)) MDNS.addService("http", "tcp", 80);
  udp.begin(UDP_PORT);
  startHttp();
  setLed(0, 10, 0);
}

void loop() {
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

  // LED: 赤=レーザー点灯中 / 緑=NAS とつながっている / 黄=NAS からの指令なし / 青点滅=Wi-Fi 接続中
  static unsigned long ledAt = 0;
  if (millis() - ledAt > 200) {
    ledAt = millis();
    if (WiFi.status() != WL_CONNECTED) setLed(0, 0, (millis() / 250) % 2 ? 20 : 0);
    else if (laserOn) setLed(25, 0, 0);
    else if (age < 2000) setLed(0, 10, 0);
    else setLed(10, 8, 0);
  }
  delay(2);
}
