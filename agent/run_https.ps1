# Quest 테스트용 실행: https://<이 PC의 IP>:8443
# 처음 한 번 인증서를 만들고, 서버를 같은 Wi-Fi에 연다.
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path "certs\cert.pem")) { & .\.venv\Scripts\python.exe tools\make_cert.py }
Write-Host "Quest 브라우저에서 위에 나온 https 주소를 여세요. (경고가 뜨면 '고급 → 계속')"
& .\.venv\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile certs\key.pem --ssl-certfile certs\cert.pem
