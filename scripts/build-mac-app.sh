#!/bin/sh
# 把 mac/ 下的源码组装成 SubMatch.app
set -e
cd "$(dirname "$0")/.."
APP="dist/SubMatch.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp mac/Info.plist "$APP/Contents/Info.plist"
cp mac/SubMatch.icns "$APP/Contents/Resources/SubMatch.icns"
cp mac/submatch.py "$APP/Contents/Resources/submatch.py"
cp core/submatch_core.py "$APP/Contents/Resources/submatch_core.py"

cat > "$APP/Contents/MacOS/SubMatch" <<'LAUNCHER'
#!/bin/sh
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
for py in \
  "$HOME/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12" \
  "$HOME/.workbuddy/binaries/python/envs/default/bin/python" \
  /opt/homebrew/bin/python3 \
  /usr/local/bin/python3 \
  /usr/bin/python3
do
  [ -x "$py" ] || continue
  if "$py" -c 'import tkinter,sys;sys.exit(0 if tkinter.TkVersion>=8.6 else 1)' 2>/dev/null; then
    exec "$py" "$DIR/submatch.py"
  fi
done
osascript -e 'display alert "SubMatch" message "找不到 Tk 8.6 以上的 Python 3。"' >/dev/null 2>&1
exit 1
LAUNCHER

chmod +x "$APP/Contents/MacOS/SubMatch"
chmod +x "$APP/Contents/Resources/submatch.py"
echo "built $APP"
