-- 价格哨兵 · SQLite schema v1
-- 原则：数据库是主记录，Excel 只是导出格式；监测记录追加式，不覆盖历史。

PRAGMA journal_mode = WAL;

-- 商品主表
CREATE TABLE IF NOT EXISTS products (
    product_id   TEXT PRIMARY KEY,          -- P0001...
    brand_group  TEXT NOT NULL,             -- 品牌/组合组（0907 A列）
    name         TEXT NOT NULL,
    vintage      TEXT,                      -- 年份（从名称解析）
    volume       TEXT,                      -- 容量（如 187ml / 1.5升）
    version      TEXT,                      -- 版本（如 新标/旧标/限定版）
    item_type    TEXT NOT NULL DEFAULT 'single',  -- single | combo
    shop         TEXT NOT NULL DEFAULT '拉菲罗斯柴尔德葡萄酒官方旗舰店',  -- 商品店铺（本任务统一官方旗舰店）
    monitor_status TEXT NOT NULL DEFAULT '待确认',  -- 待确认 | 已确认 | 名称不符
    is_active    INTEGER NOT NULL DEFAULT 1,      -- 是否在售（1/0）
    source_row   INTEGER,                   -- 0907 表行号（溯源）
    note         TEXT
);

-- 组合装成员表（item_type=combo 的商品）
CREATE TABLE IF NOT EXISTS combo_members (
    combo_id     TEXT NOT NULL REFERENCES products(product_id),
    member_name  TEXT NOT NULL,             -- 组合内商品名称（0907 原始行）
    member_product_id TEXT REFERENCES products(product_id),  -- 能对应到单品则关联
    qty          INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (combo_id, member_name)
);

-- 包装与组合表（SKU 维度：0907 三列口径 + 京东页面实际 SKU）
CREATE TABLE IF NOT EXISTS packages (
    package_id   TEXT PRIMARY KEY,          -- PK0001...
    product_id   TEXT NOT NULL REFERENCES products(product_id),
    pack_type    TEXT NOT NULL,             -- single | double | case | combo_pack | other(表外SKU)
    sku_name     TEXT,                      -- 京东商品页「共 x 款」里的原始 SKU 名
    pack_qty     INTEGER NOT NULL DEFAULT 1,
    existence    TEXT NOT NULL DEFAULT 'unconfirmed',  -- confirmed | unconfirmed | none
    source       TEXT,                      -- 0907三列 | 京东SKU面板实采
    UNIQUE (product_id, pack_type, sku_name)
);

-- 价格规则表（每个商品一行：零售价 + 三类 LPP 基准）
CREATE TABLE IF NOT EXISTS price_rules (
    rule_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     TEXT NOT NULL REFERENCES products(product_id),
    retail_price   REAL,                    -- 零售价/瓶
    lpp_single     REAL,                    -- 单瓶 LPP
    lpp_double     REAL,                    -- 双支礼盒 LPP
    lpp_case       REAL,                    -- 整箱 LPP
    price_condition TEXT NOT NULL DEFAULT '公开页面价',  -- 公开价/券后价/会员价/活动价
    effective_from TEXT NOT NULL,           -- 规则生效日期
    source         TEXT,                    -- 规则来源（如 0907表）
    UNIQUE (product_id, effective_from)
);

-- 监测记录表（追加式，不覆盖）
CREATE TABLE IF NOT EXISTS observations (
    obs_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at  TEXT NOT NULL,             -- 监测日期 YYYY-MM-DD
    observed_time TEXT,                     -- 监测时间戳 ISO8601 含时区，如 2026-09-20T21:18:32+08:00
                                            --   0907 历史表只到日期，故为空（页面显示「历史表仅到日」）
    product_id   TEXT NOT NULL REFERENCES products(product_id),
    pack_type    TEXT NOT NULL,             -- single | double | case | combo_pack | other
    sku_name     TEXT,                      -- 京东页面原始 SKU 名（防扯皮：连同 SKU 原名一起记）
    price        REAL,                      -- **到手价**（成交价，主口径）；空=未采到
    list_price   REAL,                      -- 划线价（京东价/原价）。两条破价规则同时存在：
                                            --   到手价规则用 price，划线价规则用 list_price，网页可切换
    deal_price   REAL,                      -- 兼容保留：等价于 price（到手价），历史列
    status       TEXT NOT NULL,             -- ok | out_of_stock | not_listed | not_monitored | no_such_pack | unrecognized | blank_legacy
    shop         TEXT NOT NULL DEFAULT '未记录',  -- 商品店铺（本任务=拉菲罗斯柴尔德葡萄酒官方旗舰店）
    jd_title     TEXT,                      -- 京东商品页原始标题（保险列：每次采集原样留档，用于核对是不是同一个商品）
    channel      TEXT,                      -- 京东App / 天猫 / ...
    url          TEXT,                      -- 商品链接（或 /sku 链接）
    evidence     TEXT,                      -- 截图/证据路径或说明
    method       TEXT,                      -- 采集方式：p30-harness / 人工 / 人工录入(0907表)
    confidence   TEXT,                      -- 高/中/低
    visible      INTEGER NOT NULL DEFAULT 1,  -- 1=可上网页（本人亲采的live数据）；0=封存（0907输入/店铺不符）
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_date ON observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_prod ON observations(product_id, pack_type, observed_at);

-- 异常表
-- basis：判断口径。破价规则双口径同时存在：deal=到手价规则（默认）、list=划线价规则。
--        网页与导出按 basis 切换查看；两条规则的异常各存一行，互不覆盖。
CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    anomaly_type TEXT NOT NULL,   -- below_lpp | price_down | price_up | new_listing | delisted | pack_changed | unverifiable | insufficient_evidence
    product_id   TEXT NOT NULL REFERENCES products(product_id),
    pack_type    TEXT NOT NULL,
    basis        TEXT NOT NULL DEFAULT 'deal',   -- deal（到手价口径） | list（划线价口径）
    trigger_price REAL,
    rule_ref     TEXT,            -- 对比规则描述（如 LPP单瓶=269）
    diff         REAL,            -- 差额（触发价-基准，负=低于基准）
    severity     TEXT NOT NULL DEFAULT 'medium',  -- high | medium | low | info
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',    -- open | acknowledged | resolved | closed
    assignee     TEXT,
    resolution   TEXT,
    closed_at    TEXT,
    obs_id       INTEGER REFERENCES observations(obs_id),
    UNIQUE (anomaly_type, product_id, pack_type, first_seen, basis)
);

-- 全局设置（口径默认值等）
CREATE TABLE IF NOT EXISTS settings (
    key          TEXT PRIMARY KEY,
    value        TEXT,
    note         TEXT
);

-- 监测批次表（每次采集一个批次，追踪完成率）
CREATE TABLE IF NOT EXISTS batches (
    batch_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_date   TEXT NOT NULL UNIQUE,
    started_at   TEXT,                          -- 本批次开始时间戳
    finished_at  TEXT,                          -- 本批次结束时间戳
    planned      INTEGER NOT NULL DEFAULT 0,   -- 计划采集的 商品×包装 数
    collected    INTEGER NOT NULL DEFAULT 0,   -- 实际采到价格数
    note         TEXT
);
