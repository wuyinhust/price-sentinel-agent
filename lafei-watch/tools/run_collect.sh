#!/bin/sh
# 价格哨兵 · 放量采集启动器（可反复执行，断点续采）
# 用法: tools/run_collect.sh [limit]
#   limit 省略 = 跑完 spec 里全部未完成的商品
#
# 注意：macOS 没有 setsid，nohup 裸起会在工具调用结束后被杀。
# 本脚本只负责把命令拼好打印出来——真正的托管要用工具层的后台任务，
# 或由调用方 `exec <本脚本输出的命令>`。
cd /Users/wangyao/WorkBuddy/价格哨兵 || exit 1
HARNESS="/Volumes/正式素材文件/海外测试VVV/price-sentinel-agent-wuyinhust/android-harness"
PY="$HARNESS/.venv/bin/python"
LOG="logs/run_$(date +%Y-%m-%d).log"
mkdir -p logs
LIMIT_ARG=""
if [ -n "$1" ]; then LIMIT_ARG="--limit $1"; fi
if pgrep -f collect_driver.py >/dev/null 2>&1; then
  echo "已有采集进程在跑，先停：pkill -f collect_driver.py"
  pgrep -fl collect_driver.py | head -3
  exit 1
fi
echo "已就绪。用下面的命令托管（或放进后台任务）："
echo "cd /Users/wangyao/WorkBuddy/价格哨兵 && exec \"$PY\" src/collect_driver.py payloads/spec_all.json $LIMIT_ARG >> $LOG 2>&1"
