# -*- coding: utf-8 -*-
"""
compare_ref.py：把本程式畫的 LCD 畫面與 CAM 原廠預覽截圖逐一比對

做法：
  1) 從 reference/ 的 CAM 預覽截圖切出實際 LCD 範圍（預覽圓盤中央、半徑一半的區域），縮放成 240×240。
  2) 用與截圖相同的數值、時間、填滿比例畫出本程式的版本。
  3) 每個畫面輸出一列：原廠｜本程式｜50% 疊圖｜差異放大，存到 compare/；並印出平均差異。
用法：python tools/compare_ref.py
"""
import datetime
import os
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import render  # noqa: E402

REF = os.path.join(HERE, "reference")
OUT = os.path.join(HERE, "compare")


def lcd_crop(fn, box):
    """box＝預覽圓盤外框 (x0,x1,y0,y1)；回傳圓盤中央 LCD 區域（240×240）。"""
    im = Image.open(os.path.join(REF, fn)).convert("RGB")
    x0, x1, y0, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = max(x1 - x0, y1 - y0) / 2 / render.ZOOM          # LCD 半徑＝圓盤半徑 / ZOOM
    return im.crop((cx - r, cy - r, cx + r, cy + r)).resize((240, 240), Image.LANCZOS)


def rd(value, unit, label, pct):
    """建立讀值，並讓填滿比例剛好等於 pct（CAM 預覽用的是示意填滿）。"""
    lo = value - pct * 100
    return render.Reading(value, unit, label, lo, lo + 100)


# 各比對項目：(名稱, 截圖檔, 圓盤外框, 畫面設定, 讀值, 時間)
CASES = [
    ("single", "cam_single_infographic.jpg", (18, 401, 13, 395),
     {"type": "single", "style": "radial-fill-v2"}, [rd(0, "°", "Liquid", 98 / 284)], None),
    ("dual", "cam_dual_infographic.jpg", (4, 387, 13, 395),
     {"type": "dual", "style": "bars"}, [rd(0, "°", "CPU", 0.522), rd(0, "°", "GPU", 0.607)], None),
    ("analog", "cam_clock_analog.jpg", (0, 227, 8, 236),
     {"type": "clock", "style": "analog"}, [], datetime.datetime(2026, 1, 1, 9, 33, 42)),
    ("digital", "cam_clock_digital.jpg", (556, 861, 61, 367),
     {"type": "clock", "style": "digital_gradient", "format": "12hrs",
      "digital_gradient": {"gradient": [[0, "#FF2E4D"], [100, "#2B2BFF"]]}}, [], datetime.datetime(2026, 1, 1, 9, 33)),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for name, fn, box, face, rds, now in CASES:
        ref = lcd_crop(fn, box)
        ours = render.render(face, rds, now)
        blend = Image.blend(ref, ours, 0.5)
        diff = ImageChops.difference(ref, ours).point(lambda v: min(255, v * 3))
        d = np.asarray(ImageChops.difference(ref, ours), np.float32).mean()
        row = Image.new("RGB", (4 * 480 + 50, 520), (18, 18, 20))
        dr = ImageDraw.Draw(row)
        for i, (im, cap) in enumerate(((ref, "CAM 原廠"), (ours, "本程式"), (blend, "50% 疊圖"), (diff, "差異 ×3"))):
            row.paste(im.resize((480, 480), Image.LANCZOS), (10 + i * 490, 30))
            dr.text((10 + i * 490, 8), f"{name}  {cap}", fill=(220, 220, 220))
        row.save(os.path.join(OUT, f"{name}.png"))
        rows.append((name, d))
        print(f"{name:8s} 平均差異 {d:6.2f}（0＝完全相同）")


if __name__ == "__main__":
    main()
