#!/bin/bash
# Oracle: write the expected output grid of this test input.
set -euo pipefail
mkdir -p /app
cat > /app/output.json <<'GRID'
{{EXPECTED_OUTPUT}}
GRID
