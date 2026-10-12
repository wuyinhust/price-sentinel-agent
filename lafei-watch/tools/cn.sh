#!/bin/sh
# 价格哨兵 · 中文输入（ADBKeyboard 广播注入）
# 用法: tools/cn.sh "文本"      # 清空输入框后注入文本（自动切到 ADBKeyboard）
#       tools/cn.sh --restore  # 还原到百度输入法
ADB=/Applications/Escrcpy.app/Contents/Resources/extra/mac-arm64/scrcpy/adb
ADBKB=com.android.adbkeyboard/.AdbIME
BAIDU=com.baidu.input_huawei/.ImeService

if [ "$1" = "--restore" ]; then
  "$ADB" shell ime set "$BAIDU" >/dev/null 2>&1
  echo "已还原输入法: $("$ADB" shell settings get secure default_input_method)"
  exit 0
fi

if [ -z "$1" ]; then echo "用法: tools/cn.sh \"文本\" | --restore" >&2; exit 1; fi

cur=$("$ADB" shell settings get secure default_input_method | tr -d '\r')
if [ "$cur" != "$ADBKB" ]; then
  "$ADB" shell ime set "$ADBKB" >/dev/null 2>&1
  sleep 1
fi
"$ADB" shell am broadcast -a ADB_CLEAR_TEXT >/dev/null 2>&1
"$ADB" shell am broadcast -a ADB_INPUT_TEXT --es msg "$1" >/dev/null 2>&1
sleep 0.5
echo "已注入: $1"
