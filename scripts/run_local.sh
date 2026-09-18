#!/usr/bin/env bash
set -euo pipefail
uvicorn app.main:app --reload --host 0.0.0.0 --port "${PORT:-8000}"
