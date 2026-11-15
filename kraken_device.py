# -*- coding: utf-8 -*-
"""
kraken_device.py：NZXT Kraken 2024 Plus（VID 0x1E71 / PID 0x3014）LCD 低階控制

設計重點（為什麼不用 NZXT CAM）：
- CAM 會「獨占」開啟水冷的 HID 介面，Fan Control 的 NzxtKraken 外掛就讀不到液溫、
  也無法控制泵／風扇。
- 本模組只用「共用模式」開 HID（hidapi 預設 FILE_SHARE_READ|WRITE），而且只送 LCD
  相關指令（0x30 亮度方向、0x36 傳圖、0x38 切模式），完全不碰泵／風扇轉速指令，
  所以 Fan Control 可以照常控制。
- LCD 畫面走另一個 USB 介面（MI_00，WinUSB bulk 端點 0x02），與 HID 互不干擾。

協定（參考 liquidctl kraken3.py 與 PR #912，2024 Plus 只能用 bucket-free 傳圖）：
- HID [0x36,0x01,0x00,0x01,0x06]          → 開始傳輸
- bulk 標頭 12 FA 01 E8 AB CD EF 98 76 54 32 10 + [06 00 00 00] + 長度(4 bytes little-endian)
- bulk 資料：240x240 RGB565（大端序，每像素 2 bytes，共 115200 bytes）
- HID [0x36,0x02]                          → 結束傳輸，畫面更新
- HID [0x38,0x01,0x02,0x00]                → 切回韌體內建「液溫」畫面
- HID [0x30,0x02,0x01,亮度,0,0,1,方向]       → 設亮度（0–100）與方向（0–3，×90°）
"""
import ctypes
import logging
import threading
import time
import winreg

from liquidctl import find_liquidctl_devices

_LOG = logging.getLogger("kraken")

VID = 0x1E71
PID_2024_PLUS = 0x3014
LCD_SIZE = (240, 240)

# bucket-free 傳圖用的固定魔術標頭（與 CAM／liquidctl 相同）
_BULK_MAGIC = [0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD, 0xEF, 0x98, 0x76, 0x54, 0x32, 0x10]
_BULK_CHUNK = 1024 * 1024 * 2  # 一次 bulk 寫入的最大長度
FRAME_SETTLE_S = 0.03   # 畫面資料傳完到送「結束」之間的等待（秒），避免畫面撕裂
REPLY_TIMEOUT_MS = 100  # 等待指令回應的上限（毫秒）。liquidctl 預設 5000ms，裝置沒回時會讓主迴圈卡 5 秒


def _read_interface_guid(pid, serial):
    """
    從登錄檔讀出 MI_00（WinUSB 介面）的 DeviceInterfaceGUIDs。
    這台水冷的 GUID 是廠商自訂的 {30143013-...}，不是 winusbcdc 預設的通用 GUID，
    所以 liquidctl 原版會找不到 bulk 介面，LCD 無法傳圖。
    """
    base = r"SYSTEM\CurrentControlSet\Enum\USB\VID_%04X&PID_%04X&MI_00" % (VID, pid)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            i = 0
            while True:
                try:
                    inst = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, inst + r"\Device Parameters") as p:
                        val, _ = winreg.QueryValueEx(p, "DeviceInterfaceGUIDs")
                        if isinstance(val, list) and val:
                            return val[0]
                except OSError:
                    continue
    except OSError:
        pass
    return None


def _make_ctypes_guid(guid_cls, text):
    """把 '{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}' 字串轉成 winusbcdc 的 GUID 結構。"""
    h = text.strip("{}").replace("-", "")
    d1, d2, d3 = int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16)
    # winusbcdc 的 GUID.Data4 宣告為 c_byte（有號）陣列，大於 127 的值要轉成負數
    d4 = (ctypes.c_byte * 8)(*[b - 256 if b > 127 else b for b in bytes.fromhex(h[16:32])])
    return guid_cls(d1, d2, d3, d4)


class KrakenLCD:
    """開啟水冷、送畫面、切模式。用 with 語法確保結束時釋放裝置。"""

    def __init__(self, need_bulk=True):
        # need_bulk=False：只用 HID（讀狀態、亮度、切液溫），不佔用傳圖介面；
        # WinUSB 只能被一個程式開啟，背景的 run 正在推畫面時，其他指令就要用這個模式
        self.need_bulk = need_bulk
        self.dev = None          # liquidctl 的 KrakenZ3 物件（只借用它的 HID 讀寫）
        self.bulk = None         # winusbcdc.WinUsbPy（LCD 畫面傳輸）
        self._first_frame = True  # 開機後第一張圖要送兩次（韌體行為，CAM 也這樣做）
        # 裝置鎖：RGB 在獨立執行緒送封包，LCD 傳圖在主迴圈；同一時間只讓一方使用 USB，
        # 避免 RGB 的 HID 封包插進傳圖的「開始→資料→結束」之間（可重入，方法之間可互相呼叫）
        self.lock = threading.RLock()
        self.frames_sent = 0      # 已送出的 LCD 畫面數（效能統計用）
        self.frame_retries = 0    # 傳圖未確認而重送的次數（追查疊圖用）

    # ---------- 連線 ----------
    def open(self):
        devs = [d for d in find_liquidctl_devices(vendor=VID) if "Kraken" in d.description]
        if not devs:
            raise RuntimeError("找不到 NZXT Kraken（請確認 USB 已連接、NZXT CAM 已完全關閉）")
        self.dev = devs[0]
        self.dev.connect()  # 共用模式開 HID；不呼叫 initialize()，避免干擾 Fan Control

        if not self.need_bulk:
            if getattr(self.dev, "bulk_device", None) is not None:
                try:
                    self.dev.bulk_device.close_winusb_device()
                except Exception:
                    pass
                self.dev.bulk_device = None
            return self

        # liquidctl 自己找 bulk 介面失敗時（GUID 不同），改用登錄檔讀到的 GUID 重找
        self.bulk = getattr(self.dev, "bulk_device", None)
        if self.bulk is None:
            self.bulk = self._open_bulk_with_registry_guid()
            self.dev.bulk_device = self.bulk
        if self.bulk is None:
            raise RuntimeError("無法開啟 LCD 傳圖介面（MI_00）：可能被另一個 kraken_lcd run 或 NZXT CAM 佔用")
        return self

    def _open_bulk_with_registry_guid(self):
        """
        直接組出 WinUSB 介面的裝置路徑並開啟。
        實測 winusbcdc.list_usb_devices() 在這台電腦列不出任何裝置（介面明明存在），
        所以改成：從登錄檔列出 MI_00 的實例 ID，配上介面 GUID 組路徑，逐一嘗試開啟；
        拔掉的舊實例會開啟失敗，自然跳過。
        """
        from winusbcdc import WinUsbPy

        pid = self.dev.product_id
        base = r"SYSTEM\CurrentControlSet\Enum\USB\VID_%04X&PID_%04X&MI_00" % (VID, pid)
        candidates = []  # (實例 ID, [GUID...])
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                i = 0
                while True:
                    try:
                        inst = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    guids = []
                    try:
                        with winreg.OpenKey(k, inst + r"\Device Parameters") as p:
                            val, _ = winreg.QueryValueEx(p, "DeviceInterfaceGUIDs")
                            guids = list(val) if isinstance(val, list) else [val]
                    except OSError:
                        pass
                    # 通用 WinUSB GUID 也試（這台兩個 GUID 都有註冊）
                    guids.append("{dee824ef-729b-4a0e-9c14-b7117d33a817}")
                    candidates.append((inst, guids))
        except OSError:
            return None

        w = WinUsbPy()
        for inst, guids in candidates:
            for g in guids:
                path = r"\\?\usb#vid_%04x&pid_%04x&mi_00#%s#%s" % (VID, pid, inst.lower(), g.lower())
                try:
                    ok = w.init_winusb_device_with_path(path)
                except OSError as e:
                    # 已被其他程式（例如背景的 run 或 NZXT CAM）佔用，或該實例已拔除
                    _LOG.debug("無法開啟 %s：%s", path, e)
                    ok = False
                if ok:
                    _LOG.debug("已開啟 WinUSB 介面：%s", path)
                    return w
        return None

    def close(self):
        try:
            if self.bulk is not None:
                self.bulk.close_winusb_device()
        except Exception:
            pass
        try:
            if self.dev is not None:
                self.dev.disconnect()
        except Exception:
            pass
        self.dev = self.bulk = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ---------- HID 小工具 ----------
    def _write(self, data):
        """送一個 64 bytes 的 HID 報告（不足補 0）。"""
        with self.lock:
            self.dev._write(list(data))

    def _read(self, timeout_ms=REPLY_TIMEOUT_MS):
        """讀一筆 HID 回報，最多等 timeout_ms 毫秒；逾時回傳空 list。"""
        from liquidctl.error import Timeout
        try:
            return self.dev.device.read(64, timeout=max(1, int(timeout_ms)))
        except Timeout:
            # liquidctl 逾時會丟例外（不是回傳空值）；這裡只是「這段時間沒有回報」，不是裝置故障，
            # 不攔下的話主迴圈會當成斷線而重連（燈效、畫面因此每隔幾秒停住 3 秒）
            return []

    def _drain(self):
        """清掉累積的回報，避免 HID 緩衝區堆積（Fan Control 自己有獨立的緩衝區，不受影響）。"""
        try:
            self.dev.device.clear_enqueued_reports()
        except Exception:
            pass

    def read_status(self, timeout_s=1.5):
        """
        被動讀取水冷的狀態廣播（0x75 0x01），回傳 dict：液溫、泵／風扇轉速與 duty。
        不主動送查詢指令，所以完全不影響 Fan Control。讀不到回傳 None。
        """
        t0 = time.time()
        while True:
            left = timeout_s - (time.time() - t0)
            if left <= 0:
                return None
            # 每次讀取最多等剩餘時間（原本用 liquidctl 預設 5 秒，廣播沒來時會卡住整個迴圈）。
            # 只讀不寫，不必拿裝置鎖（RGB 執行緒只寫不讀，hidapi 讀寫可同時進行），避免等待時擋住燈效
            msg = self._read(left * 1000)
            if msg and len(msg) > 25 and msg[0] == 0x75 and msg[1] == 0x01:
                return {
                    "liquid": msg[15] + msg[16] / 10.0,
                    "pump_rpm": msg[18] << 8 | msg[17],
                    "pump_duty": msg[19],
                    "fan_rpm": msg[24] << 8 | msg[23],
                    "fan_duty": msg[25],
                }

    def get_lcd_info(self):
        """讀目前 LCD 亮度（%）與方向（0–3）；最多等約 1 秒，讀不到時回傳預設值（亮度 100、方向 0）。"""
        with self.lock:
            self._drain()
            self._write([0x30, 0x01])
            t0 = time.time()
            while time.time() - t0 < 1.0:
                msg = self._read(200)
                if msg and len(msg) > 0x1A and msg[0] == 0x31 and msg[1] == 0x01:
                    self.last_lcd_raw = list(msg[14:40])     # 完整回應（去掉固定標頭），追查顯示模式用
                    return {"brightness": msg[0x18], "orientation": msg[0x1A]}
        _LOG.warning("讀不到 LCD 亮度／方向回應，使用預設值")
        return {"brightness": 100, "orientation": 0}

    # ---------- LCD 指令 ----------
    def set_brightness_orientation(self, brightness=None, orientation=None):
        """設亮度（0–100）與／或方向（0=0°、1=90°、2=180°、3=270°）；沒給的參數維持原值。"""
        with self.lock:
            cur = self.get_lcd_info()
            b = cur["brightness"] if brightness is None else max(0, min(100, int(brightness)))
            o = cur["orientation"] if orientation is None else int(orientation) % 4
            self._write([0x30, 0x02, 0x01, b, 0x00, 0x00, 0x01, o])

    def set_liquid_mode(self, clear=False):
        """
        切回韌體內建的液溫畫面（拔掉電腦程式也會持續顯示）。
        做法與 liquidctl 的 set_screen("lcd", "liquid") 相同：只送一個切換指令 0x38 0x01 0x02 0x00。
        （曾試過先送黑畫面清除殘影，但韌體之後就不肯切到液溫畫面、整片黑，所以拿掉；clear 參數保留以相容舊呼叫）
        """
        with self.lock:
            self._write([0x38, 0x01, 0x02, 0x00])
            self._drain()
        self._first_frame = True        # 之後離開液溫模式的第一張自訂畫面送兩次（兩個緩衝都換掉）

    def _read_reply(self, prefix, timeout_ms):
        """讀回報直到出現指定開頭（例如 0x37 0x02）的那一筆；中間的狀態廣播等其他回報略過。逾時回傳 None。"""
        t0 = time.time()
        while True:
            left = timeout_ms - (time.time() - t0) * 1000
            if left <= 0:
                return None
            msg = self._read(left)
            if msg and msg[0] == prefix[0] and msg[1] == prefix[1]:
                return msg

    def _send_frame_once(self, data, info):
        """
        實際傳一次畫面，回傳 (成功與否, 結束回應)。
        成功的判斷：「結束傳輸」的回應 0x37 0x02 第 14 byte 為 1（平常實測都是 1）。
        """
        self._drain()                                        # 先清掉舊回報，免得把上一張的回應當成這張的
        self._write([0x36, 0x01, 0x00, 0x01, 0x06])          # 開始傳輸
        self._read_reply((0x37, 0x01), 300)
        self.bulk.write(0x02, bytes(_BULK_MAGIC + info))     # 傳輸標頭
        for i in range(0, len(data), _BULK_CHUNK):
            self.bulk.write(0x02, bytes(data[i:i + _BULK_CHUNK]))  # 畫面資料
        # 稍等再送「結束」：USB 傳完不代表裝置已把資料寫進畫面記憶體，
        # 太快送結束會讓螢幕刷出「一半新、一半舊」的畫面
        time.sleep(FRAME_SETTLE_S)
        self._write([0x36, 0x02])                            # 結束傳輸 → 畫面更新
        end = self._read_reply((0x37, 0x02), 400)
        return (end is not None and len(end) > 14 and end[14] == 1), end

    def send_frame_rgb565(self, data, double=False):
        """
        傳一張 240x240 RGB565 畫面（bytes，長度 115200）。
        每次都檢查裝置的「結束傳輸」回應；沒回應或狀態不對（例如傳到一半被別的程式的 HID 指令打斷，
        畫面會變成新舊混在一起的「疊圖」）就重送，最多 3 次，並記到日誌方便追查。
        LCD 為雙緩衝、兩個緩衝會交替顯示：只送一次時另一個緩衝還是舊畫面，看起來就是新舊兩張「疊圖」。
        所以 double=True（時鐘、資訊圖、圖片等每秒最多一張的畫面）一律送兩次；
        GIF 這類連續換格的只在換頁後第一張送兩次。
        """
        info = [0x06, 0x00, 0x00, 0x00] + list(len(data).to_bytes(4, "little"))
        times = 2 if (self._first_frame or double) else 1
        with self.lock:                                   # 整段傳圖期間不讓 RGB 執行緒插入封包
            for _ in range(times):
                for attempt in range(3):
                    ok, end = self._send_frame_once(data, info)
                    if ok:
                        break
                    self.frame_retries += 1
                    if self.frame_retries <= 20 or self.frame_retries % 50 == 0:   # 只記前幾次，避免洗版
                        _LOG.warning("LCD 傳圖未確認（第 %d 次重送，累計 %d），結束回應：%s", attempt + 1,
                                     self.frame_retries, None if end is None else list(end[:24]))
            self._first_frame = False
            self.frames_sent += 1
            self._drain()


def image_to_rgb565(img, orientation=0):
    """
    PIL 影像 → 裝置用的 RGB565（大端序）bytes。
    韌體不會自己旋轉自訂畫面，所以依 LCD 方向設定先把圖轉好（與 liquidctl 相同：-90°×方向）。
    """
    import numpy as np
    from PIL import Image

    im = img.convert("RGB")
    if im.size != LCD_SIZE:
        im = im.resize(LCD_SIZE, Image.LANCZOS)
    if orientation:
        im = im.rotate(orientation * -90)
    a = np.asarray(im, dtype=np.uint16)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype(">u2").tobytes()
