"""スマホ用の操作画面（ホーム画面に追加するとアプリのように使える）。"""

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">
<rect width="96" height="96" rx="22" fill="#1f2937"/>
<path d="M22 70V34l12 10h28l12-10v36a8 8 0 0 1-8 8H30a8 8 0 0 1-8-8z" fill="#f9fafb"/>
<circle cx="38" cy="58" r="4" fill="#1f2937"/><circle cx="58" cy="58" r="4" fill="#1f2937"/>
<circle cx="76" cy="22" r="7" fill="#ef4444"/>
</svg>"""

MANIFEST = """{
  "name": "ねこレーザー",
  "short_name": "ねこレーザー",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#111827",
  "theme_color": "#111827",
  "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]
}"""

CONTROL_PAGE = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#111827">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="ねこレーザー">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg">
<title>ねこレーザー</title>
<style>
:root {
  --bg: #f3f4f6; --card: #ffffff; --text: #111827; --sub: #6b7280; --line: #e5e7eb;
  --on: #16a34a; --off: #9ca3af; --warn: #b45309; --warn-bg: #fef3c7; --bar: #60a5fa;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #0b1120; --card: #1f2937; --text: #f9fafb; --sub: #9ca3af; --line: #374151;
          --on: #22c55e; --off: #4b5563; --warn: #fcd34d; --warn-bg: #422006; --bar: #3b82f6; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font-family: system-ui, -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif;
       padding: max(16px, env(safe-area-inset-top)) 16px 32px; }
main { max-width: 480px; margin: 0 auto; display: grid; gap: 14px; }
h1 { font-size: 18px; margin: 4px 0 0; font-weight: 600; }
.card { background: var(--card); border-radius: 16px; padding: 16px; }
.hero { display: grid; justify-items: center; gap: 10px; padding: 24px 16px; }
#toggle { width: 148px; height: 148px; border-radius: 50%; border: none; cursor: pointer;
          font-size: 34px; font-weight: 700; color: #fff; background: var(--off);
          box-shadow: 0 6px 18px rgba(0,0,0,.18); transition: background .2s, transform .1s; }
#toggle.on { background: var(--on); }
#toggle:active { transform: scale(.96); }
#toggle:disabled { opacity: .6; }
#label { font-size: 20px; font-weight: 600; text-align: center; }
#sub { color: var(--sub); font-size: 14px; text-align: center; }
.warn { background: var(--warn-bg); color: var(--warn); border-radius: 12px; padding: 10px 12px;
        font-size: 14px; display: none; }
.row { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid var(--line);
       font-size: 15px; }
.row:last-child { border-bottom: none; }
.row span:first-child { color: var(--sub); }
.big { font-size: 28px; font-weight: 700; }
.bars { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; align-items: end; height: 70px;
        margin-top: 10px; }
.bar { background: var(--bar); border-radius: 4px 4px 0 0; min-height: 2px; }
.days { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; font-size: 11px; color: var(--sub);
        text-align: center; margin-top: 4px; }
button.plain:disabled { opacity: .45; }
button.plain { width: 100%; padding: 12px; border-radius: 12px; border: 1px solid var(--line);
               background: transparent; color: var(--text); font-size: 15px; cursor: pointer; }
#live img { width: 100%; border-radius: 10px; margin-top: 10px; display: block; cursor: crosshair; }
pre { background: var(--bg); padding: 8px; border-radius: 8px; white-space: pre-wrap; word-break: break-all;
      font-size: 12px; }
details summary { cursor: pointer; color: var(--sub); font-size: 14px; margin-top: 10px; }
</style></head>
<body><main>
<h1>ねこレーザー</h1>

<div id="offline" class="warn"></div>
<div id="needcal" class="warn">まだ位置合わせ（キャリブレーション）をしていません。下の「調整」から行ってください。</div>

<section class="card hero">
  <button id="toggle" disabled>…</button>
  <div id="label">接続中…</div>
  <div id="sub"></div>
</section>

<section class="card">
  <div style="color:var(--sub);font-size:14px">今日遊んだ時間</div>
  <div class="big" id="today">–</div>
  <div class="bars" id="bars"></div>
  <div class="days" id="days"></div>
</section>

<section class="card" id="live">
  <button class="plain" id="liveBtn">ライブ映像を見る</button>
  <div id="liveBox" hidden>
    <img id="liveImg" alt="カメラ映像">
    <details>
      <summary>遊ぶ範囲の設定用（映像をタップして座標を記録）</summary>
      <button class="plain" id="clearPts" style="margin-top:8px">記録をクリア</button>
      <pre id="pts">（まだ記録していません）</pre>
    </details>
  </div>
</section>

<section class="card" id="detail"></section>

<section class="card" id="adjust">
  <details id="adjustBox">
    <summary style="margin-top:0">調整（設置したとき・取り付けを動かしたとき）</summary>
    <p style="font-size:14px;color:var(--sub)">① 可動範囲: スライダーでレーザーを動かし、<b>床の遊ばせたい範囲の端</b>を記録して保存します。
      人や猫の目に向けないでください。</p>
    <div class="row"><span>パン（左右）</span><span id="panVal">–</span></div>
    <input type="range" id="pan" min="0" max="180" step="1" style="width:100%">
    <div class="row"><span>チルト（上下）</span><span id="tiltVal">–</span></div>
    <input type="range" id="tilt" min="0" max="180" step="1" style="width:100%">
    <label style="display:block;margin:10px 0;font-size:15px"><input type="checkbox" id="aimLaser"> レーザーを点ける</label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <button class="plain" data-set="pan_min">パンの端①に記録</button>
      <button class="plain" data-set="pan_max">パンの端②に記録</button>
      <button class="plain" data-set="tilt_min">チルトの端①に記録</button>
      <button class="plain" data-set="tilt_max">チルトの端②に記録</button>
    </div>
    <pre id="limits"></pre>
    <button class="plain" id="saveLimits">この可動範囲を保存</button>
    <button class="plain" id="stopAim" style="margin-top:8px">調整を終える（レーザーを消す）</button>
    <div id="limitMsg" style="font-size:14px;margin:8px 0"></div>
    <hr style="border:none;border-top:1px solid var(--line);margin:16px 0">
    <p style="font-size:14px;color:var(--sub)">② 位置合わせ: レーザーを 81 か所で点滅させ、カメラの映像とサーボの角度を対応付けます（約 3 分）。
      <b>猫と人がいない状態で</b>行ってください。可動範囲を変えたら、やり直してください。</p>
    <button class="plain" id="calBtn">位置合わせを開始</button>
    <div id="calMsg" style="font-size:14px;margin:8px 0;white-space:pre-wrap"></div>
    <a id="calImg" href="/calibration.jpg" target="_blank" style="font-size:14px" hidden>結果の画像を見る</a>
  </details>
</section>

<section class="card" id="maint" hidden>
  <details>
    <summary style="margin-top:0">メンテナンス（ESP32）</summary>
    <div id="fwInfo" style="font-size:14px;margin:10px 0"></div>
    <div style="font-size:14px;color:var(--sub)">新しいファームウェア（cattoy_esp32.ino.bin）</div>
    <input type="file" id="fwFile" accept=".bin" style="margin:8px 0;width:100%">
    <button class="plain" id="fwBtn">ファームウェアを書き換える</button>
    <div id="fwMsg" style="font-size:14px;margin:8px 0;white-space:pre-wrap"></div>
    <button class="plain" id="rebootBtn">ESP32 を再起動</button>
  </details>
</section>
</main>
<script>
const $ = id => document.getElementById(id);
let st = null, timer = null, busy = false, pts = [];

function fmtMin(s) { return s < 60 ? Math.round(s) + ' 秒' : Math.round(s / 60) + ' 分'; }
function fmtSpan(s) { return s >= 86400 ? Math.floor(s / 86400) + ' 日' : s >= 3600 ? Math.floor(s / 3600) + ' 時間' : fmtMin(s); }

function render() {
  if (!st) return;
  const t = $('toggle');
  t.disabled = busy;
  t.textContent = st.enabled ? 'ON' : 'OFF';
  t.classList.toggle('on', st.enabled);
  $('label').textContent = st.label;
  const sub = [];
  if (st.enabled && st.mode === 'play') sub.push('今回 ' + fmtMin(st.session_played_s) + ' / ' + fmtMin(st.session_max_s));
  if (st.active_hours) sub.push('動作時間 ' + st.active_hours);
  if (st.enabled && st.person && st.mode === 'play') sub.push('人がいるため消灯中');
  $('sub').textContent = sub.join('　');

  const off = $('offline');
  if (st.esp32 && !st.esp32.online) { off.textContent = 'ESP32 とつながっていません（電源と Wi-Fi を確認してください）'; off.style.display = 'block'; }
  else off.style.display = 'none';

  $('today').textContent = fmtMin(st.today_play_s);
  const max = Math.max(60, ...st.recent.map(r => r[1]));
  $('bars').innerHTML = st.recent.map(r => '<div class="bar" title="' + fmtMin(r[1]) + '" style="height:' + (r[1] / max * 100) + '%"></div>').join('');
  $('days').innerHTML = st.recent.map(r => '<div>' + Number(r[0].slice(8)) + '日</div>').join('');

  const visionText = { off: '停止中', standby: '待機（ときどき確認）', active: '連続で認識中' }[st.vision] || st.vision;
  const rows = [
    ['猫', st.cat ? '見えています' : '–'],
    ['認識', st.detector + '・' + visionText + (st.vision === 'active' ? '（' + st.fps + ' fps）' : '')],
    ['映像の遅れ', st.latency_ms + ' ms'],
  ];
  if (st.esp32 && st.esp32.online) rows.push(['ESP32 の電波', st.esp32.rssi + ' dBm']);
  $('detail').innerHTML = rows.map(r => '<div class="row"><span>' + r[0] + '</span><span>' + r[1] + '</span></div>').join('');

  $('needcal').style.display = st.needs_calibration ? 'block' : 'none';
  renderAdjust();

  $('maint').hidden = !st.esp32;
  if (st.esp32) {
    const e = st.esp32;
    $('fwInfo').textContent = e.online
      ? 'ファームウェア ' + e.fw + '（' + (e.built || '') + '・' + (e.partition || '') + '）　起動から ' + fmtSpan(e.uptime_s || 0) +
        (e.mac ? '　IP ' + e.ip + '・MAC ' + e.mac : '')
      : 'ESP32 とつながっていません';
  }
}

async function refresh() {
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    st = await r.json();
  } catch (e) {
    $('label').textContent = 'NAS とつながっていません';
    $('toggle').disabled = true;
    return;
  }
  render();
}

$('toggle').onclick = async () => {
  if (!st) return;
  busy = true; render();
  try {
    const r = await fetch('/api/enabled', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ enabled: !st.enabled }) });
    st = await r.json();
  } finally { busy = false; render(); }
};

$('liveBtn').onclick = () => {
  const box = $('liveBox'), img = $('liveImg');
  if (box.hidden) { img.src = '/stream?t=' + Date.now(); box.hidden = false; $('liveBtn').textContent = 'ライブ映像を閉じる'; }
  else { img.removeAttribute('src'); box.hidden = true; $('liveBtn').textContent = 'ライブ映像を見る'; }
};
$('liveImg').onclick = e => {
  const r = e.target.getBoundingClientRect();
  pts.push('[' + ((e.clientX - r.left) / r.width).toFixed(3) + ', ' + ((e.clientY - r.top) / r.height).toFixed(3) + ']');
  $('pts').textContent = 'play_area = [' + pts.join(', ') + ']\\nfinish_point = ' + pts[pts.length - 1];
};
$('clearPts').onclick = () => { pts = []; $('pts').textContent = '（まだ記録していません）'; };

// ---------------------------------------------------------------- 調整
let limits = null, aimSending = false, aimQueued = false, aimInit = false;
function renderAdjust() {
  const sv = st.servo, m = st.maintenance || {};
  if (!limits) limits = { pan_min: sv.pan_min, pan_max: sv.pan_max, tilt_min: sv.tilt_min, tilt_max: sv.tilt_max };
  if (!aimInit) {
    $('pan').max = sv.range; $('tilt').max = sv.range;
    $('pan').value = sv.pan; $('tilt').value = sv.tilt; aimInit = true;
  }
  $('panVal').textContent = $('pan').value + '°';
  $('tiltVal').textContent = $('tilt').value + '°';
  $('limits').textContent = 'パン ' + Math.min(limits.pan_min, limits.pan_max) + '° 〜 ' + Math.max(limits.pan_min, limits.pan_max) +
    '°　チルト ' + Math.min(limits.tilt_min, limits.tilt_max) + '° 〜 ' + Math.max(limits.tilt_min, limits.tilt_max) + '°' +
    '（保存済み: パン ' + sv.pan_min + '〜' + sv.pan_max + '°・チルト ' + sv.tilt_min + '〜' + sv.tilt_max + '°）';
  const calibrating = m.mode === 'calibrating';
  $('calBtn').disabled = calibrating;
  if (calibrating) $('calMsg').textContent = '位置合わせ中… ' + (m.message || '');
  else if (m.result) $('calMsg').textContent = m.result.ok
    ? '完了しました（' + m.result.points + ' 点・誤差 ' + m.result.rms_px + ' px・映像の遅れ ' + m.result.latency_ms + ' ms）。誤差は 3 px 以下が目安です。'
    : '失敗しました: ' + m.result.error;
  else if (st.calibration) $('calMsg').textContent = '前回の結果: ' + st.calibration.points + ' 点・誤差 ' + st.calibration.rms_px + ' px';
  $('calImg').hidden = !(st.calibration || (m.result && m.result.ok));
}
async function post(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
async function sendAim() {
  if (aimSending) { aimQueued = true; return; }
  aimSending = true;
  try {
    await post('/api/aim', { pan: +$('pan').value, tilt: +$('tilt').value, laser: $('aimLaser').checked });
    $('limitMsg').textContent = '';
  } catch (e) { $('limitMsg').textContent = '動かせません: ' + e.message; }
  aimSending = false;
  if (aimQueued) { aimQueued = false; sendAim(); }
}
['pan', 'tilt'].forEach(id => $(id).addEventListener('input', () => {
  $(id + 'Val').textContent = $(id).value + '°'; sendAim();
}));
$('aimLaser').onchange = sendAim;
document.querySelectorAll('[data-set]').forEach(b => b.onclick = () => {
  const key = b.dataset.set;
  limits[key] = +$(key.startsWith('pan') ? 'pan' : 'tilt').value;
  renderAdjust();
});
$('saveLimits').onclick = async () => {
  const body = {
    pan_min: Math.min(limits.pan_min, limits.pan_max), pan_max: Math.max(limits.pan_min, limits.pan_max),
    tilt_min: Math.min(limits.tilt_min, limits.tilt_max), tilt_max: Math.max(limits.tilt_min, limits.tilt_max),
  };
  try { await post('/api/servo-limits', body); $('limitMsg').textContent = '保存しました。続けて ② の位置合わせを行ってください。'; limits = null; refresh(); }
  catch (e) { $('limitMsg').textContent = '保存できません: ' + e.message; }
};
$('stopAim').onclick = async () => { $('aimLaser').checked = false; await post('/api/aim/stop').catch(() => {}); };
$('calBtn').onclick = async () => {
  if (!confirm('レーザーが部屋のあちこちを照らします。猫と人がいないことを確認しましたか？')) return;
  $('aimLaser').checked = false;
  try { await post('/api/calibrate'); refresh(); } catch (e) { $('calMsg').textContent = '開始できません: ' + e.message; }
};

$('fwBtn').onclick = () => {
  const f = $('fwFile').files[0];
  if (!f) { $('fwMsg').textContent = 'ファイルを選んでください'; return; }
  if (!confirm(f.name + ' を書き込みます。書き換え中はレーザーとサーボが止まり、終わると ESP32 が再起動します。よろしいですか？')) return;
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/esp32/firmware');
  xhr.upload.onprogress = e => { if (e.lengthComputable) $('fwMsg').textContent = '送信中… ' + Math.round(e.loaded / e.total * 100) + '%'; };
  xhr.onload = () => {
    let r = {}; try { r = JSON.parse(xhr.responseText); } catch (e) {}
    $('fwMsg').textContent = xhr.status === 200
      ? '版 ' + (r.fw || '?') + ' に書き換えました。ESP32 が再起動します（1 分ほどで上の版の表示が変わります）。\\n新しい版で Wi-Fi につながらない場合は、自動で前の版に戻ります。'
      : '失敗しました: ' + (r.error || xhr.status);
    $('fwBtn').disabled = false;
  };
  xhr.onerror = () => { $('fwMsg').textContent = '送信に失敗しました'; $('fwBtn').disabled = false; };
  $('fwBtn').disabled = true;
  xhr.send(f);
};
$('rebootBtn').onclick = async () => {
  if (!confirm('ESP32 を再起動しますか？')) return;
  const r = await fetch('/api/esp32/reboot', { method: 'POST' });
  const j = await r.json().catch(() => ({}));
  $('fwMsg').textContent = r.ok ? '再起動しています…' : '失敗しました: ' + (j.error || r.status);
};

function start() { refresh(); timer = setInterval(refresh, 2000); }
function stop() { clearInterval(timer); timer = null; }
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    stop(); if (!$('liveBox').hidden) $('liveBtn').click();
    if ($('aimLaser').checked) { $('aimLaser').checked = false; post('/api/aim/stop').catch(() => {}); }
  }
  else if (!timer) start();
});
start();
</script>
</body></html>
"""
