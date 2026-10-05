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
</main>
<script>
const $ = id => document.getElementById(id);
let st = null, timer = null, busy = false, pts = [];

function fmtMin(s) { return s < 60 ? Math.round(s) + ' 秒' : Math.round(s / 60) + ' 分'; }

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

function start() { refresh(); timer = setInterval(refresh, 2000); }
function stop() { clearInterval(timer); timer = null; }
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { stop(); if (!$('liveBox').hidden) $('liveBtn').click(); }
  else if (!timer) start();
});
start();
</script>
</body></html>
"""
