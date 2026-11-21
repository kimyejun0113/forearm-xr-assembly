# Quest 3 연결: 포트를 잇고 헤드셋 브라우저에서 페이지를 연다. USB 선이 없어도 같은 Wi-Fi면 무선(adb over Wi-Fi)으로.
# 사용법:  powershell -ExecutionPolicy Bypass -File tools\connect_quest.ps1 [페이지]
#   처음 한 번(또는 헤드셋을 껐다 켠 뒤): USB로 꽂고 실행 → 무선으로 바꾸고 헤드셋 IP를 tools\quest_ip.txt 에 저장
#   그다음부터: 선을 빼고 실행해도 된다 (노트북과 헤드셋이 같은 Wi-Fi여야 함)
param([string]$Page = "xr/xr.html", [int]$Port = 8000)

$root = Split-Path $PSScriptRoot -Parent
$adb = Join-Path $PSScriptRoot "android\platform-tools\adb.exe"
$py = Join-Path $root ".venv\Scripts\python.exe"
$ipFile = Join-Path $PSScriptRoot "quest_ip.txt"
$url = "http://localhost:$Port/$Page"

# 1) 노트북 서버 확인
try { Invoke-WebRequest -Uri "http://localhost:$Port/api/kb" -UseBasicParsing -TimeoutSec 3 | Out-Null }
catch {
    Write-Host "노트북 서버가 꺼져 있어요. 다른 창에서 먼저 켜 주세요 (agent 폴더에서):"
    Write-Host "  ..\.venv\Scripts\python -m uvicorn server.main:app --host 127.0.0.1 --port $Port"
    exit 1
}

# 2) 헤드셋 찾기: USB가 꽂혀 있으면 무선 모드로 바꿔 두고, 무선으로 붙는다
& $adb start-server *> $null
$usb = & $adb devices | Select-String "^(\S+)\s+device$" | ForEach-Object { $_.Matches[0].Groups[1].Value } | Where-Object { $_ -notmatch ":" } | Select-Object -First 1
if ($usb) {
    $ip = (& $adb -s $usb shell "ip -4 addr show wlan0" | Select-String "inet (\d+\.\d+\.\d+\.\d+)").Matches.Groups[1].Value
    if ($ip) {
        Set-Content -Path $ipFile -Value $ip -Encoding ascii
        & $adb -s $usb tcpip 5555 | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "무선 모드 켬. 헤드셋 IP $ip (이제 선을 빼도 돼요)"
    }
}
$serial = $null
if (Test-Path $ipFile) {
    $ip = (Get-Content $ipFile -Raw).Trim()
    $r = & $adb connect "${ip}:5555" 2>&1 | Out-String
    if ($r -match "connected") { $serial = "${ip}:5555" }
}
if (-not $serial -and $usb) { $serial = $usb; Write-Host "무선 연결이 안 돼서 USB로 이어요 (노트북과 헤드셋이 같은 Wi-Fi인지 확인)" }
if (-not $serial) {
    Write-Host "헤드셋을 찾지 못했어요."
    Write-Host " - 노트북과 헤드셋이 같은 Wi-Fi인지 확인하세요."
    Write-Host " - 헤드셋을 껐다 켰다면 무선 모드가 풀려요: USB로 한 번 꽂고 다시 실행하세요."
    & $adb devices
    exit 1
}
& $adb -s $serial reverse "tcp:$Port" "tcp:$Port" | Out-Null
& $adb -s $serial forward tcp:9222 localabstract:chrome_devtools_remote | Out-Null
Write-Host "연결: $serial $(if ($serial -match ':') {'(무선)'} else {'(USB)'})"

# 3) 이미 열린 탭이 있으면 새로고침, 없으면 새로 열기
$env:PYTHONIOENCODING = "utf-8"
$reused = $false
if (Test-Path $py) {
    $out = & $py (Join-Path $PSScriptRoot "quest_eval.py") "location.href='$url'; 'ok'" $Page 2>$null
    if ($LASTEXITCODE -eq 0 -and $out -match "ok") { $reused = $true }
}
if (-not $reused) {
    & $adb -s $serial shell am start -a android.intent.action.VIEW -d $url com.oculus.browser | Out-Null
}
Write-Host "헤드셋에서 $url 를 열었어요. $(if ($reused) {'(기존 탭 새로고침)'} else {'(새 탭)'})"
