#!/bin/bash
# 一鍵啟動本機語音輸入
# 用法：在終端機執行 ./run.sh
cd "$(dirname "$0")"
exec ./venv/bin/python voice_typing.py
