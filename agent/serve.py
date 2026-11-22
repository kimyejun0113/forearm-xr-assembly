"""서버 하나로 두 군데를 연다 (지식·세션·설명 녹화 처리를 같이 쓴다).

  http://127.0.0.1:8000          노트북 화면, adb 터널(USB)용
  https://<노트북 Wi-Fi IP>:8443  헤드셋이 와이파이로 바로 붙는 곳.
                                  무선 adb는 자꾸 끊겨서(네트워크는 멀쩡해도) 녹화 업로드·AI 연결이 같이 끊겼다.
                                  카메라·마이크는 https에서만 열리므로 자체 서명 인증서를 쓴다 (헤드셋에서 처음 한 번 '고급 → 계속').

8443은 같은 Wi-Fi의 아무나 API 키를 쓰지 못하게 허용한 기기만 받는다:
  이 노트북, tools/quest_ip.txt 에 적힌 헤드셋 IP, 환경 변수 XR_ALLOW="ip1,ip2". XR_OPEN=1 이면 모두 허용.
  막히면 서버 창에 그 기기 IP가 찍힌다.

실행 (agent 폴더):  ..\\.venv\\Scripts\\python serve.py
"""
import asyncio, ipaddress, os, sys
from pathlib import Path
from cryptography import x509

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from tools import make_cert  # noqa: E402

CERT, KEY = ROOT / "certs/cert.pem", ROOT / "certs/key.pem"
QUEST_IP = ROOT.parent / "tools/quest_ip.txt"


def ensure_cert():
    """인증서가 없거나 지금 Wi-Fi IP가 안 들어 있으면 새로 만든다 (장소가 바뀌면 IP도 바뀐다)."""
    ips = [i for i in make_cert.local_ips() if i != "127.0.0.1"]
    if CERT.exists() and KEY.exists():
        cert = x509.load_pem_x509_certificate(CERT.read_bytes())
        have = {str(a) for a in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)}
        if set(ips) <= have:
            return ips, False
    make_cert.main()
    return ips, True


def allowed():
    ok = {"127.0.0.1", "::1"} | {i for i in make_cert.local_ips()}
    if QUEST_IP.exists():
        ok |= {x.strip() for x in QUEST_IP.read_text(encoding="utf-8").split() if x.strip()}
    ok |= {x.strip() for x in os.environ.get("XR_ALLOW", "").split(",") if x.strip()}
    return ok


class Guard:
    """https 쪽 문지기: 허용한 기기만 통과 (http·websocket 둘 다)"""
    def __init__(self, app):
        self.app, self.warned = app, set()

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and os.environ.get("XR_OPEN") != "1":
            ip = (scope.get("client") or ("?",))[0]
            if ip not in allowed():
                if ip not in self.warned:
                    self.warned.add(ip)
                    print(f"[8443] 허용 안 된 기기 {ip} 를 막았어요. 헤드셋이면 tools/quest_ip.txt 에 이 IP를 적으세요.", flush=True)
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = f"허용 안 된 기기예요 ({ip}). 노트북의 tools/quest_ip.txt 에 이 IP를 적으면 열려요.".encode("utf-8")
                await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


async def main():
    import uvicorn
    from server.main import app
    ips, made = ensure_cert()
    # 헤드셋이 잠깐 응답을 못 해도(와이파이 절전·CPU가 바쁠 때) 바로 끊지 않게: 기본 20초 → 60초 기다림
    ws = dict(ws_ping_interval=20, ws_ping_timeout=60)
    plain = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info", **ws))
    secure = uvicorn.Server(uvicorn.Config(Guard(app), host="0.0.0.0", port=8443, log_level="info", **ws,
                                           ssl_certfile=str(CERT), ssl_keyfile=str(KEY), lifespan="off"))
    print("노트북: http://localhost:8000")
    for i in ips:
        if ipaddress.ip_address(i).is_private:
            print(f"헤드셋 브라우저에서 열 주소 → https://{i}:8443/xr/xr.html" + ("  (인증서를 새로 만들었어요: 헤드셋에서 '고급 → 계속' 다시)" if made else ""))
    print("허용한 기기:", ", ".join(sorted(allowed())), flush=True)
    await asyncio.gather(plain.serve(), secure.serve())


if __name__ == "__main__":
    os.chdir(ROOT)
    asyncio.run(main())
