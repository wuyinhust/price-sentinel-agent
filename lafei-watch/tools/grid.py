#!/usr/bin/env python3
"""给 P30 截图叠加坐标网格，便于从图上直接读设备坐标（1080x2340）。

用法:
    python tools/grid.py /tmp/shot.png            # -> /tmp/shot.grid.png
    python tools/grid.py /tmp/shot.png out.png 200

背景: JD App 首页/列表页有自动轮播，uiautomator dump 拿不到 idle 状态
（ERROR: could not get idle state），无障碍树不可用，只能靠截图 + 坐标点击。
本脚本画出每 100px 一条细线、每 200px 一条粗线并标注坐标。
"""
import sys

from PIL import Image, ImageDraw

SIZE = (1080, 2340)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.rsplit(".", 1)[0] + ".grid.png"
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 100

    im = Image.open(src).convert("RGB")
    if im.size != SIZE:
        im = im.resize(SIZE)
    d = ImageDraw.Draw(im)

    for x in range(0, SIZE[0] + 1, step):
        major = (x % (step * 2) == 0)
        d.line([(x, 0), (x, SIZE[1])], fill=(255, 0, 128) if major else (255, 160, 200), width=3 if major else 1)
        if major:
            for y in range(60, SIZE[1], 300):
                d.text((x + 6, y), str(x), fill=(255, 0, 128))

    for y in range(0, SIZE[1] + 1, step):
        major = (y % (step * 2) == 0)
        d.line([(0, y), (SIZE[0], y)], fill=(0, 120, 255) if major else (150, 200, 255), width=3 if major else 1)
        if major:
            for x in range(6, SIZE[0], 280):
                d.text((x, y + 6), str(y), fill=(0, 120, 255))

    im.save(dst)
    print(dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
