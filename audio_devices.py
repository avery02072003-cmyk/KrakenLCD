# -*- coding: utf-8 -*-
"""
audio_devices.py：直接向 Windows 查詢「目前」有哪些播放裝置，以及預設播放裝置是哪一個。

為什麼不用 pyaudiowpatch／PortAudio 列裝置：PortAudio 只在「完全重新初始化」時才重新列舉裝置，
音訊燈效正在擷取時它一直處於初始化狀態，拔掉或插上的裝置都不會反映出來。
這裡改用 Windows Core Audio（IMMDeviceEnumerator），每次呼叫都是即時結果，而且只用 ctypes，不需額外套件。

回傳的裝置名稱是 Windows 的「易記名稱」（例如「喇叭 (Realtek(R) Audio)」），
與 pyaudiowpatch 的 WASAPI 裝置名稱相同（後者 loopback 裝置多了「 [Loopback]」字尾）。
"""
import ctypes
from ctypes import POINTER, byref, c_ubyte, c_uint, c_ulong, c_ushort, c_void_p, c_wchar_p

_ole32 = ctypes.windll.ole32

# ---------- COM 基本型別 ----------
class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


def _guid(s):
    """字串 → GUID 結構。"""
    g = GUID()
    _ole32.CLSIDFromString(c_wchar_p(s), byref(g))
    return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


class PROPVARIANT(ctypes.Structure):
    """只用到字串型別（VT_LPWSTR）；整體大小在 64 位元是 24 位元組。"""
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                ("val", c_void_p), ("pad", c_void_p)]


CLSID_MMDeviceEnumerator = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
PKEY_Device_FriendlyName = PROPERTYKEY(_guid("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)
CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = -2147417850      # 0x80010106：這個執行緒已用別種模式初始化 COM（仍可使用）
E_RENDER, E_MULTIMEDIA = 0, 1         # 播放裝置；預設裝置角色（多媒體，與 PortAudio 判斷預設裝置的方式相同）
DEVICE_STATE_ACTIVE = 0x1
STGM_READ = 0
VT_LPWSTR = 31


def _method(obj, index, *argtypes):
    """取 COM 物件 vtable 第 index 個方法（0~2 是 IUnknown 的 QueryInterface／AddRef／Release）。"""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtbl[index])
    return lambda *a: fn(obj, *a)


def _release(obj):
    if obj:
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        ctypes.WINFUNCTYPE(c_ulong, c_void_p)(vtbl[2])(obj)


def _friendly_name(dev):
    """IMMDevice → 易記名稱（IMMDevice::OpenPropertyStore → IPropertyStore::GetValue）。"""
    store = c_void_p()
    _method(dev, 4, c_ulong, POINTER(c_void_p))(STGM_READ, byref(store))
    try:
        pv = PROPVARIANT()
        _method(store, 5, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))(byref(PKEY_Device_FriendlyName), byref(pv))
        try:
            return ctypes.wstring_at(pv.val) if pv.vt == VT_LPWSTR and pv.val else ""
        finally:
            _ole32.PropVariantClear(byref(pv))
    finally:
        _release(store)


def list_render_devices():
    """
    回傳 {"devices": [目前啟用中的播放裝置名稱...], "default": 預設播放裝置名稱}。
    每次都是即時查詢（拔插裝置立刻反映）。失敗時丟出例外，由呼叫端決定備援。
    """
    hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    need_uninit = hr in (0, 1)                 # S_OK／S_FALSE 都要配對 CoUninitialize
    if hr not in (0, 1, RPC_E_CHANGED_MODE & 0xFFFFFFFF, RPC_E_CHANGED_MODE):
        raise OSError("CoInitializeEx 失敗：0x%08X" % (hr & 0xFFFFFFFF))
    enum = c_void_p()
    try:
        _ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                byref(IID_IMMDeviceEnumerator), byref(enum))
        if not enum:
            raise OSError("無法建立 MMDeviceEnumerator")
        # 啟用中的播放裝置
        coll = c_void_p()
        _method(enum, 3, c_uint, c_ulong, POINTER(c_void_p))(E_RENDER, DEVICE_STATE_ACTIVE, byref(coll))
        names = []
        try:
            n = c_uint()
            _method(coll, 3, POINTER(c_uint))(byref(n))
            for i in range(n.value):
                dev = c_void_p()
                _method(coll, 4, c_uint, POINTER(c_void_p))(i, byref(dev))
                try:
                    names.append(_friendly_name(dev))
                finally:
                    _release(dev)
        finally:
            _release(coll)
        # 預設播放裝置（沒有任何播放裝置時會失敗 → 回傳空字串）
        default = ""
        dev = c_void_p()
        try:
            _method(enum, 4, c_uint, c_uint, POINTER(c_void_p))(E_RENDER, E_MULTIMEDIA, byref(dev))
            default = _friendly_name(dev)
        except OSError:
            pass
        finally:
            _release(dev)
        return {"devices": [x for x in names if x], "default": default}
    finally:
        _release(enum)
        if need_uninit:
            _ole32.CoUninitialize()


if __name__ == "__main__":
    import json
    print(json.dumps(list_render_devices(), ensure_ascii=False, indent=2))
