"""Harness for connection-center integration tests.

Real components, no mocks:
- a test certificate authority and a TLS certificate for the fixture host names;
- a DNS server (dnslib, UDP on 127.0.0.1) that answers A and TXT queries from a mutable table;
- the fixture agents (tests/connect/fixture_agents.py) as an HTTPS server in its own process;
- the AEGIS backend as a separate uvicorn process with its own fresh database.

AEGIS is told to use that DNS server and to trust that CA. Fixture names that resolve to 127.0.0.1 are
listed explicitly in AEGIS_CONNECT_ALLOWED_PRIVATE_HOSTS; other names (internal.test, meta.test, ...) are not,
so the SSRF defences are exercised for real.
"""
from __future__ import annotations

import datetime as dt
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
FIXTURE_HOSTS = ["agent.test", "a2a.agent.test", "mcp.agent.test", "api.agent.test", "web.agent.test", "cross.agent.test",
                 "redir.agent.test", "big.agent.test", "oauth.agent.test"]
A_RECORDS: Dict[str, List[str]] = {h: ["127.0.0.1"] for h in FIXTURE_HOSTS}
A_RECORDS.update({"internal.test": ["127.0.0.1"], "meta.test": ["169.254.169.254"], "ten.test": ["10.0.0.8"],
                  "mixed.test": ["93.184.216.34", "192.168.1.20"], "elsewhere.test": ["127.0.0.1"]})
TXT_RECORDS: Dict[str, List[str]] = {}
ADMIN = ("connadmin", "conn-admin-password-1")
BEARER_TOKEN = "fixture-scoped-token-7f3a91c2"  # matches fixture_agents.BEARER
OAUTH_CLIENT = ("aegis-test-client", "fixture-client-secret-55aa")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_certs(d: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AEGIS test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
          .not_valid_after(now + dt.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    names = FIXTURE_HOSTS + ["internal.test", "elsewhere.test"]
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent.test")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    (d / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (d / "cert.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (d / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))


def start_dns(port: int):
    from dnslib import A, QTYPE, RCODE, RR, TXT
    from dnslib.server import BaseResolver, DNSLogger, DNSServer

    class Resolver(BaseResolver):
        def resolve(self, request, handler):
            reply = request.reply()
            name = str(request.q.qname).rstrip(".").lower()
            qt = QTYPE[request.q.qtype]
            known = name in A_RECORDS or name in TXT_RECORDS
            if qt == "A":
                for ip in A_RECORDS.get(name, []):
                    reply.add_answer(RR(request.q.qname, QTYPE.A, rdata=A(ip), ttl=1))
            elif qt == "TXT":
                for v in TXT_RECORDS.get(name, []):
                    reply.add_answer(RR(request.q.qname, QTYPE.TXT, rdata=TXT(v), ttl=1))
            if not known:
                reply.header.rcode = RCODE.NXDOMAIN
            return reply

    srv = DNSServer(Resolver(), port=port, address="127.0.0.1", logger=DNSLogger("-request,-reply,-truncated,-error,-recv,-send,-data"))
    srv.start_thread()
    return srv


def wait_http(url: str, verify=True, timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            httpx.get(url, verify=verify, trust_env=False, timeout=2)
            return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not start")


class Env:
    def __init__(self, aegis: str, fixture_port: int, ca: str, data_dir: Path) -> None:
        self.aegis = aegis
        self.fport = fixture_port
        self.ca = ca
        self.data_dir = data_dir
        self.http = httpx.Client(base_url=aegis + "/api/v1", trust_env=False, timeout=60)
        self.tokens: Dict[str, str] = {}

    def url(self, host: str, path: str = "/") -> str:
        return f"https://{host}:{self.fport}{path}"

    def fixture(self, host: str = "agent.test") -> httpx.Client:
        # The test process reaches fixture hosts through 127.0.0.1 with the right Host/SNI.
        return httpx.Client(base_url=f"https://127.0.0.1:{self.fport}", verify=False, trust_env=False, timeout=30,
                            headers={"Host": f"{host}:{self.fport}"})

    def login(self, user: str, password: str) -> Dict[str, str]:
        if user not in self.tokens:
            r = self.http.post("/auth/login", json={"username": user, "password": password})
            r.raise_for_status()
            self.tokens[user] = r.json()["token"]
        return {"Authorization": f"Bearer {self.tokens[user]}"}


@pytest.fixture(scope="session")
def cenv():
    tmp = Path(tempfile.mkdtemp(prefix="aegis-connect-"))
    make_certs(tmp)
    dns_port, fport, aport = free_port(), free_port(), free_port()
    dns = start_dns(dns_port)
    fixture = subprocess.Popen([PY, str(ROOT / "tests/connect/fixture_agents.py"), "--port", str(fport),
                                "--cert", str(tmp / "cert.pem"), "--key", str(tmp / "key.pem")], cwd=str(ROOT))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AEGIS_", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"))}
    env.update({
        "AEGIS_DATA_DIR": str(tmp / "data"), "AEGIS_ADMIN_USERNAME": ADMIN[0], "AEGIS_ADMIN_PASSWORD": ADMIN[1],
        "AEGIS_CONNECT_DNS_SERVERS": f"127.0.0.1:{dns_port}", "AEGIS_CONNECT_CA_BUNDLE": str(tmp / "ca.pem"),
        "AEGIS_CONNECT_ALLOWED_PRIVATE_HOSTS": ",".join(FIXTURE_HOSTS), "OLLAMA_BASE_URL": "http://127.0.0.1:9",
        "AEGIS_CONNECT_TIMEOUT_SECONDS": "5", "AEGIS_CONNECT_AGENT_TIMEOUT_SECONDS": "20", "AEGIS_ECOMMERCE_TOKEN": "",
        "AEGIS_LOG_LEVEL": "WARNING",
    })
    subprocess.run([PY, "-m", "app.cli", "init"], cwd=str(ROOT / "backend"), env=env, check=True, capture_output=True)
    aegis = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(aport)],
                             cwd=str(ROOT / "backend"), env=env)
    try:
        wait_http(f"http://127.0.0.1:{aport}/api/v1/health")
        wait_http(f"https://127.0.0.1:{fport}/__fixture/state", verify=False)
        e = Env(f"http://127.0.0.1:{aport}", fport, str(tmp / "ca.pem"), tmp / "data")
        admin = e.login(*ADMIN)
        for u in ("dev1", "dev2"):
            e.http.post("/users", json={"username": u, "password": f"{u}-password-123", "role": "operator"}, headers=admin).raise_for_status()
        e.http.post("/users", json={"username": "watcher", "password": "watcher-password-123", "role": "viewer"},
                    headers=admin).raise_for_status()
        yield e
    finally:
        aegis.terminate()
        fixture.terminate()
        dns.stop()
        aegis.wait(10)
        fixture.wait(10)
