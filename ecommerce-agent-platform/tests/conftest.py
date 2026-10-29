"""End-to-end harness: real AEGIS backend + real e-commerce backend + an Ollama-protocol TEST DOUBLE,
each in its own server process, linked with real service tokens exactly as `make link-ecommerce` does.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import httpx
import pytest

TESTS = Path(__file__).resolve().parent
PLATFORM = TESTS.parent
REPO = PLATFORM.parent
AEGIS_BACKEND = REPO / "backend"
SHOP_BACKEND = PLATFORM / "backend"
PY = sys.executable
ADMIN_PW = "aegis-admin-password-1"
OPERATOR_PW = "aegis-operator-password-1"
SHOP_PW = "shop-demo-password"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(cmd, cwd, env) -> str:
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"{cmd} failed:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def wait(url: str, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up")


@dataclass
class Stack:
    aegis: str
    shop: str
    double: str
    tmp: Path
    envs: Dict[str, Dict[str, str]]
    procs: Dict[str, subprocess.Popen]

    def aegis_token(self, role: str = "operator") -> str:
        user, pw = ("admin", ADMIN_PW) if role == "admin" else ("operator", OPERATOR_PW)
        return httpx.post(f"{self.aegis}/api/v1/auth/login", json={"username": user, "password": pw}).json()["token"]

    def shop_token(self, email: str = "asha@shopper.test") -> str:
        r = httpx.post(f"{self.shop}/api/auth/login", json={"email": email, "password": SHOP_PW})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def start(self, name: str) -> None:
        cfg = {"aegis": (AEGIS_BACKEND, "app.main:app", self.aegis), "shop": (SHOP_BACKEND, "shop.main:app", self.shop),
               "double": (TESTS, "doubles.ollama_double:app", self.double)}[name]
        port = cfg[2].rsplit(":", 1)[1]
        log = open(self.tmp / f"{name}.log", "a")
        self.procs[name] = subprocess.Popen([PY, "-m", "uvicorn", cfg[1], "--host", "127.0.0.1", "--port", port, "--log-level", "warning"],
                                            cwd=cfg[0], env=self.envs[name], stdout=log, stderr=log)
        wait(f"{cfg[2]}/" + {"aegis": "api/v1/health", "shop": "api/health", "double": "api/tags"}[name])

    def stop(self, name: str) -> None:
        p = self.procs.pop(name, None)
        if p:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:  # open WebSockets can delay a graceful shutdown
                p.kill()
                p.wait(5)


@pytest.fixture(scope="session")
def stack():
    tmp = Path(tempfile.mkdtemp(prefix="shop-e2e-"))
    ports = {k: free_port() for k in ("aegis", "shop", "double")}
    urls = {k: f"http://127.0.0.1:{v}" for k, v in ports.items()}
    internal = secrets.token_urlsafe(24)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("AEGIS_", "SHOP_", "OLLAMA_"))}
    aegis_env = {**base, "AEGIS_DATA_DIR": str(tmp / "aegis"), "AEGIS_ADMIN_PASSWORD": ADMIN_PW,
                 "AEGIS_ECOMMERCE_BASE_URL": urls["shop"], "AEGIS_ECOMMERCE_TOKEN": internal,
                 "AEGIS_CORS_ORIGINS": "http://localhost:5173"}
    run([PY, "-m", "app.cli", "init"], AEGIS_BACKEND, aegis_env)
    run([PY, "-m", "app.cli", "create-user", "operator", "operator"], AEGIS_BACKEND, {**aegis_env, "AEGIS_NEW_USER_PASSWORD": OPERATOR_PW})
    svc = run([PY, "-m", "app.cli", "create-service-token", "shop", "--agents", "shop-assistant", "--namespaces", "shop", "--quiet"],
              AEGIS_BACKEND, aegis_env).strip()
    shop_env = {**base, "SHOP_DATA_DIR": str(tmp / "shop"), "SHOP_DEMO_PASSWORD": SHOP_PW, "AEGIS_BASE_URL": urls["aegis"],
                "AEGIS_API_TOKEN": svc, "SHOP_INTERNAL_TOKEN": internal, "OLLAMA_BASE_URL": urls["double"],
                "OLLAMA_MODEL": "test-double", "SHOP_MODEL_TIMEOUT_SECONDS": "10"}
    run([PY, "-m", "shop.cli", "init"], SHOP_BACKEND, shop_env)
    st = Stack(aegis=urls["aegis"], shop=urls["shop"], double=urls["double"], tmp=tmp,
               envs={"aegis": aegis_env, "shop": shop_env, "double": base}, procs={})
    for name in ("double", "aegis", "shop"):
        st.start(name)
    # wait until the shop has registered its tools with SENTINEL
    deadline = time.time() + 30
    while time.time() < deadline and httpx.get(f"{st.shop}/api/health").json()["sentinel_registration"] != "ok":
        time.sleep(0.3)
    yield st
    for name in list(st.procs):
        st.stop(name)
