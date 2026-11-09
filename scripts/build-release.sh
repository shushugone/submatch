#!/bin/sh
# 生成 dist/ 下的发布包
set -e
cd "$(dirname "$0")/.."
VERSION="${1:-1.0.0}"

./scripts/sync-core.sh
./scripts/build-mac-app.sh

rm -f "dist/SubMatch-macOS-${VERSION}.zip" "dist/submatch-docker-${VERSION}.zip"
ditto -c -k --sequesterRsrc --keepParent dist/SubMatch.app "dist/SubMatch-macOS-${VERSION}.zip"
(cd docker && zip -r -q "../dist/submatch-docker-${VERSION}.zip" . -x "*.DS_Store" "__pycache__/*")

echo "release artifacts:"
ls -lh dist/*.zip
