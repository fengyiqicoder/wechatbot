#!/bin/bash
# 幂等拉起 WDA + USB 端口转发（8100 控制 / 9100 视频流）。
# --build 先编译安装 WDA（首次、换手机、或 Xcode 升级后需要）。
# 签名：WDA_TEAM_ID 不给时，用 Xcode 里已登录账号的 Team（scripts/team_id.py）。
set -u
BASE="$(cd "$(dirname "$0")/.." && pwd)"
PY="$BASE/.venv/bin/python"
LOG="${WECHATBOT_HOME:-$HOME/Library/Application Support/WeChatBot}/logs"; mkdir -p "$LOG"
WDA_PROJ="${WDA_PROJECT:-$HOME/.wda/WebDriverAgent/WebDriverAgent.xcodeproj}"
BUILD=0; [ "${1:-}" = "--build" ] && BUILD=1

TEAM="${WDA_TEAM_ID:-$(python3 "$BASE/scripts/team_id.py")}"
[ -z "$TEAM" ] && { echo "Xcode 里还没有登录 Apple ID：打开 Xcode → Settings → Accounts → + 登录（免费 Apple ID 也行），然后重跑"; exit 1; }
BUNDLE="${WDA_BUNDLE_ID:-com.wechatbot.wda.$(echo "$TEAM" | tr 'A-Z' 'a-z')}"

UDID="${WDA_UDID:-$("$PY" -m pymobiledevice3 usbmux list --usb 2>/dev/null \
  | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["Identifier"] if d else "")')}"
[ -z "$UDID" ] && { echo "未检测到 USB 连接的 iPhone（插线、解锁、点「信任此电脑」）"; exit 1; }
echo "设备: $UDID  签名 Team: $TEAM"

XC=(xcodebuild -project "$WDA_PROJ" -scheme WebDriverAgentRunner -destination "id=$UDID"
    -allowProvisioningUpdates DEVELOPMENT_TEAM="$TEAM" PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE"
    CODE_SIGN_STYLE=Automatic)

if [ $BUILD = 1 ]; then
  echo "编译 WDA（首次约 2-5 分钟）..."
  "${XC[@]}" build-for-testing > "$LOG/wda_build.log" 2>&1 || {
    tail -20 "$LOG/wda_build.log"; echo "编译失败，完整日志：$LOG/wda_build.log"; exit 1; }
fi

forward() {  # $1=端口
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1 && return
  "$PY" "$BASE/scripts/detach.py" --log "$LOG/forward_$1.log" -- \
    "$BASE/.venv/bin/pymobiledevice3" usbmux forward "$1" "$1" --serial "$UDID" >/dev/null
}

forward 8100
forward 9100
if ! curl -s -m 3 http://127.0.0.1:8100/status | grep -q '"state"'; then
  echo "启动 WDA（约 1 分钟）..."
  : > "$LOG/wda.log"
  "$PY" "$BASE/scripts/detach.py" --log "$LOG/wda.log" -- "${XC[@]}" test-without-building >/dev/null
  for i in $(seq 1 60); do
    grep -q ServerURLHere "$LOG/wda.log" 2>/dev/null && break
    if grep -qE "TEST EXECUTE FAILED|not been explicitly trusted|Untrusted Developer" "$LOG/wda.log" 2>/dev/null; then
      grep -qE "trusted|Untrusted" "$LOG/wda.log" \
        && echo "iPhone 上要信任开发者：设置 → 通用 → VPN与设备管理 → 点你的 Apple ID → 信任，然后重跑" \
        || echo "WDA 启动失败，见 $LOG/wda.log（常见解法：解锁 iPhone 后重试；没编译过先加 --build）"
      exit 1
    fi
    sleep 3
  done
fi
for i in $(seq 1 15); do
  curl -s -m 3 http://127.0.0.1:8100/status | grep -q '"state"' && { echo "WDA 就绪"; exit 0; }
  sleep 1
done
echo "WDA /status 不通，见 $LOG/"; exit 1
