"""Choose the shopping agent's model and store any Ollama API key in ecommerce-agent-platform/.env.

The key is read with a hidden prompt (never echoed, never passed on the command line, never printed)
and written only to the git-ignored .env file with mode 0600.

  python scripts/configure_model.py local  qwen3:8b         # local Ollama, no key
  python scripts/configure_model.py cloud  gpt-oss:120b     # https://ollama.com, prompts for the key
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLATFORM.parent / "scripts"))
from envfile import ensure, upsert  # noqa: E402


def main(argv: list) -> int:
    if len(argv) != 2 or argv[0] not in ("local", "cloud"):
        print(__doc__)
        return 2
    mode, model = argv
    env = PLATFORM / ".env"
    ensure(env, PLATFORM / ".env.example")
    if mode == "local":
        upsert(env, {"SHOP_MODEL_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://127.0.0.1:11434", "OLLAMA_MODEL": model, "OLLAMA_API_KEY": ""})
        print(f"Using local Ollama model {model}. Run: ollama pull {model}")
        return 0
    key = getpass.getpass("Ollama API key (input hidden, Enter keeps the current one): ").strip()
    values = {"SHOP_MODEL_PROVIDER": "ollama", "OLLAMA_BASE_URL": "https://ollama.com", "OLLAMA_MODEL": model}
    if key:
        values["OLLAMA_API_KEY"] = key
    upsert(env, values)
    print(f"Using Ollama cloud model {model}. Key {'updated' if key else 'unchanged'} in .env (not shown).")
    print("Cloud requests count against your Ollama plan. Check: make -C ecommerce-agent-platform check-model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
