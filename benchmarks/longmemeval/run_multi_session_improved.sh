#!/bin/bash
set -e
cd "$(dirname "$0")"
export TAG="multi_session_improved"
python3 run.py 50 s 2>&1
echo ""
echo "=== Running LLM judge ==="
LATEST=$(ls -t results/multi_session_improved_s_n50_*.json | head -1)
python3 llm_judge.py "$LATEST"
