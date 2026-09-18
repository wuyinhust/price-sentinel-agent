# 华为 P30 前置设置指南

把一台华为 P30 接进 `android-harness` / `price-sentinel` 的采集通道之前，
需要先在手机上完成下面这些设置。**顺序有讲究，别跳步。**

> 本文面向 P30（`ELE-L29` / 麒麟 980）。华为同期机型（P30 Pro、Mate 20/30、
> nova 5/6 等）设置项基本一致，差别只在菜单层级。
> P30 出厂是 EMUI 9.1，多数机器后来升到了 **EMUI 10** 或 **HarmonyOS**——
> 菜单路径按你手机上实际看到的为准，本文两种路径都给。

---

## 0. 先建立正确的预期

**华为的坑和小米不是同一个坑。** 这一点必须先说清楚，否则会照着 MIUI 的排查
思路白折腾半天：

| | 小米 / MIUI / HyperOS | 华为 / EMUI / HarmonyOS |
|---|---|---|
| 主要障碍 | **输入注入闸门**：`USB调试(安全设置)` 默认关闭，只开「USB调试」不足以注入输入事件 | **USB 调试会自己关掉**：切换 USB 连接模式时被系统自动取消 |
| 第二个障碍 | 没有 | **HDB 抢占连接**：`允许 HiSuite 通过 HDB 连接设备` 开着时 `adb devices` 可以是空的 |
| 输入注入闸门 | 确认存在，且**软件层面绕不过**（`INJECT_EVENTS` 一直是 granted） | 公开资料中**未见**独立开关，**但也没有证据说华为不拦**——必须现场实测，见第 5 节 |
| 状态属性 | `persist.security.adbinput`（空/0 = 关） | 无对应属性 |
| 纯净模式 | 无 | HarmonyOS 有，会拦 adb 安装，要关（见第 7 节） |

一句话：**小米是"手断了"，华为是"线断了"。** 先解决连接，再验证手脚。

---

## 1. 打开开发者选项

1. 设置 → **关于手机**（About phone）
2. 找到 **版本号**（Build number），**连点 7 次**
3. 提示「您现在是开发者了！」

> P30 上是「版本号」不是「OS 版本」——那是小米的叫法。

### 之后去哪里找它

| 系统 | 路径 |
|---|---|
| EMUI 10 / HarmonyOS | 设置 → **系统和更新** → 开发者选项 |
| EMUI 9 | 设置 → **系统** → 开发者选项 |

---

## 2. 必开 / 必关清单

进开发者选项，逐条对照。**这张表是全文的核心。**

| # | 开关 | 位置 | 设为 | 不开/不关会怎样 |
|---|---|---|---|---|
| 1 | **USB 调试**<br>USB debugging | 开发者选项 | **开** | 什么都没法做 |
| 2 | **「仅充电」模式下允许 ADB 调试**<br>Allow ADB debugging in charge only mode | 开发者选项 | **开** | ⚠️ 华为头号坑。手机默认「仅充电」模式，不开这项的话 USB 调试**开关会自动弹回关闭**，看着像"设备突然掉线" |
| 3 | **允许 HiSuite 通过 HDB 连接设备**<br>Allow HiSuite to use HDB | 设置 → 安全 → 更多安全设置 | **关** | ⚠️ 华为二号坑。开着时 HDB 会占用调试通道，`adb devices` 出不来设备。**改完要拔插一次 USB** |
| 4 | **监控 ADB 安装应用**<br>Monitor ADB app installation | 开发者选项 | **关** | 开着时每次 `adb install` 都要有人在手机上点确认，自动化全断 |
| 5 | **保持唤醒状态**<br>Stay awake | 开发者选项 | **开** | 长跑采集时息屏（P30 上叫「不锁定屏幕」或「保持唤醒」） |
| 6 | **自动系统更新**<br>Automatic system update | 开发者选项 | **关**（建议） | 免得半夜自动升级重启，把环境打回原形 |
| 7 | **撤销 USB 调试授权** | 开发者选项 | 按需用 | 授权状态混乱时的重置入口，见第 4 节 |

---

## 3. USB 连接模式：默认是「仅充电」

华为插上电脑默认只在充电，不建数据通道。两种解法，**任选其一，第 2 种更省事**：

- **A.** 下拉通知栏 → 点「正在通过 USB 充电」→ 选 **传输文件**（File transfer / MTP）
- **B.** 直接在开发者选项里开 **「'仅充电'模式下允许 ADB 调试**（清单第 2 项）

### EMUI 的一个怪癖

EMUI 5.0+ 上，**如果先开 USB 调试再插线，开关可能自己弹回去。**
正确顺序：

```
1. 先用数据线插上电脑
2. 再进开发者选项开 USB 调试
3. 如果它又自己关了，拔线 → 重插 → 再开一次
```

---

## 4. 首次授权

插线后手机上应弹出 **「允许 USB 调试吗？」**（Allow USB debugging?）

- 勾选 **「一律允许使用这台计算机进行调试」**（Always allow）
- 点 **确定 / 允许**

### 弹窗不出现怎么办

按顺序试，通常第一步就够：

```bash
adb kill-server
adb start-server
adb devices          # 重新插线后再看
```

还不行就重置设备端授权：

> 开发者选项 → **撤销 USB 调试授权**（Revoke USB debugging authorizations）
> → 拔线 → 重插 → 弹窗会重新出现

> 如果电脑端 `~/.android/adbkey` 被删过，设备会认不出这台电脑，重连时会
> 重新弹授权框。这不是故障。

---

## 5. 验证连接（一条条跑，不要看 UI 猜）

```bash
# 1. 设备在不在。要看到 "device"，不是 "unauthorized"、也不是空白
adb devices -l

# 2. 确认认对了机器
adb shell getprop ro.product.model          # 期望含 ELE-L29 / P30

# 3. 两个总开关的状态（这两个都是 1 才正常）
adb shell settings get global adb_enabled                    # → 1
adb shell settings get global development_settings_enabled   # → 1
```

`adb devices` 是空白 → 回去看第 2 节清单第 3 项（HDB）。
`unauthorized` → 回去看第 4 节。

---

## 6. 输入注入：华为的情况（必须实测）

这是全文**唯一一个不能给你确定答案**的地方，因为它没有公开的权威结论。

已知事实：

- 小米靠 `USB调试(安全设置)` 这一个开关控制 `input` / `settings put` / `wm size`
  / scrcpy 默认输入四件事，**华为的公开资料里搜不到这个开关**。
- 各家投屏/自动化工具的华为专页提到的都是「仅充电模式下允许 ADB 调试」，
  而小米专页才会提「USB 调试（安全设置）」——**旁证华为可能不拦输入**。
- 但 `persist.security.adbinput` 是 MIUI 专有属性，**在华为上读到空值是正常的**，
  空值不代表"闸门关着"，只代表"这台机器不是小米"。别拿它判断华为。

### 无害探测：跑这一条就知道

```bash
adb shell input keyevent 0 && echo "=== input OK ==="
```

- **打印 `=== input OK ===`** → 输入注入可用，直接跳到第 8 节。
- **报 `SecurityException ... INJECT_EVENTS`** → 输入被拦。
  此时按顺序试：
  1. 在开发者选项里找有没有类似 **「USB 调试（安全设置）」/「允许模拟点击」**
     的条目（不同 EMUI 版本可能叫法不同，有的机器根本没有）
  2. 找不到就换 **scrcpy UHID 后端**绕过框架层检查：
     ```bash
     python -m androidharness.cli mirror --input uhid
     ```
     UHID 走内核虚拟 HID，事件不进框架，被 MIUI 拦的那条路对它无效。
     **这条路在小米上实测成立，华为大概率同样成立，但需现场确认。**

### 顺带验证设置写入能力

采集通道要用 `settings put` 改息屏超时，这也是一个能力探测点：

```bash
adb shell settings get system screen_off_timeout          # 先读
adb shell settings put system screen_off_timeout 1800000  # 写 30 分钟
adb shell settings get system screen_off_timeout          # 读回，必须是 1800000
```

⚠️ **必须读回确认。** 写入被拒时命令会正常返回、不报错，但值没变——
只看退出码会被骗（`adb ... | xxx` 管道后的 `$?` 更是取的是管道末端的退出码）。

---

## 7. 纯净模式（只有 HarmonyOS 有）

P30 如果升到了 HarmonyOS，会多一个 **纯净模式**（Pure Mode），
它会拦掉 `adb install` 和侧载，报 `INSTALL_FAILED_USER_RESTRICTED` 一类的错。

**关掉它：** 设置 → **系统和更新** → **纯净模式** → 关闭
（或保留纯净模式但关闭「增强防护」）

> 如果你的 P30 还是 EMUI，没有这个菜单，跳过。

---

## 8. 中文输入（全 Android 通病）

`adb shell input text 你好` 打不出中文，这是 **Android 全平台**的限制，
不是华为特有的（华为上大概率是静默丢弃，小米上实测是抛
`NullPointerException`）。

**好消息：价格哨兵已经绕开了这个问题。**

- 京东通道走 **deep link**，关键词做百分号编码塞进 URL，全程不打字
- 淘宝通道走 **拼音 + 选候选词**（`jiyin` → 点「吉饮咖啡」），再用 OCR 读结果

所以**只有在你要新增采集通道、且必须手打中文时**才需要装输入法：

> 侧载 **ADBKeyboard**（`com.android.adbkeyboard`），然后
> `adb shell am broadcast -a ADB_INPUT_TEXT --es msg '你好'`
>
> 注意华为上侧载前要先关纯净模式（第 7 节）。

---

## 9. 跑起来

手机侧全部设置完，在电脑上：

```bash
# 安卓控制通道自检（会报二进制、设备、屏幕、无障碍树、设置写入）
python -m androidharness.cli doctor

# 校验采集配置，不碰设备
make check-monitor

# 真机采集
make collect

# 看已经采到什么价
make baseline
```

`doctor` 里如果有哪一段是红的，回上面找对应的章节——它的报错是刻意写成
「告诉你该去开哪个开关」的，不是泛泛的失败。

---

## 10. 一页速查

```
① 设置 → 关于手机 → 版本号 ×7
② 开发者选项：USB 调试 = 开
③ 开发者选项：「'仅充电'模式下允许 ADB 调试 = 开   ← 华为关键
④ 设置 → 安全 → 更多安全设置：允许 HiSuite 通过 HDB 连接设备 = 关   ← 华为关键
⑤ 开发者选项：监控 ADB 安装应用 = 关
⑥ 插线 → 手机弹窗 → 一律允许
⑦ adb devices -l  →  看到 device
⑧ adb shell input keyevent 0  →  不报错就能用
⑨ HarmonyOS：关闭纯净模式
```

---

## 附：与 Redmi K40 的差异速查

| 项目 | Redmi K40（MIUI / HyperOS，已实测） | 华为 P30（EMUI / HarmonyOS） |
|---|---|---|
| 开发者选项入口 | 设置 → 我的设备 → 全部参数与信息 → 连点「OS 版本」 | 设置 → 关于手机 → 连点「版本号」 |
| 输入注入闸门 | **有**：开发者选项 → 「USB调试（安全设置）」，默认关 | 公开资料未见独立开关，**需实测** |
| 闸门状态属性 | `getprop persist.security.adbinput` | 无此属性 |
| 开启闸门的附带条件 | 需插 SIM + 登录小米账号 | — |
| USB 调试自动关闭 | 否 | **是**，靠「'仅充电'模式下允许 ADB 调试」根治 |
| HDB 抢通道 | 无此概念 | **有**，必须关「允许 HiSuite 通过 HDB 连接设备」 |
| ADB 安装确认 | 「USB 安装」开关 | 「监控 ADB 安装应用」，建议关 |
| 纯净模式 | 无 | HarmonyOS 有，需关 |
| 输入注入被拦时的退路 | scrcpy `--input uhid`（已实测可行） | scrcpy `--input uhid`（大概率可行，待验证） |

---

## 参考

- 华为官方社区：[Why cannot turn on the USB debugging switch?](https://consumer.huawei.com/en/community/details/topicId-7030/)
  （「'仅充电'模式下允许 ADB 调试」的官方说明）
- 华为官方支持：[裝置無法連接手機助手或連接不穩定?](https://consumer.huawei.com/hk/support/content/zh-hk00698619/)
- AirDroid 帮助中心：[如何在華為裝置上啟用 USB 偵錯?](https://help.airdroid.com/)
  （EMUI 4.x / 5.x / 8–9 / HarmonyOS 10.x 各版本菜单路径）
