#!/bin/bash
# 一鍵安裝：裝相依套件、建虛擬環境、產生 App。
# 用法：在這個資料夾執行  bash install.sh
set -e
cd "$(dirname "$0")"

echo "==> 檢查環境"
if [ "$(uname -m)" != "arm64" ]; then
  echo "！這個工具需要 Apple Silicon（M 系列）Mac。偵測到非 arm64，停止。"
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "！找不到 Homebrew。請先到 https://brew.sh 安裝後再執行。"
  exit 1
fi

echo "==> 安裝 python@3.12 與 portaudio（已裝會自動略過）"
brew install python@3.12 portaudio

echo "==> 建立虛擬環境並安裝套件"
/opt/homebrew/bin/python3.12 -m venv venv
./venv/bin/pip install --upgrade pip --quiet
./venv/bin/pip install -r requirements.txt

echo "==> 產生「語音輸入.app」"
bash build_app.sh

echo ""
echo "完成！接下來："
echo "1. 到「應用程式」點兩下「語音輸入」"
echo "2. 第一次會要求開「輔助使用」權限，照系統提示開給它"
echo "3. 按一下右 ⌘ 開始說話、再按一下停止，文字會自動貼上"
echo "（第一次轉錄會下載約 1.5GB 模型，只下載一次）"
