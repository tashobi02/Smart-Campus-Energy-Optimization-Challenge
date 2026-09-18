#!/usr/bin/env bash
# Posts the first public sample case to the optimize-energy endpoint.
set -euo pipefail

SAMPLE_FILE="${1:-tests/fixtures/public_cases.json}"

# Extract the first case's input using python
PAYLOAD=$(python3 -c "
import json, sys
with open('$SAMPLE_FILE') as f:
    data = json.load(f)
print(json.dumps(data['cases'][0]['input']))
")

curl -s -X POST \
  http://localhost:"${PORT:-8000}"/optimize-energy \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" | python3 -m json.tool
