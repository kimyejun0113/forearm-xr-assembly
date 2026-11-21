# PC에서 개발·시험용 실행: http://localhost:8000 (카메라·마이크는 localhost에서만 열림)
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 8000
