"""Environment + shared helpers. Reads proto_aegis/.env, then falls back to ../proto_board/.env for the DB settings."""
import os
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent


def load_env():
    for p in (HERE / ".env", ROOT / "proto_board" / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if v.strip():
                        os.environ.setdefault(k.strip(), v.strip())


load_env()


def have(*names):
    return all(os.environ.get(n) for n in names)
