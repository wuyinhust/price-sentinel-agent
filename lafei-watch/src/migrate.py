#!/usr/bin/env python3
"""幂等迁移：监测时间戳 + 店铺/SKU 字段 + 0907 数据封存 + 双价格口径。

规则一：给监测记录补「监测时间戳」，免得事后为“这条价是什么时候采的”扯皮。
规则二：0907 表只是任务输入——0903/0907 的监测价一律封存（visible=0），
        派生异常清空，网页与导出只认本人亲自采集的 live 记录。
规则三：本任务店铺口径 = 拉菲罗斯柴尔德葡萄酒官方旗舰店；
        店铺不符的 live 记录同样封存（保留证据，但不进正式监控）。
规则四（v3，W Sir 0920 定）：破价口径采用**到手价**，但到手价与划线价都记；
        observations.price = 到手价（主口径），observations.list_price = 划线价；
        anomalies.basis 区分 deal/list 两套破价规则，网页与导出可切换。
        observations.jd_title 保存京东页面原始标题，作为「是不是同一个商品」的保险列。

可重复执行，不会覆盖已有值。
"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "sentinel.db"
EVIDENCE = ROOT / "evidence"
TZ = timezone(timedelta(hours=8))

DEFAULT_SHOP = "拉菲罗斯柴尔德葡萄酒官方旗舰店"
LEGACY_METHOD = "人工录入(0907表)"


def cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def add_col(conn, table, name, ddl, log):
    if name not in cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        log.append(f"{table}.{name} 已创建")


def main():
    conn = sqlite3.connect(DB)
    changed = []

    # --- v1：时间戳 ---
    add_col(conn, "observations", "observed_time", "TEXT", changed)
    add_col(conn, "batches", "started_at", "TEXT", changed)
    add_col(conn, "batches", "finished_at", "TEXT", changed)

    # --- v2：店铺 / SKU / 可见性 ---
    add_col(conn, "products", "shop", "TEXT", changed)
    add_col(conn, "products", "monitor_status", "TEXT NOT NULL DEFAULT '待确认'", changed)
    add_col(conn, "observations", "visible", "INTEGER NOT NULL DEFAULT 1", changed)
    add_col(conn, "observations", "sku_name", "TEXT", changed)
    add_col(conn, "packages", "sku_name", "TEXT", changed)
    add_col(conn, "packages", "source", "TEXT", changed)

    # --- v3：双价格口径（到手价 / 划线价）+ 京东原始标题保险列 ---
    # price 语义改为「到手价」（主口径），划线价另存 list_price；
    # 两条破价规则（到手价规则 / 划线价规则）同时存在，网页可切换，见 anomalies.basis。
    add_col(conn, "observations", "list_price", "REAL", changed)
    add_col(conn, "observations", "jd_title", "TEXT", changed)

    # 老记录语义迁移：v2 时 price=划线价、deal_price=到手价 → 交换，list_price 存划线价。
    # 幂等护栏：只处理 list_price 仍为空、且确实记过两个不同价的记录。
    n = conn.execute(
        """UPDATE observations SET list_price=price, price=deal_price
           WHERE list_price IS NULL AND deal_price IS NOT NULL AND price IS NOT NULL
             AND price != deal_price""").rowcount
    if n:
        changed.append(f"{n} 条记录按新口径交换 price(到手价)/list_price(划线价)")
    # deal_price 兼容保留：与 price 对齐（等价列）
    n = conn.execute("UPDATE observations SET deal_price=price WHERE deal_price IS NULL").rowcount

    # anomalies 加 basis：SQLite 改不了表内 UNIQUE 约束，需要重建表
    if "basis" not in cols(conn, "anomalies"):
        conn.execute("ALTER TABLE anomalies RENAME TO anomalies_v2")
        conn.execute("""
        CREATE TABLE anomalies (
            anomaly_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            anomaly_type TEXT NOT NULL,
            product_id   TEXT NOT NULL REFERENCES products(product_id),
            pack_type    TEXT NOT NULL,
            basis        TEXT NOT NULL DEFAULT 'deal',
            trigger_price REAL,
            rule_ref     TEXT,
            diff         REAL,
            severity     TEXT NOT NULL DEFAULT 'medium',
            first_seen   TEXT NOT NULL,
            last_seen    TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'open',
            assignee     TEXT,
            resolution   TEXT,
            closed_at    TEXT,
            obs_id       INTEGER REFERENCES observations(obs_id),
            UNIQUE (anomaly_type, product_id, pack_type, first_seen, basis)
        )""")
        conn.execute("""INSERT INTO anomalies
            (anomaly_id,anomaly_type,product_id,pack_type,basis,trigger_price,rule_ref,diff,
             severity,first_seen,last_seen,status,assignee,resolution,closed_at,obs_id)
            SELECT anomaly_id,anomaly_type,product_id,pack_type,'deal',trigger_price,rule_ref,diff,
                   severity,first_seen,last_seen,status,assignee,resolution,closed_at,obs_id
            FROM anomalies_v2""")
        conn.execute("DROP TABLE anomalies_v2")
        changed.append("anomalies 表已重建：新增 basis（破价口径 deal/list）")

    conn.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT, note TEXT)""")
    conn.execute("INSERT OR IGNORE INTO settings (key,value,note) VALUES "
                 "('pricing_basis','deal','破价默认口径：deal=到手价(list=划线价)；网页可切换')")

    # 用截图 mtime 回填缺失的时间戳
    filled = 0
    for obs_id, ev in conn.execute(
            "SELECT obs_id, evidence FROM observations "
            "WHERE observed_time IS NULL AND evidence LIKE 'evidence/%'").fetchall():
        f = (ROOT / ev.split(";")[0].strip()).resolve()
        if f.is_file():
            ts = datetime.fromtimestamp(f.stat().st_mtime, TZ).isoformat(timespec="seconds")
            conn.execute("UPDATE observations SET observed_time=? WHERE obs_id=?", (ts, obs_id))
            filled += 1
    if filled:
        changed.append(f"{filled} 条记录用截图 mtime 回填了监测时间")

    # --- 规则二：0907 导入记录一律封存 ---
    n = conn.execute("UPDATE observations SET visible=0 WHERE method=? OR observed_at IN "
                     "('2026-09-03','2026-09-07')", (LEGACY_METHOD,)).rowcount
    if n:
        changed.append(f"{n} 条 0907 导入记录已封存（visible=0）")
    # 0907 表没记店铺，不编造：置为明确哨兵值（列是 NOT NULL，不用空串）
    conn.execute("UPDATE observations SET shop='未记录(0907历史)' WHERE method=?", (LEGACY_METHOD,))

    # --- 规则三：店铺口径不符的 live 记录封存 ---
    rows = conn.execute(
        "SELECT obs_id, shop FROM observations WHERE visible=1 AND method!=?", (LEGACY_METHOD,)).fetchall()
    bad = 0
    for obs_id, shop in rows:
        if not shop or "官方旗舰店" not in shop:
            conn.execute(
                "UPDATE observations SET visible=0, "
                "note=COALESCE(note,'')||'｜店铺不符口径（非官方旗舰店），已封存' "
                "WHERE obs_id=?", (obs_id,))
            bad += 1
    if bad:
        changed.append(f"{bad} 条 live 记录因店铺不符口径被封存")
    # 0907 记录统一打标记：任务输入，非证据
    conn.execute(
        "UPDATE observations SET note='0907任务输入·非证据' || "
        "CASE WHEN note IS NULL OR note='' THEN '' ELSE '｜'||note END "
        "WHERE method=? AND (note IS NULL OR note NOT LIKE '%任务输入%')", (LEGACY_METHOD,))

    # --- 0907 派生的异常清空（异常只由 live 数据生成） ---
    # 注意：只删「由封存记录派生」或「0903/0907 历史日期」的异常。
    # 不能无条件 DELETE FROM anomalies，否则重跑迁移会把 live 采出来的异常一起抹掉。
    n = conn.execute(
        "DELETE FROM anomalies WHERE first_seen IN ('2026-09-03','2026-09-07') "
        "OR obs_id IN (SELECT obs_id FROM observations WHERE visible=0)").rowcount
    if n:
        changed.append(f"清空 {n} 条 0907 派生异常（live 异常保留，采集后由 rules.py 重算）")

    # --- products 默认店铺，monitor_status 待确认 ---
    conn.execute("UPDATE products SET shop=? WHERE shop IS NULL", (DEFAULT_SHOP,))
    conn.execute("UPDATE products SET monitor_status='待确认' WHERE monitor_status IS NULL")

    # --- 批次时间与完成数：live 批次按可见记录重算 ---
    for batch_date, in conn.execute(
            "SELECT batch_date FROM batches WHERE batch_date >= '2026-09-20'").fetchall():
        got = conn.execute("SELECT COUNT(*) FROM observations WHERE observed_at=? AND visible=1",
                           (batch_date,)).fetchone()[0]
        conn.execute("UPDATE batches SET collected=? WHERE batch_date=?", (got, batch_date))
    for batch_date, in conn.execute("SELECT batch_date FROM batches").fetchall():
        t0 = conn.execute("SELECT MIN(observed_time) FROM observations WHERE observed_at=? AND visible=1",
                          (batch_date,)).fetchone()[0]
        if t0:
            conn.execute("UPDATE batches SET started_at=COALESCE(started_at, ?), finished_at=? "
                         "WHERE batch_date=?", (t0, t0, batch_date))
    conn.commit()

    print("迁移完成：", "；".join(changed) if changed else "无变更（已是新版本）")
    for d, n in conn.execute("SELECT observed_at, COUNT(*) FROM observations "
                             "WHERE observed_time IS NULL GROUP BY 1 ORDER BY 1"):
        print(f"  无时间戳：{d}  {n} 条（历史表未记录时间）")
    print("  可见(live)记录：", conn.execute(
        "SELECT COUNT(*) FROM observations WHERE visible=1").fetchone()[0])
    print("  封存(非证据)记录：", conn.execute(
        "SELECT COUNT(*) FROM observations WHERE visible=0").fetchone()[0])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
