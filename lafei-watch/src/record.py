#!/usr/bin/env python3
"""把一次「商品详情页 SKU 面板实采」的结果写进库（visible=1 的 live 记录）。

用法:
    python src/record.py payload.json            # 写入
    python src/record.py payload.json --dry-run  # 只打印将要写入的内容
    python src/record.py payload.json --force    # 同一天同 SKU 已有记录时覆盖重写

payload.json 结构:
{
  "batch_date": "2026-09-20",
  "product_id": "P0002",
  "observed_time": "2026-09-20T23:2x:xx+08:00",   // 可省，默认当前时间
  "shop": "拉菲罗斯柴尔德葡萄酒官方旗舰店",
  "monitor_status": "已确认",                     // 可省，默认不动
  "jd_title": "拉菲【旗舰店】巴斯克赤霞珠红葡萄酒 …",  // 京东页面原始标题（保险列）
  "note": "整体备注（如：页面标 规格(3)）",
  "skus": [
    {"pack_type": "single", "sku_name": "巴斯克赤霞珠单支",
     "price": 94.0, "list_price": 99.0, "status": "ok",
     "url": "https://item.jd.com/10152168265683.html",
     "evidence": "evidence/2026-09-20/xxx.png",
     "note": "型号编码10152168265683；页面标到手价94/划线价99"}
  ]
}

口径（W Sir 0920 定，与 docs/监控计划-0920.md 第 8 节一致）：
  price      = **到手价**（成交价，主口径，below_lpp 默认用它）；
  list_price = 划线价（京东价），另存一份；
  两条破价规则同时存在（anomalies.basis = deal / list），网页与导出可切换。
  deal_price 列兼容保留，写库时与 price 同值。

写库动作：
  1) 每个 SKU 追加一条 observations(visible=1, method='p30-harness(adb)')；
  2) 按 (product_id, pack_type) 回填 packages.sku_name / source='京东SKU面板实采' / existence='confirmed'；
  3) 更新 products.monitor_status（payload 给了才改）；
  4) 刷新 batches 当天批次的 started_at/finished_at/collected。
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "sentinel.db"
CST = timezone(timedelta(hours=8))
METHOD = "p30-harness(adb)"
CHANNEL = "京东App"
DEFAULT_SHOP = "拉菲罗斯柴尔德葡萄酒官方旗舰店"


def now_iso() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("payload")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    data = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    obs_time = data.get("observed_time") or now_iso()
    batch_date = data.get("batch_date") or obs_time[:10]
    shop = data.get("shop") or DEFAULT_SHOP
    pid = data["product_id"]
    skus = data["skus"]

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    if cur.execute("SELECT 1 FROM products WHERE product_id=?", (pid,)).fetchone() is None:
        print(f"商品 {pid} 不在 products 表里，先补商品再采（不猜）。", file=sys.stderr)
        return 2

    written, skipped = 0, []
    for s in skus:
        exists = cur.execute(
            """SELECT obs_id FROM observations
               WHERE product_id=? AND observed_at=? AND pack_type=?
                 AND COALESCE(sku_name,'')=COALESCE(?,'') AND visible=1""",
            (pid, batch_date, s["pack_type"], s.get("sku_name")),
        ).fetchone()
        if exists and not args.force:
            skipped.append((s["pack_type"], s.get("sku_name"), exists[0]))
            continue
        if exists and args.force:
            cur.execute("DELETE FROM observations WHERE obs_id=?", (exists[0],))
            cur.execute("DELETE FROM anomalies WHERE obs_id=?", (exists[0],))

        print(f"  + {pid} {s['pack_type']:<6} {s.get('sku_name')}  到手价={s.get('price')} "
              f"划线价={s.get('list_price')} status={s.get('status','ok')} ev={s.get('evidence')}")
        if args.dry_run:
            written += 1
            continue

        cur.execute(
            """INSERT INTO observations
               (observed_at,observed_time,product_id,pack_type,sku_name,price,list_price,deal_price,
                status,shop,jd_title,channel,url,evidence,method,confidence,visible,note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (batch_date, obs_time, pid, s["pack_type"], s.get("sku_name"),
             s.get("price"), s.get("list_price"), s.get("price"),
             s.get("status", "ok"), shop, s.get("jd_title") or data.get("jd_title"),
             CHANNEL, s.get("url"), s.get("evidence"), METHOD,
             s.get("confidence", "高"), s.get("note")),
        )
        written += 1

        if s.get("sku_name") and s["pack_type"] != "combo_pack":
            cur.execute(
                """UPDATE packages SET sku_name=?, source='京东SKU面板实采', existence='confirmed'
                   WHERE product_id=? AND pack_type=?""",
                (s["sku_name"], pid, s["pack_type"]),
            )

    if data.get("monitor_status"):
        cur.execute("UPDATE products SET monitor_status=? WHERE product_id=?",
                    (data["monitor_status"], pid))

    if not args.dry_run:
        planned = cur.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        cur.execute("INSERT OR IGNORE INTO batches (batch_date,planned,note) VALUES (?,?,?)",
                    (batch_date, planned, "0920 基线采集"))
        cur.execute("SELECT started_at FROM batches WHERE batch_date=?", (batch_date,)).fetchone()
        cur.execute(
            """UPDATE batches SET planned=?,
                 started_at=COALESCE(started_at,?),
                 finished_at=?,
                 collected=(SELECT COUNT(*) FROM observations o
                            WHERE o.observed_at=? AND o.visible=1 AND o.status='ok')
               WHERE batch_date=?""",
            (planned, obs_time, obs_time, batch_date, batch_date),
        )
        conn.commit()

    conn.close()
    print(f"{'（dry-run）将写入' if args.dry_run else '已写入'} {written} 条监测记录；"
          f"跳过重复 {len(skipped)} 条"
          + (f" -> {skipped}" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
