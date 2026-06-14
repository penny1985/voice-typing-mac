#!/bin/bash
# 把語音輸入編譯成可點兩下的「語音輸入.app」。
# 會用「這個資料夾的實際路徑」產生啟動器，所以換電腦／換位置都能用。
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

APP_NAME="語音輸入.app"
TARGET="/Applications/$APP_NAME"
if ! touch "/Applications/.write_test" 2>/dev/null; then
  mkdir -p "$HOME/Applications"
  TARGET="$HOME/Applications/$APP_NAME"
else
  rm -f "/Applications/.write_test"
fi

# 依本機實際路徑即時產生啟動器腳本
TMP="$(mktemp -t voicetyping).applescript"
cat > "$TMP" <<EOF
with timeout of 315360000 seconds
	do shell script "$DIR/venv/bin/python $DIR/menubar_app.py > /tmp/voice-typing.log 2>&1"
end timeout
EOF

rm -rf "$TARGET"
osacompile -o "$TARGET" "$TMP"
rm -f "$TMP"
echo "已建立：$TARGET"
echo "指向：$DIR/menubar_app.py"
