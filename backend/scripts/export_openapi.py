"""Export the application's real OpenAPI without a database connection or live secrets.

Run from backend: python scripts/export_openapi.py --output ../frontend/openapi.json
CI passes --check to detect a stale committed contract.
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://contract:unused@localhost/contract")
os.environ.setdefault("BROKER_URL", "amqp://contract:unused@localhost//")
os.environ.setdefault("MFA_ENCRYPTION_KEY", base64.urlsafe_b64encode(bytes(range(32))).decode())
os.environ.setdefault("AUDIT_HMAC_KEY", "openapi-export-placeholder-not-a-runtime-secret")

from app.main import app  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="../frontend/openapi.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(args.output)
    content = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI contract is stale. Regenerate frontend/openapi.json.")
        print("OpenAPI contract matches backend")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"OpenAPI exported: {target}")


if __name__ == "__main__":
    main()
