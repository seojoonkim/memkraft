#!/bin/bash
set -e
cd "$(dirname "$0")"
export TAG="${TAG:-agg_smoke}"
python3 run.py "${1:-3}" "${2:-oracle}"
