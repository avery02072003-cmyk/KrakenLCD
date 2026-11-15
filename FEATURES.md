# NZXT CAM 功能清單（Kraken 2024 Plus）與本程式對照

擷取來源：NZXT CAM 4.76.9 實機操作畫面、CAM 匯出設定檔（`reference/cam_settings_backup.json`）、
CAM app.asar 內的前端程式碼。擷取日期 2026-11-14～15。

狀態欄：✅ 已實作｜🟡 部分／外觀尚未與原廠一致｜⬜ 未實作｜➖ 不做（交給 Fan Control 或與硬體無關）

---

## 1. 硬體

| 項目 | 內容 |
|---|---|
| 水冷頭 | Kraken 2024 Plus（USB 1E71:3014），韌體 1.0.7 |
| LCD | 240×240 圓形，RGB565，CAM 設定旋轉 -90° |
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
| 圖片／GIF | Image / GIF | 檔案；GIF 循環播放 | ✅ |
| 單一資訊圖 | InfographicLCD：`radial-fill-v2`、`InfographicFullFill`、`InfographicRadialFill` | 讀值 1 個、讀值文字顏色、標籤顏色、視覺化雙色漸層、環形漸層（綠 0%→黃 50%→紅 100%）、背景色、NZXT logo 開關與顏色 | ✅ radial-fill-v2 依 CAM 渲染核心原生 240×240 預覽逐像素重建（平均差異 1.5／255）；full-fill 依原廠著色器（整面水平漸層）重建，文字位置推測；radial-fill 在 Kraken Plus 上 CAM 會自動換成預設版面，故同 v2 |
| 雙資訊圖 | DualInfographic：`dual-infographic-base`（長條）、弧形 | 讀值 2 個、兩色（6B00DE / D600BF；CAM 的第三色「底條」不會傳給渲染核心，所以不提供）、背景 | ✅ 長條依原生預覽逐像素重建（平均差異 2.3／255，底條固定 #898989）；弧形（左右兩道）無原廠參考，推測 |
| 單一資訊圖＋GIF 背景 | GifInfographic / GifDualInfographic | 同上＋GIF 檔 | ✅ |
| 時鐘 | Clockface：`analog`、`digital`、`digital_gradient` | 類比：背景、錶盤、秒針色（預設 8A00FC）；數位：錶盤、文字、漸層（4FC5FA→8221FF）、12/24 小時；漸層數位：漸層（00ffd3→0d00ff）、12/24 小時 | ✅ 漸層數位依原生預覽重建（Oklab 由上到下，平均差異 1.1／255）；類比指針取自 CAM 內嵌 SVG、比例依舊截圖校正；數位模式 dial／gradient 欄位用途不明，目前只用文字色 |
| 網頁整合 | WebIntegration | 網址（CAM 用離屏瀏覽器渲染） | ⬜ |
| 輪播 | Carousel | 多個畫面、每畫面停留時間（使用者目前 5000 ms） | ✅ |

可選讀值（`monitoringKey`）：`LiquidTemp`、`CPUTemp`、`CPULoad`、`CPUFrequency`、`GPUTemp`、`GPULoad`、`GPUFrequency`。

調色盤：20 個預設色塊＋自訂 HEX。原廠字型為 **Gotham**（CAM asar 內的 `GothamSSm-Bold_Web.woff`），logo 字型 `NZXT-Font.ttf`。

### 使用者目前的輪播設定（要能重現）

| # | 畫面 | 內容 |
|---|---|---|
| 1 | 單一資訊圖 `radial-fill-v2` | LiquidTemp，色 6B00DE / D600BF，環形漸層 00ff00 / ffff00 / ff0000，白色 logo |
| 2 | 雙資訊圖 `dual-infographic-base` | CPUTemp / GPUTemp，色 6B00DE / D600BF / 818181 |
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
| 一般：開機啟動、語言、溫度單位 °C/°F | ✅ | 溫度單位已支援；開機啟動用 `install_autostart.ps1`（工作排程器，登入後 10 秒以最高權限啟動） |
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
