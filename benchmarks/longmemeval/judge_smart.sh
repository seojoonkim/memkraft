#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 llm_judge.py results/v102_smart_oracle_n50_20260422_0239.json
