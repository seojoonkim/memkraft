#!/bin/bash
set -e
cd "$(dirname "$0")"
export MK_SEARCH_MODE=smart
export TAG=v102_smart
python3 run.py 50 oracle
