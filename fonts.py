# -*- coding: utf-8 -*-
"""
fonts.py：取得與 NZXT CAM LCD 畫面相同的字型

CAM 的 LCD 畫面由原生模組 cam-core/dist/index.node（Rust＋wgpu）繪製，
模組內嵌了三套字型：Gotham Bold、Gotham Light、NZXT logo 字型。
這裡直接從使用者電腦上的 CAM 安裝檔把它們切出來，存到本資料夾的 fonts/
（已列入 .gitignore，不散布字型檔）。找不到 CAM 時退回 Windows 內建字型。
"""
import os
import re
import struct

from PIL import ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

# CAM 原生模組的預設位置（可用環境變數覆寫）
CAM_CORE = os.environ.get(
    "KRAKEN_CAM_CORE",
    r"C:\Program Files\NZXT CAM\resources\app.asar.unpacked\node_modules\@nzxt\cam-core\dist\index.node",
)

# 要取出的字型：檔名 → name 表裡用來辨識的字串
_WANTED = {
    "Gotham-Bold.ttf": b"Gotham-Bold",
    "Gotham-Light.ttf": b"Gotham-Light",
    "NZXT-Font.ttf": b"NZXT-Font",
}

# 退回用的系統字型（粗、細）
_FALLBACK = {
    "Gotham-Bold.ttf": [r"C:\Windows\Fonts\bahnschrift.ttf", r"C:\Windows\Fonts\segoeuib.ttf"],
    "Gotham-Light.ttf": [r"C:\Windows\Fonts\segoeuil.ttf", r"C:\Windows\Fonts\bahnschrift.ttf"],
    "NZXT-Font.ttf": [],
}


def _scan_sfnt(blob):
    """
    在二進位檔中找出所有內嵌的 OpenType 字型（sfnt 標頭），
    依表格目錄算出整個字型的長度，回傳 [(字型位元組, name 表內容)]。
    """
    found = []
    # TrueType 字型以 0x00010000 開頭；CFF 字型（Gotham 就是）以 'OTTO' 開頭
    starts = []
    for sig in (b"\x00\x01\x00\x00", b"OTTO"):
        j = blob.find(sig)
        while j >= 0:
            starts.append(j)
            j = blob.find(sig, j + 1)
    for i in sorted(starts):
        try:
            n = struct.unpack(">H", blob[i + 4:i + 6])[0]  # 表格數量
            if 4 <= n <= 40:
                tables, end, ok = {}, 0, True
                for k in range(n):
                    tag, _cs, off, ln = struct.unpack(">4sIII", blob[i + 12 + 16 * k:i + 28 + 16 * k])
                    if not re.fullmatch(rb"[A-Za-z0-9/ ]{4}", tag):
                        ok = False
                        break
                    tables[tag] = (off, ln)
                    end = max(end, off + ln)
                if ok and b"name" in tables and b"cmap" in tables and end < 5_000_000:
                    font = blob[i:i + end]
                    no, nl = tables[b"name"]
                    found.append((font, font[no:no + nl].replace(b"\x00", b"")))
        except struct.error:
            pass
    return found


def ensure_fonts():
    """第一次使用時從 CAM 取出字型；已存在就略過。回傳是否全部到位。"""
    if all(os.path.exists(os.path.join(FONT_DIR, f)) for f in _WANTED):
        return True
    if not os.path.exists(CAM_CORE):
        return False
    with open(CAM_CORE, "rb") as fh:
        blob = fh.read()
    os.makedirs(FONT_DIR, exist_ok=True)
    for font, name in _scan_sfnt(blob):
        for fn, key in _WANTED.items():
            path = os.path.join(FONT_DIR, fn)
            if key in name and not os.path.exists(path):
                with open(path, "wb") as out:
                    out.write(font)
    return all(os.path.exists(os.path.join(FONT_DIR, f)) for f in _WANTED)


def font_path(name):
    """回傳字型檔路徑（優先 CAM 原廠字型，否則系統字型；都沒有回傳 None）。"""
    ensure_fonts()
    p = os.path.join(FONT_DIR, name)
    if os.path.exists(p):
        return p
    for alt in _FALLBACK.get(name, []):
        if os.path.exists(alt):
            return alt
    return None


_cache = {}


def get(name, size):
    """取得 PIL 字型物件（依檔名與像素大小快取）。"""
    size = max(1, int(round(size)))
    key = (name, size)
    if key not in _cache:
        p = font_path(name)
        _cache[key] = ImageFont.truetype(p, size) if p else ImageFont.load_default()
    return _cache[key]


def size_for_height(name, height_px, sample="0"):
    """
    求字型大小，使 sample 字元的實際墨跡高度等於 height_px。
    原廠畫面是以「數字高度」量測的，所以用這個把量測值換成字型大小。
    """
    ref = 200
    f = get(name, ref)
    l, t, r, b = f.getbbox(sample)
    h = max(1, b - t)
    return ref * height_px / h
