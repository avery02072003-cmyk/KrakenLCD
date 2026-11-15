# -*- coding: utf-8 -*-
"""
probe.py：唯讀探測 NZXT Kraken（不改任何設定）
- 確認 liquidctl 能以「共用模式」開啟 HID（Fan Control 執行中也能開）
- 確認 WinUSB bulk 介面（MI_00，傳 LCD 畫面用）找得到
- 讀韌體版本、LCD 亮度／方向、一筆狀態回報（液溫、泵／風扇轉速）
"""
import sys
import time

from liquidctl import find_liquidctl_devices

# 只找 NZXT（VID 0x1E71）的 Kraken
devs = [d for d in find_liquidctl_devices(vendor=0x1E71) if "Kraken" in d.description]
if not devs:
    print("找不到 Kraken 裝置")
    sys.exit(1)

dev = devs[0]
print("裝置：", dev.description, "PID=0x%04X" % dev.product_id)

with dev.connect():
    # bulk_device 為 None 表示 WinUSB 介面沒找到（LCD 傳圖就無法使用）
    print("WinUSB bulk 介面：", "有" if getattr(dev, "bulk_device", None) else "沒有")

    # 韌體版本：送 0x10 0x01，回 0x11 0x01
    dev._get_fw_version()
    print("韌體版本：", ".".join(str(x) for x in dev._fw))

    # LCD 資訊：送 0x30 0x01，回 0x31 0x01（第 0x18 位元組=亮度，0x1A=方向）
    dev.device.clear_enqueued_reports()
    dev._write([0x30, 0x01])
    info = {}

    def parse(msg):
        info["brightness"] = msg[0x18]
        info["orientation"] = msg[0x1A] * 90

    dev._read_until({b"\x31\x01": parse})
    print("LCD 亮度：%d%%，方向：%d°" % (info["brightness"], info["orientation"]))

    # 被動讀幾筆回報，看看 Fan Control 外掛造成的狀態訊息長什麼樣（只印前綴）
    seen = {}
    t0 = time.time()
    while time.time() - t0 < 2.0:
        msg = dev.device.read(64)
        if msg:
            key = "%02X %02X" % (msg[0], msg[1])
            seen[key] = seen.get(key, 0) + 1
            if key == "75 01":
                # 狀態回報：液溫（整數+小數）、泵轉速、泵 duty、風扇轉速、風扇 duty
                print("狀態：液溫 %.1f°C 泵 %d rpm (%d%%) 風扇 %d rpm (%d%%)" % (
                    msg[15] + msg[16] / 10, msg[18] << 8 | msg[17], msg[19],
                    msg[24] << 8 | msg[23], msg[25]))
    print("2 秒內收到的訊息類型：", seen)
