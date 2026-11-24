# 인수인계: 로봇 분해·재조립 XR 도우미 (다른 AI용)

> 이 문서는 다음에 이 저장소를 맡을 AI 에이전트를 위한 요약이다. 2026-11-22 기준.
> 저장소: `https://github.com/kimyejun0113/forearm-xr-assembly` (브랜치 `main`)
> 사용자에게는 **모든 글을 한국어로**, 쉬운 말로 쓴다 (커밋 메시지도 한국어로 "무엇이 고쳐졌는지" 위주).

## 1. 무엇을 만드는가
CG&I × KAIST 2026 XR·AI 윈터스쿨 해커톤 (11/21~22) 프로젝트.
설계자가 말로 알려 주던 조립 요령을 CAD 부품·단계에 묶어 두고, 담당자가 바뀌어도 **Meta Quest 3** 패스스루 위에서 실물 크기 3D CAD를 보며 혼자 분해·재조립하게 돕는다. 음성으로 묻고 답하는 AI 조수("자비스")가 붙어 있다.

로봇은 두 개:
- `forearm` (로봇팔 전완, 기본) — 원본 `cad/forearm.STEP`
- `leg_L` (왼다리, S08) — 설계자 HTML 조립 안내서 + 원본 CAD에서 `tools/guide_to_robot.py`로 만듦

## 2. 구조 한눈에
```
Quest 3 브라우저 ──https://<노트북 Wi-Fi IP>:8443/xr/xr.html──┐
노트북 브라우저 ──http://localhost:8000/xr/xr.html────────────┤
                                                              ▼
                     agent/serve.py (FastAPI 서버 하나, 포트 8000 + 8443)
                     ├─ /xr/*        → 저장소 루트 web/ (3D·XR 화면)
                     ├─ /            → agent/client/ (예전 2D 조수 화면)
                     ├─ /api/robots, /api/kb?robot=   단계·부품 지식
                     ├─ /api/session, /ws/{sid}       질문·답 (WebSocket)
                     ├─ /api/stt  (faster-whisper)    음성 인식
                     ├─ /api/tts  (Supertonic→Edge)   음성 답
                     ├─ /api/briefing                 설계자 설명 녹화 → 지식 합치기
                     └─ /api/aggregate, /api/archive  작업자 기록 모으기·보관함
```

| 경로 | 내용 |
|---|---|
| `web/xr.html` | **메인 화면** (약 1600줄, three.js + WebXR). 단계 조립 보기, 자유 분해, 충돌·볼트 막힘, 패널, 시선 클릭, 음성 루프(계속 듣다가 한 마디씩 `/api/stt`) |
| `web/assets/<robot>.glb / .assembly.json / .parts.json` | 3D 모델, 조립 순서·볼트·막힘 (assembly.json은 설계자가 고치는 파일) |
| `agent/serve.py` | 실행 진입점. 자체 서명 인증서 자동 생성(Wi-Fi IP 바뀌면 재생성), 8443은 허용 기기만 (`tools/quest_ip.txt`, `XR_ALLOW`, `XR_OPEN=1`) |
| `agent/server/main.py` | 라우트, 로봇별 `Knowledge` 로드, 단계 주의 문장 미리 만들기 |
| `agent/server/agent.py` | 질문 처리: 명령(이동·슬라이드·조용히·보관함·북마크) 정규식 → 검색 → LLM 답(JSON: speech/text/used/highlight/confident) |
| `agent/server/knowledge.py` | 지식 검색 |
| `agent/server/llm.py` | OpenAI 호환 클라이언트. `agent/.env`에 키 없으면 `available()==False`, 규칙·검색만으로 동작 |
| `agent/server/local_ai.py` | 키 없이 쓰는 것: faster-whisper STT, Supertonic/Edge TTS, Windows OCR, DuckDuckGo |
| `agent/server/ko_num.py` | 숫자를 한글로 읽기 (TTS용) |
| `agent/server/session.py`, `aggregate.py`, `archive.py` | 작업자별 기록(jsonl), 모아서 다음 작업자 지식으로 |
| `agent/ingest/*` | 설계자 자료 → 지식: 슬라이드 렌더·읽기, 녹음 전사, `build_kb.py`, `merge.py`(녹화 설명을 기존 지식에 LLM으로 골라 합침, 반대되는 말은 "확인 필요") |
| `agent/data/kb/kb.json` | forearm 지식 / `agent/data/kb/robots/leg_L.json` 왼다리 지식 (`*.speech.json`은 딸린 녹화 기록이라 로봇 목록에서 제외) |
| `tools/step_to_glb.py`, `plan_disassembly.py`, `add_bolts.py`, `guide_to_robot.py` | CAD → GLB, 충돌 계산으로 조립 순서, STEP 구멍으로 볼트 생성, 안내서 → 로봇 |
| `tools/connect_quest.ps1`, `quest_eval.py` | adb 연결(USB), 헤드셋 브라우저 상태 확인(디버그) |
| `agent/tests/*` | `simulate.py`(키 없이 작업자 2명 흉내), `eval_questions.py`(질문 정답 평가), `feature_check.py`, `local_ai_check.py` |

## 3. 실행
```powershell
# 처음 한 번 (저장소 루트)
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r agent\requirements.txt
copy agent\.env.example agent\.env   # 키 넣기 (없어도 동작)

# 서버 (실제 시연용)
powershell -ExecutionPolicy Bypass -File agent\run_https.ps1
```
- 노트북: `http://localhost:8000/xr/xr.html`
- Quest: 같은 Wi-Fi에서 `https://<노트북 IP>:8443/xr/xr.html` → 인증서 경고 "고급 → 계속". 막히면 서버 창에 찍힌 헤드셋 IP를 `tools/quest_ip.txt`에 적는다.
- 미리보기용 설정은 `.claude/launch.json` (`agent-test`는 8002 포트).
- 예전 `http.server` 실행법은 쓰지 않는다. 지금은 위 서버 하나로 3D와 AI가 같이 돈다.

`.gitignore`로 빠진 것 (저장소에 없음): `.venv/`, `agent/.env`, `agent/certs/`, `agent/data/sessions*/`, `agent/data/briefings/`, `agent/data/kb/aggregate.json`, `agent/data/kb/history/`, `tools/android/`(adb), `tools/quest_ip.txt`, `tts_samples/`.

## 4. 음성 파이프라인 (최근 작업의 중심)
- **STT**: 1.6초 이하 짧은 말(“다음”, “7단계”)은 Whisper `base`(약 0.9초), 질문은 `small`(약 2.7초, Core Ultra 5 125H). 동시에 하나만 돌린다(락). `STT_HINT`에 현장 단어. 쪼개진 질문은 클라이언트에서 합친다.
- **TTS 우선순위**: Gemini(`TTS_USE_GEMINI=1`일 때만, 무료는 느림) → OpenAI 음성(`OPENAI_API_KEY`) → **Supertonic**(노트북 신경망, 기본) → Edge TTS. 숫자는 한글로, 쉼표에서 나눠 앞 조각부터 재생.
- 들리는 말은 핵심 한 문장, 자세한 건 패널(`text`).
- WebSocket은 8초 핑으로 반쯤 끊긴 연결을 알아채고 다시 붙으며, 끊겨도 보낸 질문을 잃지 않게 되어 있다.
- Windows 버전 고정 주의: `ctranslate2==4.4.0`, `onnxruntime==1.19.2`, `av<16`, `setuptools<81` (최신판은 비정상 종료).

## 5. 답변 규칙 (agent.py의 SYSTEM 프롬프트 요점)
- 근거(단계 카드·슬라이드·설계자 말·이전 작업자 기록)만 쓴다. 없으면 "자료엔 없어요", `confident:false`.
- "이거/여기" = 현재 단계 부품. 개수·볼트·공구는 현재 단계 값으로.
- "N단계"라고 하면 그 단계로 답한다. "~ 쓰는 단계/언제 넣어"는 [전체 단계]를 보고 **번호와 이름을 모두** 말한다.
- 왼·오 질문: 근거에 왼·오 이야기가 없으면 추측해서 "구분 없다"고 하지 않는다 (`LR_Q`, `LR_EVID` 정규식).
- 질문이 단계 이동으로 잘못 처리되지 않도록 **이동 명령만 말했을 때만** 이동 (`NAV` 정규식은 전체 일치).

## 6. 최근 고친 것 (최신 순)
1. "카본 튜브 쓰는 단계가 뭐야?"에 두루뭉술하게 답하던 것 → 전체 단계 보고 번호로 답
2. 질문이 단계 이동으로 처리되던 것 → 이동만 말했을 때만 이동
3. 질문 오인식 → 질문은 Whisper small, 쪼개진 질문 합치기
4. 듣기만 하고 대답 안 하던 것 → 8초 핑 재연결 / 시선 커서가 패널에 가리던 것
5. THINKING에서 멈춘 듯하던 것 → STT 하나씩, 끊겨도 질문 유지
6. 말이 늦던 것 → Supertonic 스레드 6·품질 5, 조각 재생 / 패널이 로봇에 가리지 않게
전체는 `git log`에 한국어로 자세히 있다.

## 7. 남은 것·확인 필요
- forearm 조립 1~3단계(액추에이터 1, 클램프, 브래킷) 순서는 형상 계산이 방향을 못 찾아 강제로 정함.
- 볼트 규격·길이는 구멍 크기로 추정. Part19, Part21 등 CAD 이름 그대로인 부품의 실제 이름 미확인.
- 인터넷 검색은 붙어 있지만 규격 질문은 기본적으로 "자료에 없음"으로 답함.
- `PRIOR_HINTS`(단계 시작 때 이전 작업자 질문 띄우기)는 미정, 기본 끔.

## 8. 작업할 때 지킬 것
- 사용자는 한국어로 소통, 쉬운 말 선호. 커밋 메시지도 한국어로 "증상 → 고친 방법".
- 고친 뒤에는 실제로 확인: 질문 답변은 `agent/tests/eval_questions.py`, 전체 흐름은 `simulate.py`, 화면은 노트북 브라우저 `/xr/xr.html`(마우스로도 동작).
- 헤드셋 실측이 기준이다 (합성 음성 시험에선 괜찮아도 헤드셋 마이크에선 틀린 적이 있음).
- API 키·인증서·작업자 기록은 커밋하지 않는다.
