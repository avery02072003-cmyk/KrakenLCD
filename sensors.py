# -*- coding: utf-8 -*-
"""
sensors.py：讀取要顯示在 LCD 上的數值

來源：
- 液溫：水冷自己的 HID 狀態廣播（由 KrakenLCD.read_status() 被動讀取，不影響 Fan Control）
- GPU 溫度／使用率：NVIDIA NVML（pynvml，不需管理員）
- CPU 溫度：LibreHardwareMonitorLib（借用 Fan Control 資料夾內的 DLL；需以管理員執行，
  否則溫度為 None，會自動退回顯示 CPU 使用率）
- CPU 使用率：psutil
每個來源失敗都只回傳 None，不讓整個程式中斷。
"""
import logging
import os
import sys

_LOG = logging.getLogger("sensors")

# Fan Control 內附的 LibreHardwareMonitorLib.dll 位置（可用環境變數覆寫）
LHM_DLL = os.environ.get("KRAKEN_LHM_DLL", r"H:\FanControl\LibreHardwareMonitorLib.dll")


class Sensors:
    def __init__(self):
        self._nvml = None
        self._gpu = None
        self._lhm = None
        self._cpu_hw = None
        self._init_gpu()
        self._init_cpu()
        try:  # psutil 第一次讀使用率一律回傳 0，先暖機一次並稍等取樣
            import psutil
            import time
            psutil.cpu_percent(interval=None)
            time.sleep(0.2)
        except Exception:
            pass

    # ---------- GPU（NVML）----------
    def _init_gpu(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml = pynvml
            self._gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception as e:
            _LOG.info("GPU 感測器無法使用：%s", e)

    def gpu_temp(self):
        try:
            return float(self._nvml.nvmlDeviceGetTemperature(self._gpu, 0))
        except Exception:
            return None

    def gpu_load(self):
        try:
            return float(self._nvml.nvmlDeviceGetUtilizationRates(self._gpu).gpu)
        except Exception:
            return None

    # ---------- CPU（LibreHardwareMonitor + psutil）----------
    def _init_cpu(self):
        try:
            import clr  # pythonnet
            sys.path.append(os.path.dirname(LHM_DLL))
            clr.AddReference(LHM_DLL)
            from LibreHardwareMonitor.Hardware import Computer, HardwareType
            c = Computer()
            c.IsCpuEnabled = True
            c.Open()
            self._lhm = c
            for hw in c.Hardware:
                if hw.HardwareType == HardwareType.Cpu:
                    self._cpu_hw = hw
                    break
        except Exception as e:
            _LOG.info("LibreHardwareMonitor 無法載入（CPU 溫度不可用）：%s", e)

    def cpu_temp(self):
        """優先取 CPU Package，沒有就取 Core Max（AMD 為 Tctl/Tdie）。非管理員時回傳 None。"""
        if self._cpu_hw is None:
            return None
        try:
            from LibreHardwareMonitor.Hardware import SensorType
            self._cpu_hw.Update()
            best = {}
            for s in self._cpu_hw.Sensors:
                if s.SensorType == SensorType.Temperature and s.Value is not None:
                    best[s.Name] = float(s.Value)
            for name in ("CPU Package", "Core (Tctl/Tdie)", "Core Max", "Core Average"):
                if name in best:
                    return best[name]
        except Exception:
            pass
        return None

    def cpu_load(self):
        """CPU 使用率（%）：優先取 LibreHardwareMonitor 的 CPU Total，拿不到再用 psutil。"""
        if self._cpu_hw is not None:
            try:
                from LibreHardwareMonitor.Hardware import SensorType
                self._cpu_hw.Update()
                for s in self._cpu_hw.Sensors:
                    if s.SensorType == SensorType.Load and s.Name == "CPU Total" and s.Value is not None:
                        return float(s.Value)
            except Exception:
                pass
        try:
            import psutil
            return float(psutil.cpu_percent(interval=None))
        except Exception:
            return None

    def cpu_freq(self):
        """
        CPU 時脈（GHz）。優先取 LibreHardwareMonitor 各核心 Clock 的最大值（即時時脈，
        需管理員）；拿不到就退回 psutil（Windows 上通常只回報基礎時脈）。
        """
        if self._cpu_hw is not None:
            try:
                from LibreHardwareMonitor.Hardware import SensorType
                self._cpu_hw.Update()
                clocks = [float(s.Value) for s in self._cpu_hw.Sensors
                          if s.SensorType == SensorType.Clock and s.Value is not None
                          and "Core" in s.Name and float(s.Value) > 0]
                if clocks:
                    return max(clocks) / 1000.0
            except Exception:
                pass
        try:
            import psutil
            f = psutil.cpu_freq()
            return f.current / 1000.0 if f and f.current else None
        except Exception:
            return None

    def gpu_freq(self):
        """GPU 核心時脈（GHz），來源 NVML 的 Graphics clock。"""
        try:
            return float(self._nvml.nvmlDeviceGetClockInfo(self._gpu, 0)) / 1000.0  # 0 = NVML_CLOCK_GRAPHICS
        except Exception:
            return None

    def read(self, key, status=None):
        """
        依 CAM 的 monitoringKey 取數值，回傳 (數值, 單位)。
        單位：'°'（溫度，攝氏）、'%'（使用率）、'GHz'（時脈）。
        CPU 溫度讀不到（非管理員）時改回傳 CPU 使用率。
        """
        if key == "LiquidTemp":
            return (status or {}).get("liquid"), "°"
        if key == "CPUTemp":
            v = self.cpu_temp()
            return (v, "°") if v is not None else (self.cpu_load(), "%")
        if key == "GPUTemp":
            return self.gpu_temp(), "°"
        if key == "CPULoad":
            return self.cpu_load(), "%"
        if key == "GPULoad":
            return self.gpu_load(), "%"
        if key == "CPUFrequency":
            return self.cpu_freq(), "GHz"
        if key == "GPUFrequency":
            return self.gpu_freq(), "GHz"
        return None, ""

    def close(self):
        try:
            if self._lhm is not None:
                self._lhm.Close()
        except Exception:
            pass
        try:
            if self._nvml is not None:
                self._nvml.nvmlShutdown()
        except Exception:
            pass
