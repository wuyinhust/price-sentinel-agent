# 使用指南：iPhone 与 安卓（含报错 recipe）

> 核心原则：价格与 SKU 全部从界面树解析，不用 OCR、不让模型猜。
>
> **平台选择：优先 iPhone。** iOS 侧京东风控强度低于安卓，技术指纹少。
> iPhone 统一走 [zhongerxin/iPhone-use](https://github.com/zhongerxin/iPhone-use)；
> 安卓走 `adb + android-harness`（本仓库自带），风控纪律见《京东风控避坑指南.md》。

---

## 一、iPhone 侧（zhongerxin/iPhone-use，首选）

> Codex 通过 USB 操作真实 iPhone：WebDriverAgent + 本地 MCP + 实时屏幕。
> 原理与安卓侧同构：读控件树定位，失败时看截图按坐标点。

### 1.1 安装（一段提示词交给 Codex）

```text
请帮我安装和配置 iPhone Use：
https://github.com/zhongerxin/iPhone-use

先读取仓库 README 和安装脚本，检查本机 Codex CLI、Python、Node.js、npm、
完整 Xcode，以及通过 USB 连接的 iPhone。把项目放到合适的本机目录，
运行 sh scripts/install.sh 安装插件。

如果当前聊天还没有加载新工具，明确告诉我重连或新开聊天后继续。
工具可用后读取 iphone-use-setup 技能，检查现有配置，优先复用已有 WDA。
首次配置时发现我的设备，使用我自己的 Apple 开发团队和可签名 bundle ID，
获取固定版本 WDA、配置签名、构建并启动，直到 pua_ready 返回 ready=true，
然后打开手机屏幕。不要照搬作者的设备标识或签名信息。

缺少依赖时说明具体缺项并帮助安装。Apple 账号登录、设备信任、开发者模式
或解锁需要我操作时，告诉我明确步骤，等我完成再继续。
```

依赖：完整 Xcode（仅 CLT 不够）、可签名的 Apple 账号/开发团队、USB 真机
（信任此 Mac、开开发者模式、安装期间保持解锁）、Python 3.9+、Node.js 20.19+/22.12+/24+。
WDA 固定用已验证的 16.14.0 提交，**无需越狱、无需 Appium Server**。

### 1.2 使用要点（与安卓侧对位）

| 采集步骤 | iPhone-use 做法 |
|---|---|
| 连接设备 | 新聊天启用 iPhone Use → `pua_setup(action="status")` 复用健康服务，缺服务才 start 一次 |
| 就绪判定 | 以 `pua_ready` 返回 `ready=true` 为准；start 默认最多等 20 秒，超时按同一 job 查询，不重复启动 |
| 判页/读价 | 获取紧凑控件树；仅需截图时不生成 XML |
| 连续操作 | **batch**：已知点击/输入/等待串成一次提交，减少模型往返 |
| 文本输入 | 输入与提交分开（工具默认不提交）；长文本分段输入并支持续传 |
| 读卡片列表 | 有界滚动查找、重叠读取与去重 |
| 控件找不到 | 看新截图，重新选择点击位置（工具自带引导） |
| 登录/验证码 | 一律交用户本人完成，可暂停屏幕预览 |

### 1.3 iPhone 报错 recipe

| # | 症状 | 修法 |
|---|---|---|
| I1 | `pua_ready` 一直 ready=false | `pua_setup(action="status")` 查健康；只 start 一次，超时按同一 job 查询，不要重复 start |
| I2 | WDA 装不上 / 签名报错 | 用自己的 Apple 账号与 Team，换可签名 Bundle ID；Xcode 需支持当前 iOS 版本 |
| I3 | 「不受信任的开发者」 | 手机：设置 → 通用 → VPN与设备管理 → 信任该证书 |
| I4 | 找不到设备 | 确认「信任此 Mac」、开「开发者模式」、保持解锁 |
| I5 | `Product → Test` 闪退 | Runner target 的 Signing 未选对 Team，重签后重装 |
| I6 | 控件树空 / 缺控件 | 截图回退按坐标点；长列表用滚动查找 + 重叠去重 |
| I7 | 中文输入失败/半截 | 一次给完整 Unicode；长文本分段 + 续传 |
| I8 | 屏幕预览黑屏 | 无图像时显示黑色外壳属正常；点预览底部「刷新」重建连接 |
| I9 | 会话失效 | 复用后台 job 重建 session；不要反复重装 WDA |
| I10 | 关匿名统计 | `IPHONE_USE_ANALYTICS=0` 或 `DO_NOT_TRACK=1` 后重启 MCP |

---

## 二、安卓侧（adb + android-harness）

> 风控强度高于 iOS，开跑前必读《京东风控避坑指南.md》的避坑前提与参数表。

### 2.1 环境（本机已验证）

```sh
ADB=/Applications/Escrcpy.app/Contents/Resources/extra/mac-arm64/scrcpy/adb
HARNESS=/Volumes/正式素材文件/海外测试VVV/price-sentinel-agent-wuyinhust/android-harness
PY=$HARNESS/.venv/bin/python   # Python 3.13，androidharness 已装，可从任意 cwd 导入
$PY -c "from androidharness import connect; print('ok')"
```

| 依赖 | 说明 |
|---|---|
| 手机 | 已开 USB 调试的安卓机（实测华为 P30 / ELE-AL00 / 1080×2340） |
| 京东 App | 已登录、已进过目标店铺的店内搜索页 |
| Python venv | 服务端另需 `Flask + openpyxl + PIL`（监控台/导出用） |

### 2.2 日常跑法

```bash
# 1) 从库生成采集规格（关键词阶梯 + 必需词 + 排除词）
python3 tools/make_spec.py        # → payloads/spec_all.json

# 2) 起监控台
/Users/wangyao/.workbuddy/binaries/python/envs/default/bin/python src/app.py
#    → http://127.0.0.1:8321（右上角切破价口径）

# 3) 开跑（先灰度 10 个，观察 15 分钟无异常再放量）
bash tools/run_collect.sh         # 内部等价于：
$HARNESS/.venv/bin/python src/collect_driver.py payloads/spec_all.json
#    --limit 10          # 只采前 N 个（灰度）
#    --pid P0014         # 只采某个商品
#    --force             # 忽略断点强制重采
```

断点续采：`state/collect_<日期>.json` 自动跳过已完成项；风控/登录熔断后原命令重跑即可，不要清断点。

### 2.3 工具箱

| 工具 | 用途 |
|---|---|
| `tools/hx` | android-harness 包装器（固定 adb 路径与工作目录） |
| `tools/look.sh` | 截图 + 缩图（现场取证） |
| `tools/grid.py` | 截图叠坐标网格（无界面树时定位点击点） |
| `tools/cn.sh` | ADBKeyboard 中文注入；`cn.sh --restore` 还原百度输入法 |
| `tools/make_spec.py` | 生成 spec_all.json（含关键词阶梯） |
| `tools/run_collect.sh` | 采集启动包装（自动还原输入法、写日志） |

### 2.4 界面树可用性（实测）

- **不可用**：首页、搜索/店铺列表页（自动轮播导致 `uiautomator dump` 报 `could not get idle state`）。此时 harness 返回的是**上一次成功的陈旧树**，照它点会点错——这些页面一律用截图 + 坐标。关系统动画不能解决。
- **可用**：商品详情页、SKU 面板，可精确读 bounds 直接 `tap-text`。
- 每次 `tree` 后核对返回内容与当前截图是否一致，不一致落回坐标法。

### 2.5 判页地标（京东是单 Activity 应用，包名不能判页）

| 页面 | 唯一地标（先判最专属的） |
|---|---|
| 店内搜索输入页 | `jshop:id/jw` |
| 店首页 | `jshop:id/r3`（**必须先于店内结果页判**，店首页也带商品卡） |
| 店内结果页 | `jshop:id/nh`（卡片标题）/ `jshop:id/o0`（卡片容器） |
| 详情页 | `mall:id/a3s`（SKU 名）或文本「共 N 款」或「立即购买+加入购物车」 |
| 登录/风控页 | 包名 `com.jd.lib.login.feature`；文案「登录后，购物更轻松」「快速验证」「安全验证」；screencap 返回 0 字节（FLAG_SECURE） |

价格相关资源 ID：面板行价 `mall:id/y9`、编号 `mall:id/esy`（→ `url=item.jd.com/<编号>.html`）、
面板价格头到手价 `mall:id/qs`、划线价 `mall:id/qu`。**详情页主价格区不带 qs/qu，
必须开 SKU 面板逐个点选规格才能拿到双价。**

固定坐标标定（1080×2340）：首页 tab `(225,155)`、首页搜索框 `(540,285)`、
店内头部搜索框 `(500,168)`、店内搜索按钮 `(970,168)`、全局搜索输入 `(400,190)`、
全局搜索按钮 `(920,312)`、全局结果页「店铺」标签 `(940,288)`。

### 2.6 安卓报错 recipe（症状 → 根因 → 修法）

| # | 症状 | 根因 | 修法 |
|---|---|---|---|
| A1 | 搜索成功却读不到卡片 | `texts()` 两个过滤参数都不给时返回空列表 | 都不给 = 返回全部 |
| A2 | 正确卡片被误判 | 排除词含包装词（单支/整箱/礼盒装） | 必需词全含、排除词直接淘汰，不含包装词 |
| A3 | 注入中文后读到旧页面 | 搜索页/首页拿不到 idle，读到陈旧树 | 点搜索后轮询结果 + 重试 |
| A4 | 盲按 back 掉出店铺 | 退栈无上限 | 最多退 3–4 次，掉了走重进店流程 |
| A5 | 重进店空转 | 在详情页上盲点首页坐标 | 先 back 收弹层 → `am force-stop` + `monkey` 重开 → 点首页 tab |
| A6 | 点选 `750ml*` 采到 `*6` 的价 | 长前缀同时命中 `*1`/`*6` | 先精确匹配，再退到末 8 字唯一子串 |
| A7 | 「规格(3)」找不到 | 段名不固定（规格/品种/年份） | 不写死段名 |
| A8 | SKU2/3 价格与 SKU1 相同（串价） | 价格头是"当前选中行"的价 | 硬校验「价格头到手价 == 该行面板价」，不符只记面板价、编号留空、标低可信 |
| A9 | 页面没有「共 N 款」 | 详情页版式不止一种 | 兼容「已选：xxx，1件」入口 |
| A10 | 详情页读不到划线价 | 主价格区无 `qs/qu` | 划线价在「¥2899｜已售11件」纯文本；仅单规格面板时用主价格区回填 |
| A11 | `750ml*1`/`*6` 判不出 single/case | 中文词匹配不到 `*N` | `[*×xX]\s*(\d+)` 数字兜底 |
| A12 | `setsid` 报错 / `mkdir(exist_ok=True)` EEXIST | macOS 无 setsid；沙箱 EEXIST | 工具层后台托管；先 `if not exists()` 再 mkdir |
| A13 | 全部商品「页面=other」连环失败 | 撞了登录/风控页但没识别 | 判页前先判验证页/登录页，同级熔断整批中止（见风控指南） |
| A14 | 长商品名店内搜索 0 结果 | 店内搜索吃不进长名 | 关键词阶梯：全名→去年份→去品类→去拉丁名→品牌兜底 |
| A15 | 必需词含「拉菲」永远选不出卡 | 「源自拉菲罗斯柴尔德」是家族前缀，卡片标题未必带 | 子品牌必需、家族前缀不必需 |
| A16 | `screencap` 返回 0 字节 | 当前页有 FLAG_SECURE（登录/风控页） | 本身即熔断判据，立刻停批 |
| A17 | adb 连不上设备 | 换线/换口后授权失效 | `adb kill-server && adb start-server`，手机上重授 USB 调试 |
| A18 | 输入中文变乱码 | 当前 IME 不是 ADBKeyboard | `ime set com.android.adbkeyboard/.AdbIME`，结束 `--restore` |
| A19 | 滑块验证画不过去 | `input motionevent` 每点一次 JVM 启动（≈370ms），一笔画必然超时；无 root 写不了 `/dev/input` | 验证交人工；按风控指南前提 3，弹验证即熔断，不自动尝试 |
| A20 | 卸载重装后风控照旧 | ANDROID_ID 按签名派生，重装不变 | 重装无效，见风控指南第五节 |

---

## 三、共用部分（与平台无关）

采集写库（`src/record.py`）、规则引擎（`src/rules.py`）、监控台（`src/app.py`）直接复用。
iOS 侧只需照 `src/collect_driver.py` 的输出契约（payload JSON）写一份 iphone-use 版 driver，
`method` 标 `iphone-use`（live 白名单新增采集方式必须本人先跑通一次）。

共用纪律：

1. 数据库是主记录，Excel 只是导出；每个价格带截图证据 + 秒级时间戳。
2. 页面有几款 SKU 记几款，「共 x 款」是唯一权威；表外 SKU 如实记（`other`），只记录不判断。
3. 同一商品当场只采一轮；bug 修好再重采，不边跑边修。
4. 出现任何验证/登录迹象 → 按《京东风控避坑指南.md》前提 3 熔断：停采、杀京东进程、不尝试登录、通知用户。
