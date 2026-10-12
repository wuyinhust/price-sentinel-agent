#!/usr/bin/env python3
"""价格哨兵 · 监控台（数据库是主记录，本页面只是视图）。"""
import sqlite3
from pathlib import Path

from flask import Flask, g, render_template_string, request, send_file, send_from_directory
from pathlib import Path as _P
import os as _os
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "sentinel.db"
EVIDENCE_DIR = ROOT / "evidence"

app = Flask(__name__)


def fmt_time(ts, dash="历史表仅到日"):
    """监测时间显示：2026-09-20T21:18:36+08:00 -> 2026-09-20 21:18:36"""
    if not ts:
        return f'<span class=muted>—（{dash}）</span>'
    t = str(ts).replace("T", " ")
    return f'{t[:19]}'


def ev_link(evidence):
    """证据字段 -> 文本链接 + 悬停出图（图下方标注截图时间）。多个证据用 ; 或 , 分隔。"""
    if not evidence:
        return '<span class=muted>—</span>'
    val = str(evidence)
    if "evidence/" not in val:
        return val
    import re
    from datetime import datetime
    parts = [x.strip() for x in re.split(r"[;,、]\s*", val) if x.strip()]
    out = []
    for item in parts:
        rel = item[len("evidence/"):] if item.startswith("evidence/") else None
        f = (EVIDENCE_DIR / rel) if rel else None
        if f is not None and f.is_file():
            href = "/evidence/" + quote(rel)
            name = rel.rsplit("/", 1)[-1]
            ts = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            out.append(f'<a class="ev" href="{href}" target="_blank" title="{rel}｜截图时间 {ts}">'
                       f'<span class="evtxt">📷 {name}</span>'
                       f'<span class="evpop"><img class="evimg" src="{href}" alt="{name}" loading="lazy">'
                       f'<span class="evcap">截图时间 {ts}</span></span></a>')
        else:
            out.append(f'<span class=muted>{item}</span>')
    return "<br>".join(out) if out else val


@app.route("/evidence/<path:relpath>")
def evidence_file(relpath):
    return send_from_directory(EVIDENCE_DIR, relpath)

PACK_CN = {"single": "单瓶", "double": "双支", "case": "整箱", "combo_pack": "组合", "other": "其他SKU"}
ANOM_CN = {"below_lpp": "低于LPP", "price_down": "价格下降", "price_up": "价格上涨",
           "new_listing": "新增报价", "delisted": "疑似下架", "pack_changed": "组合变化",
           "unverifiable": "无法确认", "insufficient_evidence": "证据不足"}
STATUS_CN = {"ok": "有报价", "blank_legacy": "空白(历史)", "out_of_stock": "无货",
             "not_listed": "未上架", "not_monitored": "未监测", "no_such_pack": "无该包装",
             "unrecognized": "无法识别"}
MATCH_CN = {"待确认": "待确认", "已确认": "已确认", "名称不符": "名称不符"}
DEFAULT_SHOP = "拉菲罗斯柴尔德葡萄酒官方旗舰店"
BASIS_CN = {"deal": "到手价", "list": "划线价"}
BASIS_NOTE = {"deal": "到手价口径（成交价，默认）", "list": "划线价口径（京东价/原价）"}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


def cur_basis():
    """当前查看的破价口径：deal=到手价（默认）/ list=划线价。可被 ?basis= 覆盖。"""
    b = (request.args.get("basis") or "").lower()
    if b in BASIS_CN:
        return b
    try:
        row = get_db().execute("SELECT value FROM settings WHERE key='pricing_basis'").fetchone()
        if row and row["value"] in BASIS_CN:
            return row["value"]
    except sqlite3.Error:
        pass
    return "deal"


def basis_links(cur):
    """口径切换控件：两条规则同时存在，点一下换一套看。保留当前页其它查询参数。"""
    args = {k: v for k, v in request.args.items() if k != "basis"}
    out = []
    for k, label in (("deal", "到手价规则"), ("list", "划线价规则")):
        a = dict(args)
        a["basis"] = k
        qs = "&".join(f"{k2}={quote(str(v))}" for k2, v in a.items())
        cls = " on" if k == cur else ""
        out.append(f'<a class="pill{cls}" href="{request.path}?{qs}">{label}</a>')
    return "".join(out)


@app.teardown_appcontext
def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


BASE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>价格哨兵 · 拉菲旗舰店</title>
<style>
:root{--red:#d63031;--green:#00b894;--bg:#f5f6fa;--card:#fff;--ink:#2d3436;--muted:#636e72;--line:#dfe6e9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"PingFang SC",sans-serif}
header{background:#fff;border-bottom:1px solid var(--line);padding:12px 24px;display:flex;gap:20px;align-items:baseline}
header h1{font-size:18px;margin:0}nav a{color:var(--muted);text-decoration:none;margin-right:14px}nav a:hover{color:var(--ink)}
main{max-width:1200px;margin:20px auto;padding:0 16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .num{font-size:26px;font-weight:700}.card .lbl{color:var(--muted);font-size:12px}
.card.alert .num{color:var(--red)}.card.good .num{color:var(--green)}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}
th{background:#f1f2f6;font-weight:600;white-space:nowrap}
/* 证据：文本链接 + 悬停出图 */
.ev{position:relative;display:inline-block;color:#0984e3;text-decoration:none;cursor:zoom-in;white-space:nowrap}
.ev:hover .evtxt{text-decoration:underline}
.ev .evpop{display:none}
.ev:hover .evpop{display:block;position:absolute;right:0;bottom:calc(100% + 8px);z-index:9999;
  background:#fff;border:1px solid var(--line);border-radius:10px;
  box-shadow:0 14px 36px rgba(0,0,0,.28);padding:5px 5px 0}
.ev:hover .evpop img.evimg{display:block;width:360px;height:auto;max-height:560px;
  object-fit:contain;border-radius:6px;background:#f1f2f6}
.evcap{display:block;font-size:11px;color:var(--muted);text-align:center;padding:4px 0 5px;white-space:nowrap}
td a{text-decoration:none;color:#0984e3}td a:hover{text-decoration:underline}
/* 小屏：预览图缩窄，避免横向溢出 */
@media (max-width:900px){.ev:hover .evpop img.evimg{width:240px}}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px}
.tag.high{background:#ffeaa7;color:#d63031}.tag.medium{background:#dfe6e9}.tag.low,.tag.info{background:#f1f2f6;color:var(--muted)}
.down{color:var(--red);font-weight:600}.up{color:var(--green)}
h2{font-size:15px;margin:24px 0 8px}section{margin-bottom:8px}
a.btn{display:inline-block;background:#0984e3;color:#fff;padding:6px 14px;border-radius:8px;text-decoration:none}
.muted{color:var(--muted)}.num-r{text-align:right;font-variant-numeric:tabular-nums}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.gallery figure{margin:0;background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px}
.gallery img{width:100%;height:150px;object-fit:cover;border-radius:6px;display:block;background:#f1f2f6}
.gallery figcaption{font-size:11px;color:var(--muted);margin-top:6px;word-break:break-all}
.basisbox{margin-left:auto;display:flex;align-items:center;gap:8px}
.pill{display:inline-block;padding:4px 12px;border:1px solid var(--line);border-radius:999px;
  color:var(--muted);text-decoration:none;font-size:12px;background:#fff}
.pill:hover{border-color:#0984e3;color:#0984e3}
.pill.on{background:#0984e3;border-color:#0984e3;color:#fff;font-weight:600}
.banner{background:#fffbea;border:1px solid #ffeaa7;border-radius:8px;padding:8px 12px;margin-bottom:14px;font-size:12px;color:#8a6d1a}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:11px}
</style></head><body>
<header><h1>🛰 价格哨兵</h1><nav>
<a href="/">总览</a><a href="/products">商品</a><a href="/anomalies">异常队列</a><a href="/pending">待补采</a><a href="/evidence-gallery">证据</a>
</nav><span class=muted>拉菲旗舰店 · 数据库主记录</span>
<div class=basisbox><span class=muted>破价口径：</span>{{ basis_html | safe }}
<a class=btn href="/export.xlsx?basis={{ basis }}">导出 Excel</a></div></header>
<main>{{ body | safe }}</main></body></html>"""


def page(body, basis=None):
    b = basis or cur_basis()
    return render_template_string(BASE, body=render_template_string(body, basis=b),
                                  basis=b, basis_html=basis_links(b))


@app.route("/")
def dashboard():
    db = get_db()
    basis = cur_basis()
    open_anoms = db.execute(
        "SELECT anomaly_type t, COUNT(*) n FROM anomalies WHERE status='open' AND basis=? "
        "GROUP BY 1 ORDER BY n DESC", (basis,)).fetchall()
    total_open = sum(r["n"] for r in open_anoms)
    last_batch = db.execute("SELECT * FROM batches ORDER BY batch_date DESC LIMIT 1").fetchone()
    rate = round(100 * last_batch["collected"] / last_batch["planned"], 1) if last_batch and last_batch["planned"] else 0
    below_lpp = db.execute(
        """SELECT COUNT(*) n FROM anomalies WHERE anomaly_type='below_lpp' AND status='open'
           AND basis=? AND first_seen=(SELECT MAX(batch_date) FROM batches)""", (basis,)).fetchone()["n"]
    # 待采集 = 计划（商品×包装）− 本批次 live 已采
    pending = max(0, (last_batch["planned"] or 0) - (last_batch["collected"] or 0)) if last_batch else 0
    confirmed = db.execute(
        "SELECT COUNT(*) n FROM products WHERE monitor_status='已确认'").fetchone()["n"]
    total_products = db.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    live_obs = db.execute("SELECT COUNT(*) n FROM observations WHERE visible=1").fetchone()["n"]
    latest_time = db.execute(
        "SELECT MAX(observed_time) t FROM observations WHERE visible=1").fetchone()["t"]
    changes = db.execute(
        """SELECT a.*, p.name, p.brand_group, o.observed_time
           FROM anomalies a JOIN products p USING(product_id)
           LEFT JOIN observations o ON o.obs_id = a.obs_id
           WHERE a.anomaly_type IN ('price_down','price_up','new_listing','delisted')
             AND a.status='open' AND a.basis=?
           ORDER BY a.first_seen DESC, p.source_row""", (basis,)).fetchall()
    lpp_list = db.execute(
        """SELECT a.*, p.name, p.brand_group, o.observed_time,
                  o.price AS deal_price_now, o.list_price,
                  COALESCE(o.evidence,
                    (SELECT o2.evidence FROM observations o2
                      WHERE o2.product_id=a.product_id AND o2.pack_type=a.pack_type
                        AND o2.evidence LIKE 'evidence/%' AND o2.visible=1
                      ORDER BY o2.observed_at DESC LIMIT 1)) AS evidence
           FROM anomalies a JOIN products p USING(product_id)
           LEFT JOIN observations o ON o.obs_id = a.obs_id
           WHERE a.anomaly_type='below_lpp' AND a.status='open' AND a.basis=?
           AND a.first_seen=(SELECT MAX(batch_date) FROM batches) ORDER BY a.diff""", (basis,)).fetchall()

    # 两套口径的破价数对照，方便一眼看出切换的影响
    cmp_rows = db.execute(
        """SELECT basis, COUNT(*) n FROM anomalies
           WHERE anomaly_type='below_lpp' AND status='open'
             AND first_seen=(SELECT MAX(batch_date) FROM batches) GROUP BY 1""").fetchall()
    cmp_txt = " · ".join(f"{BASIS_CN.get(r['basis'], r['basis'])}规则 {r['n']} 条" for r in cmp_rows) or "无"

    chips = " ".join(f'<span class="tag high">{ANOM_CN.get(r["t"], r["t"])} {r["n"]}</span>' for r in open_anoms)
    change_rows = "".join(
        f"""<tr><td>{c['first_seen']}</td><td>{fmt_time(c['observed_time'])}</td>
        <td>{c['brand_group']}</td><td>{c['name']}</td>
        <td>{PACK_CN.get(c['pack_type'], c['pack_type'])}</td>
        <td><span class="tag {c['severity']}">{ANOM_CN.get(c['anomaly_type'], c['anomaly_type'])}</span></td>
        <td class="num-r {'down' if c['anomaly_type']=='price_down' else ''}">{c['trigger_price'] if c['trigger_price'] is not None else '—'}</td>
        <td class=muted>{c['rule_ref'] or ''}</td></tr>""" for c in changes)
    lpp_rows = "".join(
        f"""<tr><td>{c['first_seen']}</td><td>{fmt_time(c['observed_time'])}</td><td>{c['brand_group']}</td><td>{c['name']}</td>
        <td>{PACK_CN.get(c['pack_type'], c['pack_type'])}</td>
        <td class="num-r down">{c['trigger_price']:g}</td>
        <td class=num-r>{f"{c['deal_price_now']:g}" if c['deal_price_now'] is not None else '—'}</td>
        <td class=num-r>{f"{c['list_price']:g}" if c['list_price'] is not None else '—'}</td>
        <td class=muted>{c['rule_ref']}</td>
        <td class="num-r down">{c['diff']:g}</td><td>{ev_link(c['evidence'])}</td></tr>""" for c in lpp_list)

    return page(f"""
    <div class=banner>当前破价口径：<b>{BASIS_NOTE.get(basis, basis)}</b>。
      两条规则同时计算、可右上角切换（口径不同结论可能不同，例如同一 SKU 到手价破价、划线价不破）。
      本批次对照：{cmp_txt}。</div>
    <div class=cards>
      <div class="card {'alert' if total_open else 'good'}"><div class=num>{total_open}</div><div class=lbl>开放异常（{BASIS_CN.get(basis)}口径）</div></div>
      <div class="card {'alert' if below_lpp else 'good'}"><div class=num>{below_lpp}</div><div class=lbl>最新批次破价(LPP)</div></div>
      <div class=card><div class=num>{rate}%</div><div class=lbl>最近批次完成率（{last_batch['batch_date']}）</div></div>
      <div class=card><div class=num style="font-size:16px">{fmt_time(latest_time, '尚未开始采集')}</div><div class=lbl>最近一条采集时间</div></div>
      <div class=card><div class=num>{confirmed}/{total_products}</div><div class=lbl>商品已确认（店铺+四要素）</div></div>
      <div class="card {'alert' if pending else 'good'}"><div class=num>{pending}</div><div class=lbl>待采集（计划口径）</div></div>
    </div>
    <section><h2>异常构成（开放中 · {BASIS_CN.get(basis)}口径）</h2>{chips or '<span class=muted>无</span>'}</section>
    <section><h2>最新批次 · 低于 LPP（破价）</h2>
      <table><tr><th>监测日期</th><th>监测时间</th><th>品牌</th><th>商品</th><th>包装</th>
      <th>触发价（{BASIS_CN.get(basis)}）</th><th>到手价</th><th>划线价</th><th>基准</th><th>差额</th><th>证据</th></tr>
      {lpp_rows or '<tr><td colspan=11 class=muted>无</td></tr>'}</table></section>
    <section><h2>价格与在售变化</h2>
      <table><tr><th>监测日期</th><th>监测时间</th><th>品牌</th><th>商品</th><th>包装</th><th>类型</th><th>价格</th><th>对比</th></tr>
      {change_rows or '<tr><td colspan=8 class=muted>无</td></tr>'}</table></section>
    <p class=muted><b>数据边界</b>：本页只显示本人亲自采集的 live 记录（共 {live_obs} 条）。
    0907 表的价格是任务输入、不是证据，已全部封存，不在本页出现；
    LPP / 零售价来自 0907 输入，属于<b>判断基准</b>，不是监测价。</p>
    """, basis)


@app.route("/products")
def products():
    db = get_db()
    basis = cur_basis()
    rows = db.execute("""
    WITH latest AS (
      SELECT o.* FROM observations o
      WHERE o.observed_at = (SELECT MAX(batch_date) FROM batches) AND o.visible = 1
    )
    SELECT p.product_id, p.brand_group, p.name, p.item_type, p.shop, p.monitor_status,
           l.pack_type, l.sku_name, l.price, l.list_price, l.jd_title, l.status, l.observed_time,
           r.retail_price,
           CASE l.pack_type WHEN 'single' THEN r.lpp_single WHEN 'double' THEN r.lpp_double
                            WHEN 'case' THEN r.lpp_case END AS lpp,
           (SELECT a.basis FROM anomalies a
             WHERE a.product_id=p.product_id AND a.pack_type=l.pack_type
               AND a.anomaly_type='below_lpp' AND a.basis=? AND a.status='open'
             LIMIT 1) AS below_bases
    FROM products p
    LEFT JOIN latest l ON l.product_id = p.product_id
    LEFT JOIN price_rules r ON r.product_id = p.product_id
    ORDER BY p.source_row, l.pack_type""", (basis,)).fetchall()
    # 另一口径也查一遍，用于标注「另一口径是否也破价」
    other = "list" if basis == "deal" else "deal"
    other_hit = {(r["product_id"], r["pack_type"]) for r in db.execute(
        "SELECT product_id, pack_type FROM anomalies WHERE anomaly_type='below_lpp' AND basis=? AND status='open'",
        (other,)).fetchall()}
    last_batch = db.execute("SELECT * FROM batches ORDER BY batch_date DESC LIMIT 1").fetchone()
    body_rows = ""
    for r in rows:
        cur_price = r["price"] if basis == "deal" else r["list_price"]
        flag = ""
        if cur_price is not None and r["lpp"] is not None and cur_price < r["lpp"]:
            flag = ' class=down'
        price = f"{cur_price:g}" if cur_price is not None else "—"
        wd = f"{r['price']:g}" if r["price"] is not None else "—"
        wl = f"{r['list_price']:g}" if r["list_price"] is not None else "—"
        mark = ""
        if r["below_bases"]:
            mark = ' <span class="tag high">破价</span>'
        elif (r["product_id"], r["pack_type"]) in other_hit:
            mark = f' <span class="tag info">{BASIS_CN.get(other)}破</span>'
        pack = PACK_CN.get(r["pack_type"], r["pack_type"] or "")
        if r["sku_name"]:
            pack += f'<span class=muted> · {r["sku_name"]}</span>'
        jt = f'<div class="mono muted" title="{r["jd_title"] or ""}">{(r["jd_title"] or "")[:34]}{"…" if r["jd_title"] and len(r["jd_title"]) > 34 else ""}</div>' if r["jd_title"] else ""
        body_rows += f"""<tr><td>{r['product_id']}</td><td>{r['brand_group']}</td>
        <td><a href="/product/{r['product_id']}">{r['name']}</a>{' <span class="tag info">组合</span>' if r['item_type']=='combo' else ''}{jt}</td>
        <td>{pack}{mark}</td>
        <td class=muted>{r['shop'] or ''}</td>
        <td class="tag {'info' if r['monitor_status']!='已确认' else 'low'}">{MATCH_CN.get(r['monitor_status'], r['monitor_status'] or '')}</td>
        <td class=num-r>{r['retail_price'] if r['retail_price'] is not None else ''}</td>
        <td class=num-r>{r['lpp'] if r['lpp'] is not None else ''}</td>
        <td class="num-r"{flag}>{price}</td>
        <td class=num-r>{wd}</td><td class=num-r>{wl}</td>
        <td class=muted>{STATUS_CN.get(r['status'], r['status'] or '')}</td>
        <td>{fmt_time(r['observed_time'], '未采集')}</td></tr>"""
    return page(f"""<h2>商品清单（最新 live 批次价格）</h2>
    <div class=banner>当前口径 <b>{BASIS_NOTE.get(basis, basis)}</b>：
      「监测价」列 = 该口径的价格，红色 = 该口径判破价；「原口径破」标签表示另一口径下也破价（切换即可见）。
      到手价 / 划线价两列始终同时展示，便于对照。</div>
    <p class=muted>批次 {last_batch['batch_date']} · 最近采集 {last_batch['finished_at'] or '尚未开始'} ·
    本页只显示本人亲自采集的 live 记录；未采集的格子为空（不填 0907 的价格）。</p>
    <table><tr><th>ID</th><th>品牌</th><th>商品 / 京东原始标题</th><th>包装 / SKU</th><th>店铺</th><th>商品确认</th>
    <th>零售价</th><th>LPP</th><th>监测价（{BASIS_CN.get(basis)}）</th><th>到手价</th><th>划线价</th><th>状态</th><th>监测时间</th></tr>
    {body_rows}</table>""", basis)


@app.route("/product/<pid>")
def product_detail(pid):
    db = get_db()
    basis = cur_basis()
    p = db.execute("SELECT * FROM products WHERE product_id=?", (pid,)).fetchone()
    if not p:
        return page("<p>商品不存在</p>"), 404
    hist = db.execute(
        "SELECT * FROM observations WHERE product_id=? AND visible=1 "
        "ORDER BY observed_at, pack_type", (pid,)).fetchall()
    anoms = db.execute(
        "SELECT * FROM anomalies WHERE product_id=? ORDER BY basis, first_seen", (pid,)).fetchall()
    members = db.execute("SELECT * FROM combo_members WHERE combo_id=?", (pid,)).fetchall()
    pkgs = db.execute("SELECT * FROM packages WHERE product_id=? ORDER BY pack_type", (pid,)).fetchall()
    hrows = "".join(
        f"""<tr><td>{h['observed_at']}</td><td>{fmt_time(h['observed_time'], '未采集')}</td>
        <td>{PACK_CN.get(h['pack_type'], h['pack_type'])}</td>
        <td>{h['sku_name'] or '<span class=muted>—</span>'}</td>
        <td>{h['shop'] or '<span class=muted>—</span>'}</td>
        <td class=num-r>{f"{h['price']:g}" if h['price'] is not None else '—'}</td>
        <td class=num-r>{f"{h['list_price']:g}" if h['list_price'] is not None else '—'}</td>
        <td>{STATUS_CN.get(h['status'], h['status'])}</td><td>{ev_link(h['evidence'])}</td></tr>"""
        for h in hist)
    arows = "".join(
        f"""<tr><td>{a['first_seen']}</td>
        <td><span class="tag {'high' if a['basis']=='deal' else 'info'}">{BASIS_CN.get(a['basis'], a['basis'])}规则</span></td>
        <td><span class="tag {a['severity']}">{ANOM_CN.get(a['anomaly_type'], a['anomaly_type'])}</span></td>
        <td class=num-r>{f"{a['trigger_price']:g}" if a['trigger_price'] is not None else '—'}</td>
        <td class=muted>{a['rule_ref'] or ''}</td>
        <td class=num-r>{f"{a['diff']:g}" if a['diff'] is not None else ''}</td><td>{a['status']}</td></tr>""" for a in anoms)
    mrows = "".join(f"<li>{m['member_name']}</li>" for m in members)
    prows = "".join(
        f"""<tr><td>{PACK_CN.get(k['pack_type'], k['pack_type'])}</td>
        <td>{k['sku_name'] or '<span class=muted>—</span>'}</td>
        <td>{k['existence']}</td><td class=muted>{k['source'] or ''}</td></tr>""" for k in pkgs)
    jt = hist[0]["jd_title"] if hist and hist[0]["jd_title"] else None
    return page(f"""<h2>{p['name']} <span class=muted>{p['product_id']} · {p['brand_group']}</span></h2>
    <p>商品确认：<span class="tag {'info' if p['monitor_status']!='已确认' else 'low'}">{p['monitor_status']}</span>
       · 店铺：<b>{p['shop']}</b></p>
    <p class=muted><b>京东原始标题（保险列）</b>：<span class=mono>{jt or '（未采到）'}</span></p>
    {'<h2>组合成员</h2><ul>' + mrows + '</ul>' if mrows else ''}
    <h2>包装 / SKU 清单（0907 口径 + 京东 SKU 面板实采）</h2>
    <table><tr><th>包装类型</th><th>SKU 原名</th><th>存在性</th><th>来源</th></tr>{prows}</table>
    <h2>价格历史（仅 live 采集）</h2>
    <table><tr><th>监测日期</th><th>监测时间</th><th>包装</th><th>SKU 原名</th><th>店铺</th>
    <th>到手价</th><th>划线价</th><th>状态</th><th>证据</th></tr>{hrows or '<tr><td colspan=9 class=muted>尚无 live 记录</td></tr>'}</table>
    <h2>异常记录（两套口径都在）</h2>
    <table><tr><th>日期</th><th>口径</th><th>类型</th><th>触发价</th><th>规则</th><th>差额</th><th>状态</th></tr>
    {arows or '<tr><td colspan=7 class=muted>无</td></tr>'}</table>
    <p class=muted>0907 表的价格不在本页出现（它是任务输入，不是证据）；LPP 基准来自 0907 输入，见商品清单页。</p>""", basis)


@app.route("/anomalies")
def anomalies():
    db = get_db()
    basis = cur_basis()
    status = request.args.get("status", "open")
    rows = db.execute(
        """SELECT a.*, p.name, p.brand_group, o.observed_time, o.channel, o.shop, o.sku_name,
                  o.price AS deal_price_now, o.list_price,
                  COALESCE(o.evidence,
                    (SELECT o2.evidence FROM observations o2
                      WHERE o2.product_id=a.product_id AND o2.pack_type=a.pack_type
                        AND o2.evidence LIKE 'evidence/%' AND o2.visible=1
                      ORDER BY o2.observed_at DESC LIMIT 1)) AS evidence
           FROM anomalies a JOIN products p USING(product_id)
           LEFT JOIN observations o ON o.obs_id = a.obs_id
           WHERE a.status=? AND a.basis=?
           ORDER BY a.severity='high' DESC, a.first_seen DESC, p.source_row""",
        (status, basis)).fetchall()
    body_rows = "".join(
        f"""<tr><td>{a['first_seen']}</td><td>{fmt_time(a['observed_time'], '未采集')}</td>
        <td><span class="tag {a['severity']}">{ANOM_CN.get(a['anomaly_type'], a['anomaly_type'])}</span></td>
        <td>{a['brand_group']}</td><td><a href="/product/{a['product_id']}">{a['name']}</a></td>
        <td>{PACK_CN.get(a['pack_type'], a['pack_type'])}{'<span class=muted> · ' + a['sku_name'] + '</span>' if a['sku_name'] else ''}</td>
        <td class=muted>{a['shop'] or ''}</td>
        <td class="num-r {'down' if a['anomaly_type'] in ('below_lpp','price_down') else ''}">{f"{a['trigger_price']:g}" if a['trigger_price'] is not None else '—'}</td>
        <td class=num-r>{f"{a['deal_price_now']:g}" if a['deal_price_now'] is not None else '—'}</td>
        <td class=num-r>{f"{a['list_price']:g}" if a['list_price'] is not None else '—'}</td>
        <td class=muted>{a['rule_ref'] or ''}</td><td class=num-r>{f"{a['diff']:g}" if a['diff'] is not None else ''}</td>
        <td>{ev_link(a['evidence'])}</td><td>{a['status']}</td></tr>""" for a in rows)
    counts = db.execute(
        "SELECT basis, COUNT(*) n FROM anomalies WHERE status=? GROUP BY 1", (status,)).fetchall()
    cnt_txt = " · ".join(f"{BASIS_CN.get(r['basis'], r['basis'])}规则 {r['n']} 条" for r in counts) or "无"
    return page(f"""<h2>异常队列（{status} · {BASIS_CN.get(basis)}口径）</h2>
    <div class=banner>口径：<b>{BASIS_NOTE.get(basis, basis)}</b>。两条破价规则同时存在，右上角可切换；
      当前状态下两套规则分别是：{cnt_txt}。</div>
    <p><a href="/anomalies?status=open&basis={basis}">开放</a> ·
       <a href="/anomalies?status=resolved&basis={basis}">已处理</a> ·
       <a href="/anomalies?status=closed&basis={basis}">已关闭</a></p>
    <p class=muted>异常只由 live 采集数据生成（0907 派生的异常已清空）。</p>
    <table><tr><th>发现日期</th><th>监测时间</th><th>类型</th><th>品牌</th><th>商品</th><th>包装 / SKU</th><th>店铺</th>
    <th>触发价</th><th>到手价</th><th>划线价</th><th>规则</th><th>差额</th><th>证据</th><th>状态</th></tr>
    {body_rows or '<tr><td colspan=14 class=muted>无</td></tr>'}</table>""", basis)


@app.route("/pending")
def pending():
    """待采集清单：计划口径（商品×包装）− live 已采。0907 只作「线索」列，不显示价格。"""
    db = get_db()
    batch = db.execute("SELECT MAX(batch_date) AS d FROM batches").fetchone()["d"]
    rows = db.execute(
        """
        SELECT p.product_id, p.brand_group, p.name, p.shop, p.monitor_status,
               k.pack_type, k.sku_name, k.existence,
               (SELECT o.status FROM observations o
                 WHERE o.product_id=p.product_id AND o.pack_type=k.pack_type
                   AND o.method='人工录入(0907表)' AND o.price IS NOT NULL LIMIT 1) AS legacy_has_price,
               (SELECT o.status FROM observations o
                 WHERE o.product_id=p.product_id AND o.pack_type=k.pack_type
                   AND o.method='人工录入(0907表)' AND o.price IS NULL LIMIT 1) AS legacy_status
        FROM packages k JOIN products p ON p.product_id = k.product_id
        WHERE NOT EXISTS (
            SELECT 1 FROM observations o2
             WHERE o2.product_id=p.product_id AND o2.pack_type=k.pack_type
               AND o2.visible=1 AND o2.observed_at=?)
        ORDER BY p.source_row, k.pack_type
        """, (batch,)).fetchall()
    body_rows = "".join(
        f"""<tr><td>{r['brand_group']}</td><td><a href="/product/{r['product_id']}">{r['name']}</a></td>
        <td>{PACK_CN.get(r['pack_type'], r['pack_type'])}{'<span class=muted> · ' + r['sku_name'] + '</span>' if r['sku_name'] else ''}</td>
        <td class="tag {'info' if r['monitor_status']!='已确认' else 'low'}">{r['monitor_status']}</td>
        <td class=muted>{r['shop'] or ''}</td>
        <td class=muted>{'0907有报价（任务输入线索）' if r['legacy_has_price'] else (STATUS_CN.get(r['legacy_status'], r['legacy_status']) if r['legacy_status'] else '0907无记录')}</td>
        <td>{r['existence']}</td></tr>""" for r in rows)
    return page(f"""<h2>待采集清单（计划 {len(rows)} 条，live 未覆盖）</h2>
    <p class=muted>口径：packages 计划（0907 三列 + 京东 SKU 面板）× 尚未有 live 记录的条目。
    最后一列是 <b>0907 任务输入线索</b>——只说明当年有没有记过价/是否空白，<b>不显示 0907 的价格</b>，
    也不是证据；采集完成后由 live 记录覆盖。</p>
    <table><tr><th>品牌</th><th>商品</th><th>包装 / SKU</th><th>商品确认</th><th>店铺</th><th>0907 线索（非证据）</th><th>存在性</th></tr>
    {body_rows or '<tr><td colspan=7 class=muted>全部已覆盖</td></tr>'}</table>""")


@app.route("/evidence-gallery")
def evidence_gallery():
    db = get_db()
    pics = []
    if EVIDENCE_DIR.exists():
        pics = [f for f in sorted(EVIDENCE_DIR.rglob("*"))
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    groups = {}
    for f in pics:
        rel = f.relative_to(EVIDENCE_DIR).as_posix()
        groups.setdefault(rel.split("/")[0], []).append(rel)
    with_pic = db.execute(
        "SELECT COUNT(*) n FROM observations WHERE visible=1 AND evidence LIKE 'evidence/%'").fetchone()["n"]
    with_price = db.execute(
        "SELECT COUNT(*) n FROM observations WHERE visible=1 AND status='ok'").fetchone()["n"]
    blocks = ""
    for day, rels in sorted(groups.items(), reverse=True):
        figs = "".join(
            f'<figure><a href="/evidence/{quote(r)}" target="_blank">'
            f'<img src="/evidence/{quote(r)}" loading="lazy"></a>'
            f'<figcaption>{r.rsplit("/", 1)[-1]}</figcaption></figure>' for r in rels)
        blocks += f"<h2>{day} · {len(rels)} 张</h2><div class=gallery>{figs}</div>"
    return page(f"""<h2>证据截图库</h2>
    <div class=cards>
      <div class=card><div class=num>{len(pics)}</div><div class=lbl>磁盘截图文件</div></div>
      <div class="card {'good' if with_pic else 'alert'}"><div class=num>{with_pic}</div><div class=lbl>带截图证据的 live 记录</div></div>
      <div class="card {'alert' if with_price - with_pic > 0 else 'good'}"><div class=num>{with_price - with_pic}</div><div class=lbl>有报价但缺截图（须返工）</div></div>
    </div>
    <p class=muted>截图按「日期/文件名」落盘，网页表格里的证据为文本链接，悬停即出图；点击在新标签打开原图。</p>
    {blocks or '<p class=muted>还没有截图。0920 采集时每条记录都要落一张。</p>'}""")


@app.route("/export.xlsx")
def export_xlsx():
    from export_excel import export
    date = request.args.get("date")
    basis = cur_basis()
    suffix = f"_{BASIS_CN.get(basis)}口径"
    out = ROOT / "exports" / f"拉菲旗舰店监测明细_{date or '全部'}{suffix}.xlsx"
    if not out.parent.exists():
        out.parent.mkdir()
    export(out, date, basis=basis)
    return send_file(out, as_attachment=True, download_name=out.name)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8321, debug=False)
