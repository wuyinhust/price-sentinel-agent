# 价格哨兵 · 拉菲旗舰店监测实例（lafei-watch）

> 本目录是 [price-sentinel-agent](../README.md) 通用框架之上的**实战实例**：
> 针对「拉菲罗斯柴尔德葡萄酒官方旗舰店」的京东 App 全自动价格采集与破价监控。
> 代码独立演化（平铺结构），与根目录 `src/pricesentinel/` 包是兄弟关系，不互相 import。

## 三条铁律

1. **数据库是主记录，Excel 只是导出格式。**
2. 网页与导出里出现的**每一个价格**，必须：①亲自在京东 App 采到；②带截图证据与秒级时间戳；③经离线复核通过。
3. 历史表（0903/0907）只是任务输入与判断基准，**不是证据**，不上网页、不进导出。

## 目录结构

```
lafei-watch/
├── src/
│   ├── schema.sql          # 建库脚本（products/combo_members/packages/price_rules/observations/anomalies/batches/settings 八表）
│   ├── seed_import.py      # 种子导入（幂等）
│   ├── collect_driver.py   # 全自动采集驱动器（店内搜索 → 详情页 → SKU 面板，~950 行）
│   ├── record.py           # payload → 写库（--dry-run / --force）
│   ├── rules.py            # 双口径规则引擎（到手价 deal / 划线价 list 各存一行异常）
│   ├── migrate.py          # 幂等迁移
│   ├── export_excel.py     # 21 列 Excel 导出，按口径出两版
│   ├── app.py              # Web 监控台 http://127.0.0.1:8321（右上角切口径）
│   └── capture.py          # 截图归档
├── tools/
│   ├── hx                  # android-harness 包装器
│   ├── look.sh             # 截图+缩图
│   ├── grid.py             # 截图叠坐标网格
│   ├── cn.sh               # ADBKeyboard 中文注入（--restore 还原）
│   ├── make_spec.py        # 生成采集规格（关键词阶梯/必需词/排除词）
│   └── run_collect.sh      # 采集启动包装
├── docs/
│   ├── 监控计划-0920.md        # 数据边界、四要素、验收标准
│   ├── 使用指南-iPhone与安卓.md  # 双平台用法 + 报错 recipe（A1–A19 / I1–I10）
│   ├── 京东风控避坑指南.md      # 三级升级模型 + 行为指纹 + 熔断实现
│   └── 风控复盘-0921.md        # 2026-09-21 凌晨实战复盘（铁证与代价账）
└── seed/seed_0907.csv      # 商品清单种子（任务输入）
```

运行产物（`data/ evidence/ logs/ state/ exports/ backup/ payloads/`）在本机，不入库（见根 `.gitignore`）。

## 快速开始

```bash
# 1. 建库 + 导种子
python3 src/seed_import.py

# 2. 起监控台
/Users/wangyao/.workbuddy/binaries/python/envs/default/bin/python src/app.py
# → http://127.0.0.1:8321

# 3. 生成采集规格并开跑（先灰度 10 个）
python3 tools/make_spec.py
bash tools/run_collect.sh
```

双平台采集的完整用法与报错 recipe 见 **[docs/使用指南-iPhone与安卓.md](docs/使用指南-iPhone与安卓.md)**；
**iPhone 侧统一使用 [zhongerxin/iPhone-use](https://github.com/zhongerxin/iPhone-use)**，本目录不维护 iOS 实现。

风控是这条链路上最贵的失败模式，开跑前必读 **[docs/京东风控避坑指南.md](docs/京东风控避坑指南.md)**。
