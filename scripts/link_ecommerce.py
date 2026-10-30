"""Link the e-commerce platform to AEGIS.

Creates a SENTINEL service token bound to agent `shop-assistant` and tool namespace `shop`, and a
random internal token AEGIS uses to start test scenarios in the shop backend. Writes both into the
git-ignored .env files (mode 0600). Tokens are never printed.

Run from the repository root after `make init`:  make link-ecommerce
Restart both backends afterwards.
"""
from __future__ import annotations

import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from envfile import ensure, upsert  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SHOP = ROOT / "ecommerce-agent-platform"


def main() -> int:
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    ensure(ROOT / ".env", ROOT / ".env.example")
    ensure(SHOP / ".env", SHOP / ".env.example")
    name = "shop-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    r = subprocess.run([str(py), "-m", "app.cli", "create-service-token", name, "--agents", "shop-assistant",
                        "--namespaces", "shop", "--quiet"], cwd=ROOT / "backend", capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("Could not create the service token. Did you run `make init` first?\n" + r.stderr[-2000:])
        return 1
    svc = r.stdout.strip().splitlines()[-1].strip()
    if not svc.startswith("aegis_svc_"):
        sys.stderr.write("Unexpected output from create-service-token; nothing was written.\n")
        return 1
    internal = secrets.token_urlsafe(32)
    upsert(ROOT / ".env", {"AEGIS_ECOMMERCE_TOKEN": internal})
    upsert(SHOP / ".env", {"AEGIS_API_TOKEN": svc, "SHOP_INTERNAL_TOKEN": internal})
    print(f"Linked. Service token '{name}' written to ecommerce-agent-platform/.env; internal token written to both .env files.")
    print("Values were not printed. Restart both backends to load them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
