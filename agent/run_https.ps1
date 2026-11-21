# 서버 실행: 노트북 http://localhost:8000 + 헤드셋 https://<노트북 Wi-Fi IP>:8443 (서버 하나, 지식·세션 공유)
# 인증서는 serve.py가 알아서 만든다 (Wi-Fi IP가 바뀌면 다시 만듦 → 헤드셋에서 '고급 → 계속' 한 번 더).
# 8443은 이 노트북과 tools\quest_ip.txt 의 헤드셋 IP만 받는다.
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
& ..\.venv\Scripts\python.exe serve.py
