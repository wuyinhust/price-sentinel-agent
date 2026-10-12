#!/usr/bin/env python3
"""Excel 导出：数据库 -> 监测明细 xlsx（Excel 只是导出格式，库是主记录）。

导出字段（W Sir 0920 定稿）：
监测日期、监测时间、店铺、品牌、商品、规格、包装类型、SKU原名、参考价、LPP、
监测价（当前口径）、到手价、划线价、库存状态、渠道、链接、证据、判断结果、异常状态、
京东原始标题（保险列）、备注

口径（W Sir 0920 定）：
- 破价口径用**到手价**（默认），但到手价与划线价**都记**；
- 两条破价规则同时存在（anomalies.basis = deal / list），导出按 `basis` 参数切换：
    export(out, date, basis="deal")  -> 「监测价」列 = 到手价，判断结果取到手价规则
    export(out, date, basis="list")  -> 「监测价」列 = 划线价，判断结果取划线价规则
  两列价格在任何口径下都完整展示，方便对照。

监测时间：精确到秒（含时区）。0907 历史表未记录时分秒，导出留空并写「历史表仅到日」，
不编造时间——免得事后为“这条价是什么时候采的”扯皮。
"""
import sqlite3
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "sentinel.db"

BASIS_CN = {"deal": "到手价", "list": "划线价"}
PACK_CN = {"single": "单瓶", "double": "双支礼盒", "case": "整箱装", "combo_pack": "组合装",
           "other": "表外SKU"}
STATUS_CN = {"ok": "有报价", "blank_legacy": "空白(历史未区分)", "out_of_stock": "无货",
             "not_listed": "未上架", "not_monitored": "未监测", "no_such_pack": "无该包装",
             "unrecognized": "无法识别"}
ANOM_CN = {"below_lpp": "低于LPP", "price_down": "价格下降", "price_up": "价格上涨",
           "new_listing": "新增报价", "delisted": "疑似下架", "pack_changed": "组合内容变化",
           "unverifiable": "价格无法确认", "insufficient_evidence": "证据不足"}
ANOM_STATUS_CN = {"open": "待处理", "acknowledged": "已知晓", "resolved": "已处理", "closed": "已关闭"}

RED_FILL = PatternFill("solid", fgColor="FFC7CE")
HEADER_FILL = PatternFill("solid", fgColor="DAE3F5")
BASIS_FILL = PatternFill("solid", fgColor="FFF2CC")

HEADERS = ["监测日期", "监测时间", "店铺", "品牌", "商品", "规格", "包装类型", "SKU原名",
           "参考价", "LPP", "监测价", "到手价", "划线价", "库存状态", "渠道",
           "链接", "证据", "判断结果", "异常状态", "京东原始标题", "备注"]
WIDTHS = [11, 20, 30, 14, 46, 14, 10, 20, 9, 9, 11, 9, 9, 14, 12, 24, 20, 14, 10, 50, 40]


def query_rows(conn, date=None, basis="deal"):
    where = "WHERE o.observed_at = ? AND o.visible = 1" if date else "WHERE o.visible = 1"
    params = (date,) if date else ()
    sql = f"""
    SELECT o.observed_at, o.observed_time, o.shop, p.brand_group, p.name,
           TRIM(COALESCE(p.vintage,'') || ' ' || COALESCE(p.volume,'') || ' ' || COALESCE(p.version,'')),
           o.pack_type, o.sku_name,
           r.retail_price,
           CASE o.pack_type WHEN 'single' THEN r.lpp_single WHEN 'double' THEN r.lpp_double
                            WHEN 'case' THEN r.lpp_case END,
           o.price, o.list_price, o.status, o.channel,
           o.url, o.evidence, o.jd_title,
           (SELECT GROUP_CONCAT(a.anomaly_type || ':' || a.status) FROM anomalies a
             WHERE a.product_id=o.product_id AND a.pack_type=o.pack_type
               AND a.first_seen=o.observed_at AND a.basis=?),
           o.note
    FROM observations o
    JOIN products p ON p.product_id = o.product_id
    LEFT JOIN price_rules r ON r.product_id = o.product_id
    {where}
    ORDER BY p.source_row, o.pack_type, o.observed_at
    """
    return conn.execute(sql, (basis,) + params).fetchall()


def export(out_path, date=None, db_path=DB, basis="deal"):
    conn = sqlite3.connect(db_path)
    wb = Workbook()
    ws = wb.active
    ws.title = "监测明细"
    ws.append([h if h != "监测价" else f"监测价（{BASIS_CN.get(basis, basis)}）" for h in HEADERS])
    for i, cell in enumerate(ws[1], 1):
        cell.font = Font(bold=True)
        cell.fill = BASIS_FILL if i == 11 else HEADER_FILL

    for row in query_rows(conn, date, basis):
        (d, dtime, shop, brand, name, spec, ptype, sku, retail, lpp, deal, lst, status,
         channel, url, evidence, jd_title, anoms, note) = row
        # 监测价列 = 当前口径的价格；两列价格始终都写
        price = deal if basis == "deal" else lst
        time_cell = str(dtime).replace("T", " ")[:19] if dtime else f"{d} (历史表仅到日)"
        verdicts, anom_status = [], set()
        if anoms:
            for item in anoms.split(","):
                t, s = item.split(":")
                verdicts.append(ANOM_CN.get(t, t))
                anom_status.add(ANOM_STATUS_CN.get(s, s))
        elif price is not None:
            verdicts.append("正常")
        else:
            verdicts.append("无数据")
        ws.append([d, time_cell, shop, brand, name, spec.strip() or None, PACK_CN.get(ptype, ptype), sku,
                   retail, lpp, price, deal, lst, STATUS_CN.get(status, status),
                   channel, url, evidence,
                   "、".join(verdicts), "、".join(sorted(anom_status)) or None, jd_title, note])
        # 证据列 -> 本地截图超链接（点击直接打开图片）
        if evidence and str(evidence).startswith("evidence/"):
            shot = (ROOT / str(evidence)).resolve()
            if shot.is_file():
                cell = ws.cell(row=ws.max_row, column=17)
                cell.hyperlink = str(shot)
                cell.font = Font(color="0984E3", underline="single")
        if anoms and "below_lpp:open" in anoms:
            for cell in ws[ws.max_row]:
                cell.fill = RED_FILL

    ws.freeze_panes = "A2"
    for i, w in enumerate(WIDTHS, 1):
        col = chr(64 + i) if i <= 26 else "A" + chr(64 + i - 26)
        ws.column_dimensions[col].width = w
    wb.save(out_path)
    conn.close()
    return out_path


if __name__ == "__main__":
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else None
    basis = sys.argv[2] if len(sys.argv) > 2 else "deal"
    out = ROOT / "exports" / f"拉菲旗舰店监测明细_{date or '全部'}_{BASIS_CN.get(basis, basis)}口径.xlsx"
    if not out.parent.exists():
        out.parent.mkdir()
    print(export(out, date, basis=basis))
