#!/usr/bin/env python3
"""从库里生成采集规格 spec.json —— 决定每个商品在店内用什么关键词搜、靠哪些词判定卡片。

原则（对应 docs/监控计划-0920.md 规则②）：关键词用**完整商品名**（去掉京东不用的
「源自拉菲罗斯柴尔德」这种表内全称缀），绝不用「拉菲」这种泛词。
匹配改用组合锚点：品牌家族 + 品种 + 系列/年份限定词；同时排除会误命中同店其他品牌
（传奇/波尔多/组合装）的关键词。
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "sentinel.db"

VARIETALS = ["赤霞珠", "霞多丽", "西拉", "桃红", "梅洛", "长相思", "品丽珠",
             "味而多", "赛美蓉", "白诗南", "维欧尼", "歌海娜", "慕合怀特", "佳美娜"]
SERIES = ["十世", "科洛", "珍藏", "精选", "酿酒师", "传奇", "奥希耶", "乐王吉",
          "杜哈米隆", "威廉费尔", "凯萨天堂", "利诗", "凯洛", "雾禾", "珍宝",
          "大师", "古堡", "正牌", "副牌", "小拉菲", "巴斯克"]
# 排除词只用来挡「同店但别的品牌/别的商品」，绝不放包装词——
# 京东卡片标题天然带「单支/整箱」这类 SKU 标识，之前误把它们当负分，把正确卡片扣成 1 分。
NOISE = ["传奇", "波尔多", "组合", "迷你", "187ml", "古堡", "奥希耶", "乐王吉",
         "杜哈米隆", "威廉费尔", "凯萨天堂", "利诗", "雾禾", "珍宝"]


def shoppable_keyword(name: str) -> str:
    kw = name.replace("源自拉菲罗斯柴尔德", "")
    kw = re.sub(r"（[^）]*）", "", kw)
    kw = re.sub(r"\([^)]*\)", "", kw)
    return kw.strip()


def keyword_ladder(name: str) -> list[str]:
    """关键词阶梯：先全名，搜不到就逐级放宽。

    为什么需要：京东店内搜索对**过长的名字**直接 0 结果（P0025
    「巴斯克瞰御Caneten红葡萄酒2022」实测空手而归），但只要把年份/品类词/
    拉丁名一步步摘掉，就能命中同一件商品。阶梯只放宽「搜索词」，
    判定仍靠 tokens + exclude，不会因此抓到别的商品。
    """
    base = shoppable_keyword(name)
    steps = [base]
    no_year = re.sub(r"(19|20)\d{2}\s*年?", "", base).strip()
    steps.append(no_year)
    no_kind = re.sub(r"(桃红|红|白)?葡萄酒", "", no_year).strip()
    steps.append(no_kind)
    no_latin = re.sub(r"[A-Za-z]+", "", no_kind).strip()
    steps.append(no_latin)
    for word in ("巴斯克", "拉菲"):
        if word in name:
            steps.append(word)
            break
    out, seen = [], set()
    for step in steps:
        step = step.strip()
        if len(step) < 2 or step in seen:
            continue
        seen.add(step)
        out.append(step)
    return out


def tokens_for(name: str) -> list[str]:
    out = []
    house_marker = "源自拉菲罗斯柴尔德" in name
    for word in ["巴斯克", "拉菲"]:
        if word in name:
            # 「源自拉菲罗斯柴尔德」里的「拉菲」只是家族前缀，京东卡片标题未必带，
            # 不能当必需词（否则 P0030 传奇玫瑰这类必然选不出卡）。巴斯克是子品牌，照旧必需。
            if word == "拉菲" and house_marker:
                break
            out.append(word)
            break
    for var in VARIETALS:
        if var in name:
            out.append(var)
            break
    for series in SERIES:
        if series in name and series not in out:
            out.append(series)
    # 加「名字里独有、京东标题也会带」的中文串（如 瞰御 / 惟乐 / 白鹭），
    # 提高必需词的信息量——不然只剩「巴斯克」一个词，宽关键词下会选错卡。
    residual = shoppable_keyword(name)
    for word in ("巴斯克", "拉菲", "源自", "罗斯柴尔德"):
        residual = residual.replace(word, "")
    for var in VARIETALS:
        residual = residual.replace(var, "")
    for series in SERIES:
        residual = residual.replace(series, "")
    residual = re.sub(r"(桃红|红|白)?葡萄酒", "", residual)
    residual = re.sub(r"[A-Za-z0-9年月]", "", residual)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,4}", residual):
        if chunk not in out:
            out.append(chunk)
    return out


def excludes_for(name: str) -> list[str]:
    return [word for word in NOISE if word not in name]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default=None, help="source_row 区间，如 3-13；默认全部未确认")
    ap.add_argument("--out", default=str(ROOT / "payloads" / "spec_lafei.json"))
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    sql = "SELECT product_id, name, brand_group, source_row FROM products"
    params = []
    if args.rows:
        low, _, high = args.rows.partition("-")
        sql += " WHERE source_row BETWEEN ? AND ?"
        params = [int(low), int(high) if high else int(low)]
    sql += " ORDER BY source_row"

    items = []
    for row in conn.execute(sql, params):
        name = row["name"]
        ladder = keyword_ladder(name)
        items.append({
            "pid": row["product_id"],
            "name": name,
            "brand_group": row["brand_group"],
            "keyword": ladder[0],
            "keywords": ladder,
            "tokens": tokens_for(name),
            "exclude": excludes_for(name),
        })
    conn.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {out} —— {len(items)} 个商品")
    for item in items[:14]:
        print(f"  {item['pid']}  tokens={item['tokens']}")
        print(f"        阶梯={' → '.join(item['keywords'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
