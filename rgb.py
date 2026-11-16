# -*- coding: utf-8 -*-
"""
rgb.py：Kraken 2024 Plus 的 RGB 燈效（F240 RGB Core 風扇，2 顆 × 8 顆 LED）

協定（NZXT HUE2 的 Direct 寫法；OpenKraken issue #12 已在 Kraken 2024 Plus 實機確認）：
  1) [0x22, 0x10, mask, 0x00] + 每顆 LED 3 位元組（順序 G, R, B），最多 20 顆
  2) [0x22, 0xA0, mask, 0x00, 0x01, 0x00, 0x00, 0x28, 0x00, 0x00, 0x80, 0x00, 0x32, 0x00, 0x00, 0x01] 套用
  mask = 0x01（Plus 只有通道 0，接的就是散熱排風扇）。
2026-11-15 實機對照測試：Direct 逐格送每秒 10 格時燈完全不更新、每秒 2 格才會動（卡頓）；
而 HUE2 韌體燈效封包 0x2A 0x04 送一次就能跑出流暢動畫（CAM 也是這樣做）。
所以有韌體模式的效果一律走 0x2A 0x04（見 HW_MODES），只有音樂律動、溫度變色、
自訂每顆顏色等韌體做不到的效果才逐格送（速率上限 STREAM_MAX_FPS）。
亮度沒有裝置指令，直接乘在顏色上。

LED 編號假設：0～7 為第一顆風扇、8～15 為第二顆，各自沿圓周排列（方向需目視確認）。
"""
import colorsys
import logging
import math
import random
import threading
import time

_LOG = logging.getLogger("rgb")

LED_COUNT = 16
LEDS_PER_FAN = 8
SPEEDS = [0.4, 0.7, 1.0, 1.5, 2.2]   # CAM 速度滑桿 5 段，中間為「正常」

# 各效果的 CAM 預設值（擷取自 CAM 4.76.9，見 FEATURES.md 第 5 節）
EFFECT_DEFAULTS = {
    "fixed": {"colors": ["#FFFFFF"]},
    "spectrum_wave": {},
    "breathing": {"colors": ["#6B00DE"]},
    "fading": {"colors": ["#8A00FC", "#00FF00", "#0000FF"]},
    "covering_marquee": {"colors": ["#6B00DE", "#D600BF"]},
    "temperature_monitor": {"temp_source": "LiquidTemp", "temp_range": [15, 90],
                            "gradient": [[0, "#00FF00"], [50, "#FFFF00"], [100, "#FF0000"]]},
    "marquee": {"colors": ["#6B00DE"], "led_size": 3},
    "pulse": {"colors": ["#6B00DE"]},
    "super_rainbow": {},
    "audio_bass": {"colors": ["#FFFFFF"]},
    "audio_level": {"gradient": [[0, "#00FF00"], [50, "#FFFF00"], [100, "#FF0000"]]},
    "rainbow_flow": {},
    "alternating": {"colors": ["#6B00DE", "#D600BF"], "led_size": 4, "moving": True},
    "tai_chi": {"colors": ["#FF0000", "#0000FF"]},
    "starry_night": {"colors": ["#51007A"]},
    "rainbow_pulse": {},
    "candle": {"colors": ["#FFA500"]},
    "wave": {"colors": ["#FFFFFF"]},
    "water_cooler": {},
    "loading": {"colors": ["#51007A"]},
}


# ---------------- 小工具 ----------------
def hex_rgb(c):
    c = str(c).lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (255, 255, 255)


def scale(c, k):
    """顏色乘上亮度係數 k（0~1）。"""
    k = max(0.0, min(1.0, k))
    return tuple(int(round(v * k)) for v in c)


def mix(a, b, t):
    """兩色線性混合。"""
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def hue(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def gradient(stops, t):
    """色標 [[位置0~100,'#hex'],...] 在 t（0~1）處的顏色。"""
    pts = sorted((float(p) / 100.0, hex_rgb(c)) for p, c in stops)
    t = max(0.0, min(1.0, t))
    for (p0, c0), (p1, c1) in zip(pts, pts[1:]):
        if t <= p1:
            return mix(c0, c1, (t - p0) / max(1e-6, p1 - p0))
    return pts[-1][1]


def pos(i):
    """LED i → (風扇編號, 圓周上的位置 0~1)。"""
    return i // LEDS_PER_FAN, (i % LEDS_PER_FAN) / LEDS_PER_FAN


# ---------------- 音訊（Audio Bass／Audio Level 用）----------------
AUDIO_DEFAULTS = {             # 音訊燈效的擷取設定（config.json 的 rgb 區段，網頁控制台可調）
    "audio_device": "",        # 擷取哪個播放裝置的聲音；空字串＝跟隨 Windows 預設播放裝置
    "audio_auto_gain": True,   # 自動增益：依近期最大音量自動調整，小聲的歌也會亮滿
    "audio_sensitivity": 100,  # 靈敏度 %：最後再乘上的倍率（自動或手動增益都適用）
    "audio_gate": 0,           # 雜訊門檻 %（滿刻度振幅的百分比）：低於門檻一律當成安靜；預設 0＝不擋（底噪已由自動增益的下限壓住）
}
AUDIO_STATUS = {"device": "", "ok": False, "level": 0.0, "bass": 0.0, "raw_level": 0.0, "raw_bass": 0.0}


def _windows_audio():
    """
    即時向 Windows 查詢播放裝置清單與預設裝置（audio_devices.py，Core Audio）。
    每次都是最新狀態（拔插、改預設立刻反映）；查詢失敗回傳 None，呼叫端改用 PortAudio 的結果。
    """
    try:
        import audio_devices
        return audio_devices.list_render_devices()
    except Exception as e:
        _LOG.debug("Windows 音訊裝置查詢失敗：%s", e)
        return None


def list_audio_devices():
    """
    列出可擷取的播放裝置名稱與目前的 Windows 預設裝置（給網頁控制台）。
    優先用 Windows Core Audio 即時查詢；失敗才退回 PortAudio
    （PortAudio 在音訊燈效擷取中不會重新列舉，拔插裝置不會反映）。
    """
    win = _windows_audio()
    if win is not None:
        return win
    try:
        import pyaudiowpatch as pa
    except ImportError:
        return {"devices": [], "default": ""}
    p = pa.PyAudio()
    try:
        names = [_loop_name(d["name"]) for d in p.get_loopback_device_info_generator()]
        try:
            default = _loop_name(p.get_default_wasapi_loopback()["name"])
        except Exception:
            default = ""
        return {"devices": names, "default": default}
    finally:
        p.terminate()


def _loop_name(name):
    """裝置名稱去掉 pyaudiowpatch 加的「 [Loopback]」字尾，給使用者看也拿來比對設定。"""
    return name.replace(" [Loopback]", "")


class AudioMeter:
    """
    擷取系統正在播放的聲音（WASAPI loopback，需要 pyaudiowpatch），在背景執行緒計算：
    level：整體音量 0~1；bass：低音（150 Hz 以下）強度 0~1。
    沒有安裝 pyaudiowpatch 時兩者固定為 0，並只記錄一次警告。

    2026-11-15 修正「沒播聲音燈卻亂跳、登入後音訊燈效失控」：
    - 舊的自動增益記住「近期最大值」但沒有下限，安靜時最大值一路衰減到底噪（約 1e-10），
      底噪就被放大成全亮。現在最大值有下限 PEAK_FLOOR，另有雜訊門檻（audio_gate）。
    - 數值改用與取樣率無關的振幅（0~1，1＝滿刻度），96 kHz 裝置用較大的分析區塊維持低頻解析度。
    - 不用阻塞式讀取：先查可讀量，沒資料時數值平滑淡出，不會卡在最後一刻。
    - 每 DEVICE_CHECK_S 秒向 Windows 即時查一次播放裝置（Core Audio）：
      跟隨預設時，Windows 預設裝置一換就切過去；指定的裝置拔掉時改用預設裝置，插回來就切回。
      超過 NO_DATA_REOPEN_S 秒完全沒資料也重開。
      PortAudio 只在完全重新初始化時才重新列舉裝置，所以每次切換都整個 terminate 再建立。
      （Windows 查詢失敗時，退回舊做法：跟隨預設時安靜一陣子就重開一次重抓預設裝置。）
    - 任何例外（裝置拔除等）都在 RETRY_S 秒後重試，執行緒不會結束。
    """
    NO_DATA_REOPEN_S = 5.0     # 這麼久完全沒資料就重開擷取
    DEVICE_CHECK_S = 2.0       # 每隔這麼久向 Windows 查一次播放裝置（預設是否改變、指定裝置是否拔插）
    QUIET_RECHECK_S = 15.0     # （Windows 查詢失敗時的備援）跟隨預設時，安靜狀態下每隔這麼久重抓預設裝置
    RETRY_S = 2.0              # 出錯後等多久重試
    FADE_S = 0.25              # 沒資料時數值淡出的時間常數
    PEAK_DECAY_S = 4.0         # 自動增益的最大值衰減時間常數
    PEAK_FLOOR = 0.05          # 自動增益最大值的下限（振幅），防止底噪被放大
    MANUAL_FULL = 0.5          # 手動增益時，振幅到這麼大算滿格（再乘靈敏度）

    def __init__(self, cfg=None):
        self.level = 0.0
        self.bass = 0.0
        self._ok = False
        self._stop = False
        self._reopen = False
        self._peak_l, self._peak_b = self.PEAK_FLOOR, self.PEAK_FLOOR   # 重開擷取時保留
        self.device = ""                             # 目前擷取的裝置名稱
        self._warned = set()                         # 已警告過「找不到」的指定裝置（同一裝置只警告一次）
        self._target_miss = ""                       # 想用卻在 PortAudio 找不到的裝置（避免每 2 秒重開一次）
        self.configure(cfg or {})
        threading.Thread(target=self._run, daemon=True).start()

    def configure(self, cfg):
        """套用擷取設定（換效果或改參數時呼叫）；換了指定裝置就讓擷取執行緒重開。"""
        get = lambda k: cfg.get(k, AUDIO_DEFAULTS[k])
        want = str(get("audio_device") or "")
        if want != getattr(self, "want_device", want):
            self._reopen = True
        self.want_device = want
        self.auto_gain = bool(get("audio_auto_gain"))
        self.sensitivity = max(0.0, float(get("audio_sensitivity"))) / 100.0
        self.gate = max(0.0, float(get("audio_gate"))) / 100.0

    def _publish(self):
        AUDIO_STATUS.update(device=self.device, ok=self._ok, level=round(self.level, 3), bass=round(self.bass, 3))

    def _run(self):
        try:
            import numpy as np
            import pyaudiowpatch as pa
        except ImportError:
            _LOG.warning("未安裝 pyaudiowpatch，音訊燈效改顯示靜態顏色（pip install pyaudiowpatch）")
            return
        fails = 0
        while not self._stop:
            try:
                self._capture(np, pa)        # 正常情況會一直待在裡面；回傳表示要重開
                fails = 0
            except Exception as e:
                self._ok = False
                self._publish()
                fails += 1
                if fails <= 3 or fails % 30 == 0:          # 裝置長時間不在時避免洗版
                    _LOG.warning("音訊擷取失敗（第 %d 次），%.0f 秒後重試：%s", fails, self.RETRY_S, e)
                time.sleep(self.RETRY_S)
        AUDIO_STATUS.update(ok=False, level=0.0, bass=0.0)

    def _target(self, win):
        """依設定與 Windows 即時清單，算出「應該擷取哪個裝置」的名稱（win 為 None 時回傳空字串）。"""
        if win is None:
            return ""
        if self.want_device and self.want_device in win["devices"]:
            return self.want_device
        return win["default"]

    def _pick_device(self, p, win):
        """
        依設定挑裝置，回傳 (PortAudio 裝置資訊, 是否跟隨預設)：
        有指定且找得到就用它；否則用 Windows 預設播放裝置。
        指定裝置找不到時只在第一次記錄警告，裝置回來後清掉紀錄（下次拔掉會再警告一次）。
        """
        loops = {_loop_name(d["name"]): d for d in p.get_loopback_device_info_generator()}
        target = self._target(win)
        self._target_miss = target if target and target not in loops else ""
        if self.want_device:
            # 指定裝置要 Windows 清單與 PortAudio 都有才算在（以 Windows 即時清單為準）
            if self.want_device in loops and (win is None or self.want_device in win["devices"]):
                self._warned.discard(self.want_device)
                return loops[self.want_device], False
            if self.want_device not in self._warned:
                self._warned.add(self.want_device)
                _LOG.warning("找不到指定的音訊裝置「%s」，先改用 Windows 預設裝置，裝置接回後會自動切回", self.want_device)
        if win is not None and win["default"] in loops:
            return loops[win["default"]], True
        return p.get_default_wasapi_loopback(), True

    def _capture(self, np, pa):
        """開啟裝置並持續分析；需要重開（換裝置、長時間沒資料、跟隨預設時定期重抓）就回傳。"""
        p = pa.PyAudio()
        st = None
        try:
            dev, follow = self._pick_device(p, _windows_audio())
            rate, ch = int(dev["defaultSampleRate"]), max(1, dev["maxInputChannels"])
            n = 1024 if rate <= 48000 else 2048            # 每格約 21 ms；96 kHz 用 2048 維持低頻解析度
            st = p.open(format=pa.paFloat32, channels=ch, rate=rate, input=True,
                        input_device_index=dev["index"], frames_per_buffer=n)
            name = _loop_name(dev["name"])
            if name != self.device:
                _LOG.info("音訊擷取裝置：%s（%d Hz、%d 聲道）", name, rate, ch)
            self.device = name
            self._ok = True
            self._reopen = False
            freqs = np.fft.rfftfreq(n, 1.0 / rate)
            low = (freqs > 0) & (freqs < 150)               # 低音頻段（不含直流）
            if not low.any():
                low[1] = True
            win = np.hanning(n)
            norm = 2.0 / win.sum()                          # FFT 幅度換成正弦波振幅（0~1）
            t_open = last_data = last_tick = last_loud = last_check = time.time()
            win_ok = True                                   # Windows 即時查詢是否可用
            while not self._stop and not self._reopen:
                now = time.time()
                if st.get_read_available() >= n:
                    x = np.frombuffer(st.read(n, exception_on_overflow=False), np.float32)
                    x = x.reshape(-1, ch).mean(axis=1)
                    lv = float(np.sqrt(2.0 * np.mean(x * x)))                    # 整體振幅
                    bs = float((np.abs(np.fft.rfft(x * win)) * norm)[low].max())  # 低音振幅
                    AUDIO_STATUS.update(raw_level=round(lv, 5), raw_bass=round(bs, 5))
                    lv = lv if lv >= self.gate else 0.0              # 雜訊門檻
                    bs = bs if bs >= self.gate else 0.0
                    if lv > 0:
                        last_loud = now
                    if self.auto_gain:
                        k = math.exp(-(now - last_tick) / self.PEAK_DECAY_S)
                        self._peak_l = max(lv, self._peak_l * k, self.PEAK_FLOOR)
                        self._peak_b = max(bs, self._peak_b * k, self.PEAK_FLOOR)
                        lvn, bsn = lv / self._peak_l, bs / self._peak_b
                    else:
                        lvn, bsn = lv / self.MANUAL_FULL, bs / self.MANUAL_FULL
                    self.level = min(1.0, lvn * self.sensitivity)
                    self.bass = min(1.0, bsn * self.sensitivity)
                    last_data = now
                else:
                    # 沒有新資料：數值依時間常數淡出，不卡在最後一刻
                    k = math.exp(-(now - last_tick) / self.FADE_S)
                    self.level *= k
                    self.bass *= k
                    if now - last_data > self.NO_DATA_REOPEN_S:
                        return
                    time.sleep(0.01)
                last_tick = now
                self._publish()
                # 定時向 Windows 查播放裝置：該擷取的裝置變了（改預設、指定裝置拔插）就重開
                if now - last_check >= self.DEVICE_CHECK_S:
                    last_check = now
                    wa = _windows_audio()                   # 不能叫 win：win 是上面的漢寧窗
                    win_ok = wa is not None
                    target = self._target(wa)
                    if target and target != self.device and target != self._target_miss:
                        _LOG.info("播放裝置有變動，改擷取：%s", target)
                        return
                # 備援（Windows 查詢失敗時）：跟隨預設、安靜超過 3 秒且距開啟已久，就重開一次重抓預設裝置
                if not win_ok and follow and now - last_loud > 3.0 and now - t_open > self.QUIET_RECHECK_S:
                    return
        finally:
            if st is not None:
                try:
                    st.stop_stream()
                    st.close()
                except Exception:
                    pass
            p.terminate()

    def close(self):
        self._stop = True


# ---------------- 效果 ----------------
class RgbEngine:
    """
    依 config.json 的 "rgb" 區段算出每一格 16 顆 LED 的顏色並送出。
    tick() 由主迴圈頻繁呼叫，內部依 fps 控制送出頻率；畫面沒變就每 5 秒重送一次（防裝置重置）。
    """

    def __init__(self, cfg, sensors=None, prev=None):
        """prev：前一個引擎；只改顏色／速度等參數時接手它的動畫時間與音訊擷取，畫面才不會跳回起點。"""
        self.cfg = dict(cfg or {})
        eff = self.cfg.get("effect", "fixed")
        for k, v in EFFECT_DEFAULTS.get(eff, {}).items():   # 沒填的參數用 CAM 預設值
            self.cfg.setdefault(k, v)
        self.sensors = sensors
        if eff.startswith("audio_"):
            if prev is not None and prev.audio is not None:
                self.audio, prev.audio = prev.audio, None     # 沿用已開好的音訊擷取（重開要花時間）
                self.audio.configure(self.cfg)                # 但套用新的裝置／增益設定
            else:
                self.audio = AudioMeter(self.cfg)
        else:
            self.audio = None
        self.last_frame = None
        self.last_send = 0.0
        same_eff = prev is not None and prev.cfg.get("effect") == eff
        self.t0 = prev.t0 if same_eff else time.time()      # 同一種效果：延續動畫進度
        self.rand = random.Random(1)
        self.stars = [0.0] * LED_COUNT      # Starry Night 每顆亮度
        self.candle = [1.0] * LED_COUNT     # Candle 每顆亮度
        if same_eff:                        # 延續星空／燭光的隨機狀態
            self.rand, self.stars, self.candle = prev.rand, prev.stars, prev.candle
        self.temp = None
        self.sent = 0                       # 已送出的格數（效能統計用）
        self.hw_sent = False                # 韌體燈效封包是否已送出（每個引擎只送一次）

    @property
    def enabled(self):
        return bool(self.cfg.get("enable", False))

    def needs_liquid(self):
        return self.enabled and self.cfg.get("effect") == "temperature_monitor" \
            and self.cfg.get("temp_source", "LiquidTemp") == "LiquidTemp"

    def close(self):
        if self.audio:
            self.audio.close()

    # --- 參數 ---
    def colors(self):
        return [hex_rgb(c) for c in (self.cfg.get("colors") or ["#FFFFFF"])]

    def speed(self):
        try:
            return SPEEDS[int(self.cfg.get("speed", 2))]
        except (ValueError, IndexError):
            return 1.0

    def direction(self):
        return -1.0 if str(self.cfg.get("direction", "forward")) in ("backward", "reverse", "-1") else 1.0

    # --- 計算一格 ---
    def frame(self, now, status=None):
        """回傳 16 個 (r,g,b)。"""
        eff = self.cfg.get("effect", "fixed")
        t = (now - self.t0) * self.speed()
        d = self.direction()
        cols = self.colors()
        n = LED_COUNT
        out = [(0, 0, 0)] * n

        if eff == "fixed":
            per = self.cfg.get("leds") or []
            out = [hex_rgb(per[i]) if i < len(per) and per[i] else cols[0] for i in range(n)]

        elif eff == "spectrum_wave":          # 全部 LED 一起依色相循環
            out = [hue(t * 0.08)] * n

        elif eff == "breathing":              # 依序對每個顏色做一次呼吸
            cyc = t * 0.25
            c = cols[int(cyc) % len(cols)]
            out = [scale(c, math.sin(math.pi * (cyc % 1.0)) ** 2)] * n

        elif eff == "fading":                 # 顏色之間平順轉換
            cyc = t * 0.25
            i = int(cyc) % len(cols)
            out = [mix(cols[i], cols[(i + 1) % len(cols)], cyc % 1.0)] * n

        elif eff == "covering_marquee":       # 一顆顆把新顏色蓋上去
            step = int(t * 6)
            i = (step // LEDS_PER_FAN) % len(cols)
            k = step % LEDS_PER_FAN
            new, old = cols[(i + 1) % len(cols)], cols[i]
            for j in range(n):
                p = j % LEDS_PER_FAN
                p = p if d > 0 else LEDS_PER_FAN - 1 - p
                out[j] = new if p <= k else old

        elif eff == "temperature_monitor":    # 依溫度在漸層上取色
            v = self._temperature(status)
            lo, hi = self.cfg.get("temp_range", [15, 90])
            c = gradient(self.cfg["gradient"], 0.0 if v is None else (v - lo) / max(1e-6, hi - lo))
            out = [c] * n

        elif eff == "marquee":                # 一段 led_size 顆的光點繞圈
            size = int(self.cfg.get("led_size", 3))
            head = (t * 6 * d) % LEDS_PER_FAN
            for j in range(n):
                p = j % LEDS_PER_FAN
                dist = (head - p) % LEDS_PER_FAN if d > 0 else (p - head) % LEDS_PER_FAN
                out[j] = cols[0] if dist < size else (0, 0, 0)

        elif eff == "pulse":                  # 快速亮起、慢慢熄滅，依序換色
            cyc = t * 0.8
            c = cols[int(cyc) % len(cols)]
            ph = cyc % 1.0
            out = [scale(c, (ph / 0.15) if ph < 0.15 else (1 - (ph - 0.15) / 0.85) ** 2)] * n

        elif eff == "super_rainbow":          # 彩虹沿圓周快速流動
            out = [hue(pos(j)[1] * d + t * 0.6) for j in range(n)]

        elif eff == "rainbow_flow":           # 彩虹沿圓周平順流動
            out = [hue(pos(j)[1] * d + t * 0.15) for j in range(n)]

        elif eff == "rainbow_pulse":          # 彩虹分布＋整體脈動
            k = 0.5 + 0.5 * math.sin(t * 2.5)
            out = [hue(pos(j)[1] * d + t * 0.1, 1, 0.15 + 0.85 * k) for j in range(n)]

        elif eff == "alternating":            # 兩色交錯，每組 led_size 顆，可移動
            size = max(1, int(self.cfg.get("led_size", 4)))
            shift = int(t * 4 * d) if self.cfg.get("moving", True) else 0
            a, b = cols[0], cols[1 % len(cols)]
            out = [a if ((j % LEDS_PER_FAN + shift) // size) % 2 == 0 else b for j in range(n)]

        elif eff == "tai_chi":                # 兩色各占半圈，旋轉
            a, b = cols[0], cols[1 % len(cols)]
            rot = t * 0.4 * d
            out = [a if ((pos(j)[1] + rot) % 1.0) < 0.5 else b for j in range(n)]

        elif eff == "starry_night":           # 隨機閃爍
            for j in range(n):
                if self.stars[j] <= 0.02 and self.rand.random() < 0.04 * self.speed():
                    self.stars[j] = 1.0
                self.stars[j] *= 0.9
                out[j] = scale(cols[0], self.stars[j])

        elif eff == "candle":                 # 燭火閃動
            for j in range(n):
                self.candle[j] += (self.rand.uniform(0.55, 1.0) - self.candle[j]) * 0.35
                out[j] = scale(cols[0], self.candle[j])

        elif eff == "wave":                   # 自選每顆 LED 顏色，亮度波沿圓周移動
            per = self.cfg.get("leds") or []
            for j in range(n):
                c = hex_rgb(per[j]) if j < len(per) and per[j] else cols[0]
                out[j] = scale(c, 0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * (pos(j)[1] - t * 0.5 * d))))

        elif eff == "water_cooler":           # 藍色水流
            out = [hue(0.6 + 0.04 * math.sin(2 * math.pi * (pos(j)[1] + t * 0.3)), 1,
                       0.45 + 0.55 * (0.5 + 0.5 * math.sin(2 * math.pi * (pos(j)[1] * 2 - t * 0.4))))
                   for j in range(n)]

        elif eff == "loading":                # 單一光點帶拖尾繞圈
            head = (t * 5 * d) % LEDS_PER_FAN
            for j in range(n):
                p = j % LEDS_PER_FAN
                dist = (head - p) % LEDS_PER_FAN if d > 0 else (p - head) % LEDS_PER_FAN
                out[j] = scale(cols[0], max(0.0, 1 - dist / 4))

        elif eff == "audio_bass":             # 低音越強越亮
            k = self.audio.bass if self.audio and self.audio._ok else 1.0
            out = [scale(cols[0], k)] * n

        elif eff == "audio_level":            # 音量越大亮越多顆，顏色依位置取漸層
            lv = self.audio.level if self.audio and self.audio._ok else 1.0
            lit = int(round(max(0.0, min(1.0, lv)) * LEDS_PER_FAN))   # 亮幾顆：靜音 0 顆、滿音量 8 顆
            for j in range(n):
                i = j % LEDS_PER_FAN
                p = i / (LEDS_PER_FAN - 1)                             # 顏色依位置取漸層
                out[j] = gradient(self.cfg["gradient"], p) if i < lit else (0, 0, 0)

        bri = float(self.cfg.get("brightness", 100)) / 100.0
        return [scale(c, bri) for c in out]

    def _temperature(self, status):
        src = self.cfg.get("temp_source", "LiquidTemp")
        if src == "LiquidTemp":
            v = (status or {}).get("liquid")
        elif self.sensors is not None:
            v = self.sensors.read(src, status)[0]
        else:
            v = None
        if v is not None:
            self.temp = v
        return self.temp

    # --- 送出 ---
    def hw_packet(self):
        """
        這個效果若有對應的韌體內建模式，回傳 0x2A 0x04 封包（list）；否則回傳 None（改由電腦逐格送）。
        「固定顏色」有自訂每顆 LED 顏色時韌體做不到，也回傳 None。
        """
        eff = self.cfg.get("effect", "fixed")
        spec = HW_MODES.get(eff)
        if spec is None or (eff == "fixed" and any(self.cfg.get("leds") or [])):
            return None
        return build_hw_packet(eff, spec, self.cfg, self.colors(),
                               int(self.cfg.get("channel_mask", 1)))

    def tick(self, dev, now, status=None):
        """
        主迴圈呼叫。dev 為 KrakenLCD。
        有韌體模式的效果：只在引擎建立後送一次 0x2A 0x04，之後由韌體自己跑動畫（與 CAM 相同），
        只定期算一格給網頁控制台當預覽、不送裝置。
        其他效果：電腦端逐格送 Direct 封包；實測這代韌體每秒超過約 2 格就完全不更新，所以速率有上限。
        """
        if not self.enabled:
            return
        hw = self.hw_packet()
        if hw is not None:
            if not self.hw_sent:
                send_hw(dev, hw)
                self.hw_sent = True
                self.sent += 1
                _LOG.info("燈效：韌體模式 %s（0x%02X）", self.cfg.get("effect"), hw[4])
            if now - self.last_send >= 0.1:                 # 預覽每秒 10 格就夠，不送裝置
                self.last_frame, self.last_send = self.frame(now, status), now
            return
        fps = min(STREAM_MAX_FPS, max(0.2, float(self.cfg.get("fps", STREAM_MAX_FPS))))
        if now - self.last_send < 1.0 / fps:
            return
        fr = self.frame(now, status)
        if fr == self.last_frame and now - self.last_send < 5.0:
            return
        send_frame(dev, fr, int(self.cfg.get("channel_mask", 1)))
        self.last_frame, self.last_send = fr, now
        self.sent += 1


# ---------- 韌體內建燈效（0x2A 0x04） ----------
# 2026-11-15 實機測試：Kraken 2024 Plus 接受 liquidctl KrakenX3 格式的 0x2A 0x04 封包，
# 送一次後韌體自己跑出流暢動畫；Direct 逐格送每秒超過約 2 格就不更新。
# 模式值、速度表、封包尾端欄位取自 liquidctl kraken3.py 的 _COLOR_MODES／_SPEED_VALUE／_write_colors。
# 每項：(模式值, 速度表編號, 最多顏色數, 尾端「模式相關」byte, 方向基準 byte)
HW_MODES = {
    "fixed": (0x00, 0, 1, 0x00, 0x00),
    "fading": (0x01, 1, 8, 0x08, 0x00),
    "spectrum_wave": (0x02, 2, 0, 0x00, 0x00),
    "marquee": (0x03, 2, 1, 0x00, 0x04),
    "covering_marquee": (0x04, 2, 8, 0x00, 0x00),
    "alternating": (0x05, 3, 2, 0x00, 0x00),        # moving=True 時改用速度表 4、方向基準 0x01
    "pulse": (0x06, 5, 8, 0x08, 0x00),
    "breathing": (0x07, 6, 8, 0x08, 0x00),
    "candle": (0x08, 0, 1, 0x00, 0x00),
    "starry_night": (0x09, 5, 1, 0x00, 0x01),
    "rainbow_flow": (0x0B, 2, 0, 0x00, 0x00),
    "super_rainbow": (0x0C, 2, 0, 0x00, 0x00),
    "rainbow_pulse": (0x0D, 2, 0, 0x00, 0x00),
    "tai_chi": (0x0E, 7, 2, 0x05, 0x00),
    "water_cooler": (0x0F, 6, 2, 0x05, 0x00),
    "loading": (0x10, 8, 1, 0x04, 0x00),
}

# 速度表：編號 → (最慢, 慢, 正常, 快, 最快) 的兩 byte 計時值（越小越快）
HW_SPEED = {
    0: ([0x32, 0x00],) * 5,
    1: ([0x50, 0x00], [0x3C, 0x00], [0x28, 0x00], [0x14, 0x00], [0x0A, 0x00]),
    2: ([0x5E, 0x01], [0x2C, 0x01], [0xFA, 0x00], [0x96, 0x00], [0x50, 0x00]),
    3: ([0x40, 0x06], [0x14, 0x05], [0xE8, 0x03], [0x20, 0x03], [0x58, 0x02]),
    4: ([0x20, 0x03], [0xBC, 0x02], [0xF4, 0x01], [0x90, 0x01], [0x2C, 0x01]),
    5: ([0x19, 0x00], [0x14, 0x00], [0x0F, 0x00], [0x07, 0x00], [0x04, 0x00]),
    6: ([0x28, 0x00], [0x1E, 0x00], [0x14, 0x00], [0x0A, 0x00], [0x04, 0x00]),
    7: ([0x32, 0x00], [0x28, 0x00], [0x1E, 0x00], [0x14, 0x00], [0x0A, 0x00]),
    8: ([0x14, 0x00],) * 5,
}

# 通道對應的「同步」值（liquidctl 的 _STATIC_VALUE；通道 0x01 為 40）
HW_STATIC = {0x01: 40, 0x02: 8, 0x04: 1, 0x07: 40}

# Direct 逐格送的速率上限（格／秒）：韌體要等指令停約 0.3 秒才會處理，送太快就完全不更新
STREAM_MAX_FPS = 2.5   # 2026-11-15 實測：每秒 3 格還會動、4 格以上就卡住；留一點餘裕


def build_hw_packet(eff, spec, cfg, colors, mask=0x01):
    """組 0x2A 0x04 韌體燈效封包。colors 為 (r,g,b) 清單；亮度直接乘在顏色上（裝置沒有亮度指令）。"""
    mval, speed_tab, maxc, mode_related, back = spec
    moving = eff == "alternating" and bool(cfg.get("moving", True))
    if moving:                                              # 會移動的交錯：另一組速度表、方向基準
        speed_tab, back = 4, 0x01
    try:
        sval = max(0, min(4, int(cfg.get("speed", 2))))
    except (TypeError, ValueError):
        sval = 2
    speed = HW_SPEED[speed_tab][sval]
    bri = max(0.0, min(1.0, float(cfg.get("brightness", 100)) / 100.0))
    if maxc == 0:                                           # 彩虹類不用顏色，送一個黑色佔位
        cols = [(0, 0, 0)]
    elif eff == "water_cooler":
        # CAM 的水冷效果沒有顏色選項；韌體需要兩色，只給一色會整片白（2026-11-15 實測藍＋白才是流動的藍色水流）
        cols = [scale((0, 0, 255), bri), scale((255, 255, 255), bri)]
    else:
        cols = [scale(c, bri) for c in colors[:maxc]] or [(255, 255, 255)]
    count = len(cols) if maxc else 0
    if eff == "water_cooler":                               # liquidctl：水冷模式顏色數固定填 1
        count = 1
    data = []
    for r, g, b in cols:
        data += [g, r, b]                                   # 線上順序 G, R, B
    data += [0, 0, 0] * (16 - len(cols))
    if str(cfg.get("direction", "forward")) in ("backward", "reverse", "-1"):
        back += 0x02
    size = int(cfg.get("led_size", 3)) if mval in (0x03, 0x05) else 0x03
    size = max(3, min(6, size)) if mval in (0x03, 0x05) else size   # 韌體只支援 3～6 顆一組
    return ([0x2A, 0x04, mask, mask, mval] + speed + data
            + [back, count, mode_related, HW_STATIC.get(mask, 40), size])


def send_hw(dev, pkt):
    """送出一個韌體燈效封包（與 LCD 傳圖共用裝置鎖）。"""
    lock = getattr(dev, "lock", None)
    if lock is None:
        import contextlib
        lock = contextlib.nullcontext()
    with lock:
        dev._write(pkt)


def send_frame(dev, leds, mask=0x01):
    """把 LED 顏色清單（(r,g,b)，最多 40 顆）用 Direct 封包送到裝置。"""
    data = []
    for r, g, b in leds:
        data += [g, r, b]                     # 線上順序為 G, R, B
    lock = getattr(dev, "lock", None)
    if lock is None:                          # 沒有裝置鎖（舊介面）時用一個不起作用的替代品
        import contextlib
        lock = contextlib.nullcontext()
    with lock:                                # 一格的封包連續送完，不讓 LCD 傳圖插進來
        dev._write([0x22, 0x10, mask, 0x00] + data[:60])
        if len(data) > 60:                    # 超過 20 顆才需要第二包
            dev._write([0x22, 0x11, mask, 0x00] + data[60:120])
        dev._write([0x22, 0xA0, mask, 0x00, 0x01, 0x00, 0x00, 0x28,
                    0x00, 0x00, 0x80, 0x00, 0x32, 0x00, 0x00, 0x01])
