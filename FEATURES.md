# NZXT CAM 功能清單（Kraken 2024 Plus）與本程式對照

[English](#nzxt-cam-feature-map-kraken-2024-plus-vs-this-tool)

擷取來源：NZXT CAM 4.76.9 實機操作畫面、CAM 匯出設定檔、CAM app.asar 內的前端程式碼。擷取日期 2026-11-14～15。

狀態欄：✅ 已實作｜🟡 部分／外觀尚未與原廠一致｜⬜ 未實作｜➖ 不做（交給 Fan Control 或與硬體無關）

---

## 1. 硬體

| 項目 | 內容 |
|---|---|
| 水冷頭 | Kraken 2024 Plus（USB 1E71:3014），韌體 1.0.7 |
| LCD | 240×240 圓形，RGB565 |
| RGB 通道 | 1 個通道，接 F240 RGB Core (EV) fan（配件代碼 0x29）：2 顆風扇，每顆 8 段 LED |
| 感測值 | 液溫、泵轉速／工作比、風扇轉速／工作比（HID 0x75 0x01 廣播） |

## 2. 監控頁（CAM「電腦監控」）

| 功能 | 狀態 | 備註 |
|---|---|---|
| CPU 溫度／負載／時脈 | ✅ | 時脈取 LibreHardwareMonitor 各核心最大值（需管理員，否則退回 psutil 基礎時脈） |
| GPU 溫度／負載／時脈 | ✅ | NVML |
| 液溫、泵與風扇轉速 | ✅ | `python kraken_lcd.py status` |
| 系統規格頁（CPU、GPU、主機板、RAM、儲存、網路、音訊） | ➖ | 純資訊頁，與水冷控制無關 |

## 3. 冷卻（CAM「冷卻」）

| 功能 | 狀態 | 備註 |
|---|---|---|
| 預設檔：靜音、效能、固定、自訂 | ➖ | 交給 Fan Control。本程式**永不送出**泵／風扇轉速指令 |
| 自訂曲線：溫度來源、20–100°C 每 5°C 一點、0–100% | ➖ | 同上 |

## 4. LCD 顯示（CAM「照明」→ LCD Display）

共 7 種模式；亮度 0–100%、旋轉 0/90/180/270°。

| 模式 | CAM 內部名稱 | 選項 | 狀態 |
|---|---|---|---|
| 液溫 | Liquid | 無 | ✅ Kraken 2024 Plus 韌體不支援切換到內建液溫畫面（0x38 指令後畫面停在上一張），改由電腦端以單一資訊圖預設樣式每秒畫一次 |
| 圖片／GIF | Image / GIF | 檔案；GIF 循環播放 | ✅ 保留原比例完整顯示，空白處補黑邊 |
| 單一資訊圖 | InfographicLCD：`radial-fill-v2`、`InfographicFullFill`、`InfographicRadialFill` | 讀值 1 個、讀值文字顏色、標籤顏色、視覺化雙色漸層、環形漸層（綠 0%→黃 50%→紅 100%）、背景色、NZXT logo 開關與顏色 | ✅ radial-fill-v2 依 CAM 渲染核心原生 240×240 預覽逐像素重建（平均差異 1.5／255）；full-fill 依原廠著色器（整面水平漸層）重建，文字位置推測；radial-fill 在 Kraken Plus 上 CAM 會自動換成預設版面，故同 v2 |
| 雙資訊圖 | DualInfographic：`dual-infographic-base`（長條）、弧形 | 讀值 2 個、兩色（6B00DE / D600BF；CAM 的第三色「底條」不會傳給渲染核心，所以不提供）、背景 | ✅ 長條依原生預覽逐像素重建（平均差異 2.3／255，底條固定 #898989）；弧形（左右兩道）無原廠參考，推測 |
| 單一資訊圖＋GIF 背景 | GifInfographic / GifDualInfographic | 同上＋GIF 檔 | ✅ |
| 時鐘 | Clockface：`analog`、`digital`、`digital_gradient` | 類比：背景、錶盤、秒針色（預設 8A00FC）；數位：錶盤、文字、漸層（4FC5FA→8221FF）、12/24 小時；漸層數位：漸層（00ffd3→0d00ff）、12/24 小時 | ✅ 漸層數位依原生預覽重建（Oklab 由上到下，平均差異 1.1／255）；類比指針取自 CAM 內嵌 SVG、比例依舊截圖校正；數位模式 dial／gradient 欄位用途不明，目前只用文字色 |
| 網頁整合 | WebIntegration | 網址（CAM 用離屏瀏覽器渲染） | ⬜ |
| 輪播 | Carousel | 多個畫面、每畫面停留時間（預設 5 秒） | ✅ |

可選讀值（`monitoringKey`）：`LiquidTemp`、`CPUTemp`、`CPULoad`、`CPUFrequency`、`GPUTemp`、`GPULoad`、`GPUFrequency`。

調色盤：20 個預設色塊＋自訂 HEX。原廠字型為 **Gotham**（CAM asar 內的 `GothamSSm-Bold_Web.woff`），logo 字型 `NZXT-Font.ttf`；本程式執行時從本機 CAM 安裝檔取出，倉庫不附字型。

### 輪播範例（開發時用來對照的設定）

| # | 畫面 | 內容 |
|---|---|---|
| 1 | 單一資訊圖 `radial-fill-v2` | LiquidTemp，色 6B00DE / D600BF，環形漸層 00ff00 / ffff00 / ff0000，白色 logo |
| 2 | 雙資訊圖 `dual-infographic-base` | CPUTemp / GPUTemp，色 6B00DE / D600BF |
| 3 | 雙資訊圖 `dual-infographic-base` | CPUFrequency / GPUFrequency |
| 4 | 時鐘 `digital_gradient` | 24 小時，漸層 00ffd3 → 0d00ff |

每個畫面停留 5 秒，LCD 旋轉 -90°。

## 5. RGB 效果（CAM「照明」→ F240 RGB Core (EV) fan）

共 20 種，另有通道亮度滑桿與全域亮度。「韌體」表示 Kraken 自己跑動畫（HUE2 協定 `0x2A 0x04`，格式同 liquidctl 的 Kraken X3，已在 2024 Plus 實測，送一次即可流暢播放）；「軟體」表示電腦端逐格計算後送出（韌體約每 0.3 秒才處理一次指令，所以上限約每秒 2.5 格）。

| # | 效果 | 選項 | 種類 |
|---|---|---|---|
| 0 | Fixed | 單色；可逐顆 LED 自訂色 | 韌體 |
| 1 | Spectrum Wave | 自動色、速度、方向 | 韌體 |
| 2 | Breathing | 最多 8 色、逐 LED 開關、速度 | 韌體 |
| 3 | Fading | 預設 3 色（紫、綠、藍）、速度 | 韌體 |
| 4 | Covering Marquee | 色彩清單、速度、方向 | 韌體 |
| 5 | Temperature Monitor | 來源（CPU／GPU／液溫）、溫度範圍 15–90°C、自訂漸層 | 軟體 |
| 6 | Marquee | 顏色、速度、方向、LED 大小 | 韌體 |
| 7 | Pulse | 顏色、速度 | 韌體 |
| 8 | Super Rainbow | 自動色、速度、方向 | 韌體 |
| 9 | Audio Bass | 單色 | 軟體（音訊） |
| 10 | Audio Level | 自訂漸層 | 軟體（音訊） |
| 11 | Rainbow Flow | 自動色、速度、方向 | 韌體 |
| 12 | Alternating | 2 色、速度、方向、LED 大小（預設 4）、「移動」開關 | 韌體 |
| 13 | Tai Chi | 2 色（預設紅、藍）、速度、方向 | 韌體 |
| 14 | Starry Night | 單色（預設 #51007A）、速度 | 韌體 |
| 15 | Rainbow Pulse | 自動色、速度、方向 | 韌體 |
| 16 | Candle | 單色（預設 #FFA500） | 韌體 |
| 17 | Wave | 自選 LED（點選 LED 個別設定）、速度、方向 | 軟體 |
| 18 | Water Cooler | 自動色（藍）、速度 | 韌體 |
| 19 | Loading | 單色（預設 #51007A）、速度 | 韌體 |

說明：第 3–8 項的先後順序取自操作紀錄，可能有一兩項前後對調；名稱與選項已確認。

狀態：✅ 標「韌體」的效果改用韌體動畫（模式值見 rgb.py 的 `HW_MODES`），動畫流暢；Temperature Monitor、Wave、Audio Bass／Audio Level 仍由電腦端逐格送（最多每秒 2.5 格，音訊效果會有延遲感）。Audio 效果需要 pyaudiowpatch 擷取系統聲音，未安裝時顯示靜態顏色。已知問題：Starry Night 韌體動畫底色偏白；Wave 的韌體模式值未知。

## 6. 設定頁

| 功能 | 狀態 | 備註 |
|---|---|---|
| 一般：開機啟動、語言、溫度單位 °C/°F | ✅ | 溫度單位已支援；控制台可切換中文／English；開機啟動用 `install_autostart.ps1`（工作排程器，登入後 10 秒以最高權限啟動） |
| 迷你模式：主題、顏色、透明度、尺寸、方向、置頂、顯示哪些讀值 | ➖ | 桌面小工具，與水冷無關 |
| 裝置韌體更新（Kraken Plus 1.0.7） | ➖ | 需要時暫時開 CAM 更新 |
| CAM 設定檔：每個設定檔綁定照明與冷卻預設，可依程式自動切換 | ⬜ | 可用多個 config 檔＋切換指令實作 |
| 設定備份：匯出／匯入 JSON | ✅ | 本程式設定就是 `config.json` |
| 支援（送出客服單）、Beta | ➖ | |

## 7. 與 CAM 共存須知

- CAM 開著時會同時送冷卻與燈效指令，與 Fan Control、本程式互搶（曾見 Fan Control 日誌 `TryOpen failed (device busy or inaccessible)`）；使用本程式時請關閉 CAM。
- 本程式以共用模式開 HID，Fan Control 可同時運作（實測推畫面期間風扇工作比照常變化）。
- LCD 用的 WinUSB 介面是獨占的：同一時間只能有一個程式推畫面。
- 建議以系統管理員身分執行：`Stop-Service CAMService; Set-Service CAMService -StartupType Manual`。CAM 每次啟動都會把服務改回「自動」。

---

# NZXT CAM feature map (Kraken 2024 Plus) vs this tool

[中文](#nzxt-cam-功能清單kraken-2024-plus與本程式對照)

Sources: NZXT CAM 4.76.9 running on the actual hardware, a CAM settings export, and the front-end code inside CAM's app.asar. Collected 2026-11-14 to 15.

Status: ✅ implemented | 🟡 partial / not yet identical to CAM | ⬜ not implemented | ➖ out of scope (left to Fan Control or unrelated to the hardware)

---

## 1. Hardware

| Item | Details |
|---|---|
| Pump head | Kraken 2024 Plus (USB 1E71:3014), firmware 1.0.7 |
| LCD | 240×240 round, RGB565 |
| RGB channel | 1 channel driving F240 RGB Core (EV) fans (accessory ID 0x29): 2 fans, 8 LED segments each |
| Sensors | Liquid temperature, pump speed/duty, fan speed/duty (HID 0x75 0x01 broadcast) |

## 2. Monitoring (CAM "PC Monitoring")

| Feature | Status | Notes |
|---|---|---|
| CPU temperature / load / clock | ✅ | Clock is the highest per-core value from LibreHardwareMonitor (needs administrator; otherwise falls back to the psutil base clock) |
| GPU temperature / load / clock | ✅ | NVML |
| Liquid temperature, pump and fan speed | ✅ | `python kraken_lcd.py status` |
| System specs page (CPU, GPU, motherboard, RAM, storage, network, audio) | ➖ | Information only, unrelated to cooler control |

## 3. Cooling (CAM "Cooling")

| Feature | Status | Notes |
|---|---|---|
| Presets: Silent, Performance, Fixed, Custom | ➖ | Left to Fan Control. This tool **never sends** pump or fan speed commands |
| Custom curve: temperature source, 20–100°C in 5°C steps, 0–100% | ➖ | Same as above |

## 4. LCD display (CAM "Lighting" → LCD Display)

7 modes; brightness 0–100%, rotation 0/90/180/270°.

| Mode | CAM internal name | Options | Status |
|---|---|---|---|
| Liquid | Liquid | None | ✅ The Kraken 2024 Plus firmware cannot switch to its built-in liquid screen (after the 0x38 command the LCD keeps the previous image), so the host draws it once per second in the default single-infographic style |
| Image / GIF | Image / GIF | File; GIFs loop | ✅ Keeps the aspect ratio, with black bars |
| Single infographic | InfographicLCD: `radial-fill-v2`, `InfographicFullFill`, `InfographicRadialFill` | 1 reading, value color, label color, two-color gradient, ring gradient (green 0% → yellow 50% → red 100%), background color, NZXT logo on/off and color | ✅ radial-fill-v2 is rebuilt pixel by pixel from the CAM renderer's native 240×240 preview (mean difference 1.5/255); full-fill is rebuilt from CAM's shader (full-screen horizontal gradient), with text positions estimated; on the Kraken Plus CAM replaces radial-fill with the default layout, so it renders like v2 |
| Dual infographic | DualInfographic: `dual-infographic-base` (bars), arcs | 2 readings, two colors (6B00DE / D600BF; CAM's third "track" color is never passed to the renderer, so it is not offered), background | ✅ Bars rebuilt pixel by pixel from the native preview (mean difference 2.3/255, track fixed at #898989); arcs (left and right) have no CAM reference and are estimated |
| Infographic + GIF background | GifInfographic / GifDualInfographic | Same as above + GIF file | ✅ |
| Clock | Clockface: `analog`, `digital`, `digital_gradient` | Analog: background, dial, second-hand color (default 8A00FC); digital: dial, text, gradient (4FC5FA→8221FF), 12/24-hour; gradient digital: gradient (00ffd3→0d00ff), 12/24-hour | ✅ Gradient digital rebuilt from the native preview (Oklab, top to bottom, mean difference 1.1/255); analog hands come from CAM's embedded SVG, proportions calibrated from older screenshots; the digital mode's dial/gradient fields have no known purpose, so only the text color is used |
| Web integration | WebIntegration | URL (CAM renders it in an off-screen browser) | ⬜ |
| Carousel | Carousel | Multiple faces, time per face (default 5 s) | ✅ |

Available readings (`monitoringKey`): `LiquidTemp`, `CPUTemp`, `CPULoad`, `CPUFrequency`, `GPUTemp`, `GPULoad`, `GPUFrequency`.

Palette: 20 preset swatches plus custom HEX. CAM's font is **Gotham** (`GothamSSm-Bold_Web.woff` inside CAM's asar) and the logo font is `NZXT-Font.ttf`; this tool extracts them from the local CAM install at runtime, and no fonts are shipped in the repository.

### Carousel example (the setup used for comparison during development)

| # | Face | Content |
|---|---|---|
| 1 | Single infographic `radial-fill-v2` | LiquidTemp, colors 6B00DE / D600BF, ring gradient 00ff00 / ffff00 / ff0000, white logo |
| 2 | Dual infographic `dual-infographic-base` | CPUTemp / GPUTemp, colors 6B00DE / D600BF |
| 3 | Dual infographic `dual-infographic-base` | CPUFrequency / GPUFrequency |
| 4 | Clock `digital_gradient` | 24-hour, gradient 00ffd3 → 0d00ff |

5 seconds per face, LCD rotated -90°.

## 5. RGB effects (CAM "Lighting" → F240 RGB Core (EV) fan)

20 effects, plus a channel brightness slider and global brightness. "Firmware" means the Kraken runs the animation itself (HUE2 protocol `0x2A 0x04`, same format as liquidctl's Kraken X3, tested on the 2024 Plus: sent once, it plays smoothly). "Host" means the PC computes each frame and sends it (the firmware only processes a command about every 0.3 s, so the limit is about 2.5 frames per second).

| # | Effect | Options | Type |
|---|---|---|---|
| 0 | Fixed | One color; optional per-LED colors | Firmware |
| 1 | Spectrum Wave | Automatic colors, speed, direction | Firmware |
| 2 | Breathing | Up to 8 colors, per-LED on/off, speed | Firmware |
| 3 | Fading | 3 default colors (purple, green, blue), speed | Firmware |
| 4 | Covering Marquee | Color list, speed, direction | Firmware |
| 5 | Temperature Monitor | Source (CPU / GPU / liquid), temperature range 15–90°C, custom gradient | Host |
| 6 | Marquee | Color, speed, direction, LED size | Firmware |
| 7 | Pulse | Color, speed | Firmware |
| 8 | Super Rainbow | Automatic colors, speed, direction | Firmware |
| 9 | Audio Bass | One color | Host (audio) |
| 10 | Audio Level | Custom gradient | Host (audio) |
| 11 | Rainbow Flow | Automatic colors, speed, direction | Firmware |
| 12 | Alternating | 2 colors, speed, direction, LED size (default 4), "moving" switch | Firmware |
| 13 | Tai Chi | 2 colors (default red, blue), speed, direction | Firmware |
| 14 | Starry Night | One color (default #51007A), speed | Firmware |
| 15 | Rainbow Pulse | Automatic colors, speed, direction | Firmware |
| 16 | Candle | One color (default #FFA500) | Firmware |
| 17 | Wave | Selected LEDs (set per LED), speed, direction | Host |
| 18 | Water Cooler | Automatic colors (blue), speed | Firmware |
| 19 | Loading | One color (default #51007A), speed | Firmware |

Note: the order of items 3–8 comes from a usage log and one or two may be swapped; names and options are confirmed.

Status: ✅ "Firmware" effects use firmware animations (mode values in `HW_MODES` in rgb.py) and play smoothly. Temperature Monitor, Wave and Audio Bass / Audio Level are still streamed from the host (at most 2.5 frames per second, so audio effects feel laggy). Audio effects need pyaudiowpatch to capture system audio and show a static color without it. Known issues: the Starry Night firmware animation has a whitish background; the firmware mode value for Wave is unknown.

## 6. Settings

| Feature | Status | Notes |
|---|---|---|
| General: start with Windows, language, temperature unit °C/°F | ✅ | Temperature unit supported; the console switches between Chinese and English; autostart via `install_autostart.ps1` (Task Scheduler, starts with highest privileges 10 s after login) |
| Mini mode: theme, color, opacity, size, orientation, always on top, which readings to show | ➖ | Desktop widget, unrelated to the cooler |
| Device firmware update (Kraken Plus 1.0.7) | ➖ | Open CAM temporarily when an update is needed |
| CAM profiles: each binds lighting and cooling presets and can switch per application | ⬜ | Could be done with multiple config files plus a switch command |
| Settings backup: export / import JSON | ✅ | This tool's settings are simply `config.json` |
| Support (submit a ticket), Beta | ➖ | |

## 7. Running alongside CAM

- While open, CAM sends its own cooling and lighting commands and fights with Fan Control and this tool (Fan Control logged `TryOpen failed (device busy or inaccessible)`); close CAM while using this tool.
- This tool opens the HID device in shared mode, so Fan Control keeps working (tested: fan duty kept changing while frames were being sent).
- The WinUSB interface used for the LCD is exclusive: only one program can push frames at a time.
- Run as administrator: `Stop-Service CAMService; Set-Service CAMService -StartupType Manual`. CAM sets the service back to "Automatic" every time it starts.
