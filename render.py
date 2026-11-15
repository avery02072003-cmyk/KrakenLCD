# -*- coding: utf-8 -*-
"""
render.py：畫出與 NZXT CAM 相同的 LCD 畫面（240×240，座標單位＝實機像素）

版面數據來源：CAM 渲染核心（cam-core 的 lcd-rendering）產生的原生 240×240 預覽 PNG
（reference/cam_native/，從 CAM 視窗直接取出，與送到實機的畫面同一套渲染），逐像素量測：
  單一資訊圖 radial-fill-v2：圓心 (120,120)、環中線半徑 107.5、線寬 22，
      可見範圍（含圓頭）從 219° 順時針 282° 到 141°，左側 9 點鐘方向對稱留 78° 缺口；底環 #3E3E3E；
      填滿段漸層在「線性光」空間沿角度內插。
      標籤 Gotham Bold 30px（筆位 x=14.5、基線 87.8），數值 Bold 87px（筆位 x=11.25、基線 168），
      「°」Bold 38.3px 緊接在數值後面。
  雙資訊圖 dual-infographic-base：兩條圓角長條 x 0.4→239.7、高 22.9，中心 y 103.2／136.56，
      底條固定 #898989（CAM 不傳第三色給渲染核心），填滿用第一／第二色；
      標籤 Bold 26px（x=2.6，基線 82.4／175.8）；數值靠右（筆位右緣 x=223.6），
      溫度 Bold 100.7px、時脈 Bold 67.7px；上方數值以基線對齊 y≈80、下方數值以字頂對齊 y=157；
      「°」為數值的 0.291 倍、頂端對齊數字頂端；GHz 為 18.9px，上方讀值放數字上方、下方讀值放數字下方。
  數位時鐘：時 Gotham Bold 150px（基線 118）、分 Gotham Light 150px（基線 234.2），水平置中；
      漸層數位時鐘的顏色在 Oklab 色彩空間由畫面頂端 y=0 漸變到底端 y=240（量測誤差 0.2 色階）。
  類比時鐘：指針形狀取自 CAM 內嵌的 Clockface-Hand.svg／Clockface-SecHand.svg。
字型：CAM 原生模組內嵌的 Gotham Bold／Light（見 fonts.py）。

繪製方式：先在 4 倍解析度（960×960）用 numpy 畫出「預乘顏色＋不透明度」圖層，
再以區塊平均縮回 240×240（等同 16 點超取樣抗鋸齒）。圖層與背景分開，GIF 背景只需每格合成一次。
"""
import datetime
import math

import numpy as np
from PIL import Image, ImageDraw

import fonts

SIZE = 240          # LCD 解析度
SS = 4              # 超取樣倍率
W = SIZE * SS       # 內部畫布邊長

BOLD = "Gotham-Bold.ttf"
LIGHT = "Gotham-Light.ttf"
LOGO = "NZXT-Font.ttf"

# CAM 預設色
CAM_DEFAULT_COLORS = ["#6B00DE", "#D600BF", "#818181"]
CAM_RING_TRACK = "#3E3E3E"      # 單一資訊圖底環（原生預覽實測 62 灰階）
CAM_BAR_TRACK = "#898989"       # 雙資訊圖底條（原生預覽實測 137 灰階，渲染核心內固定）
CAM_RING_GRADIENT = [[0, "#00ff00"], [50, "#ffff00"], [100, "#ff0000"]]

# 座標網格：每個內部像素中心對應的實機像素座標
_yy, _xx = np.mgrid[0:W, 0:W].astype(np.float32)
_X = (_xx + 0.5) / SS
_Y = (_yy + 0.5) / SS


# ---------------- 顏色工具 ----------------
def hex_rgb(c, default=(255, 255, 255)):
    """'#RRGGBB' 或 'RRGGBB' → (r,g,b)；格式不對回傳預設值。"""
    try:
        c = str(c).lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def shape_rgb(c, default=(255, 255, 255)):
    """
    圖形（背景、弧、長條、指針）實際顯示的顏色。
    CAM 渲染核心把設定色當成 gamma 2.2 解碼成線性光，再以 sRGB 曲線編碼輸出，
    暗色會變得更暗（原生預覽實測：#1A0B3D→(19,3,59)、#101830→(8,17,44)、#404040→62、#00C7FF→(0,200,255)）；
    文字（glyphon）不經此轉換、照原色顯示。
    """
    out = []
    for v in hex_rgb(c, default):
        lin = (v / 255.0) ** 2.2
        e = lin * 12.92 if lin <= 0.0031308 else 1.055 * lin ** (1 / 2.4) - 0.055
        out.append(int(round(e * 255)))
    return tuple(out)


def norm_stops(stops):
    """
    漸層色標統一成 [(位置0~1, (r,g,b)), ...]（依位置排序）。
    接受 [[位置0~100, '#hex'], ...] 或 CAM 格式 [{'position':0,'color':'hex'}, ...]。
    """
    out = []
    for s in stops or []:
        if isinstance(s, dict):
            p, c = s.get("position", 0), s.get("color", "FFFFFF")
        else:
            p, c = s
        out.append((float(p) / 100.0, hex_rgb(c)))
    out.sort(key=lambda x: x[0])
    return out or [(0.0, (255, 255, 255)), (1.0, (255, 255, 255))]


def gradient_at(stops, t):
    """依位置 t（0~1，可為 numpy 陣列）在色標間做 sRGB 線性內插，回傳 (...,3) 的 float 顏色。"""
    t = np.clip(np.asarray(t, dtype=np.float32), 0, 1)
    pos = np.array([s[0] for s in stops], dtype=np.float32)
    cols = np.array([s[1] for s in stops], dtype=np.float32)
    return np.stack([np.interp(t, pos, cols[:, k]) for k in range(3)], axis=-1)


def _to_lin(c):
    """sRGB 0~255 → 線性光 0~1。"""
    c = np.asarray(c, np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _from_lin(l):
    """線性光 0~1 → sRGB 0~255。"""
    l = np.clip(l, 0, 1)
    return np.where(l <= 0.0031308, 12.92 * l, 1.055 * l ** (1 / 2.4) - 0.055) * 255.0


# Oklab 轉換矩陣（Björn Ottosson 的定義）
_OK_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                   [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]], np.float32)
_OK_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                   [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]], np.float32)
_OK_M1i = np.linalg.inv(_OK_M1).astype(np.float32)
_OK_M2i = np.linalg.inv(_OK_M2).astype(np.float32)


def _to_oklab(c):
    return np.cbrt(_to_lin(c) @ _OK_M1.T) @ _OK_M2.T


def _from_oklab(L):
    return _from_lin((L @ _OK_M2i.T) ** 3 @ _OK_M1i.T)


def gradient_space(stops, t, space="srgb"):
    """
    多色標漸層，可選內插空間：'srgb'、'linear'（線性光）、'oklab'。
    CAM 的漸層數位時鐘用 Oklab、單一資訊圖弧形用線性光（由原生預覽擬合得知）。
    """
    t = np.clip(np.asarray(t, dtype=np.float32), 0, 1)
    pos = np.array([s[0] for s in stops], dtype=np.float32)
    cols = np.array([s[1] for s in stops], dtype=np.float32)
    if space == "linear":
        conv = _to_lin(cols)
    elif space == "oklab":
        conv = _to_oklab(cols)
    else:
        conv = cols
    mixed = np.stack([np.interp(t, pos, conv[:, k]) for k in range(3)], axis=-1)
    if space == "linear":
        return _from_lin(mixed)
    if space == "oklab":
        return np.clip(_from_oklab(mixed), 0, 255)
    return mixed


# ---------------- 圖層 ----------------
class Layer:
    """預乘顏色圖層：col 為顏色×不透明度，a 為不透明度（皆為內部 4 倍解析度）。"""

    def __init__(self):
        self.col = np.zeros((W, W, 3), np.float32)
        self.a = np.zeros((W, W), np.float32)

    def paint(self, mask, color):
        """以 mask（bool 或 0~1）把顏色疊上去。color 可為 (r,g,b) 或與畫布同形的 (W,W,3) 陣列。"""
        m = mask.astype(np.float32)
        c = np.asarray(color, np.float32)
        self.col = self.col * (1 - m)[..., None] + (c * m[..., None])
        self.a = self.a * (1 - m) + m

    def downsample(self):
        """區塊平均縮回 240×240，回傳 (預乘顏色, 不透明度)。"""
        col = self.col.reshape(SIZE, SS, SIZE, SS, 3).mean(axis=(1, 3))
        a = self.a.reshape(SIZE, SS, SIZE, SS).mean(axis=(1, 3))
        return col, a


# ---------------- 幾何形狀（回傳 bool 遮罩）----------------
def disk(cx, cy, r):
    """實心圓。"""
    return (_X - cx) ** 2 + (_Y - cy) ** 2 <= r * r


def ring(cx, cy, r, width):
    """空心圓環（中線半徑 r、線寬 width）。"""
    d = np.hypot(_X - cx, _Y - cy)
    return np.abs(d - r) <= width / 2


def arc_mask(cx, cy, r, width, start, sweep):
    """
    圓角端點的圓弧：圓心 (cx,cy)、中線半徑 r、線寬 width，
    中線從 start 度（0°=3 點鐘方向、順時針為正）畫 sweep 度，兩端加半圓帽（在中線範圍外）。
    回傳 (遮罩, 每點的角度〔相對 start，度〕)，角度用來上漸層色。
    """
    dx, dy = _X - cx, _Y - cy
    d = np.hypot(dx, dy)
    ang = (np.degrees(np.arctan2(dy, dx)) - start) % 360.0
    band = np.abs(d - r) <= width / 2
    inside = ang <= sweep
    a0, a1 = math.radians(start), math.radians(start + sweep)
    cap0 = disk(cx + r * math.cos(a0), cy + r * math.sin(a0), width / 2)
    cap1 = disk(cx + r * math.cos(a1), cy + r * math.sin(a1), width / 2)
    mask = (band & inside) | cap0 | cap1
    # 只屬於圓帽的點：起點帽給負角度、終點帽給 > sweep 的角度（漸層才會連續）
    rel = np.where(ang > (sweep + 360) / 2, ang - 360.0, ang)
    return mask, rel


def pill(x0, x1, yc, h):
    """水平圓角長條（兩端半圓），x0→x1 為含圓角的總範圍。"""
    r = h / 2
    if x1 - x0 < h:
        x1 = x0 + h
    body = (_X >= x0 + r) & (_X <= x1 - r) & (np.abs(_Y - yc) <= r)
    return body | disk(x0 + r, yc, r) | disk(x1 - r, yc, r)


def tapered(x0, y0, r0, x1, y1, r1):
    """兩端半徑不同的圓頭錐形線段（類比時鐘時／分針用），近似為沿線段半徑線性變化的圓。"""
    vx, vy = x1 - x0, y1 - y0
    L2 = vx * vx + vy * vy or 1e-6
    t = np.clip(((_X - x0) * vx + (_Y - y0) * vy) / L2, 0, 1)
    px, py = x0 + t * vx, y0 + t * vy
    rr = r0 + (r1 - r0) * t
    return (_X - px) ** 2 + (_Y - py) ** 2 <= rr * rr


def segment(x0, y0, x1, y1, width):
    """圓角端點的等寬線段。"""
    return tapered(x0, y0, width / 2, x1, y1, width / 2)


# ---------------- 文字 ----------------
def _font(font_name, size):
    return fonts.get(font_name, size * SS)


def text_mask(text, font_name, size, x, y, anchor="ls", stroke=0.0):
    """
    在內部畫布上畫文字，回傳 0~1 遮罩。x, y 為實機像素座標、size 為字級（像素）；
    anchor 用 PIL 錨點（'ls' 左-基線、'rs' 右-基線、'ms' 中-基線）；
    stroke＞0 時連同向外擴張 stroke 像素的外框一起畫（CAM 文字外框用，原生預覽實測約 1px）。
    """
    img = Image.new("L", (W, W), 0)
    sw = int(round(stroke * SS))
    ImageDraw.Draw(img).text((x * SS, y * SS), text, font=_font(font_name, size), fill=255, anchor=anchor,
                             stroke_width=sw, stroke_fill=255)
    return np.asarray(img, np.float32) / 255.0


OUTLINE_PX = 1.0     # 文字外框寬度（實機像素）


def paint_text(lay, text, font_name, size, x, y, color, outline=None, anchor="ls"):
    """畫文字；outline 有值時先畫外框（文字向外擴 OUTLINE_PX 的範圍）再畫字。文字顏色不經 gamma 轉換。"""
    if not text:
        return
    if outline:
        lay.paint(text_mask(text, font_name, size, x, y, anchor, OUTLINE_PX), hex_rgb(outline))
    lay.paint(text_mask(text, font_name, size, x, y, anchor), color)


def advance(text, font_name, size):
    """文字的前進寬度（筆位移動量，像素）。"""
    return _font(font_name, size).getlength(text) / SS


def cap_top(font_name, size, ch="0"):
    """字元墨跡頂端在基線上方多少像素（正值）。"""
    l, t, r, b = _font(font_name, size).getbbox(ch, anchor="ls")
    return -t / SS


# ---------------- 讀值 ----------------
class Reading:
    """
    一個要顯示的讀值。
    value：數值（None 表示讀不到）；unit：'°'、'%'、'GHz'；label：標籤文字；
    lo/hi：換算填滿比例的範圍；value_color／text_color：數值與標籤顏色。
    """

    def __init__(self, value, unit, label, lo=0.0, hi=100.0, value_color="#FFFFFF", text_color="#FFFFFF",
                 value_outline=None, text_outline=None):
        self.value, self.unit, self.label = value, unit, label
        self.lo, self.hi = lo, hi
        self.value_color, self.text_color = hex_rgb(value_color), hex_rgb(text_color)
        self.value_outline = value_outline or None     # 數值（含單位）外框色，None＝無外框
        self.text_outline = text_outline or None       # 標籤外框色

    def mhz_text(self):
        """時脈以 MHz 四位整數顯示（CAM 雙弧／三資訊圖版面：0 GHz 顯示成 0000）。"""
        if self.value is None:
            return "--"
        return "%04d" % round(self.value * 1000)

    @property
    def pct(self):
        """填滿比例 0~1。"""
        if self.value is None or self.hi <= self.lo:
            return 0.0
        return float(min(1.0, max(0.0, (self.value - self.lo) / (self.hi - self.lo))))

    def text(self):
        """數值文字：溫度與使用率取整數，時脈一位小數（CAM 顯示 0.0）。"""
        if self.value is None:
            return "--"
        if self.unit == "GHz":
            return "%.1f" % self.value
        return "%d" % round(self.value)


def _fit_size(text, font_name, size, max_width):
    """字太長時縮小字級，讓前進寬度不超過 max_width。"""
    w = advance(text, font_name, size)
    return size * min(1.0, max_width / w) if w > 0 else size


def draw_unit_after(lay, rd, vsize, pen_end, value_top, base, deg_ratio=0.291, deg_dy=-0.5):
    """
    在數值後面畫 ° 或 %（雙資訊圖長條版用）：
      °：Bold、字級 = 數值字級 × deg_ratio，墨跡頂端對齊數字頂端（加 deg_dy 微調），緊接數值前進寬度之後。
      %：Bold、字級 = 數值字級 × 0.3，貼齊基線放在數值右下（CAM Kraken Plus 介面樣板的擺法）。
    """
    if rd.unit == "°":
        us = vsize * deg_ratio
        ub = value_top + deg_dy + cap_top(BOLD, us, "°")
        paint_text(lay, "°", BOLD, us, pen_end, ub, rd.value_color, rd.value_outline)
    elif rd.unit == "%":
        us = vsize * 0.3
        paint_text(lay, "%", BOLD, us, pen_end + vsize * 0.03, base, rd.value_color, rd.value_outline)


def draw_label(lay, rd, size, pen_x, base):
    """畫標籤（左-基線錨點）。"""
    if rd.label:
        paint_text(lay, rd.label, BOLD, size, pen_x, base, rd.text_color, rd.text_outline)


def draw_label_center(lay, rd, size, cx, base):
    """畫標籤（以前進寬度水平置中於 cx）。"""
    if rd.label:
        paint_text(lay, rd.label, BOLD, size, cx - advance(rd.label, BOLD, size) / 2, base, rd.text_color, rd.text_outline)


def draw_logo(lay, color, y=200, height=11, size=None, outline=None):
    """NZXT logo（CAM logo 字型裡 N、Z、X、T 四個字元組成 logo 字樣），水平置中、基線在 y。"""
    size = size or fonts.size_for_height(LOGO, height, "N")
    paint_text(lay, "NZXT", LOGO, size, 120, y, hex_rgb(color), outline, "ms")


def _logo_cfg(face):
    """回傳 (是否顯示 logo, logo 顏色, logo 外框色)。"""
    lg = face.get("logo") or {}
    return bool(lg.get("enable", False)), lg.get("color", "#FFFFFF"), lg.get("outline") or None


def draw_value_unit(lay, rd, text, vsize, x, base, unit_spec):
    """
    畫數值文字（筆位 x、基線 base），並把單位接在數值前進寬度之後（各版面的單位尺寸與高度不同）。
    unit_spec：{'°': (字級, 基線相對數值基線的位移), '%': (...), 'GHz': (...)}；間距一律 -0.4px（原生量測）。
    """
    paint_text(lay, text, BOLD, vsize, x, base, rd.value_color, rd.value_outline)
    spec = unit_spec.get(rd.unit)
    if spec and text != "--":
        us, dy = spec
        paint_text(lay, rd.unit, BOLD, us, x + advance(text, BOLD, vsize) - 0.4, base + dy, rd.value_color, rd.value_outline)


def _shrink(text, size, max_w, base):
    """字太寬時縮小到 max_w，並保持數字的垂直中心不變（原生預覽 0000 的擺法）。回傳 (字級, 基線)。"""
    s2 = _fit_size(text, BOLD, size, max_w)
    if s2 < size:
        mid = base - cap_top(BOLD, size) / 2
        base = mid + cap_top(BOLD, s2) / 2
    return s2, base


# ---------------- 各版面 ----------------
def _colors(face, n=3):
    cols = list(face.get("colors") or CAM_DEFAULT_COLORS)
    while len(cols) < n:
        cols.append(CAM_DEFAULT_COLORS[len(cols)])
    return [shape_rgb(c) for c in cols]          # 這些顏色都用在圖形上 → 經 CAM 的 gamma 轉換


# 單一資訊圖 radial-fill-v2 的弧形（可見範圍含圓頭）
V2_R, V2_W = 107.5, 22.0
V2_START, V2_SWEEP = 219.0, 282.0


def layer_single(face, rd):
    """
    單一資訊圖。style：radial-fill-v2（CAM 預設）、full-fill。
    Kraken Plus（方形 LCD 機種）上 CAM 會把 radial-fill 自動換成預設版面，所以 radial-fill 也畫成 v2。
    多於兩色時 CAM 渲染核心只用前兩色（原生預覽實測：紅黃綠三色只顯示紅→黃）。
    InfographicSquare 不顯示 logo（即使設定開啟，原生預覽也沒有）。
    """
    style = face.get("style", "radial-fill-v2")
    c1, c2 = _colors(face, 2)[:2]
    lay = Layer()

    if style == "full-fill":
        # InfographicFullFill：整個畫面由左（第一色）到右（第二色）的水平漸層，文字置中。
        # 原生預覽量測：數值 Bold 89.8px 以數字本身置中、基線 143.1；° 30px 基線上移 44.2、% 22.7px 基線上移 2.1；
        # 標籤 Bold 21px 置中、基線 176；開啟 logo 時 logo（14.93px、基線 70）在上方，數值與標籤各下移約 12px。
        grad = gradient_space([(0, c1), (1, c2)], _X / SIZE, "linear")
        lay.paint(np.ones((W, W), bool), grad)
        logo_on, logo_c, logo_o = _logo_cfg(face)
        dy_v, dy_l = (11.75, 12.0) if logo_on else (0.0, 0.0)
        if logo_on:
            draw_logo(lay, logo_c, y=70.0, size=14.93, outline=logo_o)
        s = rd.text()
        vsize, base = _shrink(s, 89.8, 200, 143.1 + dy_v)
        draw_value_unit(lay, rd, s, vsize, 120 - advance(s, BOLD, vsize) / 2, base,
                        {"°": (30.0 * vsize / 89.8, -44.22 * vsize / 89.8), "%": (22.7 * vsize / 89.8, -2.07),
                         "GHz": (21.0 * vsize / 89.8, -2.07)})
        draw_label_center(lay, rd, 21.0, 120, 176.0 + dy_l)
        return lay

    # radial-fill-v2：底環＋填滿弧
    cap = math.degrees(V2_W / 2 / V2_R)                 # 圓頭對應的角度
    m, _ = arc_mask(120, 120, V2_R, V2_W, V2_START + cap, V2_SWEEP - 2 * cap)
    lay.paint(m, hex_rgb(face.get("track_color", CAM_RING_TRACK)))
    if rd.pct > 0:
        vis = V2_SWEEP * rd.pct                          # 填滿段可見角度（含圓頭）
        m, rel = arc_mask(120, 120, V2_R, V2_W, V2_START + cap, max(vis - 2 * cap, 0.01))
        if face.get("use_ring_gradient"):
            # 環形漸層：依整圈位置取色（綠→黃→紅）
            t = (rel + cap) / V2_SWEEP
            col = gradient_space(norm_stops(face.get("ring_gradient") or CAM_RING_GRADIENT), t, "linear")
        else:
            # 第一色→第二色，線性光內插；原生預覽擬合：起點在可見起點前 3°、跨度為可見填滿角度的 0.897 倍
            t = (rel + cap + 3.0) / max(vis * 0.897, 1e-3)
            col = gradient_space([(0, c1), (1, c2)], t, "linear")
        lay.paint(m, col)
    # 文字：標籤 Bold 30（筆位 14.5、基線 88.05）；數值 Bold 87.43（筆位 11.75、基線 168.25）
    draw_label(lay, rd, 30.0, 14.5, 88.05)
    s = rd.text()
    if rd.unit == "GHz":
        # 時脈：數值 Bold 78.77（基線 163.96、筆位 12.04），GHz Bold 19.54 接在後面、基線 161.69
        vsize = _fit_size(s, BOLD, 78.77, 150)
        draw_value_unit(lay, rd, s, vsize, 12.04, 163.96, {"GHz": (19.54, -2.27)})
        return lay
    vsize = _fit_size(s, BOLD, 87.43, 150)
    pen, base = 11.75, 168.25
    if rd.unit == "%":
        # %：Bold 36.4（數值的 0.416 倍）、緊接數值、基線上移 2px
        draw_value_unit(lay, rd, s, vsize, pen, base, {"%": (vsize * 0.416, -2.0)})
        return lay
    paint_text(lay, s, BOLD, vsize, pen, base, rd.value_color, rd.value_outline)
    end = pen + advance(s, BOLD, vsize)
    top = base - cap_top(BOLD, vsize)
    draw_unit_after(lay, rd, vsize, end, top, base, deg_ratio=0.44, deg_dy=-2.0)
    return lay


# 雙資訊圖長條
BAR_X0, BAR_X1, BAR_H = 0.4, 239.7, 22.9
BAR_Y = (103.2, 136.56)
DUAL_RIGHT = 223.87         # 數值筆位右緣


def _dual_value(lay, rd, top_slot):
    """
    雙資訊圖的數值：靠右對齊 DUAL_RIGHT。溫度／使用率 Bold 100.7px、時脈 Bold 67.7px（太長時縮小）。
    上方讀值以基線（約 y=80）對齊、下方讀值以數字頂端 y=157 對齊。
    """
    s = rd.text()
    vsize = 67.7 if rd.unit == "GHz" else 100.7
    right = DUAL_RIGHT
    vsize = _fit_size(s, BOLD, vsize, right - 64)
    ch = cap_top(BOLD, vsize)
    if top_slot:
        base = 80.91 - (100.7 - vsize) * 0.057
    else:
        base = 157.0 + ch
    top = base - ch
    x0 = right - advance(s, BOLD, vsize)
    paint_text(lay, s, BOLD, vsize, x0, base, rd.value_color, rd.value_outline)
    if rd.unit == "GHz":
        gs = 18.66 * vsize / 67.7
        gx = DUAL_RIGHT - 8.1
        gb = (top - 13.2) if top_slot else (base + 21.13)
        paint_text(lay, "GHz", BOLD, gs, gx, gb, rd.value_color, rd.value_outline, "rs")
    elif rd.unit == "%":
        # %：數字照樣靠右，% 以數值的 0.188 倍字級擺在數字右下角（筆位 = 數值右緣 −0.5、基線上移 2.17；原生預覽量測）
        paint_text(lay, "%", BOLD, vsize * 0.1884, right - 0.5, base - 2.17, rd.value_color, rd.value_outline)
    else:
        draw_unit_after(lay, rd, vsize, right, top, base, deg_dy=-1.0 if top_slot else 0.0)


# 雙資訊圖弧形版（DualInfographic）：與單一資訊圖同半徑／線寬的整圈底環，
# 左右兩段填滿弧分別以 9 點鐘、3 點鐘方向為中心上下對稱展開，滿格可見範圍約 160°（含圓頭）。
ARC_TRACK = (21, 21, 21)    # 底環實測 #151515
ARC_FULL = 160.0
ARC_COLS = (77.0, 161.0)    # 左右兩欄文字中心


def _arc_value(lay, rd, cx, base):
    """雙弧版數值：Bold 41（最寬 75px，超過縮小並保持垂直中心）置中於 cx；時脈顯示 MHz 四位整數、不加單位。"""
    s = rd.mhz_text() if rd.unit == "GHz" else rd.text()
    vsize, b = _shrink(s, 41.04, 75.0, base)
    spec = {"°": (26.4 * vsize / 41.04, -10.79 * vsize / 41.04), "%": (19.16 * vsize / 41.04, -1.14)}
    if rd.unit == "GHz":
        paint_text(lay, s, BOLD, vsize, cx - advance(s, BOLD, vsize) / 2, b, rd.value_color, rd.value_outline)
    else:
        draw_value_unit(lay, rd, s, vsize, cx - advance(s, BOLD, vsize) / 2, b, spec)


def layer_dual(face, r1, r2):
    """雙資訊圖。style：bars（CAM dual-infographic-base）、arcs（DualInfographic）。"""
    c1, c2, c3 = _colors(face, 3)
    lay = Layer()
    if face.get("style", "bars") == "bars":
        # 長條版：不顯示 logo（原生預覽即使開啟也沒有）
        track = hex_rgb(face.get("track_color", CAM_BAR_TRACK))
        for rd, yc, col in ((r1, BAR_Y[0], c1), (r2, BAR_Y[1], c2)):
            lay.paint(pill(BAR_X0, BAR_X1, yc, BAR_H), track)
            if rd.pct > 0:
                lay.paint(pill(BAR_X0, BAR_X0 + (BAR_X1 - BAR_X0) * rd.pct, yc, BAR_H), col)
        draw_label(lay, r1, 26.13, 2.6, 82.9)
        draw_label(lay, r2, 26.13, 2.6, 176.05)
        _dual_value(lay, r1, True)
        _dual_value(lay, r2, False)
        return lay

    # arcs：整圈底環
    lay.paint(ring(120, 120, V2_R, V2_W), ARC_TRACK)
    cap = math.degrees(V2_W / 2 / V2_R)
    for rd, center, col in ((r1, 180.0, c1), (r2, 0.0, c2)):
        if rd.pct > 0:
            half = max(ARC_FULL * rd.pct / 2 - cap, 0.005)     # 中線半角（不含圓頭）
            m, _ = arc_mask(120, 120, V2_R, V2_W, center - half, 2 * half)
            lay.paint(m, col)
    # 文字：logo（12.18px、基線 72.25）開啟時數值與標籤下移 10px
    logo_on, logo_c, logo_o = _logo_cfg(face)
    dy = 10.0 if logo_on else 0.0
    if logo_on:
        draw_logo(lay, logo_c, y=72.25, size=12.18, outline=logo_o)
    for rd, cx in ((r1, ARC_COLS[0]), (r2, ARC_COLS[1])):
        _arc_value(lay, rd, cx, 122.33 + dy)
        draw_label_center(lay, rd, 19.0, cx, 155.06 + dy)
    return lay


# 三資訊圖（TripleInfographic）：細環（中線半徑 113、線寬 5）從 135° 順時針 270° 到 45°（含圓頭），
# 顏色沿角度在 Oklab 空間由第一色漸變到第二色；目前值位置畫半徑 7 的圓點，之後留一段空隙再接未啟用段
# （Kraken Plus 的 inactiveArcOpacity=1，未啟用段與啟用段同樣不透明）。
TRI_R, TRI_W = 113.0, 5.0
TRI_START, TRI_SWEEP = 135.0, 270.0
TRI_DOT_R = 7.0
TRI_GAP = 10.5              # 圓點中心到未啟用段起點（含圓頭）的角度


def layer_triple(face, r1, r2, r3):
    """三資訊圖：左側大讀值（reading1）、右側上下兩個小讀值（reading2／reading3），中間 1px 分隔線。"""
    cols = _colors(face, 2)                       # 三資訊圖使用全部色標（原生預覽：紅黃綠三色都出現）
    tri_stops = [(i / (len(cols) - 1), c) for i, c in enumerate(cols)]
    lay = Layer()
    cap = math.degrees(TRI_W / 2 / TRI_R)
    dot_ang = TRI_START + cap + (TRI_SWEEP - 2 * cap) * r1.pct          # 圓點中心角度
    # 環形漸層顏色（依絕對角度，整圈共用）
    rel_all = ((np.degrees(np.arctan2(_Y - 120, _X - 120)) - TRI_START) % 360.0)
    col = gradient_space(tri_stops, np.clip(rel_all / TRI_SWEEP, 0, 1), "oklab")
    # 啟用段：起點到圓點
    sw1 = dot_ang - (TRI_START + cap)
    if sw1 > 0.01:
        m, _ = arc_mask(120, 120, TRI_R, TRI_W, TRI_START + cap, sw1)
        lay.paint(m, col)
    # 未啟用段：圓點後留空隙，到終點
    s2 = dot_ang + TRI_GAP + cap
    end = TRI_START + TRI_SWEEP - cap
    if end - s2 > 0.01:
        m, _ = arc_mask(120, 120, TRI_R, TRI_W, s2, end - s2)
        lay.paint(m, col)
    a = math.radians(dot_ang)
    lay.paint(disk(120 + TRI_R * math.cos(a), 120 + TRI_R * math.sin(a), TRI_DOT_R), col)

    logo_on, logo_c, logo_o = _logo_cfg(face)
    if logo_on:
        draw_logo(lay, logo_c, y=60.0, size=11.88, outline=logo_o)
    dm, ds = (3.0, 2.0) if logo_on else (0.0, 0.0)          # logo 開啟時主讀值下移 3、小讀值下移 2
    # 分隔線
    y0, y1 = (90.0, 162.0) if logo_on else (87.0, 160.0)
    lay.paint((_X >= 153) & (_X < 154) & (_Y >= y0) & (_Y < y1), (188, 188, 188))
    # 主讀值：Bold 73.24 置中於 x=86.07、基線 140；° 28.08（基線上移 32.47）、% 15.43（上移 1.66）；標籤 13.17 置中、基線 160.95
    s = r1.mhz_text() if r1.unit == "GHz" else r1.text()
    vsize, b = _shrink(s, 73.24, 110.0, 140.0 + dm)
    k = vsize / 73.24
    spec = {"°": (28.08 * k, -32.47 * k), "%": (15.43 * k, -1.66)}
    if r1.unit == "GHz":
        paint_text(lay, s, BOLD, vsize, 86.07 - advance(s, BOLD, vsize) / 2, b, r1.value_color, r1.value_outline)
    else:
        draw_value_unit(lay, r1, s, vsize, 86.07 - advance(s, BOLD, vsize) / 2, b, spec)
    draw_label_center(lay, r1, 13.17, 85.9, 160.95 + dm)
    # 右側兩個小讀值：數值 Bold 25.99（筆位 165.43，最寬 40px），標籤 Bold 8.15（筆位 166.83、在數值下方 10.68）
    for rd, vb in ((r2, 106.24 + ds), (r3, 149.24 + ds)):
        s = rd.mhz_text() if rd.unit == "GHz" else rd.text()
        vsize, b = _shrink(s, 25.99, 40.0, vb)
        k = vsize / 25.99
        if rd.unit == "GHz":
            paint_text(lay, s, BOLD, vsize, 165.43, b, rd.value_color, rd.value_outline)
        else:
            draw_value_unit(lay, rd, s, vsize, 165.43, b, {"°": (10.74 * k, -10.76 * k), "%": (8.38 * k, -0.32)})
        draw_label(lay, rd, 8.15, 166.83, vb + 10.68)
    return lay


def _clock_hm(face, now):
    """依 12/24 小時制回傳 ('HH','MM')，保留前導零（與 CAM 預覽一致）。"""
    h = now.hour
    if str(face.get("format", "24hrs")).startswith("12"):
        h = h % 12 or 12
    return "%02d" % h, "%02d" % now.minute


def _hand(lay, ang, scale, col):
    """
    時／分針，形狀取自 CAM 的 Clockface-Hand.svg（座標單位 = SVG 單位 × scale）：
    樞紐在 SVG y=60.5；針身由 y=0（半寬 1.05）錐形加粗到 y=52.8（半寬 2.15），
    接一段 y 51.8→57、寬 2 的細線，樞紐處為半徑 3、線寬 2 的空心圓。
    """
    ux, uy = math.cos(ang), math.sin(ang)
    P = lambda y: (120 + ux * (60.5 - y) * scale, 120 + uy * (60.5 - y) * scale)
    (ax, ay), (bx, by) = P(1.04), P(52.8)
    lay.paint(tapered(ax, ay, 1.05 * scale, bx, by, 2.15 * scale), col)
    (cx, cy), (dx, dy) = P(51.8), P(57.0)
    lay.paint(segment(cx, cy, dx, dy, 2 * scale), col)
    lay.paint(ring(120, 120, 3 * scale, 2 * scale), col)


def _sec_trail(lay, ang, length, col, span_deg=9.0, peak=0.6):
    """
    秒針後方的淡出拖尾（CAM 預覽中秒針逆時針側有一片漸淡的秒針色扇形，靠中心較亮、往針尖變淡）：
    從秒針角度往回 span_deg 度，不透明度隨角度由 peak 線性降到 0，並隨半徑往外再減到 40%。
    （舊截圖解析度僅約 93px，此拖尾為依截圖外觀近似。）
    """
    dx, dy = _X - 120, _Y - 120
    d = np.hypot(dx, dy)
    behind = (math.degrees(ang) - np.degrees(np.arctan2(dy, dx))) % 360.0   # 在秒針後方幾度
    fade = (1 - behind / span_deg) * (1 - 0.6 * np.clip(d / length, 0, 1))
    a = np.where((behind <= span_deg) & (d <= length) & (d >= 4), peak * fade, 0.0)
    lay.paint(a, col)


def _sec_hand(lay, ang, scale, col):
    """秒針，取自 Clockface-SecHand.svg：樞紐在 y=60；針 y 0→58、寬 1.5；樞紐空心圓半徑 2、線寬 1；尾巴 y 62.5→66。"""
    # （舊截圖中的淡出拖尾是介面動畫殘影；原生預覽沒有拖尾，故不畫）
    ux, uy = math.cos(ang), math.sin(ang)
    P = lambda y: (120 + ux * (60 - y) * scale, 120 + uy * (60 - y) * scale)
    (ax, ay), (bx, by) = P(0), P(58)
    lay.paint(segment(ax, ay, bx, by, 1.5 * scale), col)
    (cx, cy), (dx, dy) = P(62.5), P(66)
    lay.paint(segment(cx, cy, dx, dy, 1.5 * scale), col)
    lay.paint(ring(120, 120, 2 * scale, 1 * scale), col)


def layer_clock(face, now=None):
    """時鐘。style：analog、digital、digital_gradient。"""
    now = now or datetime.datetime.now()
    style = face.get("style", "digital_gradient")
    lay = Layer()
    if style == "analog":
        a = face.get("analog") or {}
        dial = shape_rgb(a.get("dial", "#FFFFFF"))            # 時／分針顏色
        sec_c = shape_rgb(a.get("seconds", "#8A00FC"))        # 秒針顏色
        for i in range(12):                                     # 12 個圓點刻度：原生預覽固定白色，不隨 dial 變
            ang = math.radians(i * 30 - 90)
            lay.paint(disk(120 + 107.7 * math.cos(ang), 120 + 107.7 * math.sin(ang), 4.2), (255, 255, 255))
        s = now.second + now.microsecond / 1e6
        m = now.minute + s / 60
        h = (now.hour % 12) + m / 60
        _hand(lay, math.radians(h / 12 * 360 - 90), 1.17, dial)
        _hand(lay, math.radians(m / 60 * 360 - 90), 1.4, dial)
        _sec_hand(lay, math.radians(s / 60 * 360 - 90), 1.57, sec_c)
        return lay

    hh, mm = _clock_hm(face, now)
    mh = text_mask(hh, BOLD, 150.3, 120, 118.0, "ms")
    mm_ = text_mask(mm, LIGHT, 150.3, 120, 233.95, "ms")
    if style == "digital_gradient":
        stops = norm_stops((face.get("digital_gradient") or {}).get("gradient")
                           or [[0, "#00ffd3"], [100, "#0d00ff"]])
        col = gradient_space(stops, _Y / SIZE, "oklab")        # 由畫面頂端到底端，Oklab 內插
        lay.paint(mh, col)
        lay.paint(mm_, col)
    else:  # digital：文字色單色（CAM 另有 dial／gradient 欄位，預覽未顯示其用途，暫未使用）
        tc = hex_rgb((face.get("digital") or {}).get("text", "#FFFFFF"))
        lay.paint(mh, tc)
        lay.paint(mm_, tc)
    return lay


# ---------------- 合成 ----------------
_OV_CACHE = {}          # 前景圖層快取：內容相同就不重畫（每張約需 0.25～0.4 秒 CPU）
_OV_CACHE_MAX = 24


def _overlay_key(face, readings, now):
    """快取鍵：畫面設定＋各讀值實際顯示的內容（文字、填滿比例、顏色）＋時鐘顯示的時間。"""
    import json
    rk = tuple((r.text(), r.unit, r.label, round(r.pct, 3), r.value_color, r.text_color) for r in readings)
    tk = None
    if face.get("type") == "clock":
        now = now or datetime.datetime.now()
        if face.get("style") == "analog":
            tk = (now.hour, now.minute, now.second)
        else:
            tk = _clock_hm(face, now)
    return json.dumps(face, sort_keys=True, ensure_ascii=False, default=str), rk, tk


def overlay(face, readings=(), now=None):
    """畫出一個畫面的前景圖層，回傳 240×240 的 (預乘顏色, 不透明度)；相同內容直接取快取。"""
    try:
        key = _overlay_key(face, readings, now)
    except Exception:
        key = None
    if key is not None and key in _OV_CACHE:
        return _OV_CACHE[key]
    res = _overlay_draw(face, readings, now)
    if key is not None:
        if len(_OV_CACHE) >= _OV_CACHE_MAX:
            _OV_CACHE.pop(next(iter(_OV_CACHE)))     # 丟掉最舊的一筆
        _OV_CACHE[key] = res
    return res


def _overlay_draw(face, readings=(), now=None):
    """實際繪製前景圖層。"""
    t = face.get("type")
    if t == "single":
        lay = layer_single(face, readings[0])
    elif t == "dual":
        lay = layer_dual(face, readings[0], readings[1])
    elif t == "triple":
        lay = layer_triple(face, readings[0], readings[1], readings[2])
    elif t == "clock":
        lay = layer_clock(face, now)
    else:
        lay = Layer()
    return lay.downsample()


def compose(ov, background="#000000"):
    """前景圖層疊到背景（顏色字串或 PIL 影像）上，回傳 PIL RGB 240×240。"""
    col, a = ov
    if isinstance(background, Image.Image):
        bg = np.asarray(background.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS), np.float32)
    else:
        bg = np.empty((SIZE, SIZE, 3), np.float32)
        bg[:] = shape_rgb(background, (0, 0, 0))            # 背景色也經 CAM 的 gamma 轉換
    out = col + bg * (1 - a)[..., None]
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


def face_background(face):
    """畫面背景色（類比時鐘用 analog.background，其餘用 background）。"""
    if face.get("type") == "clock" and face.get("style") == "analog":
        return (face.get("analog") or {}).get("background", "#000000")
    return face.get("background", "#000000")


def render(face, readings=(), now=None, background=None):
    """畫一整張畫面（前景＋背景），回傳 PIL RGB 240×240。"""
    return compose(overlay(face, readings, now), background if background is not None else face_background(face))


def circle_preview(img):
    """
    預覽用：模擬 LCD 可視範圍。Kraken Plus 的 LCD 實體是方形（CAM 程式碼把 KrakenPlus 歸為方形 LCD 機種，
    原廠畫面也用滿四個角；圓形只是外圈玻璃），所以只做小圓角，外面塗深灰。（函式名稱保留以相容舊呼叫）
    """
    # 實機會顯示到四個角，預覽不再做圓角遮罩（之前的圓角讓圖片預覽看起來像被裁掉）
    return img.convert("RGB")


if __name__ == "__main__":
    # 直接執行：輸出所有版面的預覽拼圖 preview_all.png（不碰水冷）
    import os
    liq = Reading(36.1, "°", "Liquid", 0, 100)
    cpu = Reading(52, "°", "CPU", 0, 100)
    gpu = Reading(61, "°", "GPU", 0, 100)
    cl = Reading(100, "%", "CPU", 0, 100)
    cf = Reading(4.7, "GHz", "CPU", 0, 6)
    gf = Reading(2.5, "GHz", "GPU", 0, 3)
    now = datetime.datetime(2026, 11, 15, 9, 33, 41)
    faces = [
        ({"type": "single", "style": "radial-fill-v2"}, [liq]),
        ({"type": "dual", "style": "bars"}, [cpu, gpu]),
        ({"type": "dual", "style": "bars"}, [cf, gf]),
        ({"type": "clock", "style": "digital_gradient"}, []),
        ({"type": "clock", "style": "analog"}, []),
        ({"type": "clock", "style": "digital"}, []),
        ({"type": "single", "style": "full-fill"}, [liq]),
        ({"type": "dual", "style": "bars"}, [cl, gpu]),
        ({"type": "dual", "style": "arcs"}, [cpu, gpu]),
    ]
    sheet = Image.new("RGB", (SIZE * 3 + 40, SIZE * 3 + 40), (18, 18, 20))
    for i, (f, rds) in enumerate(faces):
        img = circle_preview(render(f, rds, now))
        sheet.paste(img, (10 + (i % 3) * (SIZE + 10), 10 + (i // 3) * (SIZE + 10)))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview_all.png")
    sheet.save(out)
    print("已輸出", out)
