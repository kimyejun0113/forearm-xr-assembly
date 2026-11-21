# 로봇 분해·재조립 XR 도우미

CG&I × KAIST 2026 XR·AI 윈터스쿨 해커톤 프로젝트 (C 강의 × 3 Immersive).

설계자가 말로 알려 주던 조립 요령을 CAD 부품에 묶어 두고, 담당자가 바뀌어도 Meta Quest 3로 실물 크기 3D CAD를 보며 혼자 분해·재조립할 수 있게 돕는다.

## 지금 되는 것

- 로봇팔 전완(`cad/forearm.STEP`)을 Quest 3 패스스루 위에 실물 크기로 표시
- **순서대로 조립 보기**: 처음부터 한 단계씩 부품·볼트가 날아 들어옴. 단계마다 부품, 볼트 개수, 할 일, 주의 표시
- **자유 분해**: 부품을 손(핀치)이나 컨트롤러(트리거)로 잡아 정해진 방향으로 원하는 만큼 빼기
- **실제처럼**: CAD 형상 충돌 계산으로 막힌 부품, 볼트로 고정된 부품은 안 빠지고 "먼저 ○○를 빼야 해요" 안내
- **볼트**: CAD 볼트 구멍 위치로 볼트 19개 자동 생성
- 액추에이터는 모듈 하나로 통째로 움직임
- 양손 핀치로 확대·축소·이동·회전, 한 손 핀치로 돌리기, 그립 버튼으로 모델·패널 옮기기
- 패널 버튼은 화면 가운데 점을 1초 맞추면 눌림 (손이 바쁠 때)
- 기본 배치(시선 기준): 왼쪽 가상 로봇, 가운데 실제 로봇 자리, 오른쪽 패널

## 폴더

| 경로 | 내용 |
|---|---|
| `web/xr.html` | XR 화면 (PC 브라우저에서도 마우스로 동작) |
| `web/assets/forearm.glb` | STEP 에서 변환한 부품별 3D 모델 |
| `web/assets/forearm.assembly.json` | 조립 순서, 볼트, 막힘 정보 (**설계자가 고치는 파일**) |
| `web/assets/forearm.assembly.manual.json` | 순서 계산에 쓰는 힌트(묶음, 방향, 제목) |
| `tools/step_to_glb.py` | STEP → GLB 변환 |
| `tools/plan_disassembly.py` | 볼트 찾기 + 충돌 계산으로 조립 순서 만들기 (약 5분) |
| `tools/connect_quest.ps1` | 헤드셋 USB 연결 후 페이지 열기 |
| `tools/quest_eval.py` | 헤드셋 브라우저 상태를 노트북에서 확인 (디버그) |
| `cad/` | 원본 CAD |

## 실행

```bash
# 1) 파이썬 환경 (처음 한 번)
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

# 2) 웹 서버 (계속 켜 두기)
.venv/Scripts/python -m http.server 8000 --directory web --bind 127.0.0.1
```

- PC: 브라우저에서 http://localhost:8000/xr.html
- Quest 3: 개발자 모드 + USB 연결 후 아래 실행 → 헤드셋에서 START AR
  - adb: [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools)를 받아 `tools/android/platform-tools/` 에 풀기

```bash
powershell -ExecutionPolicy Bypass -File tools\connect_quest.ps1
```

## CAD 가 바뀌었을 때

```bash
.venv/Scripts/python tools/step_to_glb.py cad/forearm.STEP web/assets/forearm.glb
.venv/Scripts/python tools/plan_disassembly.py cad/forearm.STEP web/assets/forearm.assembly.manual.json web/assets/forearm.assembly.json
```

## 확인이 필요한 것

- 조립 1~3단계(액추에이터 1, 클램프, 브래킷) 순서: 형상 계산으로 빠지는 방향을 못 찾아 강제로 정함
- 볼트 규격·길이: 구멍 크기로 추정
- Part19, Part21 등 CAD 이름 그대로인 부품의 실제 이름
