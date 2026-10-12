#!/bin/sh
# 价格哨兵 · 一步「点击(可选) + 截图 + 缩图」
# 用法: tools/look.sh [x y [等待秒数]] <输出名>
#   仅截图:  tools/look.sh name
#   点击后:  tools/look.sh 400 158 name 3
ADB=/Applications/Escrcpy.app/Contents/Resources/extra/mac-arm64/scrcpy/adb
PY=/Users/wangyao/.workbuddy/binaries/python/envs/default/bin/python
cd /Users/wangyao/WorkBuddy/价格哨兵 || exit 1

if [ "$#" -ge 3 ]; then
  "$ADB" shell input tap "$1" "$2"
  sleep "${4:-2.5}"
  NAME="$3"
elif [ "$#" -eq 2 ]; then
  "$ADB" shell input tap "$1" "$2"
  sleep 2.5
  NAME="look"
else
  NAME="${1:-look}"
fi

tools/hx shot --out "/tmp/${NAME}.png" >/dev/null 2>&1
"$PY" - "$NAME" <<'PY'
import sys
from PIL import Image
n = sys.argv[1]
im = Image.open(f"/tmp/{n}.png")
im.resize((im.width // 2, im.height // 2)).save(f"/tmp/{n}_s.png")
print(f"/tmp/{n}_s.png", im.size)
PY
