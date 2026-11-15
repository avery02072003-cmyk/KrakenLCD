# -*- coding: utf-8 -*-
"""
kraken_lcd.py：取代 NZXT CAM 的 Kraken 2024 Plus LCD＋RGB 控制程式（與 Fan Control 共存）

用法：
  python kraken_lcd.py run                 依 config.json 持續顯示 LCD 與 RGB（改存 config.json 立即套用），
                                           並提供網頁控制台 http://127.0.0.1:8765
  python kraken_lcd.py ui                  開啟網頁控制台（常駐程式沒在跑會先啟動）
  python kraken_lcd.py run --mode clock    臨時覆寫 LCD 模式（不改 config.json）
  python kraken_lcd.py liquid              切回韌體內建液溫畫面
  python kraken_lcd.py brightness 80       設 LCD 亮度 0–100
  python kraken_lcd.py orientation 270     設裝置方向 0/90/180/270
  python kraken_lcd.py rgb ff0000          RGB 全部設成單一顏色（一次性，不需常駐）
  python kraken_lcd.py rgb-test            RGB 依序亮紅、綠、藍、逐顆，用來目視確認
  python kraken_lcd.py status              顯示液溫、泵／風扇轉速、LCD 設定
  python kraken_lcd.py preview             依 config.json 輸出 preview.png（不碰水冷）

config.json 的 "mode"（與 CAM 的 LCD 模式對應）：
  liquid    韌體內建液溫畫面        single   單一資訊圖（"single" 區段）
  dual      雙資訊圖（"dual"）       clock    時鐘（"clock"）
  image     靜態圖片（"image"）      gif      動畫 GIF（"gif"）
  carousel  輪播（"carousel"：duration_ms＋faces 清單，每個 face 自帶 "type"）
single／dual 可設 "gif_background" → GIF 背景＋讀數（CAM 的 GIF Infographic）。
RGB 設定在 "rgb" 區段，效果清單見 rgb.py／FEATURES.md。

讀值 key（同 CAM monitoringKey）：LiquidTemp、CPUTemp、CPULoad、CPUFrequency、GPUTemp、GPULoad、GPUFrequency
CPU 溫度與即時時脈需以管理員執行（LibreHardwareMonitor 限制），否則 CPU 溫度改顯示使用率。
"""
import argparse
import ctypes
import datetime
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

from PIL import Image, ImageSequence

import render
import rgb
from kraken_device import KrakenLCD, image_to_rgb565

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
LOG_PATH = os.path.join(HERE, "kraken_lcd.log")

_LOG = logging.getLogger("kraken_lcd")
_stop = False  # 收到 Ctrl+C／結束訊號時設為 True，主迴圈結束並切回液溫畫面

LABELS = {"LiquidTemp": "Liquid", "CPUTemp": "CPU", "CPULoad": "CPU", "CPUFrequency": "CPU",
          "GPUTemp": "GPU", "GPULoad": "GPU", "GPUFrequency": "GPU"}


# ---------- 共用工具 ----------
def setup_logging(verbose=False):
    """同時輸出到主控台（若有）與 kraken_lcd.log（最多 1MB×2 份）。"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stdout is not None:  # pythonw 執行時沒有主控台
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(sh)


def single_instance():
    """Windows 具名 Mutex：只允許一個 run（兩個程式同時推畫面會互相蓋掉）。回傳 None 表示已有另一個在跑。"""
    h = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\KrakenLcdRunner")
    # 183 = 已存在；5 = 拒絕存取（另一份以系統管理員身分執行時，一般權限開不了它的 Mutex，也代表已在執行）
    if ctypes.windll.kernel32.GetLastError() in (183, 5) or not h:
        return None
    return h


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _on_signal(*_):
    global _stop
    _stop = True


_restart = False   # 網頁控制台按了「重新啟動」：主迴圈結束後自己再開一個新程序
_MUTEX = None      # 單一實例 Mutex 的 handle（重新啟動前要先關掉，新程序才拿得到）


def _request_restart():
    """網頁控制台呼叫：結束主迴圈，結束後由 main() 重新啟動程序。"""
    global _stop, _restart
    _restart = True
    _stop = True


def _spawn_self(args):
    """關掉 Mutex，以相同參數另開一個獨立的新程序（子程序承襲目前的系統管理員權限）。"""
    import subprocess
    if _MUTEX:
        ctypes.windll.kernel32.CloseHandle(_MUTEX)
    cmd = [sys.executable, os.path.abspath(__file__), "run", "--restarted"]
    if args.mode:
        cmd += ["--mode", args.mode]
    if args.verbose:
        cmd.append("-v")
    try:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：與目前程序脫鉤，舊程序結束不影響新程序
        subprocess.Popen(cmd, cwd=HERE, creationflags=0x00000008 | 0x00000200, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _LOG.info("重新啟動：已開啟新的常駐程式")
    except Exception as e:
        # 備援：交給工作排程（以最高權限執行；目前這個程序即將結束，所以排程不會被「已在執行」擋下）
        _LOG.warning("重新啟動失敗（%s），改用工作排程啟動", e)
        subprocess.run(["schtasks", "/Run", "/TN", "KrakenLCD"], capture_output=True)


# ---------- 讀值 ----------
class SensorHub:
    """
    感測器的共用快取：常駐迴圈與網頁控制台（另一個執行緒）都透過它讀值。
    加鎖避免兩個執行緒同時呼叫 LibreHardwareMonitor；同一個 key 0.8 秒內重複讀取直接回傳快取。
    """
    KEYS = ["LiquidTemp", "CPUTemp", "CPULoad", "CPUFrequency", "GPUTemp", "GPULoad", "GPUFrequency"]

    def __init__(self, sensors):
        import threading
        self.s = sensors
        self.lock = threading.Lock()
        self.cache = {}
        self.status = None          # 最近一次水冷狀態（液溫來源）

    def read(self, key, status=None):
        if status:
            self.status = status
        now = time.time()
        with self.lock:
            hit = self.cache.get(key)
            if hit and now - hit[0] < 0.8:
                return hit[1]
            v = self.s.read(key, self.status) if self.s else (None, "")
            self.cache[key] = (now, v)
            return v

    def snapshot(self):
        """所有讀值目前的數值（給網頁顯示）。"""
        return {k: list(self.read(k)) for k in self.KEYS}

    def close(self):
        """關閉底層感測器（LibreHardwareMonitor、NVML）。"""
        if self.s:
            self.s.close()


def make_reading(rcfg, cfg, sensors, status):
    """依 reading 設定（key＋配色）取數值，包成 render.Reading；溫度依 temperature_unit 換算 °F。"""
    rcfg = rcfg or {}
    key = rcfg.get("key", "LiquidTemp")
    v, unit = sensors.read(key, status) if sensors else (None, "°")
    label = rcfg.get("label") or LABELS.get(key, key)
    if unit == "°":
        lo, hi = cfg.get("temp_min", 0), cfg.get("temp_max", 100)
        if str(cfg.get("temperature_unit", "C")).upper() == "F":
            f = lambda c: None if c is None else c * 9 / 5 + 32
            v, lo, hi = f(v), f(lo), f(hi)
    elif unit == "GHz":
        lo, hi = 0, cfg.get("cpu_freq_max" if key.startswith("CPU") else "gpu_freq_max", 6.0)
    else:
        lo, hi = 0, 100
    return render.Reading(v, unit, label, lo, hi, rcfg.get("color", "#FFFFFF"), rcfg.get("text_color", "#FFFFFF"),
                          rcfg.get("outline"), rcfg.get("text_outline"))      # 數值／標籤外框色（空＝無）


def face_readings(face, cfg, sensors, status):
    """依畫面類型取 1～3 個讀值。"""
    if face.get("type") == "single":
        return [make_reading(face.get("reading"), cfg, sensors, status)]
    if face.get("type") == "dual":
        return [make_reading(face.get("reading1"), cfg, sensors, status),
                make_reading(face.get("reading2"), cfg, sensors, status)]
    if face.get("type") == "triple":
        return [make_reading(face.get(k), cfg, sensors, status) for k in ("reading1", "reading2", "reading3")]
    return []


# 「液溫」模式的畫面：Kraken 2024 Plus 的韌體不支援切換到內建液溫畫面（0x38 切換指令沒反應、畫面停在上一張；
# liquidctl PR 912 也指出這台沒有實作 bucket 相關指令），所以改由電腦端畫：
# 用 CAM 單一資訊圖的預設樣式顯示液溫（與網頁控制台「單一資訊圖」的預設值相同）
LIQUID_FACE = {"type": "single", "style": "radial-fill-v2",
               "reading": {"key": "LiquidTemp", "color": "#FFFFFF", "text_color": "#FFFFFF"},
               "colors": ["#6B00DE", "#D600BF"],
               "ring_gradient": [[0, "#00ff00"], [50, "#ffff00"], [100, "#ff0000"]],
               "use_ring_gradient": False, "background": "#000000",
               "logo": {"enable": True, "color": "#FFFFFF"}, "gif_background": ""}


def face_needs_liquid(face):
    if face.get("type", "liquid") == "liquid":
        return True
    keys = [(face.get(k) or {}).get("key") for k in ("reading", "reading1", "reading2", "reading3")]
    return "LiquidTemp" in keys


def fit_image(im, size=(240, 240)):
    """
    圖片／GIF 縮放：保留原比例完整放進 240x240，多出來的地方補黑邊（4:3 照片會上下留黑）。
    實機與網頁預覽都用這個函式，兩邊一定一致。
    """
    im = im.convert("RGB")
    if im.size == size:
        return im
    k = min(size[0] / im.width, size[1] / im.height)
    w, h = max(1, round(im.width * k)), max(1, round(im.height * k))
    out = Image.new("RGB", size, (0, 0, 0))
    out.paste(im.resize((w, h), Image.LANCZOS), ((size[0] - w) // 2, (size[1] - h) // 2))
    return out


def rotate(img, cfg):
    """
    軟體旋轉（對應 CAM 的 LCD rotation 設定，單位度、與 CAM 同號；CAM 目前為 -90）。
    與裝置本身的方向設定（orientation）分開，兩者會疊加。
    """
    r = int(cfg.get("rotation", 0) or 0) % 360
    return img.rotate(-r) if r else img


# ---------- GIF ----------
def load_gif(path):
    """GIF → [(PIL RGB 影像 240x240, 秒數)]。"""
    frames = []
    with Image.open(path) as im:
        for fr in ImageSequence.Iterator(im):
            dur = max(0.02, (fr.info.get("duration") or 100) / 1000.0)
            frames.append((fit_image(fr.convert("RGBA")), dur))
    return frames


# ---------- 一個畫面的播放器 ----------
class FacePlayer:
    """
    負責一個 LCD 畫面（single／dual／clock／image／gif／liquid）的出圖節奏。
    step() 在需要時送出一張圖，回傳建議的下次呼叫間隔（秒）。
    """

    def __init__(self, face, cfg, sensors, orient):
        self.face, self.cfg, self.sensors, self.orient = face, cfg, sensors, orient
        self.t = face.get("type", "liquid")
        self.next_time = 0.0
        self.frame_i = 0
        self.gif = None           # [(影像或 RGB565, 秒數)]
        self.ov = None            # 讀數前景圖層（GIF 背景模式用）
        self.ov_time = 0.0
        self.last_key = None      # 時鐘：上次畫的秒數

    def _send(self, k, img, double=True):
        """送一張畫面；double=True 時送兩次讓 LCD 兩個緩衝都換成新圖（避免新舊疊圖），GIF 傳 False。"""
        k.send_frame_rgb565(image_to_rgb565(rotate(img, self.cfg), self.orient), double=double)

    def step(self, k, now, status):
        t, face, cfg = self.t, self.face, self.cfg
        interval = float(cfg.get("interval_s", 1.0))

        # 圖片／GIF 畫面沒選檔案或檔案不存在：改顯示韌體液溫畫面（只記一次警告），不讓整個程式當掉重連
        if t in ("image", "gif") and not os.path.isfile(face.get("file") or ""):
            if self.next_time == 0.0:
                _LOG.warning("%s 畫面找不到檔案「%s」，改顯示液溫畫面", t, face.get("file") or "")
            t = "liquid"

        if t == "liquid":                             # 電腦端畫液溫畫面（韌體液溫畫面在這台叫不出來）
            if now >= self.next_time:
                self._send(k, render.render(LIQUID_FACE, face_readings(LIQUID_FACE, cfg, self.sensors, status)))
                self.next_time = now + interval
            return 0.05

        if t == "image":
            if now >= self.next_time:                 # 每 30 秒重送，避免裝置重置後畫面消失
                with Image.open(face.get("file", "")) as im:
                    self._send(k, fit_image(im))
                self.next_time = now + 30
            return 0.5

        if t == "gif":
            if self.gif is None:
                self.gif = [(image_to_rgb565(rotate(f, cfg), self.orient), d) for f, d in load_gif(face.get("file", ""))]
                _LOG.info("GIF 載入 %d 格", len(self.gif))
            if now >= self.next_time:
                data, dur = self.gif[self.frame_i % len(self.gif)]
                k.send_frame_rgb565(data)
                self.frame_i += 1
                self.next_time = now + dur
            return max(0.0, self.next_time - time.time())

        # 資訊圖／時鐘
        bg_path = face.get("gif_background") or ""
        if bg_path and t in ("single", "dual"):
            if self.gif is None:
                self.gif = load_gif(bg_path)
                _LOG.info("GIF 背景載入 %d 格", len(self.gif))
            if self.ov is None or now - self.ov_time >= interval:
                self.ov = render.overlay(face, face_readings(face, cfg, self.sensors, status))
                self.ov_time = now
            if now >= self.next_time:
                frame, dur = self.gif[self.frame_i % len(self.gif)]
                self._send(k, render.compose(self.ov, frame), double=False)   # GIF 背景連續換格，不送兩次
                self.frame_i += 1
                self.next_time = now + dur
            return max(0.0, self.next_time - time.time())

        if t == "clock":
            dt = datetime.datetime.now()
            key = (dt.hour, dt.minute, dt.second)
            if key != self.last_key:                  # 每秒更新一次
                self._send(k, render.render(face, now=dt))
                self.last_key = key
            return 0.05

        if now >= self.next_time:                     # single／dual
            self._send(k, render.render(face, face_readings(face, cfg, self.sensors, status)))
            self.next_time = now + interval
        return 0.05


def current_faces(cfg, mode):
    """依模式回傳 (畫面清單, 每個畫面秒數)。非輪播模式只有一個畫面。"""
    if mode == "carousel":
        c = cfg.get("carousel") or {}
        faces = [f for f in (c.get("faces") or []) if f]
        return faces or [{"type": "liquid"}], max(1.0, float(c.get("duration_ms", 5000)) / 1000.0)
    if mode in ("single", "dual", "clock", "image", "gif"):
        face = dict(cfg.get(mode) or {})
        face["type"] = mode
        return [face], 0
    return [{"type": "liquid"}], 0


# ---------- RGB 執行緒 ----------
class RgbThread(threading.Thread):
    """
    RGB 燈效獨立執行緒：依 fps 不停計算並送出燈效畫格，不受 LCD 渲染／傳圖（每張約 0.1～0.4 秒）影響。
    主迴圈換設定時只要替換 self.engine；送出失敗時把錯誤存在 self.error，由主迴圈負責重連。
    """

    def __init__(self, k):
        super().__init__(daemon=True)
        self.k = k
        self.engine = None        # 目前的 rgb.RgbEngine（主迴圈設定）
        self.status = None        # 最新的水冷狀態（溫度燈效用）
        self.error = None
        self.alive = True

    def run(self):
        while self.alive and not _stop:
            eng = self.engine
            try:
                if eng is not None and eng.enabled:
                    eng.tick(self.k, time.time(), self.status)
                    time.sleep(1.0 / max(0.2, float(eng.cfg.get("fps", 20))) / 2)   # 半個畫格週期檢查一次
                else:
                    time.sleep(0.1)
            except Exception as e:  # 裝置斷線等：交給主迴圈重連
                self.error = e
                return


# ---------- 主迴圈 ----------
def run_loop(args):
    """連線 → 依模式送 LCD 畫面並更新 RGB；出錯（拔線、睡眠喚醒）就重連；config.json 變更即時套用。"""
    from sensors import Sensors
    import webui

    sensors = SensorHub(Sensors())
    state = webui.SharedState()
    state.on_restart = _request_restart         # 網頁控制台「重新啟動」按鈕
    webui.start(CONFIG_PATH, state, sensors, lambda f, c: preview_face(f, c, sensors, state.status),
                {"effects": rgb.EFFECT_DEFAULTS, "keys": SensorHub.KEYS, "labels": LABELS,
                 "audio": _has_audio(), "admin": _is_admin()})
    cfg_mtime, cfg = None, {}
    engine = None
    while not _stop:
        try:
            with KrakenLCD() as k:
                _LOG.info("已連線 Kraken，開始顯示")
                # 重連後一定重新載入設定（舊版沿用 mtime 不重載，但畫面清單被清空 → 每 3 秒當掉一次）
                cfg_mtime = None
                rgb_thr = RgbThread(k)
                rgb_thr.engine = engine
                rgb_thr.start()
                st = {"t": time.time(), "lcd": 0, "lcd_s": 0.0, "lcd_max": 0.0, "loop_max": 0.0, "rgb0": 0, "eng": None}
                applied = None        # 已套用的 (亮度, 方向)
                orient = 0
                faces, dur, mode = [], 0, None
                player, face_idx = None, -1
                t_start = time.time()
                status, last_status = None, 0.0
                last_loop = time.time()
                while not _stop:
                    now = time.time()
                    # 1) config.json 變更偵測：重建畫面清單與 RGB 引擎
                    try:
                        m = os.path.getmtime(CONFIG_PATH)
                        if m != cfg_mtime:
                            new_cfg, cfg_mtime = load_config(), m
                            new_mode = args.mode or new_cfg.get("mode", "liquid")
                            new_faces, new_dur = current_faces(new_cfg, new_mode)
                            strip = lambda c: json.dumps({k: v for k, v in c.items() if k != "rgb"}, sort_keys=True)
                            first = not faces
                            lcd_changed = first or strip(new_cfg) != strip(cfg)
                            rgb_changed = first or engine is None or new_cfg.get("rgb") != cfg.get("rgb")
                            cfg = new_cfg
                            # 只重建真正有變動的部分：控制台每次改動約 0.6 秒就自動存檔，
                            # 舊版每存一次就全部重來（燈效從頭開始、重送亮度方向、輪播跳回第一頁），連續調整時會一直卡頓
                            if lcd_changed:
                                restart = first or new_mode != mode or len(new_faces) != len(faces) or new_dur != dur
                                faces, dur, mode = new_faces, new_dur, new_mode
                                player = None                    # 用新設定重畫目前這一頁
                                k._first_frame = True            # 新設定的第一張送兩次，馬上看到（不必等下一張）
                                if restart:                      # 模式或頁數變了才從第一頁重新開始輪播
                                    face_idx, t_start = -1, now
                            if rgb_changed:
                                old = engine
                                engine = rgb.RgbEngine(cfg.get("rgb"), sensors, prev=old)
                                if old:
                                    old.close()
                                rgb_thr.engine = engine          # 交給 RGB 執行緒，下一格就用新效果
                            _LOG.info("載入設定：mode=%s，畫面 %d 個，RGB=%s（變動：%s）", mode, len(faces),
                                      engine.cfg.get("effect") if engine.enabled else "不控制",
                                      "、".join(x for x, f in (("LCD", lcd_changed), ("RGB", rgb_changed)) if f) or "無")
                    except Exception as e:
                        _LOG.warning("讀取 config.json 失敗：%s", e)

                    # 2) 睡眠喚醒偵測：迴圈間隔異常長 → 裝置可能重置，重新同步
                    if now - last_loop > 10:
                        _LOG.info("偵測到暫停 %.0f 秒（可能睡眠喚醒），重新同步", now - last_loop)
                        k._first_frame, applied, player, face_idx = True, None, None, -1
                    last_loop = now

                    # 3) 亮度／方向（null 表示沿用裝置目前設定）
                    want = (cfg.get("brightness"), cfg.get("orientation"))
                    if want != applied:
                        o = None if want[1] is None else int(want[1]) // 90
                        if want[0] is not None or o is not None:
                            k.set_brightness_orientation(want[0], o)
                        applied = want
                        new_orient = k.get_lcd_info()["orientation"]
                        if new_orient != orient or player is None:   # 方向變了，畫面要用新方向重畫
                            player = None
                        orient = new_orient

                    # 4) 輪播換頁
                    idx = int((now - t_start) / dur) % len(faces) if dur else 0
                    if idx != face_idx or player is None:
                        if idx != face_idx:
                            k._first_frame = True     # 換頁的第一張圖送兩次，確保整張畫面完整更新（不殘留前一頁）
                        player, face_idx = FacePlayer(faces[idx], cfg, sensors, orient), idx

                    # 5) 液溫（資訊圖、溫度燈效需要，或網頁控制台 10 秒內有人在看時才讀；被動讀取不影響 Fan Control）
                    need_liq = face_needs_liquid(faces[idx]) or (engine and engine.needs_liquid()) \
                        or now - state.last_ui < 10
                    if need_liq and now - last_status >= float(cfg.get("interval_s", 1.0)):
                        status = k.read_status(0.3) or status
                        last_status = now
                        sensors.status = status

                    # 6) 出圖（RGB 由獨立執行緒送）
                    if rgb_thr.error is not None:
                        raise rgb_thr.error
                    rgb_thr.status = status
                    t_lcd = time.time()
                    n_before = k.frames_sent
                    wait = player.step(k, now, status)
                    if k.frames_sent != n_before:            # 這圈有送出畫面：記錄渲染＋傳圖耗時
                        dt = time.time() - t_lcd
                        st["lcd"] += 1
                        st["lcd_s"] += dt
                        st["lcd_max"] = max(st["lcd_max"], dt)
                    st["loop_max"] = max(st["loop_max"], time.time() - now)
                    if time.time() - st["t"] >= 60:          # 每分鐘記一次效能統計
                        el = time.time() - st["t"]
                        # 期間換過燈效時新引擎從 0 起算，不能直接相減（否則會出現負數）
                        cur = engine.sent if engine else 0
                        rgb_n = cur - st["rgb0"] if st.get("eng") is engine else cur
                        _LOG.info("效能：LCD %d 張（平均 %.0f ms、最長 %.0f ms，累計重送 %d），RGB %.1f 格/秒，主迴圈最長 %.0f ms",
                                  st["lcd"], 1000 * st["lcd_s"] / max(1, st["lcd"]), 1000 * st["lcd_max"],
                                  k.frame_retries, rgb_n / el, 1000 * st["loop_max"])
                        st = {"t": time.time(), "lcd": 0, "lcd_s": 0.0, "lcd_max": 0.0, "loop_max": 0.0,
                              "rgb0": engine.sent if engine else 0, "eng": engine}

                    # 7) 更新給網頁控制台的即時狀態
                    with state.lock:
                        state.status, state.face_index = status, idx
                        state.mode = args.mode or cfg.get("mode")
                        state.rgb = engine.last_frame if engine and engine.enabled else None
                        state.error = None
                    time.sleep(max(0.005, min(wait, 0.5)))

                # 正常結束：停掉 RGB 執行緒，切回韌體液溫畫面，避免停在最後一格
                rgb_thr.alive = False
                rgb_thr.join(1.0)
                if _restart:                        # 重新啟動：畫面留著，新程序馬上接手
                    _LOG.info("重新啟動中…")
                else:
                    k.set_liquid_mode()
                    _LOG.info("已停止，切回液溫畫面")
        except Exception as e:
            _LOG.warning("裝置錯誤，3 秒後重連：%s", e, exc_info=True)   # 附完整追蹤，方便找出錯位置
            with state.lock:
                state.error = str(e)
            for _ in range(30):
                if _stop:
                    break
                time.sleep(0.1)
    if engine:
        engine.close()
    sensors.close()


def preview_face(face, cfg, sensors, status):
    """網頁控制台用：依畫面設定畫一張預覽（正向、圓形外塗灰）。圖片／GIF 畫面顯示檔案本身。"""
    t = face.get("type")
    try:
        if t in ("image", "gif"):
            with Image.open(face.get("file", "")) as im:
                img = fit_image(im)                   # 與實機相同：保留比例、補黑邊
        elif t == "liquid":
            img = render.render(LIQUID_FACE, face_readings(LIQUID_FACE, cfg, sensors, status or {"liquid": 30.0}))
        elif t in ("single", "dual", "clock"):
            bg = None
            if face.get("gif_background") and t in ("single", "dual"):
                with Image.open(face["gif_background"]) as im:
                    bg = im.convert("RGB").resize((240, 240), Image.LANCZOS)
            img = render.render(face, face_readings(face, cfg, sensors, status or {"liquid": 30.0}), background=bg)
        else:
            return None
    except Exception:
        return None
    return render.circle_preview(img)


def _has_audio():
    try:
        import pyaudiowpatch  # noqa: F401
        return True
    except ImportError:
        return False


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def cmd_ui():
    """開啟網頁控制台；常駐程式沒在跑時先用工作排程（或直接）啟動它。"""
    import subprocess
    import webbrowser
    if single_instance_probe():
        print("常駐程式未執行，正在啟動…")
        r = subprocess.run(["schtasks", "/Run", "/TN", "KrakenLCD"], capture_output=True)
        if r.returncode != 0:
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            subprocess.Popen([pyw, os.path.join(HERE, "kraken_lcd.py"), "run"], cwd=HERE)
        time.sleep(4)
    webbrowser.open("http://127.0.0.1:8765")


def single_instance_probe():
    """回傳 True 表示目前沒有常駐程式在跑（試開 Mutex 後立刻關掉）。"""
    k32 = ctypes.windll.kernel32
    h = k32.CreateMutexW(None, False, "Global\\KrakenLcdRunner")
    exists = k32.GetLastError() in (183, 5) or not h   # 5 = 拒絕存取（系統管理員身分的常駐程式）
    if h:
        k32.CloseHandle(h)
    return not exists


# ---------- 單次指令 ----------
def cmd_status():
    with KrakenLCD(need_bulk=False) as k:
        s = k.read_status()
        i = k.get_lcd_info()
    if s:
        print("液溫 %.1f°C｜泵 %d rpm（%d%%）｜風扇 %d rpm（%d%%）" % (
            s["liquid"], s["pump_rpm"], s["pump_duty"], s["fan_rpm"], s["fan_duty"]))
    print("LCD 亮度 %d%%｜方向 %d°" % (i["brightness"], i["orientation"] * 90))


def cmd_rgb(color):
    """一次性把 16 顆 LED 設成同一色。"""
    c = rgb.hex_rgb(color)
    with KrakenLCD(need_bulk=False) as k:
        rgb.send_frame(k, [c] * rgb.LED_COUNT)
    print("RGB 已設為 #%02X%02X%02X" % c)


def cmd_rgb_test():
    """目視測試：紅 → 綠 → 藍（各 2 秒）→ 逐顆點亮（確認 LED 順序）→ 白。"""
    with KrakenLCD(need_bulk=False) as k:
        for name, c in (("紅", (255, 0, 0)), ("綠", (0, 255, 0)), ("藍", (0, 0, 255))):
            print("全部", name)
            rgb.send_frame(k, [c] * rgb.LED_COUNT)
            time.sleep(2)
        print("逐顆點亮：第一顆風扇紅色、第二顆風扇藍色")
        for i in range(rgb.LED_COUNT):
            leds = [(0, 0, 0)] * rgb.LED_COUNT
            for j in range(i + 1):
                leds[j] = (255, 0, 0) if j < 8 else (0, 0, 255)
            rgb.send_frame(k, leds)
            time.sleep(0.4)
        rgb.send_frame(k, [(255, 255, 255)] * rgb.LED_COUNT)
        print("結束：全部白色")


def cmd_preview():
    """用 config.json 的設定和目前感測值輸出 preview.png（輪播則每個畫面並排；液溫用 30°C 代替）。"""
    from sensors import Sensors
    cfg = load_config()
    faces, _ = current_faces(cfg, cfg.get("mode", "carousel"))
    s = Sensors()
    imgs = []
    for f in faces:
        if f.get("type") in ("single", "dual", "clock"):
            bg = load_gif(f["gif_background"])[0][0] if f.get("gif_background") else None
            img = render.render(f, face_readings(f, cfg, s, {"liquid": 30.0}), background=bg)
            imgs.append(render.circle_preview(img))   # 預覽顯示正向（旋轉只用在送到 LCD 的畫面）
    s.close()
    if not imgs:
        print("目前模式沒有可預覽的資訊圖／時鐘畫面")
        return
    sheet = Image.new("RGB", (250 * len(imgs) + 10, 260), (18, 18, 20))
    for i, im in enumerate(imgs):
        sheet.paste(im, (10 + i * 250, 10))
    out = os.path.join(HERE, "preview.png")
    sheet.save(out)
    print("已輸出", out)


def main():
    ap = argparse.ArgumentParser(description="NZXT Kraken LCD＋RGB 控制（取代 NZXT CAM，與 Fan Control 共存）")
    ap.add_argument("command", choices=["run", "ui", "liquid", "brightness", "orientation", "rgb", "rgb-test",
                                        "status", "preview"])
    ap.add_argument("value", nargs="?", help="brightness／orientation 的數值，或 rgb 的顏色")
    ap.add_argument("--mode", help="run 時臨時覆寫 config.json 的 mode")
    ap.add_argument("-v", "--verbose", action="store_true", help="輸出除錯訊息")
    ap.add_argument("--restarted", action="store_true", help="（內部用）由網頁控制台重新啟動時帶入，會等舊程序結束")
    args = ap.parse_args()
    setup_logging(args.verbose)

    if args.command == "run":
        global _MUTEX
        _MUTEX = single_instance()
        if _MUTEX is None and args.restarted:      # 重新啟動：舊程序可能還沒完全結束，最多等 15 秒
            for _ in range(150):
                time.sleep(0.1)
                _MUTEX = single_instance()
                if _MUTEX is not None:
                    break
        if _MUTEX is None:
            _LOG.error("已有另一個 kraken_lcd run 在執行，本次結束")
            sys.exit(1)
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
        try:
            run_loop(args)
        finally:
            if _restart:                            # 收尾就算出錯也要把新程序開起來
                _spawn_self(args)
    elif args.command == "ui":
        cmd_ui()
    elif args.command == "liquid":
        with KrakenLCD(need_bulk=False) as k:
            k.set_liquid_mode()
    elif args.command == "brightness":
        with KrakenLCD(need_bulk=False) as k:
            k.set_brightness_orientation(brightness=int(args.value))
    elif args.command == "orientation":
        with KrakenLCD(need_bulk=False) as k:
            k.set_brightness_orientation(orientation=int(args.value) // 90)
    elif args.command == "rgb":
        cmd_rgb(args.value or "FFFFFF")
    elif args.command == "rgb-test":
        cmd_rgb_test()
    elif args.command == "status":
        cmd_status()
    elif args.command == "preview":
        cmd_preview()


if __name__ == "__main__":
    main()
