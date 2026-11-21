"""Quest 브라우저에서 카메라·마이크를 쓰려면 https가 필요하다. 같은 Wi-Fi용 자체 서명 인증서를 만든다.

사용: .venv\\Scripts\\python tools\\make_cert.py   → certs/cert.pem, certs/key.pem
이 PC의 사설 IP들과 localhost를 인증서에 넣는다. Quest에서는 처음 접속할 때 '안전하지 않음 → 계속'을 한 번 누르면 된다.
"""
import datetime, ipaddress, socket
from pathlib import Path
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]


def local_ips():
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # 기본 경로로 나가는 IP (Wi-Fi)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ips.add(s.getsockname()[0]); s.close()
    except OSError:
        pass
    return sorted(ips)


def main():
    out = ROOT / "certs"; out.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forearm-agent.local")])
    ips = local_ips()
    san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=60))
            .add_extension(x509.SubjectAlternativeName(san), critical=False).sign(key, hashes.SHA256()))
    (out / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                    serialization.NoEncryption()))
    (out / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print("인증서 생성:", out)
    for i in ips:
        if i != "127.0.0.1":
            print(f"  Quest에서 열 주소 → https://{i}:8443")


if __name__ == "__main__":
    main()
