# -*- coding: utf-8 -*-
"""
compare_native.py：與 CAM 渲染核心產生的原生 240×240 預覽逐像素比對

reference/cam_native/carousel_0~3.png 是從 CAM 視窗直接取出的原廠預覽 PNG（與實機畫面同一套渲染），
依序為：單一資訊圖（液溫）、雙資訊圖（CPU/GPU 溫度）、雙資訊圖（CPU/GPU 時脈）、漸層數位時鐘（14:32）。
輸出 compare/native_N.png：原廠｜本程式｜50% 疊圖｜差異×3（各放大 2 倍），並印出平均差異。
用法：python tools/compare_native.py
"""
import datetime
import json
import os
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import render  # noqa: E402

REF = os.path.join(HERE, "reference", "cam_native")
OUT = os.path.join(HERE, "compare")

# CAM 預覽用的示意填滿比例（由原生預覽量得）
DEMO = {
    0: [(0, "°", "Liquid", 0.3617)],
    1: [(0, "°", "CPU", 0.5272), (0, "°", "GPU", 0.6112)],
    2: [(0.0, "GHz", "CPU", 0.6355), (0.0, "GHz", "GPU", 0.6355)],
}


def rd(value, unit, label, pct):
    """建立讀值，讓填滿比例剛好等於 pct。"""
    span = 100.0
    lo = value - pct * span
    return render.Reading(value, unit, label, lo, lo + span)


def main():
    os.makedirs(OUT, exist_ok=True)
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    faces = cfg["carousel"]["faces"]
    for i, face in enumerate(faces[:4]):
        ref = Image.open(os.path.join(REF, f"carousel_{i}.png")).convert("RGB")
        rds = [rd(*x) for x in DEMO.get(i, [])]
        now = datetime.datetime(2026, 1, 1, 14, 32)
        ours = render.render(face, rds, now)
        blend = Image.blend(ref, ours, 0.5)
        diff = ImageChops.difference(ref, ours)
        d = np.asarray(diff, np.float32).mean()
        row = Image.new("RGB", (4 * 490 + 10, 520), (18, 18, 20))
        dr = ImageDraw.Draw(row)
        for k, (im, cap) in enumerate(((ref, "CAM"), (ours, "ours"), (blend, "blend"),
                                       (diff.point(lambda v: min(255, v * 3)), "diff x3"))):
            row.paste(im.resize((480, 480), Image.NEAREST), (10 + k * 490, 30))
            dr.text((10 + k * 490, 8), f"{i} {cap}", fill=(220, 220, 220))
        row.save(os.path.join(OUT, f"native_{i}.png"))
        print(f"畫面 {i}（{face['type']}/{face.get('style')}）平均差異 {d:6.2f}")


if __name__ == "__main__":
    main()
