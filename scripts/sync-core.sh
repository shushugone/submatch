#!/bin/sh
# 把 core/submatch_core.py 同步到 mac/ 和 docker/（两处各留一份，方便单独打包）
set -e
cd "$(dirname "$0")/.."
cp core/submatch_core.py mac/submatch_core.py
cp core/submatch_core.py docker/submatch_core.py
echo "core synced"
