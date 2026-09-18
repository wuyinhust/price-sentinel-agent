# 华为 P30 设置指南

照着做，12 步。手机上做完，电脑上跑第 12 步验证。

---

## 手机设置

**1. 打开开发者选项**

设置 → 关于手机 → 连点「版本号」7 次

**2. 进开发者选项**

设置 → 系统和更新 → 开发者选项

> EMUI 9 上路径是：设置 → 系统 → 开发者选项

**3. 打开 USB 调试**

开发者选项 → USB 调试 → 打开

**4. 打开「'仅充电'模式下允许 ADB 调试」**

开发者选项 → 找到这一项 → 打开

> 必须开。不开的话第 3 步的开关会自己弹回关闭，插上线也认不到设备。

**5. 关闭 HDB**

设置 → 安全 → 更多安全设置 → 「允许 HiSuite 通过 HDB 连接设备」→ 关闭

> 必须关。开着会占住调试通道，`adb devices` 是空白的。改完拔插一次数据线。

**6. 关闭 ADB 安装确认**

开发者选项 → 「监控 ADB 安装应用」→ 关闭

**7. 打开保持唤醒**

开发者选项 → 「保持唤醒状态」（有的版本叫「不锁定屏幕」）→ 打开

**8. 关闭系统自动更新**

开发者选项 → 「自动系统更新」→ 关闭

**9. 关闭纯净模式**

设置 → 系统和更新 → 纯净模式 → 关闭

> 只有 HarmonyOS 有这个菜单。EMUI 上找不到就跳过。

---

## 连接

**10. 用数据线插上电脑**

**11. 手机弹出「允许 USB 调试吗？」**

勾选「一律允许使用这台计算机进行调试」→ 点确定

---

## 电脑上验证

**12. 跑这三条**

```bash
# 必须看到 device（不是 unauthorized，也不是空白）
adb devices -l

# 确认认对了机器
adb shell getprop ro.product.model        # 应含 ELE-L29 或 P30

# 输入注入可用（不报错就 OK）
adb shell input keyevent 0 && echo OK
```

三条都过 → 设置完成。

---

## 出问题查这里

| 现象 | 处理 |
|---|---|
| `adb devices` 空白 | 回步骤 5（HDB） |
| 显示 `unauthorized` | 拔插数据线，重做步骤 11 |
| 弹窗不出现 | `adb kill-server && adb start-server`，再拔插 |
| 还是没弹窗 | 开发者选项 → 「撤销 USB 调试授权」→ 拔插 |
| USB 调试开关自己弹回去 | 回步骤 4 |
| `input` 报 `SecurityException` | 见下 |

**input 被拦（`SecurityException ... INJECT_EVENTS`）**

先在开发者选项里找有没有「USB 调试（安全设置）」或「允许模拟点击」之类，有就打开。
找不到就换 UHID 通道，绕开框架层检查：

```bash
python -m androidharness.cli mirror --input uhid
```

---

## 设置完之后

```bash
python -m androidharness.cli doctor    # 全绿就绪
make check-monitor                     # 校验配置，不碰设备
make collect                           # 真机采集
make baseline                          # 看采到的价
```

---

## 一页速查

```
① 设置 → 关于手机 → 版本号 ×7
② 开发者选项：USB 调试 = 开
③ 开发者选项：「'仅充电'模式下允许 ADB 调试」= 开
④ 设置 → 安全 → 更多安全设置：允许 HiSuite 通过 HDB 连接设备 = 关
⑤ 开发者选项：监控 ADB 安装应用 = 关
⑥ HarmonyOS：关闭纯净模式
⑦ 插线 → 手机弹窗 → 一律允许
⑧ adb devices -l → 看到 device
```
