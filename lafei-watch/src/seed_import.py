#!/usr/bin/env python3
"""0907 种子导入：seed_0907.csv -> sentinel.db

规则：
- 组合装区（一个 brand_group 含多行、且组名是组合/礼盒类）-> 一个 combo 商品 + combo_members
- 单品 -> products + packages(single/double/case) + price_rules + observations(0903/0907)
- 空白价格 -> observations.status='blank_legacy'（历史空白，原因未区分），不当作 0 或无货
"""
import csv
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "sentinel.db"
SEED = ROOT / "seed" / "seed_0907.csv"
SCHEMA = ROOT / "src" / "schema.sql"

PACK_COLS = [("single", "lpp_single"), ("double", "lpp_double"), ("case", "lpp_case")]
DATES = [("2026-09-03", "d0903"), ("2026-09-07", "d0907")]


def parse_name(name):
    vintage = None
    m = re.search(r"[（(\s]((?:19|20)\d{2})\s*年?\s*[）)]?", name)
    if m:
        vintage = m.group(1)
    volume = None
    m = re.search(r"(\d+(?:\.\d+)?\s*(?:ml|升))", name, re.I)
    if m:
        volume = m.group(1).replace(" ", "")
    version = None
    m = re.search(r"【(.+?)】", name)
    if m:
        version = m.group(1)
    elif "限定版" in name:
        version = "限定版"
    return vintage, volume, version


def f(v):
    v = (v or "").strip()
    return float(v) if v else None


def main():
    reader = csv.reader(SEED.open(encoding="utf-8-sig"))
    header = next(reader)
    ncol = len(header)
    rows = []
    for raw in reader:
        if not raw:
            continue
        raw = raw + [""] * (ncol - len(raw))  # 尾部空缺一律按空白补齐
        rows.append(dict(zip(header, raw)))
    if not DB.parent.exists():
        DB.parent.mkdir()
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    cur = conn.cursor()

    # 分组：保持原顺序
    groups = {}
    order = []
    for r in rows:
        g = r["brand_group"]
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(r)

    # 组合组判定：组名含 组合/礼盒/三支装/全家福/郊游/联名/世纪年份/各两瓶 且组内多行
    def is_combo_group(gname, members):
        kw = ("组合", "礼盒", "三支装", "全家福", "郊游", "联名", "世纪年份", "各两瓶")
        return len(members) > 1 and any(k in gname for k in kw)

    p_seq = 0
    pk_seq = 0
    n_products = n_combos = n_obs = n_rules = 0

    for gname in order:
        members = groups[gname]
        if is_combo_group(gname, members):
            # 一个 combo 商品：价格取组内第一行（0907 合并区首行）
            p_seq += 1
            cid = f"P{p_seq:04d}"
            head = members[0]
            cur.execute(
                "INSERT INTO products(product_id,brand_group,name,item_type,source_row,note) VALUES(?,?,?,?,?,?)",
                (cid, gname, gname, "combo", head["row_no"], "0907组合装，价格为整组价"),
            )
            n_combos += 1
            for mrow in members:
                cur.execute(
                    "INSERT INTO combo_members(combo_id,member_name) VALUES(?,?)",
                    (cid, mrow["product_name"]),
                )
            pk_seq += 1
            cur.execute(
                "INSERT INTO packages(package_id,product_id,pack_type,pack_qty,existence) VALUES(?,?,?,?,?)",
                (f"PK{pk_seq:04d}", cid, "combo_pack", len(members),
                 "confirmed" if any(f(m["d0907_single"]) or f(m["d0903_single"]) for m in members) else "unconfirmed"),
            )
            # 规则：组合只有零售价（C列），无 LPP
            if f(head["retail_price"]):
                cur.execute(
                    "INSERT INTO price_rules(product_id,retail_price,effective_from,source) VALUES(?,?,?,?)",
                    (cid, f(head["retail_price"]), "2026-09-07", "0907表"),
                )
                n_rules += 1
            for date, pre in DATES:
                price = f(head[f"{pre}_single"])
                cur.execute(
                    """INSERT INTO observations(observed_at,product_id,pack_type,price,status,channel,evidence,method,confidence)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (date, cid, "combo_pack", price,
                     "ok" if price is not None else "blank_legacy",
                     "未标注(0907历史)", f"0907表 行{head['row_no']}", "人工录入(0907表)", "中"),
                )
                n_obs += 1
        else:
            for r in members:
                p_seq += 1
                pid = f"P{p_seq:04d}"
                vintage, volume, version = parse_name(r["product_name"])
                note = r["note"] or None
                cur.execute(
                    """INSERT INTO products(product_id,brand_group,name,vintage,volume,version,item_type,source_row,note)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (pid, r["brand_group"], r["product_name"], vintage, volume, version,
                     "single", r["row_no"], note),
                )
                n_products += 1
                # 包装：三类都建行；有无任何价格证据决定 existence
                for ptype, lppcol in PACK_COLS:
                    has_evidence = any(f(r[f"{prefix}_{ptype}"]) for _, prefix in DATES) or f(r[lppcol])
                    pk_seq += 1
                    cur.execute(
                        "INSERT INTO packages(package_id,product_id,pack_type,existence) VALUES(?,?,?,?)",
                        (f"PK{pk_seq:04d}", pid, ptype,
                         "confirmed" if has_evidence else "unconfirmed"),
                    )
                # 规则
                if any(f(r[c]) for c in ("retail_price", "lpp_single", "lpp_double", "lpp_case")):
                    cur.execute(
                        """INSERT INTO price_rules(product_id,retail_price,lpp_single,lpp_double,lpp_case,effective_from,source)
                           VALUES(?,?,?,?,?,?,?)""",
                        (pid, f(r["retail_price"]), f(r["lpp_single"]), f(r["lpp_double"]),
                         f(r["lpp_case"]), "2026-09-07", "0907表"),
                    )
                    n_rules += 1
                # 监测记录
                for date, pre in DATES:
                    for ptype in ("single", "double", "case"):
                        price = f(r[f"{pre}_{ptype}"])
                        red = r["red_cells"] or ""
                        col_letter = {"2026-09-03": {"single": "G", "double": "H", "case": "I"},
                                      "2026-09-07": {"single": "J", "double": "K", "case": "L"}}[date][ptype]
                        cell_note = "0907人工标红" if col_letter in red else None
                        cur.execute(
                            """INSERT INTO observations(observed_at,product_id,pack_type,price,status,channel,evidence,method,confidence,note)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (date, pid, ptype, price,
                             "ok" if price is not None else "blank_legacy",
                             "未标注(0907历史)", f"0907表 行{r['row_no']} 列{col_letter}",
                             "人工录入(0907表)", "中", cell_note),
                        )
                        n_obs += 1

    # 批次
    for date, pre in DATES:
        planned = cur.execute(
            "SELECT COUNT(*) FROM observations WHERE observed_at=?", (date,)).fetchone()[0]
        collected = cur.execute(
            "SELECT COUNT(*) FROM observations WHERE observed_at=? AND status='ok'", (date,)).fetchone()[0]
        cur.execute(
            "INSERT INTO batches(batch_date,planned,collected,note) VALUES(?,?,?,?)",
            (date, planned, collected, "0907历史批次（导入）"),
        )

    conn.commit()
    print(f"products(single)={n_products} combos={n_combos} rules={n_rules} observations={n_obs}")
    print(f"groups={len(order)} packages={pk_seq}")
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
