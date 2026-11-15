# -*- coding: utf-8 -*-
"""
cam_variants.py：產生「要請 CAM 原生渲染核心預覽」的圖形物件清單，並把回傳的 PNG 存檔

用法（CAM 需以 --remote-debugging-port=9333 啟動，並已掛好 previewGraphic 攔截）：
  python tools/cam_variants.py make  <輸出 gen.js>       # 產生在 CAM 內執行的 JS
  python tools/cam_variants.py save  <結果 json> <資料夾>  # 把 base64 PNG 存成檔案
圖形物件格式取自 CAM 170.bundle.js 的 fromInfographicsGraphic / fromClockfaceGraphic 等函式。
只呼叫 previewGraphic（純預覽、不送到裝置）。
"""
import base64
import json
import os
import sys


def c(h):
    """十六進位色碼 → CAM 用的 {r,g,b}。"""
    h = h.lstrip("#")
    return {"r": int(h[0:2], 16), "g": int(h[2:4], 16), "b": int(h[4:6], 16)}


def grad(*hexes, scale=1.0):
    """平均分布的漸層色標；scale 用來測試位置單位（1 或 0.01）。"""
    n = len(hexes)
    return [{"position": (i / (n - 1)) * scale, "color": c(h)} for i, h in enumerate(hexes)]


def reading(key, rc="#FFFFFF", lc="#FFFFFF", fmt="Celsius", label=None, outline=None):
    """單一讀值設定（readingConfig＝數值顏色、labelConfig＝標籤顏色、outline＝外框）。"""
    lbl = label or {"LiquidTemp": "Liquid"}.get(key, "CPU" if key.startswith("CPU") else "GPU")
    r = {"dataKey": key, "readingConfig": {"color": c(rc)}, "labelConfig": {"color": c(lc)},
         "labelTextTranslated": lbl, "temperatureFormat": fmt}
    if outline:
        r["readingConfig"]["outline"] = {"color": c(outline)}
        r["labelConfig"]["outline"] = {"color": c(outline)}
    return r


def ic(**kw):
    """資訊圖設定；"$IC" 標記讓 JS 端補上 selectedGpuId 與液溫來源裝置。"""
    d = {"$IC": 1}
    logo = kw.pop("logo", None)
    if logo:
        d["logoConfig"] = {"color": c(logo)}
    d.update(kw)
    return d


# ---- 變體清單：名稱 → 圖形物件 ----
V = {}
# 類比時鐘：指針（dial）、秒針（seconds）、背景
V["analog_default"] = {"colors": {"dial": c("FFFFFF"), "seconds": c("FF0000"), "background": c("000000")}, "graphicType": "ClockfaceAnalog"}
V["analog_color"] = {"colors": {"dial": c("00E5FF"), "seconds": c("FFD400"), "background": c("1A0B3D")}, "graphicType": "ClockfaceAnalog"}
# 數位時鐘：dial＝小時、text＝分鐘、背景為漸層
V["digital_24"] = {"is12ClockFormat": False, "colors": {"dial": c("FFFFFF"), "background": grad("000000", "000000"), "text": c("FFFFFF")}, "graphicType": "ClockfaceDigital"}
V["digital_12_grad"] = {"is12ClockFormat": True, "colors": {"dial": c("FF3B30"), "background": grad("0A1F44", "6B00DE"), "text": c("30D158")}, "graphicType": "ClockfaceDigital"}
V["digital_bg_pos100"] = {"is12ClockFormat": False, "colors": {"dial": c("FFFFFF"), "background": grad("0A1F44", "6B00DE", scale=0.01), "text": c("FFFFFF")}, "graphicType": "ClockfaceDigital"}
# 漸層數位時鐘
V["dgrad_3stop_12"] = {"is12ClockFormat": True, "gradient": grad("FF0000", "00FF00", "0000FF"), "graphicType": "ClockfaceDigitalGradient"}
# 單一資訊圖（radial-fill-v2 → InfographicSquare）
V["sq_cpu_3stop"] = {"infographicConfig": ic(logo="FFFFFF", reading=reading("CPUTemp", "FF9500", "30D158")), "arcBackground": c("404040"), "arcGradientStops": grad("FF0000", "FFFF00", "00FF00", scale=0.01), "graphicType": "InfographicSquare"}
V["sq_nologo_f"] = {"infographicConfig": ic(reading=reading("LiquidTemp", fmt="Fahrenheit")), "arcBackground": c("404040"), "arcGradientStops": grad("6B00DE", "D600BF", scale=0.01), "graphicType": "InfographicSquare"}
V["sq_load"] = {"infographicConfig": ic(logo="FF0000", reading=reading("GPULoad")), "arcBackground": c("404040"), "arcGradientStops": grad("00C7FF", "0040FF", scale=0.01), "graphicType": "InfographicSquare"}
V["sq_freq"] = {"infographicConfig": ic(reading=reading("CPUFrequency")), "arcBackground": c("404040"), "arcGradientStops": grad("6B00DE", "D600BF", scale=0.01), "graphicType": "InfographicSquare"}
V["sq_pos1"] = {"infographicConfig": ic(reading=reading("LiquidTemp")), "arcBackground": c("404040"), "arcGradientStops": grad("6B00DE", "D600BF"), "graphicType": "InfographicSquare"}
V["sq_outline"] = {"infographicConfig": {"$IC": 1, "logoConfig": {"color": c("FFFFFF"), "outline": {"color": c("FF0000")}}, "reading": reading("LiquidTemp", outline="FF0000")}, "arcBackground": c("404040"), "arcGradientStops": grad("6B00DE", "D600BF", scale=0.01), "graphicType": "InfographicSquare"}
# 全畫面漸層（InfographicFullFill）
V["full_2stop"] = {"infographicConfig": ic(reading=reading("LiquidTemp")), "backgroundGradient": grad("6B00DE", "D600BF"), "graphicType": "InfographicFullFill"}
V["full_3stop_logo"] = {"infographicConfig": ic(logo="FFFFFF", reading=reading("CPULoad", "000000", "000000")), "backgroundGradient": grad("FF0000", "FFFF00", "00FF00"), "graphicType": "InfographicFullFill"}
V["full_pos100"] = {"infographicConfig": ic(reading=reading("LiquidTemp")), "backgroundGradient": grad("6B00DE", "D600BF", scale=0.01), "graphicType": "InfographicFullFill"}
# 舊版環形（InfographicRadialFill）
V["radial"] = {"infographicConfig": ic(reading=reading("LiquidTemp")), "backgroundColor": c("000000"), "gradientStops": grad("6B00DE", "D600BF"), "inactiveArcOpacity": 1, "graphicType": "InfographicRadialFill"}
# 雙資訊圖：弧形版（DualInfographic）與長條版（DualInfographicSquare）
V["dual_arcs"] = {"infographicConfig": ic(reading1=reading("CPUTemp"), reading2=reading("GPUTemp")), "leftArcColor": c("6B00DE"), "rightArcColor": c("D600BF"), "backgroundColor": c("000000"), "graphicType": "DualInfographic"}
V["dual_arcs_logo"] = {"infographicConfig": ic(logo="FFFFFF", reading1=reading("CPULoad", "FF9500", "30D158"), reading2=reading("GPUFrequency")), "leftArcColor": c("FF0000"), "rightArcColor": c("00FF00"), "backgroundColor": c("101830"), "graphicType": "DualInfographic"}
V["dual_bars_color"] = {"infographicConfig": ic(logo="FFFFFF", reading1=reading("LiquidTemp", "FF9500", "30D158"), reading2=reading("CPULoad")), "topBarColor": c("FF0000"), "bottomBarColor": c("00C7FF"), "graphicType": "DualInfographicSquare"}
# 三資訊圖（TripleInfographic）
V["triple"] = {"infographicConfig": ic(reading1=reading("LiquidTemp"), reading2=reading("CPUTemp"), reading3=reading("GPUTemp")), "backgroundColor": c("000000"), "gradientStops": grad("6B00DE", "D600BF"), "inactiveArcOpacity": 1, "graphicType": "TripleInfographic"}
V["triple_logo"] = {"infographicConfig": ic(logo="FFFFFF", reading1=reading("CPULoad"), reading2=reading("GPULoad"), reading3=reading("CPUFrequency")), "backgroundColor": c("000000"), "gradientStops": grad("FF0000", "FFFF00", "00FF00"), "inactiveArcOpacity": 1, "graphicType": "TripleInfographic"}
# 音訊視覺化（預覽可能只有靜態畫面）
V["audio"] = {"colors": grad("6B00DE", "D600BF"), "frequencyScaling": 0.4, "invert": False, "nzxtLogo": True, "sensitivity": 50, "graphicType": "AudioVisualizer"}

JS = r"""
(async () => {
  // 在 CAM 視窗內：逐一呼叫原生 previewGraphic，回傳 {名稱: {時間, base64 PNG} 或錯誤}
  const V = __V__;
  const dev = window.__args[0][0];
  const base = window.__args[0][1].infographicConfig;
  const out = {};
  for (const [name, g0] of Object.entries(V)) {
    const g = JSON.parse(JSON.stringify(g0));
    if (g.infographicConfig && g.infographicConfig.$IC) {
      delete g.infographicConfig.$IC;
      g.infographicConfig.selectedGpuId = base.selectedGpuId;
      g.infographicConfig.liquidTempSourceDeviceId = base.liquidTempSourceDeviceId;
    }
    try { out[name] = { t: new Date().toTimeString().slice(0, 8), png: await window.__lcd.previewGraphic(dev, g) }; }
    catch (e) { out[name] = { err: String(e).slice(0, 300) }; }
  }
  return JSON.stringify(out);
})()
"""


def main():
    """make：輸出 JS；save：解碼結果存 PNG 與 variants.json（含時間與圖形物件）。"""
    if sys.argv[1] == "make":
        open(sys.argv[2], "w", encoding="utf-8").write(JS.replace("__V__", json.dumps(V)))
        print(f"已產生 {len(V)} 個變體")
        return
    res = json.load(open(sys.argv[2], encoding="utf-8"))
    os.makedirs(sys.argv[3], exist_ok=True)
    meta = {}
    for k, v in res.items():
        if v.get("png"):
            open(os.path.join(sys.argv[3], k + ".png"), "wb").write(base64.b64decode(v["png"]))
            meta[k] = {"time": v["t"], "graphic": V[k]}
            print(f"{k:20s} OK  {v['t']}")
        else:
            print(f"{k:20s} 失敗 {v.get('err')}")
    json.dump(meta, open(os.path.join(sys.argv[3], "variants.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
