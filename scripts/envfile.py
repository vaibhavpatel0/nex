"""Tiny helpers to update KEY=VALUE lines in a .env file without printing values."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict


def ensure(env: Path, example: Path) -> None:
    if not env.exists():
        shutil.copyfile(example, env)
    os.chmod(env, 0o600)


def upsert(env: Path, values: Dict[str, str]) -> None:
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    done = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip().lstrip("# ").strip() if "=" in line else None
        if key in values and key not in done:
            out.append(f"{key}={values[key]}")
            done.add(key)
        else:
            out.append(line)
    for k, v in values.items():
        if k not in done:
            out.append(f"{k}={v}")
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(env, 0o600)
