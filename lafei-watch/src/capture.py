#!/usr/bin/env python3
"""0920 采集助手：手机截图 -> evidence/<日期>/<名称>.png，并打印库内可引用的证据字段。

为什么需要它：验收标准要求「任意一条异常都能回看证据」，所以证据不能乱放。
统一规则：evidence/YYYY-MM-DD/<渠道>_<商品或包装>_<价格或说明>.png

用法：
    python src/capture.py jd商品页_传奇整箱469            # 截当前屏幕并归档
    python src/capture.py --date 2026-09-20 jd搜索页_拉菲 --text   # 从 adb exec-out 拉取
    python src/capture.py --list                          # 列出已有证据

实现说明：优先用 android-harness 的 shot（它已处理 adb 路径与截图格式），
不可用时回退到 Escrcpy 内置 adb 的 screencap。
"""
import argparse
import subprocess
import sys
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
HARNESS = Path("/Volumes/正式素材文件/海外测试VVV/price-sentinel-agent-wuyinhust/android-harness")
ADB = Path("/Applications/Escrcpy.app/Contents/Resources/extra/mac-arm64/scrcpy/adb")


def adb_screencap(dest: Path) -> bool:
    if not ADB.exists():
        return False
    with dest.open("wb") as fh:
        r = subprocess.run([str(ADB), "exec-out", "screencap", "-p"], stdout=fh)
    return r.returncode == 0 and dest.stat().st_size > 1024


def harness_shot(dest: Path) -> bool:
    shot = HARNESS / "screenshot.png"
    if not (HARNESS / ".venv/bin/android-harness").exists():
        return False
    r = subprocess.run([str(HARNESS / ".venv/bin/android-harness"), "shot"],
                       cwd=HARNESS, capture_output=True)
    if r.returncode != 0 or not shot.exists():
        return False
    dest.write_bytes(shot.read_bytes())
    return dest.stat().st_size > 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="证据名（不含扩展名），会原样作为文件名")
    ap.add_argument("--date", default=None, help="日期目录，默认今天")
    ap.add_argument("--list", action="store_true", help="列出已有证据")
    args = ap.parse_args()

    if args.list or not args.name:
        for day in sorted([d for d in EVIDENCE.iterdir() if d.is_dir()], reverse=True) if EVIDENCE.exists() else []:
            pics = sorted(p for p in day.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
            print(f"{day.name}  ({len(pics)} 张)")
            for p in pics:
                print(f"  evidence/{day.name}/{p.name}")
        return

    day = args.date or _date.today().isoformat()
    out_dir = EVIDENCE / day
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name if args.name.lower().endswith(".png") else args.name + ".png"
    dest = out_dir / name

    ok = harness_shot(dest) or adb_screencap(dest)
    if not ok:
        print("截图失败：android-harness 与 Escrcpy adb 都不可用", file=sys.stderr)
        sys.exit(1)
    print(f"已归档 {dest}  ({dest.stat().st_size // 1024} KB)")
    print(f"库内 evidence 字段填： evidence/{day}/{name}")


if __name__ == "__main__":
    main()
