# -*- coding: utf-8 -*-
"""
fit_text.py：從 CAM 原生預覽量出文字的字級與位置（供調整 render.py 版面用）

做法：
  1. 由像素顏色估出該段文字的覆蓋率（alpha）：alpha = (像素 − 背景)·(文字色 − 背景) / |文字色 − 背景|²
  2. 在指定框內找 alpha>0.5 的外框，與字型在 100px 時的墨跡外框比較，得到初始字級與筆位
  3. 在字級 ±3%、位置 ±1.5px 內搜尋，使本程式 text_mask 與原生覆蓋率差異最小
用法：在其他腳本 import fit，或直接執行本檔查看範例。
"""
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import render  # noqa: E402

REF = os.path.join(HERE, "reference", "cam_native", "variants")


def load(name):
    """讀入原生預覽為 float 陣列 (240,240,3)。"""
    return np.asarray(Image.open(os.path.join(REF, name + ".png")).convert("RGB"), np.float64)


def alpha_map(img, color, bg):
    """估計文字覆蓋率；bg 可為單一顏色或 (240,240,3) 陣列。"""
    c = np.array(render.hex_rgb(color), np.float64)
    b = np.broadcast_to(np.array(bg, np.float64), img.shape)
    d = c - b
    return np.clip(((img - b) * d).sum(2) / np.maximum((d * d).sum(2), 1.0), 0, 1)


def _ds(m):
    """超取樣遮罩 → 240×240 覆蓋率。"""
    return m.reshape(240, render.SS, 240, render.SS).mean((1, 3))


def _bbox(a, thr=0.5):
    ys, xs = np.where(a > thr)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def fit(ref_a, box, font, text, anchor="ls", iters=3, init=None):
    """
    ref_a：原生覆蓋率；box：(x0,y0,x1,y1) 搜尋範圍；回傳 (字級, x, y, 誤差)。
    錨點 anchor 同 PIL（ls＝左-基線、ms＝中-基線、rs＝右-基線）；init＝(字級, x, y) 手動初始值。
    """
    x0, y0, x1, y1 = box
    sub = np.zeros_like(ref_a)
    sub[y0:y1, x0:x1] = ref_a[y0:y1, x0:x1]
    if init:
        s, x, y = init
    else:
        rx0, ry0, rx1, ry1 = _bbox(sub)
        # 100px、筆位 (120,150) 時的墨跡外框 → 線性換算初始值
        m = _ds(render.text_mask(text, font, 100, 120, 150, anchor))
        mx0, my0, mx1, my1 = _bbox(m)
        s = 100 * (ry1 - ry0) / (my1 - my0)
        x = rx0 - (mx0 - 120) * s / 100
        y = ry1 - (my1 - 150) * s / 100

    def score(s_, x_, y_):
        mm = _ds(render.text_mask(text, font, s_, x_, y_, anchor))
        return np.abs(mm[y0:y1, x0:x1] - sub[y0:y1, x0:x1]).sum()

    best = (score(s, x, y), s, x, y)
    step = 0.5
    for _ in range(iters):
        _, sc, xc, yc = best
        for ss in sc * np.array([0.97, 0.98, 0.99, 0.995, 1, 1.005, 1.01, 1.02, 1.03]):
            for dx in np.arange(-3, 3.01, 1) * step:
                for dy in np.arange(-3, 3.01, 1) * step:
                    v = score(ss, xc + dx, yc + dy)
                    if v < best[0]:
                        best = (v, ss, xc + dx, yc + dy)
        step /= 2
    return best[1], best[2], best[3], best[0]


def ink_bbox(ref_a, box, thr=0.5):
    """框內覆蓋率 > thr 的外框（裝置像素）。"""
    x0, y0, x1, y1 = box
    a = np.zeros_like(ref_a)
    a[y0:y1, x0:x1] = ref_a[y0:y1, x0:x1]
    return _bbox(a, thr)


if __name__ == "__main__":
    img = load("sq_load")
    a = alpha_map(img, "#FFFFFF", (0, 0, 0))
    print("GPU label", fit(a, (10, 60, 100, 100), render.BOLD, "GPU"))
    print("value 0 ", fit(a, (10, 100, 75, 180), render.BOLD, "0"))
    print("%       ", fit(a, (75, 140, 115, 180), render.BOLD, "%"))
