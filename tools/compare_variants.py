# -*- coding: utf-8 -*-
"""
compare_variants.py：把 reference/cam_native/variants/ 內 CAM 原生預覽（由 cam_variants.py 取得）
對應成本程式的畫面設定，逐一渲染並比對。

輸出 compare/var_<名稱>.png：原廠｜本程式｜50% 疊圖｜差異×3（各放大 2 倍），並印出平均差異。
用法：python tools/compare_variants.py [名稱片段 ...]   （不給參數＝全部）
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

REF = os.path.join(HERE, "reference", "cam_native", "variants")
OUT = os.path.join(HERE, "compare")


def hx(c):
    """CAM {r,g,b} → '#RRGGBB'。"""
    return "#%02X%02X%02X" % (c["r"], c["g"], c["b"])


def stops(g):
    """CAM 漸層色標 → 本程式 [[位置 0~100, 色碼], ...]（位置一律正規化成 0~100）。"""
    mx = max(s["position"] for s in g) or 1.0
    return [[s["position"] / mx * 100, hx(s["color"])] for s in g]


# CAM 預覽在沒有感測資料時使用的示意填滿比例（由原生預覽量測）
DEMO_PCT = {"LiquidTemp": 0.3617, "CPUTemp": 0.5272, "GPUTemp": 0.6112,
            "CPUFrequency": 0.6355, "GPUFrequency": 0.6355, "CPULoad": 0.5, "GPULoad": 0.5}
# 單一資訊圖預覽的示意填滿比例（量測填滿弧終點角度）
SINGLE_PCT = {"LiquidTemp": 0.3631, "CPUTemp": 0.5, "GPUTemp": 0.5,
              "CPULoad": 0.6184, "GPULoad": 0.6184, "CPUFrequency": 0.6184, "GPUFrequency": 0.6184}
# 雙弧版（量測填滿弧角度，滿格 160°）與三資訊圖（量測圓點角度）的示意填滿比例
ARC_PCT = {"CPUTemp": 0.5, "GPUTemp": 0.5894, "CPULoad": 0.6094, "GPULoad": 0.6094,
           "CPUFrequency": 0.6138, "GPUFrequency": 0.6138, "LiquidTemp": 0.3631}
BAR_PCT = {"LiquidTemp": 0.3953, "CPULoad": 0.6377, "GPULoad": 0.6377}
TRI_PCT = {"LiquidTemp": 0.334, "CPULoad": 0.6007}
UNIT = {"LiquidTemp": "°", "CPUTemp": "°", "GPUTemp": "°", "CPULoad": "%", "GPULoad": "%",
        "CPUFrequency": "GHz", "GPUFrequency": "GHz"}


def rd(r, pct=None):
    """CAM reading 設定 → render.Reading（數值 0，填滿比例取示意值）。"""
    key = r["dataKey"]
    p = DEMO_PCT.get(key, 0.5) if pct is None else pct
    vo = hx(r["readingConfig"]["outline"]["color"]) if "outline" in r["readingConfig"] else None
    to = hx(r["labelConfig"]["outline"]["color"]) if "outline" in r["labelConfig"] else None
    return render.Reading(0.0, UNIT[key], r["labelTextTranslated"], -p * 100, (1 - p) * 100,
                          hx(r["readingConfig"]["color"]), hx(r["labelConfig"]["color"]), vo, to)


def to_face(g):
    """CAM 圖形物件 → (本程式畫面設定, 讀值清單)。"""
    t = g["graphicType"]
    ic = g.get("infographicConfig") or {}
    logo = ic.get("logoConfig")
    lg = {"enable": bool(logo), "color": hx(logo["color"]) if logo else "#FFFFFF",
          "outline": hx(logo["outline"]["color"]) if logo and "outline" in logo else None}
    if t == "ClockfaceAnalog":
        c = g["colors"]
        return {"type": "clock", "style": "analog",
                "analog": {"dial": hx(c["dial"]), "seconds": hx(c["seconds"]), "background": hx(c["background"])}}, []
    if t == "ClockfaceDigitalGradient":
        return {"type": "clock", "style": "digital_gradient", "format": "12hrs" if g["is12ClockFormat"] else "24hrs",
                "digital_gradient": {"gradient": stops(g["gradient"])}}, []
    if t == "ClockfaceDigital":
        c = g["colors"]
        return {"type": "clock", "style": "digital", "format": "12hrs" if g["is12ClockFormat"] else "24hrs",
                "digital": {"dial": hx(c["dial"]), "text": hx(c["text"]), "gradient": stops(c["background"])}}, []
    if t == "InfographicSquare":
        st = stops(g["arcGradientStops"])
        p = SINGLE_PCT.get(ic["reading"]["dataKey"])
        return {"type": "single", "style": "radial-fill-v2", "colors": [s[1] for s in st], "logo": lg}, [rd(ic["reading"], p)]
    if t == "InfographicFullFill":
        st = stops(g["backgroundGradient"])
        return {"type": "single", "style": "full-fill", "colors": [s[1] for s in st], "logo": lg}, [rd(ic["reading"])]
    if t == "InfographicRadialFill":
        st = stops(g["gradientStops"])
        return {"type": "single", "style": "radial-fill", "colors": [s[1] for s in st], "logo": lg,
                "background": hx(g["backgroundColor"])}, [rd(ic["reading"])]
    if t == "DualInfographic":
        return {"type": "dual", "style": "arcs", "colors": [hx(g["leftArcColor"]), hx(g["rightArcColor"])],
                "background": hx(g["backgroundColor"]), "logo": lg}, [rd(ic["reading1"], ARC_PCT.get(ic["reading1"]["dataKey"])), rd(ic["reading2"], ARC_PCT.get(ic["reading2"]["dataKey"]))]
    if t == "DualInfographicSquare":
        return {"type": "dual", "style": "bars", "colors": [hx(g["topBarColor"]), hx(g["bottomBarColor"])],
                "logo": lg}, [rd(ic["reading1"], BAR_PCT.get(ic["reading1"]["dataKey"])), rd(ic["reading2"], BAR_PCT.get(ic["reading2"]["dataKey"]))]
    if t == "TripleInfographic":
        st = stops(g["gradientStops"])
        return {"type": "triple", "colors": [s[1] for s in st], "background": hx(g["backgroundColor"]), "logo": lg}, \
            [rd(ic["reading1"], TRI_PCT.get(ic["reading1"]["dataKey"])), rd(ic["reading2"]), rd(ic["reading3"])]
    if t == "AudioVisualizer":
        st = stops(g["colors"])
        return {"type": "audio", "colors": [s[1] for s in st], "logo": {"enable": g["nzxtLogo"]}}, []
    raise ValueError(t)


def main():
    """逐一比對；參數為名稱片段時只比對符合者。"""
    os.makedirs(OUT, exist_ok=True)
    meta = json.load(open(os.path.join(REF, "variants.json"), encoding="utf-8"))
    pats = sys.argv[1:]
    for name, m in meta.items():
        if pats and not any(p in name for p in pats):
            continue
        face, rds = to_face(m["graphic"])
        hh, mi, ss = map(int, m["time"].split(":"))
        now = datetime.datetime(2026, 11, 15, hh, mi, ss)
        ref = Image.open(os.path.join(REF, name + ".png")).convert("RGB")
        try:
            ours = render.render(face, rds, now)
        except Exception as e:  # 尚未支援的版面
            print(f"{name:20s} 無法渲染：{e}")
            continue
        diff = ImageChops.difference(ref, ours)
        d = np.asarray(diff, np.float32).mean()
        row = Image.new("RGB", (4 * 490 + 10, 520), (18, 18, 20))
        dr = ImageDraw.Draw(row)
        for k, (im, cap) in enumerate(((ref, "CAM"), (ours, "ours"), (Image.blend(ref, ours, 0.5), "blend"),
                                       (diff.point(lambda v: min(255, v * 3)), "diff x3"))):
            row.paste(im.resize((480, 480), Image.NEAREST), (10 + k * 490, 30))
            dr.text((10 + k * 490, 8), f"{name} {cap}", fill=(220, 220, 220))
        row.save(os.path.join(OUT, f"var_{name}.png"))
        print(f"{name:20s} 平均差異 {d:6.2f}")


if __name__ == "__main__":
    main()
