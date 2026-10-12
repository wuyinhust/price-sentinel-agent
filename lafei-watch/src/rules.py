#!/usr/bin/env python3
"""异常规则引擎：结构化规则判断，替代人工标红。**双口径同时计算**。

W Sir 0920 定的口径：
- 破价口径用**到手价**，但到手价与划线价都记；
- **两条规则同时存在**，网页与导出可切换查看：
    * basis=deal —— 到手价规则：用 observations.price（默认）
    * basis=list —— 划线价规则：用 observations.list_price
- anomalies.basis 就是这条异常属于哪套规则。

规则（每套口径各跑一遍）：
- below_lpp     监测价 < 对应包装 LPP（破价）
- price_down    较上一 live 监测日下降
- price_up      较上一 live 监测日上涨
- new_listing   上一日空白、本日有报价（新增报价/疑似新上架）
- delisted      上一日有报价、本日空白（疑似下架/缺货，需复核）

数据边界：只看 visible=1 的 live 记录（本人亲自采集）。0907 表导入的数据已封存，
          既不作证据也不参与比较；表外 SKU（pack_type=other）只记录、不判断。

用法：
    python src/rules.py            # 按两套口径重算（保留已存在的异常行）
    python src/rules.py --reset    # 先清空 live 派生异常再重算（改口径/改价后建议用）
"""
import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "sentinel.db"

BASES = {"deal": ("到手价", "o.price"), "list": ("划线价", "o.list_price")}
DEFAULT_BASIS = "deal"
LIVE = "o.visible = 1"
SEVERITY = {"below_lpp": "high", "price_down": "medium", "price_up": "low",
            "new_listing": "info", "delisted": "medium"}


def add_anomaly(cur, atype, pid, ptype, price, rule_ref, diff, date, obs_id, basis):
    cur.execute(
        """INSERT OR IGNORE INTO anomalies
           (anomaly_type,product_id,pack_type,basis,trigger_price,rule_ref,diff,
            severity,first_seen,last_seen,status,obs_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (atype, pid, ptype, basis, price, rule_ref, diff, SEVERITY[atype], date, date, "open", obs_id),
    )


def run(db_path=DB, reset=False):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if reset:
        cur.execute("DELETE FROM anomalies WHERE obs_id IN "
                    "(SELECT obs_id FROM observations WHERE visible=1)")

    dates = [r[0] for r in cur.execute(
        "SELECT DISTINCT o.observed_at FROM observations o WHERE o.visible=1 ORDER BY o.observed_at")]

    for date in dates:
        for basis, (label, price_col) in BASES.items():
            # --- below_lpp：当日所有 ok 记录对比规则（仅 single/double/case 有 LPP 基准） ---
            q = f"""
            SELECT o.obs_id, o.product_id, o.pack_type, {price_col},
                   r.lpp_single, r.lpp_double, r.lpp_case
            FROM observations o
            JOIN price_rules r ON r.product_id = o.product_id
            WHERE o.observed_at = ? AND o.status = 'ok' AND o.visible = 1
            """
            for obs_id, pid, ptype, price, ls, ld, lc in cur.execute(q, (date,)).fetchall():
                lpp = {"single": ls, "double": ld, "case": lc}.get(ptype)
                if lpp is not None and price is not None and price < lpp:
                    add_anomaly(cur, "below_lpp", pid, ptype, price,
                                f"LPP({ptype})={lpp}｜{label}口径", round(price - lpp, 2),
                                date, obs_id, basis)

            # --- 与上一 live 监测日比较（同口径、同 SKU） ---
            prev_dates = [d for d in dates if d < date]
            if not prev_dates:
                continue
            prev = prev_dates[-1]
            q2 = f"""
            SELECT c.obs_id, c.product_id, c.pack_type, {price_col.replace('o.', 'c.')},
                   {price_col.replace('o.', 'p.')}, p.status
            FROM observations c
            JOIN observations p ON p.product_id = c.product_id AND p.pack_type = c.pack_type
                 AND COALESCE(p.sku_name,'') = COALESCE(c.sku_name,'')
            WHERE c.observed_at = ? AND p.observed_at = ?
              AND c.visible = 1 AND p.visible = 1
            """
            for obs_id, pid, ptype, cur_price, prev_price, prev_status in cur.execute(q2, (date, prev)).fetchall():
                if cur_price is not None and prev_price is not None:
                    if cur_price < prev_price:
                        add_anomaly(cur, "price_down", pid, ptype, cur_price,
                                    f"上日({prev})={prev_price}｜{label}口径",
                                    round(cur_price - prev_price, 2), date, obs_id, basis)
                    elif cur_price > prev_price:
                        add_anomaly(cur, "price_up", pid, ptype, cur_price,
                                    f"上日({prev})={prev_price}｜{label}口径",
                                    round(cur_price - prev_price, 2), date, obs_id, basis)
                elif cur_price is not None and prev_price is None:
                    add_anomaly(cur, "new_listing", pid, ptype, cur_price,
                                f"上日({prev})空白｜{label}口径", None, date, obs_id, basis)
                elif cur_price is None and prev_price is not None:
                    add_anomaly(cur, "delisted", pid, ptype, None,
                                f"上日({prev})={prev_price}｜{label}口径", None, date, obs_id, basis)

    conn.commit()
    stats = cur.execute(
        "SELECT basis, anomaly_type, severity, COUNT(*) FROM anomalies "
        "GROUP BY 1,2,3 ORDER BY 1,2").fetchall()
    conn.close()
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清空 live 派生异常再重算")
    a = ap.parse_args()
    rows = run(reset=a.reset)
    for row in rows:
        print(row)
    if not rows:
        print("（无异常）")
