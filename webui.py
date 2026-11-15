# -*- coding: utf-8 -*-
"""
webui.py：網頁控制台的本機伺服器（只綁定 127.0.0.1，其他電腦連不到）

由常駐程式（kraken_lcd.py run）在背景執行緒啟動，瀏覽器開 http://127.0.0.1:8765 即可操作。
API：
  GET  /                 控制台網頁（ui/index.html）
  GET  /api/config       讀 config.json
  POST /api/config       寫 config.json（常駐程式偵測到檔案變更會立即套用）
  GET  /api/meta         RGB 效果預設值、讀值清單等（給網頁建表單用）
  GET  /api/state        即時狀態：液溫／轉速、各讀值、目前送出的 16 顆 LED 顏色
  GET  /api/audio_devices 音訊燈效可擷取的播放裝置清單與 Windows 預設裝置
  POST /api/restart      重新啟動常駐程式（關閉後自己再開新程序）
  POST /api/preview      依送來的畫面設定回傳預覽圖（PNG，base64）
  POST /api/upload       上傳圖片／GIF，存到 media/ 並回傳路徑
"""
import base64
import io
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, "ui")
MEDIA_DIR = os.path.join(HERE, "media")
PORT = 8765


class SharedState:
    """常駐程式與網頁伺服器共用的即時狀態（加鎖保護）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.status = None          # 水冷狀態（液溫、泵、風扇）
        self.rgb = None             # 目前送出的 16 顆 LED 顏色
        self.face_index = 0         # 輪播目前第幾個畫面
        self.mode = None
        self.last_ui = 0.0          # 最近一次網頁請求時間（有人在看時才持續讀液溫）
        self.error = None
        self.on_restart = None      # 常駐程式設定：網頁按「重新啟動」時呼叫

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "rgb": self.rgb, "face_index": self.face_index,
                    "mode": self.mode, "error": self.error}


def _make_handler(cfg_path, state, hub, preview_fn, meta):
    """建立 HTTP 處理類別（用閉包帶入共用物件）。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):          # 不在主控台印每個請求
            pass

        # ---- 回應工具 ----
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length", "0") or 0)
            return self.rfile.read(n) if n else b""

        def _local_only(self):
            """拒絕非本機來源（Host 標頭必須是 127.0.0.1／localhost，防 DNS rebinding）。"""
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                self._send(403, {"error": "forbidden"})
                return False
            return True

        # ---- GET ----
        def do_GET(self):
            if not self._local_only():
                return
            state.last_ui = time.time()
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                with open(os.path.join(UI_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/config":
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return self._send(200, f.read())
            if path == "/api/meta":
                return self._send(200, meta)
            if path == "/api/state":
                snap = state.snapshot()
                snap["values"] = hub.snapshot()
                import rgb                                  # 音訊燈效的即時音量（控制台音量表用）
                snap["audio"] = dict(rgb.AUDIO_STATUS)
                return self._send(200, snap)
            if path == "/api/audio_devices":
                import rgb                                  # 可擷取的播放裝置清單＋目前 Windows 預設裝置
                return self._send(200, rgb.list_audio_devices())
            self._send(404, {"error": "not found"})

        # ---- POST ----
        def do_POST(self):
            if not self._local_only():
                return
            state.last_ui = time.time()
            path = self.path.split("?")[0]
            try:
                if path == "/api/config":
                    cfg = json.loads(self._body().decode("utf-8"))
                    if not isinstance(cfg, dict):
                        raise ValueError("設定必須是 JSON 物件")
                    tmp = cfg_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, cfg_path)         # 原子替換，常駐程式不會讀到寫一半的檔案
                    return self._send(200, {"ok": True})
                if path == "/api/restart":
                    if not state.on_restart:
                        return self._send(503, {"error": "restart unavailable"})
                    state.on_restart()                # 主迴圈收尾後由常駐程式自己開新程序
                    return self._send(200, {"ok": True})
                if path == "/api/preview":
                    req = json.loads(self._body().decode("utf-8"))
                    img = preview_fn(req.get("face") or {}, req.get("cfg") or {})
                    if img is None:
                        return self._send(200, {"image": None})
                    buf = io.BytesIO()
                    img.save(buf, "PNG")
                    return self._send(200, {"image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
                if path == "/api/upload":
                    m = re.search(r"name=([^&]+)", self.path)
                    from urllib.parse import unquote
                    name = os.path.basename(unquote(m.group(1))) if m else "upload.bin"
                    name = re.sub(r'[<>:"/\\|?*]', "_", name)
                    os.makedirs(MEDIA_DIR, exist_ok=True)
                    dst = os.path.join(MEDIA_DIR, name)
                    with open(dst, "wb") as f:
                        f.write(self._body())
                    return self._send(200, {"path": dst})
            except Exception as e:
                return self._send(400, {"error": str(e)})
            self._send(404, {"error": "not found"})

    return Handler


def start(cfg_path, state, hub, preview_fn, meta, port=PORT):
    """在背景執行緒啟動伺服器；連接埠被占用時記錄錯誤並放棄（不影響 LCD／RGB）。"""
    import logging
    log = logging.getLogger("webui")
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(cfg_path, state, hub, preview_fn, meta))
    except OSError as e:
        log.warning("網頁控制台無法啟動（連接埠 %d）：%s", port, e)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("網頁控制台：http://127.0.0.1:%d", port)
    return srv
