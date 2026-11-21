# Quest 3 를 USB 로 연결한 뒤 실행: 포트를 잇고 헤드셋 브라우저에서 페이지를 연다.
# 사용법:  powershell -ExecutionPolicy Bypass -File tools\connect_quest.ps1 [페이지]
#   예)    powershell -ExecutionPolicy Bypass -File tools\connect_quest.ps1 xr/xr.html
param([string]$Page = "xr/xr.html", [int]$Port = 8000)

$root = Split-Path $PSScriptRoot -Parent
$adb = Join-Path $PSScriptRoot "android\platform-tools\adb.exe"
$py = Join-Path $root ".venv\Scripts\python.exe"
$url = "http://localhost:$Port/$Page"

# 1) 노트북 서버 확인
try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 | Out-Null }
catch {
    Write-Host "노트북 서버가 꺼져 있어요. 다른 창에서 먼저 켜 주세요:"
    Write-Host "  .venv\Scripts\python -m http.server $Port --directory web --bind 127.0.0.1"
    exit 1
}

# 2) 헤드셋 확인
& $adb start-server *> $null
$devices = & $adb devices | Select-String "`tdevice$"
if (-not $devices) {
    Write-Host "헤드셋을 찾지 못했어요. USB 케이블을 확인하고, 헤드셋 안의 'USB 디버깅 허용' 창을 허용하세요."
    & $adb devices
    exit 1
}
& $adb reverse "tcp:$Port" "tcp:$Port" | Out-Null
& $adb forward tcp:9222 localabstract:chrome_devtools_remote | Out-Null

# 3) 이미 열린 탭이 있으면 새로고침, 없으면 새로 열기
$env:PYTHONIOENCODING = "utf-8"
$reused = $false
if (Test-Path $py) {
    $out = & $py (Join-Path $PSScriptRoot "quest_eval.py") "location.href='$url'; 'ok'" $Page 2>$null
    if ($LASTEXITCODE -eq 0 -and $out -match "ok") { $reused = $true }
}
if (-not $reused) {
    & $adb shell am start -a android.intent.action.VIEW -d $url com.oculus.browser | Out-Null
}
Write-Host "헤드셋에서 $url 를 열었어요. $(if ($reused) {'(기존 탭 새로고침)'} else {'(새 탭)'})"
