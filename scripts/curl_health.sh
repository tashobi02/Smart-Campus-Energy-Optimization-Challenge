#!/usr/bin/env bash
set -euo pipefail
curl -s http://localhost:"${PORT:-8000}"/health | python3 -m json.tool
