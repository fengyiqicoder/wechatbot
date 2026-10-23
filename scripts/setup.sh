#!/bin/bash
# 一次性安装：Python 环境 + WebDriverAgent 源码与编译。可重复执行。
set -eu
BASE="$(cd "$(dirname "$0")/.." && pwd)"
WDA_DIR="${WDA_DIR:-$HOME/.wda/WebDriverAgent}"

command -v xcodebuild >/dev/null || { echo "需要先安装 Xcode（App Store）"; exit 1; }
xcodebuild -version >/dev/null 2>&1 || { echo "Xcode 未完成初始化：先打开一次 Xcode 并同意协议"; exit 1; }

if [ ! -x "$BASE/.venv/bin/python" ]; then
  PYBIN="$(command -v python3.12 || command -v python3.13 || command -v python3.11 || command -v python3)"
  "$PYBIN" -m venv "$BASE/.venv"
fi
"$BASE/.venv/bin/pip" install -q --upgrade pip
"$BASE/.venv/bin/pip" install -q -r "$BASE/requirements.txt"
echo "Python 环境就绪"

if [ ! -d "$WDA_DIR" ]; then
  mkdir -p "$(dirname "$WDA_DIR")"
  git clone --depth 1 https://github.com/appium/WebDriverAgent.git "$WDA_DIR"
fi
echo "WebDriverAgent 源码在 $WDA_DIR"

"$BASE/scripts/start_wda.sh" --build
