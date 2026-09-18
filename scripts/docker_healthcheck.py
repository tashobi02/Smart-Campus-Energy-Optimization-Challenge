#!/usr/bin/env python3
"""Docker HEALTHCHECK probe for GET /health.

Uses only the standard library so the production image does not need curl
installed. Exits 0 when the service reports {"status": "ok"}, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

PORT = os.environ.get("PORT", "8000")
URL = f"http://127.0.0.1:{PORT}/health"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=3) as resp:
            if resp.status != 200:
                return 1
            body = json.loads(resp.read())
            return 0 if body.get("status") == "ok" else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
