# install_autostart.ps1：建立「登入時自動執行 Kraken LCD 控制程式」的工作排程
#
# 用法：
#   一般 PowerShell 執行   → 以一般權限執行（CPU 溫度讀不到，改顯示 CPU 使用率）
#   系統管理員 PowerShell → 以最高權限執行（可讀 CPU 溫度與即時時脈，建議）
#   powershell -ExecutionPolicy Bypass -File H:\KrakenLCD\kraken_lcd\install_autostart.ps1
#   加 -Remove 參數則移除排程。
param([switch]$Remove)

$TaskName = 'KrakenLCD'                                   # 工作排程名稱
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path   # 本腳本所在資料夾（程式資料夾）

# 移除模式：刪掉排程後結束
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已移除工作排程 $TaskName"
    return
}

# 找 pythonw.exe（無主控台視窗的 Python），與目前 PATH 上的 python 同一套
$py = (Get-Command python -ErrorAction Stop).Source
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { throw "找不到 pythonw.exe：$pyw" }

# 判斷是否為系統管理員，決定執行權限等級
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$level = if ($isAdmin) { 'Highest' } else { 'Limited' }

# 動作：pythonw kraken_lcd.py run（工作目錄設為程式資料夾）
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$Here\kraken_lcd.py`" run" -WorkingDirectory $Here
# 觸發：目前使用者登入後 10 秒（等 USB 裝置與 Fan Control 就緒）
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT10S'
# 執行身分：目前使用者、互動式登入
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel $level
# 設定：筆電用電池也執行、不限執行時間、失敗時每分鐘重試（最多 3 次）
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'NZXT Kraken LCD＋RGB 控制（取代 NZXT CAM，與 Fan Control 共存）' -Force | Out-Null

Write-Host "已建立工作排程 $TaskName（權限：$level），下次登入自動執行。"
if (-not $isAdmin) {
    Write-Host '提示：以系統管理員身分重新執行本腳本，可改成最高權限（讀得到 CPU 溫度與即時時脈）。'
}
