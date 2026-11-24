# 포어암 조립 도우미 (에이전트)
> 전체 실행법은 저장소 루트 [README.md](../README.md)를 따른다 (아래 uvicorn 실행은 초기 방식).

설계자의 PPT와 설명을 안경으로 기록해 정리하고, 작업자가 조립하다 물으면 음성과 화면으로 답한다. 작업자마다 기록이 쌓여 다음 작업자에게 전달된다.

## 실행
```powershell
.venv\Scripts\python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```
Quest 3 브라우저에서 `http://<PC의 IP>:8000` 을 연다. 마이크는 https에서만 열리므로, Quest에서 음성을 쓰려면 https 터널(예: ngrok)이나 인증서가 필요하다. 입력창은 http에서도 된다.

## 자료 넣기 (설계자 설명 → 지식)
| 순서 | 명령 | 하는 일 |
|---|---|---|
| 1 | `python ingest/render_slides.py data/source/deck data/source/assets` | 슬라이드를 안경 카메라로 찍은 것 같은 이미지로 (`data/captures`). 실제 안경 사진이 있으면 이 단계 대신 그 사진을 `data/captures`에 넣는다 |
| 2 | `python ingest/read_slides.py` | 비전 AI가 사진을 읽어 `data/kb/slides_read.json` (키 필요. 지금 파일은 Claude가 사진을 보고 같은 형식으로 만든 것) |
| 3 | `python ingest/transcribe.py <녹음> [넘김기록.json]` | 설계자 말 → `data/source/speech.json` (키 필요. 지금 파일은 발표자 노트를 말로 본 대역) |
| 4 | `python ingest/build_kb.py` | 슬라이드·말·단계를 묶어 `data/kb/kb.json` (단계 카드, 예외 표시, 기준 시간, 지식 조각) |

`data/source/steps.json` 은 3D 뷰어와 같이 쓰는 단계 목록이다. 3D 쪽 단계가 정해지면 이 파일을 그 순서로 고친 뒤 4번을 다시 돌린다.

## 3D 뷰어 연결 (팀원 모듈)
화면(`client/app.js`)은 `window.viewer3d` 가 있으면 그걸 쓰고, 없으면 임시 화면을 쓴다.
```js
window.viewer3d = {
  goto(stepId) {},        // 단계로 이동 (음성 "다음/이전/N단계"도 여기로 온다)
  highlight(partIds) {},  // 답변에 나온 부품 강조. id는 steps.json의 parts 키
  onStep(cb) {}           // 3D에서 단계가 바뀌면 cb(stepId, "voice"|"ui") 호출
};
```

## 기록과 쌓인 지식
- 작업자별 기록: `data/sessions/<작업자>/<세션>.jsonl` (단계 들어감·나감, 질문과 답, 알림, 표시한 장면, 마지막 한마디)
- 모아서 정리: `POST /api/aggregate` → `data/kb/aggregate.json` (단계 시간 중간값, 자주 묻는 질문, 어려움 점수, 팁)

## 시험
`python tests/simulate.py` — 작업자 2명을 흉내 내서 질문·알림·기록·전달까지 확인한다 (키 없이 동작).

## 키가 없을 때와 있을 때
| | 키 없음 (지금) | 키 있음 |
|---|---|---|
| 답변 | 검색으로 찾은 근거를 그대로 읽어 줌 | 근거만 써서 두 문장으로 생성 |
| 음성 인식 | 브라우저 음성 인식 (PC 크롬) | 서버 Whisper (Quest 포함) |
| 음성 답 | 브라우저 speechSynthesis | 서버 TTS |
| 슬라이드 읽기 | 미리 만든 slides_read.json | 비전 AI가 사진을 읽음 |
| 인터넷 검색 | 없음 | 아직 연결 안 함 (규격 질문은 "자료에 없음"으로 답함) |
