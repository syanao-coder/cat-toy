"""ブラウザ用の画面（http://<NAS や Pi の IP>:<port>/ を開く）。

- run のとき: スマホ用の操作画面（ON/OFF・状態・遊んだ時間・ライブ映像）
- calibrate のとき: キャリブレーションの様子を見るだけのプレビュー

標準ライブラリだけで動く。認証はないので家庭内 LAN でのみ使うこと。
映像をクリックすると正規化座標（0〜1）が表示され、play_area / finish_point の設定にそのまま使える。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import numpy as np

from . import webpage
from .behavior import Command
from .geometry import Point
from .tracker import CatState, Detection

log = logging.getLogger(__name__)

_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cat-toy preview</title>
<style>
 body { font-family: sans-serif; margin: 12px; background: #111; color: #eee; }
 img { max-width: 100%; cursor: crosshair; display: block; }
 pre { background: #222; padding: 8px; white-space: pre-wrap; }
 button { margin: 4px 4px 4px 0; }
</style></head><body>
<h3>cat-toy プレビュー</h3>
<img id="v" src="/stream" alt="camera">
<p>画像をクリックすると座標（0〜1）を記録します。config.toml の play_area / finish_point に貼り付けてください。</p>
<button onclick="pts=[];show()">クリア</button>
<button onclick="pts.pop();show()">1点戻す</button>
<pre id="out">（まだ記録していません）</pre>
<script>
let pts = [];
const img = document.getElementById('v');
img.addEventListener('click', e => {
  const r = img.getBoundingClientRect();
  const x = ((e.clientX - r.left) / r.width).toFixed(3);
  const y = ((e.clientY - r.top) / r.height).toFixed(3);
  pts.push('[' + x + ', ' + y + ']');
  show();
});
function show() {
  document.getElementById('out').textContent = pts.length
    ? 'play_area = [' + pts.join(', ') + ']\\nfinish_point = ' + pts[pts.length - 1]
    : '（まだ記録していません）';
}
</script></body></html>
"""


class PreviewServer:
    """MJPEG のプレビューと、（status / set_enabled を渡したときは）スマホ用の操作画面を配信する。"""

    def __init__(
        self,
        port: int,
        status: Callable[[], dict] | None = None,
        set_enabled: Callable[[bool], None] | None = None,
    ):
        self._jpeg: bytes | None = None
        self._cond = threading.Condition()
        self._viewers = 0
        self._snapshot_at = -1e9
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:
                log.debug(fmt, *args)

            def _send(self, code: int, ctype: str, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, data: object, code: int = 200) -> None:
                self._send(code, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path == "/":
                    page = webpage.CONTROL_PAGE if status is not None else _PAGE
                    self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
                elif path == "/manifest.webmanifest" and status is not None:
                    self._send(200, "application/manifest+json", webpage.MANIFEST.encode("utf-8"))
                elif path == "/icon.svg":
                    self._send(200, "image/svg+xml", webpage.ICON_SVG.encode("utf-8"))
                elif path == "/api/status" and status is not None:
                    self._json(status())
                elif path == "/snapshot.jpg":
                    server._snapshot_at = time.monotonic()
                    jpeg = server.wait_frame(timeout=3.0)
                    if jpeg is None:
                        self.send_error(503)
                        return
                    self._send(200, "image/jpeg", jpeg)
                elif path == "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    with server._cond:
                        server._viewers += 1
                    try:
                        while True:
                            jpeg = server.wait_frame(timeout=5.0)
                            if jpeg is None:
                                continue
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                            self.wfile.write(jpeg + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        with server._cond:
                            server._viewers -= 1
                else:
                    self.send_error(404)

            def do_POST(self) -> None:
                if self.path == "/api/enabled" and set_enabled is not None and status is not None:
                    try:
                        n = int(self.headers.get("Content-Length", "0"))
                        body = json.loads(self.rfile.read(n) or b"{}")
                        set_enabled(bool(body["enabled"]))
                    except (ValueError, KeyError):
                        self._json({"error": "enabled (true/false) を送ってください"}, 400)
                        return
                    self._json(status())
                else:
                    self.send_error(404)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        log.info("%s: http://<このマシンのIP>:%d/", "操作画面" if status is not None else "プレビュー", port)

    def has_viewers(self) -> bool:
        """映像を見ている人がいるか（いなければ、重ね描きと JPEG 圧縮を省いて負荷を下げる）。"""
        with self._cond:
            return self._viewers > 0 or time.monotonic() - self._snapshot_at < 3.0

    def wait_frame(self, timeout: float) -> bytes | None:
        with self._cond:
            self._cond.wait(timeout)
            return self._jpeg

    def update(self, bgr: np.ndarray) -> None:
        import cv2

        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with self._cond:
                self._jpeg = buf.tobytes()
                self._cond.notify_all()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def draw_overlay(
    frame: np.ndarray,
    area: list[Point] | None = None,
    detections: list[Detection] | None = None,
    cat: CatState | None = None,
    cmd: Command | None = None,
    finish_point: Point | None = None,
    keepout_margin: float = 0.0,
    text: str = "",
) -> np.ndarray:
    import cv2

    img = frame.copy()
    if area and len(area) >= 3:
        pts = np.array([[int(x), int(y)] for x, y in area], dtype=np.int32)
        cv2.polylines(img, [pts], True, (255, 200, 0), 1)
    if finish_point is not None:
        cv2.drawMarker(img, (int(finish_point[0]), int(finish_point[1])), (255, 0, 255), cv2.MARKER_STAR, 14)
    for d in detections or []:
        x1, y1, x2, y2 = map(int, d.box)
        color = (0, 200, 0) if d.label == "cat" else (0, 0, 255)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 1)
        cv2.putText(img, f"{d.label} {d.conf:.2f}", (x1, max(y1 - 4, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
    if cat is not None:
        m = keepout_margin * cat.size
        x1, y1, x2, y2 = cat.box
        cv2.rectangle(img, (int(x1 - m), int(y1 - m)), (int(x2 + m), int(y2 + m)), (0, 255, 255), 1)
        cx, cy = cat.center
        cv2.arrowedLine(img, (int(cx), int(cy)), (int(cx + cat.velocity[0] * 0.5), int(cy + cat.velocity[1] * 0.5)), (0, 255, 255), 2)
    if cmd is not None and cmd.target is not None:
        tx, ty = int(cmd.target[0]), int(cmd.target[1])
        if cmd.laser_on:
            cv2.circle(img, (tx, ty), 7, (0, 0, 255), 2)
        else:
            cv2.circle(img, (tx, ty), 7, (128, 128, 128), 1)
    if cmd is not None:
        label = cmd.mode.value + (f"/{cmd.move.value}" if cmd.move else "")
        text = f"{label}  {text}".strip()
    if text:
        cv2.putText(img, text, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return img
