#!/usr/bin/env python3
"""价格哨兵 · 京东「店内搜索 → 商品详情页 → SKU 面板」全自动采集驱动器。

为什么要有它：监控计划要求每个商品**如实全量记录页面上的全部 SKU**（含表外规格），
179 个商品靠人手逐屏点不现实。本脚本用 android-harness（adb）在手机里跑完整流程，
**价格与 SKU 全部从无障碍树读取**（不是 OCR、不靠猜），每个 SKU 存一张证据截图。

跑法：
    HARNESS/.venv/bin/python src/collect_driver.py spec.json [--limit N] [--pid P0001]

前置条件（脚本会自己检查）：
  - 京东 App 已登录、已进过「拉菲罗斯柴尔德葡萄酒官方旗舰店」的店内搜索页；
  - 手机会自动切到 ADBKeyboard 注入中文，脚本结束会还原百度输入法。

产出：
  payloads/<日期>/<pid>.json      采集载荷（再交给 src/record.py 写库）
  evidence/<日期>/<名称>.png      每个 SKU 一张证据
  logs/collect_<日期>.log         过程日志
  state/collect_<日期>.state.json 断点续采状态
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from androidharness import connect

ROOT = Path(__file__).resolve().parent.parent
PY3 = "/Users/wangyao/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
ADB_BIN = "/Applications/Escrcpy.app/Contents/Resources/extra/mac-arm64/scrcpy/adb"
CST = timezone(timedelta(hours=8))

SHOP_PKG = "com.jd.lib.jshop.feature"
DETAIL_PKG = "com.jd.lib.productdetail.feature"
JD_PKG = "com.jingdong.app.mall"
LOGIN_PKG = "com.jd.lib.login.feature"     # 登录拦截页（京东掉登录后会拦下搜索/店内搜索）

RID_TITLE = f"{SHOP_PKG}:id/nh"        # 店内结果卡片标题
RID_CARD = f"{SHOP_PKG}:id/o0"         # 店内结果卡片容器（可点）
RID_EDIT = f"{SHOP_PKG}:id/jw"         # 店内搜索输入框
RID_SEARCH_BTN = f"{SHOP_PKG}:id/jk"   # 店内搜索按钮
RID_SKU_NAME = f"{JD_PKG}:id/a3s"      # SKU 面板 · 规格名
RID_SKU_PRICE = f"{JD_PKG}:id/y9"      # SKU 面板 · 规格价
RID_SKU_CODE = f"{JD_PKG}:id/esy"      # SKU 面板 · 编号
RID_DEAL = f"{JD_PKG}:id/qs"           # 大字成交价（到手价）
RID_LIST = f"{JD_PKG}:id/qu"           # 划线价
RID_STORE_ENTRY = f"{SHOP_PKG}:id/r3"  # 店铺首页的搜索入口

# 首页/全局搜索页的地标坐标（实测标定，见 docs 5.9）
XY_HOME_TAB = (225, 155)
XY_HOME_SEARCH = (540, 285)
XY_GLOBAL_INPUT = (400, 190)
XY_GLOBAL_SEARCH_BTN = (920, 312)
XY_RESULT_SHOP_TAB = (940, 288)

# 商品详情页专属标记（这些词只出现在详情页，不会出现在店内卡片列表上，
# 用它判页比「包名」和「到手价」都稳——京东是单 Activity 应用，包名根本区分不了）。
DETAIL_MARKERS = ("单品购买", "组合套装", "支持送礼物", "一键送礼",
                  "优惠换购", "新人三单礼", "店铺近30天", "图集")

ZWSP = "\u200b"
DEFAULT_SHOP = "拉菲罗斯柴尔德葡萄酒官方旗舰店"
DISMISS_WORDS = ("关闭", "我知道了", "稍后再说", "以后再说", "暂不需要", "取消")
VERIFY_WORDS = ("拖动下方滑块", "请完成验证", "安全验证", "滑动验证", "点击完成验证")
# 掉登录的判据（2026-09-21 凌晨踩到：京东会中途踢登录，之后搜索/店内搜索全部被拦。
# 若不识别，整批 167 个商品会一路「页面=other」失败下去，白烧几小时）。
LOGIN_MARKERS = ("登录后，购物更轻松", "账号密码登录", "获取验证码", "新用户注册",
                 "其他登录方式", "企业用户登录")


class LoginWall(RuntimeError):
    """京东把登录拦在前面了——只能人工登录一次，采集必须立刻停。"""

CASE_RE = re.compile(r"整箱|箱装|整件|6支|6 支|×6|X6|x6|\*6|750ml\*6|六支")
DOUBLE_RE = re.compile(r"双支|两支|2支|双瓶|二支")
SINGLE_RE = re.compile(r"单支|单瓶|1支|一支|单支装")
# 京东常用「750ml*1 / *2礼盒 / 750ml*6」这种规格写法，光靠中文词判不出来
PACK_N_RE = re.compile(r"[*×xX]\s*(\d+)")


def now_iso() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    """沙箱下 Path.mkdir(exist_ok=True) 会抛 EEXIST，必须先判断再建。"""
    if not path.exists():
        path.mkdir(parents=True)
    return path


def today() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def clean(text: str) -> str:
    return (text or "").replace(ZWSP, "").replace("\u2060", "").strip()


def infer_pack_type(name: str) -> str:
    n = clean(name)
    if CASE_RE.search(n):
        return "case"
    if DOUBLE_RE.search(n):
        return "double"
    if SINGLE_RE.search(n):
        return "single"
    match = PACK_N_RE.search(n)
    if match:
        bottles = int(match.group(1))
        if bottles >= 4:
            return "case"
        if bottles == 2:
            return "double"
        if bottles == 1:
            return "single"
    return "other"


class Collector:
    def __init__(self, day: str, *, rest_min=1.2, rest_max=3.5, verbose=True):
        self.phone = connect()
        self.day = day
        self.log_path = ROOT / "logs" / f"collect_{day}.log"
        ensure_dir(self.log_path.parent)
        self.rest_min, self.rest_max = rest_min, rest_max
        self.verbose = verbose
        self.evidence_dir = ROOT / "evidence" / day
        ensure_dir(self.evidence_dir)
        self.payload_dir = ROOT / "payloads" / day
        ensure_dir(self.payload_dir)
        self.fail_dir = self.evidence_dir / "_fail"
        self.ime_switched = False
        self.last_shot_bytes: bytes | None = None

    # ---- 基础设施 -------------------------------------------------------
    def log(self, message: str) -> None:
        line = f"{datetime.now(CST).strftime('%H:%M:%S')}  {message}"
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.verbose:
            print(line, flush=True)

    def rest(self, low: float | None = None, high: float | None = None) -> None:
        time.sleep(random.uniform(low or self.rest_min, high or self.rest_max))

    def adb(self, *args: str, timeout: float = 30.0) -> str:
        r = subprocess.run([ADB_BIN, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout

    def ensure_adb_keyboard(self) -> None:
        if self.ime_switched:
            return
        cur = self.adb("shell", "settings", "get", "secure", "default_input_method").strip()
        if "adbkeyboard" not in cur:
            self.adb("shell", "ime", "set", "com.android.adbkeyboard/.AdbIME")
            time.sleep(0.8)
        self.ime_switched = True

    def restore_ime(self) -> None:
        self.adb("shell", "ime", "set", "com.baidu.input_huawei/.ImeService")
        self.log("已还原百度输入法")

    def inject_cn(self, text: str) -> None:
        self.ensure_adb_keyboard()
        self.adb("shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT")
        self.adb("shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text)
        time.sleep(0.6)

    def pkg(self) -> str:
        return self.phone.current_package() or ""

    def tree(self, attempts: int = 4):
        return self.phone.tree(attempts=attempts)

    def nodes(self, tree, rid: str):
        return [n for n in tree.walk() if n.visible and n.resource_id == rid]

    def texts(self, tree, *, contains: str | None = None, pattern: str | None = None):
        """取可见节点文本。contains/pattern 都不给 = 取全部（别再犯「两个都不给就返回空」的错）。"""
        out = []
        for n in tree.walk():
            if not n.visible:
                continue
            t = clean(n.text) or clean(n.content_desc)
            if not t:
                continue
            if contains is not None:
                if contains in t:
                    out.append((t, n))
            elif pattern is not None:
                if re.search(pattern, t):
                    out.append((t, n))
            else:
                out.append((t, n))
        return out

    def tap(self, node) -> None:
        target = node.tap_target()
        x, y = target.center
        self.adb("shell", "input", "tap", str(x), str(y))

    def tap_xy(self, x: int, y: int) -> None:
        self.adb("shell", "input", "tap", str(x), str(y))

    def back(self) -> None:
        self.adb("shell", "input", "keyevent", "4")

    def shot(self, name: str) -> str:
        """截图并归档到 evidence/<日期>/<name>.png，返回库内可引用的相对路径。"""
        safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", name)[:110]
        dest = self.evidence_dir / f"{safe}.png"
        data = self.phone.screenshot_bytes()
        dest.write_bytes(data)
        self.last_shot_bytes = data
        return f"evidence/{self.day}/{dest.name}"

    def shot_fail(self, name: str) -> str:
        ensure_dir(self.fail_dir)
        safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", name)[:110]
        dest = self.fail_dir / f"{safe}.png"
        dest.write_bytes(self.phone.screenshot_bytes())
        return str(dest)

    # ---- 页面识别与弹窗 -------------------------------------------------
    def detect_verify(self, tree) -> str | None:
        blob = " ".join(t for t, _ in self.texts(tree))
        for word in VERIFY_WORDS:
            if word in blob:
                return word
        return None

    def login_wall(self, tree=None) -> bool:
        """是否被登录拦下：包名命中登录模块，或页面出现登录专属文案。"""
        try:
            if LOGIN_PKG in self.pkg():
                return True
        except Exception:
            pass
        try:
            tree = tree if tree is not None else self.tree(attempts=1)
        except Exception:
            return False
        if tree is None:
            return False
        blob = " ".join(t for t, _ in self.texts(tree))
        return any(m in blob for m in LOGIN_MARKERS)

    def guard_login(self, tree=None) -> None:
        """发现掉登录立刻抛 LoginWall，让批次整体停下（而不是逐个失败到天亮）。"""
        if self.login_wall(tree):
            raise LoginWall("京东掉登录：搜索/店内搜索已被登录页拦下")

    def dismiss_popups(self) -> int:
        try:
            tree = self.tree(attempts=1)
        except Exception:
            return 0
        hit = 0
        for word in DISMISS_WORDS:
            for t, n in self.texts(tree, contains=word):
                if len(t) <= 8 and n.clickable and n.bounds[1] > 1200:
                    self.log(f"  关弹窗：{t}")
                    self.tap(n)
                    hit += 1
                    time.sleep(1.2)
                    return hit
        return 0

    def wait_pkg(self, want: str, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if want in self.pkg():
                time.sleep(0.8)
                return True
            time.sleep(0.6)
        return False

    # ---- 页面识别（京东是单 Activity 应用，包名不可靠，一律看内容地标）----
    def page_kind(self, tree=None) -> str:
        try:
            tree = tree if tree is not None else self.tree()
        except Exception:
            return "unknown"
        if self.nodes(tree, RID_EDIT):
            return "store_search"
        blob = " ".join(t for t, _ in self.texts(tree))
        if any(m in blob for m in LOGIN_MARKERS):
            return "login"
        if any(m in blob for m in DETAIL_MARKERS) or ("立即购买" in blob and "加入购物车" in blob):
            return "detail"
        if self.nodes(tree, RID_STORE_ENTRY):      # 店首页（也带商品卡，必须先于 store_results 判）
            return "store_home"
        if self.nodes(tree, RID_SKU_NAME) or self.texts(tree, pattern=r"共\s*\d+\s*款"):
            return "detail"
        if self.nodes(tree, RID_TITLE):
            return "store_results"
        if self.texts(tree, contains="搜索历史"):
            return "global_search"
        return "other"

    def sheet_open(self, tree=None) -> bool:
        try:
            tree = tree if tree is not None else self.tree()
        except Exception:
            return False
        return bool(self.nodes(tree, RID_SKU_NAME))

    def wait_kind(self, want: str, timeout: float = 25.0) -> str:
        deadline = time.monotonic() + timeout
        kind = "unknown"
        while time.monotonic() < deadline:
            kind = self.page_kind()
            if kind == want:
                time.sleep(0.7)
                return kind
            time.sleep(0.8)
        return kind

    def ensure_search_page(self, max_back: int = 4) -> bool:
        """确保停在**店内可搜索的页**。退栈最多 4 次，掉出店铺就重进。"""
        for _ in range(max_back + 1):
            kind = self.page_kind()
            if kind in ("store_search", "store_results"):
                return True
            if kind == "store_home":
                try:
                    entry = self.nodes(self.tree(), RID_STORE_ENTRY)
                except Exception:
                    entry = []
                if entry:
                    self.log("  店首页 → 点店内搜索入口")
                    self.tap(entry[0])
                    time.sleep(1.8)
                    continue
            if kind == "global_search":
                break                      # 已在搜索域，直接走重进店
            if kind == "detail" and self.sheet_open():
                self.back()                # 先关 SKU 面板
                time.sleep(1.6)
                continue
            # detail / other / unknown：一律先退一层再说
            self.log(f"  当前页={kind}，退一层")
            self.back()
            time.sleep(1.8)
        for attempt in range(2):
            if self.reenter_store():
                return True
            self.log(f"  重进店第 {attempt + 1} 次没成功，再试")
            time.sleep(3)
        return False

    def go_home(self) -> None:
        """把京东拉回首页。先关可能的弹层/大图，再强杀重开（最稳，慢一点但只用在兜底路径）。"""
        for _ in range(2):
            self.back()
            time.sleep(1.2)
        self.adb("shell", "am", "force-stop", JD_PKG)
        time.sleep(1.5)
        self.adb("shell", "monkey", "-p", JD_PKG, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(6.0)
        self.dismiss_popups()
        self.tap_xy(*XY_HOME_TAB)
        time.sleep(2.5)
        self.log(f"  回首页后页面={self.page_kind()}")

    def reenter_store(self, shop_hint: str = "拉菲罗斯柴尔德") -> bool:
        """掉出店铺后的确定性重进：回首页 → 全局搜索 → 店铺标签 → 进店 → 店内搜索页。"""
        self.log("  ⤴ 掉出店铺，走全局搜索重新进店")
        if self.page_kind() != "global_search":
            self.go_home()
            self.tap_xy(*XY_HOME_SEARCH)
            time.sleep(3.5)
        self.tap_xy(*XY_GLOBAL_INPUT)
        time.sleep(1.0)
        self.inject_cn(shop_hint)
        time.sleep(0.5)
        self.tap_xy(*XY_GLOBAL_SEARCH_BTN)
        time.sleep(4.5)
        # 「店铺」标签优先按树找（盲点坐标会打偏到商品卡上，曾误进详情页）
        tapped_tab = False
        for _ in range(3):
            try:
                tree = self.tree()
            except Exception:
                tree = None
            if tree is not None:
                cands = [(t, n) for t, n in self.texts(tree, contains="店铺")
                         if clean(t) in ("店铺", "店铺筛选") and n.bounds[1] < 400]
                if cands:
                    self.log(f"  点「店铺」标签 @ {cands[0][1].center}")
                    self.tap(cands[0][1])
                    tapped_tab = True
                    break
            time.sleep(1.5)
        if not tapped_tab:
            self.log("  树里没找到「店铺」标签，退回盲点坐标")
            self.tap_xy(*XY_RESULT_SHOP_TAB)
        time.sleep(4.0)
        self.dismiss_popups()
        # 店铺列表轮询（单次读树容易读到陈旧页）
        shop_node = None
        for _ in range(4):
            try:
                tree = self.tree()
            except Exception:
                time.sleep(2.5)
                continue
            for t, n in self.texts(tree, contains=shop_hint):
                if "旗舰店" in t and (shop_node is None or len(t) > len(clean(shop_node.text))):
                    shop_node = n
            if shop_node is not None:
                break
            time.sleep(2.5)
        if shop_node is None:
            self.log(f"  ! 店铺列表里没找到「{shop_hint}」")
            self.shot_fail("重进店_未找到店铺")
            return False
        shop_y = shop_node.center[1]
        best, best_gap = None, 1e9
        for t, n in self.texts(tree, contains="进店"):
            gap = abs(n.center[1] - shop_y)
            if gap < best_gap:
                best, best_gap = n, gap
        self.log(f"  找到店铺行 y={shop_y}，进店按钮 gap={best_gap:.0f}")
        self.tap(best or shop_node)
        time.sleep(4.5)
        kind = self.page_kind()
        self.log(f"  进店后页面={kind}")
        if kind in ("store_home", "store_results"):
            try:
                entry = self.nodes(self.tree(), RID_STORE_ENTRY)
            except Exception:
                entry = []
            if entry:
                self.tap(entry[0])
                time.sleep(2.0)
                kind = self.page_kind()
            if kind == "store_results":
                return True
        return kind in ("store_search", "store_results")

    # ---- 店内搜索 -------------------------------------------------------
    def store_search(self, keyword: str) -> list[dict]:
        """店内搜索：确保在输入页 → 注入关键词 → 点搜索 → 轮询结果。"""
        for _ in range(3):
            tree = self.tree()
            if self.nodes(tree, RID_EDIT):
                break
            self.log("  店内结果页 → 点头部搜索框进输入页")
            self.tap_xy(500, 168)
            time.sleep(2.2)
        tree = self.tree()
        edit = self.nodes(tree, RID_EDIT)
        if not edit:
            raise RuntimeError("找不到店内搜索输入框")
        self.tap(edit[0])
        time.sleep(1.3)
        self.inject_cn(keyword)
        time.sleep(0.8)

        for attempt in range(3):
            cards = self.read_cards()
            if cards:
                return cards
            self.log(f"  第 {attempt + 1} 次点搜索")
            btn = self.nodes(self.tree(), RID_SEARCH_BTN)
            if btn:
                self.tap(btn[0])
            else:
                self.tap_xy(970, 168)
            time.sleep(4.5)
        return self.read_cards()

    def read_cards(self) -> list[dict]:
        try:
            tree = self.tree(attempts=4)
        except Exception:
            return []
        cards, seen = [], set()
        for t, n in self.texts(tree):
            if not n.resource_id == RID_TITLE:
                continue
            title = t
            box, cursor = None, n
            while cursor is not None:
                if cursor.resource_id == RID_CARD:
                    box = cursor
                    break
                cursor = cursor.parent
            if box is None:
                continue
            key = (title[:40], box.bounds)
            if key in seen:
                continue
            seen.add(key)
            price = self.card_price(tree, box)
            cards.append({"title": title, "box": box, "price": price})
        return cards

    @staticmethod
    def card_price(tree, box):
        """卡片区域内的价格：取出该卡片纵范围内全部 ¥ 文本。"""
        left, top, right, bottom = box.bounds
        found = []
        for n in tree.walk():
            if not n.visible or not n.text:
                continue
            t = clean(n.text)
            if not t.startswith("¥"):
                continue
            x, y = n.center
            if left <= x <= right and top <= y <= bottom:
                found.append(t)
        return found

    def pick_card(self, cards: list[dict], tokens: list[str], exclude: list[str],
                  relaxed: bool = False):
        """选卡片：**必需词必须全含**（品牌+品种+系列），命中排除词直接淘汰；
        合格的按「信息更全、位置更靠前」排序。

        relaxed=True 时才允许「必需词未全含」的候选进来，按命中词数排序——
        只用在关键词阶梯的最后一档兜底，且调用方要留痕待人工复核。
        """
        scored = []
        for c in cards:
            t = c["title"]
            if any(k in t for k in exclude):
                continue
            hit = sum(1 for k in tokens if k in t)
            if tokens and not relaxed and hit != len(tokens):
                continue
            if relaxed and not tokens:
                continue
            scored.append((hit, len(t), -c["box"].bounds[1], c))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [(s[0], s[3]) for s in scored]

    # ---- 详情页解析 -----------------------------------------------------
    def parse_detail(self, tree) -> dict:
        info = {"title": None, "deal": None, "list": None, "spec_buttons": [], "count": None}
        cands = [t for t, _ in self.texts(tree) if ("【旗舰店】" in t or t.startswith("拉菲")) and len(t) > 12]
        if cands:
            info["title"] = max(cands, key=len)
        for n in self.nodes(tree, RID_DEAL):
            info["deal"] = self._num(n.text)
        for n in self.nodes(tree, RID_LIST):
            info["list"] = self._num(n.text)
        # 有的版式主价格区不带 qs/qu，只是一行「¥2899 | 已售11件」纯文本。
        # 详情页的划线价在这里读得最准（面板只显示当前选中行的价）。
        for t, n in self.texts(tree, pattern=r"¥\s*[\d,]+(\.\d+)?[^\d]{0,8}已售"):
            m = re.search(r"¥\s*([\d,]+(?:\.\d+)?)", t)
            if m:
                info["list_from_detail"] = float(m.group(1).replace(",", ""))
                break
        for t, n in self.texts(tree, pattern=r"[\d,]+(\.\d+)?元[，,]?\s*到手价"):
            m = re.search(r"([\d,]+(?:\.\d+)?)元", t)
            if m:
                info["deal_from_detail"] = float(m.group(1).replace(",", ""))
                break
        for t, n in self.texts(tree, pattern=r"^(已选中)?(款式|规格|品种|年份)"):
            if n.clickable:
                info["spec_buttons"].append((t, n))
        for t, n in self.texts(tree, pattern=r"共\s*\d+\s*款"):
            m = re.search(r"共\s*(\d+)\s*款", t)
            if m:
                info["count"] = int(m.group(1))
                info["_count_node"] = n
                break
        # 另一种版式：详情页没有「共N款」，只有一个「已选: xxx，1件」行，
        # 点它同样能打开 SKU 面板（巴斯克十世年份套装就是这种）。
        if info.get("_count_node") is None:
            for t, n in self.texts(tree, pattern=r"已选[:：]"):
                if len(t) <= 60 and n.bounds[1] > 1200:
                    info["_select_node"] = n
                    info["_select_text"] = t
                    break
        return info

    @staticmethod
    def _num(text: str):
        m = re.search(r"(\d+(?:\.\d+)?)", (text or "").replace(",", ""))
        return float(m.group(1)) if m else None

    def open_sheet(self, detail: dict) -> bool:
        node = detail.get("_count_node") or detail.get("_select_node")
        if node is None:
            return False
        self.tap(node)
        self.rest(2.2, 3.4)
        return True

    def parse_sheet(self, tree) -> dict:
        """SKU 面板：价格头 + 每个规格（名/价/编号）。"""
        out = {"rows": [], "deal": None, "list": None, "code": None}
        for n in self.nodes(tree, RID_DEAL):
            out["deal"] = self._num(n.text)
        for n in self.nodes(tree, RID_LIST):
            out["list"] = self._num(n.text)
        for n in self.nodes(tree, RID_SKU_CODE):
            m = re.search(r"(\d{6,})", n.text or "")
            if m:
                out["code"] = m.group(1)
        names = self.nodes(tree, RID_SKU_NAME)
        prices = self.nodes(tree, RID_SKU_PRICE)
        for name_node in names:
            name = clean(name_node.text)
            if not name:
                continue
            ny = name_node.center[1]
            nearest, best = None, 1e9
            for p in prices:
                d = abs(p.center[1] - ny)
                if d < best:
                    nearest, best = p, d
            price = self._num(nearest.text) if nearest and best < 70 else None
            out["rows"].append({"name": name, "panel_price": price, "node": name_node})
        return out

    def _find_row(self, sheet: dict, row_name: str):
        """在面板里按名字找行。**先精确匹配**，再退到「末 8 字唯一子串」。

        别用长前缀匹配：`巴斯克霞多丽白葡萄酒750ml*` 同时命中 `750ml*1` 和 `750ml*6`
        两行，会把第 3 行点成第 1 行（P0003 踩过）。
        """
        want = clean(row_name)
        for r in sheet["rows"]:
            if clean(r["name"]) == want:
                return r
        tail = want[-8:]
        if tail:
            cands = [r for r in sheet["rows"] if tail in clean(r["name"])]
            if len(cands) == 1:
                return cands[0]
        return None

    def select_sku(self, row_name: str, panel_price, prev_code: str | None):
        """点选一个规格并**确认价格头真的切到了这一行**。

        主确认信号：价格头的到手价 == 该行面板价（面板行价才是这一行自己的价）。
        次确认信号：编号发生变化。都不满足就算没点中，换点位重试；
        三次都失败则反正只信面板行价，划线价与编号留空（绝不张冠李戴）。
        """
        sheet = None
        for attempt in range(3):
            try:
                sheet = self.parse_sheet(self.tree(attempts=3))
            except Exception:
                continue
            target = self._find_row(sheet, row_name)
            if target is None:
                self.log(f"    面板里找不到规格「{row_name}」")
                break
            node = target["node"]
            if attempt == 0:
                self.tap(node)
            else:
                left, top, right, bottom = node.bounds
                self.tap_xy(left + 40, (top + bottom) // 2)
            self.rest(1.7, 2.8)
            try:
                after = self.parse_sheet(self.tree(attempts=3))
            except Exception:
                continue
            code, deal = after["code"], after["deal"]
            if panel_price is not None and deal is not None and abs(deal - panel_price) < 0.01:
                return after, True
            if panel_price is None and prev_code is not None and code and code != prev_code:
                return after, True
            self.log(f"    规格「{row_name}」未确认切到本行"
                     f"（头部到手={deal} 本行面板价={panel_price} 编号={code}），重试 {attempt + 1}")
        return (sheet or {"rows": [], "deal": None, "list": None, "code": None}), False

    # ---- 单个商品全流程 -------------------------------------------------
    def collect_product(self, spec: dict) -> dict:
        pid, name = spec["pid"], spec["name"]
        tokens, exclude = spec.get("tokens", []), spec.get("exclude", [])
        # 关键词阶梯：先全名，搜不到再逐级放宽（京东店内搜不到太长的名字，P0025 就卡在这）
        keywords = spec.get("keywords") or [spec["keyword"]]
        self.log(f"▶ {pid} {name}")
        result = {"product_id": pid, "ok": False, "reason": None, "skus": [], "cards": []}

        best, used_kw, cards = None, None, []
        for i, kw in enumerate(keywords):
            if not self.ensure_search_page():
                result["reason"] = "退不回店内搜索页"
                return result
            self.dismiss_popups()
            self.guard_login()

            cards = self.store_search(kw)
            result["cards"] = [{"title": c["title"], "price": c["price"]} for c in cards]
            self.guard_login()
            if not cards:
                self.log(f"  关键词「{kw}」0 结果，换下一档")
                continue
            ranked = self.pick_card(cards, tokens, exclude)
            if not ranked and i == len(keywords) - 1:
                # 最后一档还选不出来 → 放宽为「命中词最多且不含排除词」，但要留痕待复核
                ranked = self.pick_card(cards, [], exclude, relaxed=True)
                if ranked:
                    self.log("  ! 宽松匹配兜底（必需词未全含），已记待复核")
            if ranked:
                best, used_kw = ranked[0], kw
                self.log(f"  关键词「{kw}」候选 {len(cards)} 张，选中："
                         f"{best[1]['title'][:52]}  {best[1]['price']}")
                if len(ranked) > 1:
                    self.log(f"  次选：{ranked[1][1]['title'][:52]}  {ranked[1][1]['price']}")
                break
            self.log(f"  关键词「{kw}」有 {len(cards)} 张候选但都不含必需词 {tokens}，换下一档")
            self.log(f"    候选标题：{[c['title'][:26] for c in cards][:6]}")
        else:
            result["reason"] = f"关键词阶梯全试过仍无匹配（{len(cards)} 张候选）"
            self.shot_fail(f"{pid}_无匹配卡片")
            return result
        if best is None:
            result["reason"] = "无匹配卡片"
            self.shot_fail(f"{pid}_无匹配卡片")
            return result

        result["keyword_used"] = used_kw
        self.tap(best[1]["box"])
        kind = self.wait_kind("detail", timeout=25)
        if kind != "detail":
            result["reason"] = f"没进详情页（页面={kind}）"
            self.shot_fail(f"{pid}_未进详情页")
            self.guard_login()
            return result
        self.rest(2.4, 4.0)
        self.dismiss_popups()

        tree = self.tree()
        verify = self.detect_verify(tree)
        if verify:
            result["reason"] = f"触发风控验证：{verify}"
            self.shot_fail(f"{pid}_风控验证")
            return result

        detail = self.parse_detail(tree)
        result["jd_title"] = detail["title"]
        result["detail_deal"] = detail["deal"]
        result["detail_list"] = detail["list"]
        result["count"] = detail["count"]
        self.log(f"  标题：{(detail['title'] or '')[:60]}")
        self.log(f"  详情页 到手价={detail['deal']} 划线价={detail['list']} 共{detail['count']}款 "
                 f"款式行={[t for t, _ in detail['spec_buttons']]}"
                 f"{' 选择入口=' + detail['_select_text'][:30] if detail.get('_select_text') else ''}")
        self.shot(f"jd_{pid}_{name[:14]}_详情页")

        if not self.open_sheet(detail):
            result["reason"] = "页面上没有「共N款」入口"
            return result

        tree = self.tree()
        sheet = self.parse_sheet(tree)
        self.log(f"  面板 规格数={len(sheet['rows'])} 头价 到手={sheet['deal']} 划线={sheet['list']} 编号={sheet['code']}")
        pane_ev = self.shot(f"jd_{pid}_{name[:14]}_SKU面板_{len(sheet['rows'])}款")
        if not sheet["rows"]:
            result["reason"] = "SKU 面板解析为空"
            self.shot_fail(f"{pid}_面板空")
            return result

        prev_code = None
        for idx, row in enumerate(sheet["rows"], 1):
            pack = infer_pack_type(row["name"])
            now, confirmed = self.select_sku(row["name"], row["panel_price"], prev_code)
            if confirmed and now.get("deal") is not None:
                code = now.get("code") or ""
                price = now["deal"]
                list_price = now["list"]
                note = (f"京东型号编码 {code or '未读到'}；面板价 {row['panel_price']}，"
                        f"选中后到手价 {now['deal']}，划线价 {now['list']}。")
                confidence = "高"
            else:
                # 头部没能确认属于本行 → 只信这一行自己的面板价，不张冠李戴
                code = ""
                price = row["panel_price"]
                list_price = None
                note = (f"面板价 {row['panel_price']}；点选后价格头未确认切到本行，"
                        f"故划线价与型号编码留空（不编造），需人工复核。")
                confidence = "低"
            # 面板只显示一个规格、且价格头没给划线价时，用详情页那条「¥xxxx 已售N」回填
            if list_price is None and len(sheet["rows"]) == 1 and detail.get("list_from_detail"):
                list_price = detail["list_from_detail"]
                note += f" 划线价取自详情页主价格区（¥{list_price:g}）。"
            sku = {
                "pack_type": pack,
                "sku_name": row["name"],
                "price": price,
                "list_price": list_price,
                "status": "ok",
                "confidence": confidence,
                "url": f"https://item.jd.com/{code}.html" if code else None,
                "evidence": self.shot(f"jd_{pid}_{name[:10]}_SKU{idx}_{row['name'][:18]}_到手{price}"),
                "note": note,
            }
            result["skus"].append(sku)
            if code:
                prev_code = code
            flag = "" if confirmed else "  ⚠未确认切到本行"
            self.log(f"    · {pack:<6} {row['name']}  到手={sku['price']} "
                     f"划线={sku['list_price']} 编号={code or '-'}{flag}")
            self.rest(1.5, 2.8)

        if pane_ev and result["skus"]:
            result["skus"][0]["panel_evidence"] = pane_ev
        result["ok"] = bool(result["skus"])
        return result

    # ---- 批量 -----------------------------------------------------------
    def run(self, specs: list[dict], limit: int | None = None) -> None:
        state_path = ROOT / "state" / f"collect_{self.day}.json"
        ensure_dir(state_path.parent)
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        self.log(f"=== 批次开始：{len(specs)} 个商品候选，已完成 {len(state)} ===")
        done_this_run = 0
        fails_in_a_row = 0

        for spec in specs:
            if limit is not None and done_this_run >= limit:
                self.log(f"达到本次上限 {limit}，停止。")
                break
            pid = spec["pid"]
            if pid in state and state[pid].get("ok"):
                self.log(f"跳过已采 {pid}")
                continue
            try:
                res = self.collect_product(spec)
            except LoginWall as wall:
                # 掉登录不是「这个商品采不到」，是整条路被封——立刻停批，别把剩下的全烧成失败。
                self.log(f"  🛑 检测到登录拦截，批次中止：{wall}")
                self.log(f"  🛑 已完成 {done_this_run} 个，剩余未采。请在手机上登录京东后重跑（断点会续采）。")
                try:
                    self.shot_fail("掉登录_批次中止")
                except Exception:
                    pass
                break
            except Exception as error:  # 采集器不许因单点异常整体崩掉
                res = {"product_id": pid, "ok": False, "reason": f"异常 {type(error).__name__}: {error}", "skus": []}
                try:
                    self.shot_fail(f"{pid}_异常")
                except Exception:
                    pass
            state[pid] = {"ok": res["ok"], "reason": res.get("reason"), "at": now_iso(),
                          "skus": len(res.get("skus", []))}
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

            if res["ok"]:
                self.write_payload(spec, res)
                self.log(f"  ✓ {pid} 完成 {len(res['skus'])} 个 SKU")
                self.ensure_search_page()
                fails_in_a_row = 0
            else:
                self.log(f"  ✗ {pid} 失败：{res.get('reason')}")
                self.shot_fail(f"{pid}_失败")
                self.ensure_search_page()
                fails_in_a_row += 1
                if fails_in_a_row >= 3:
                    self.guard_login()
                    self.log("  ⚠ 连续 3 个失败，暂停 5 分钟（可能遇风控，需人工看一眼手机）")
                    time.sleep(300)
                    fails_in_a_row = 0

            done_this_run += 1
            # 拟真人节奏：每 5-8 个商品休息一次
            if done_this_run % random.choice((5, 6, 7, 8)) == 0:
                nap = random.uniform(65, 175)
                self.log(f"  —— 休息 {nap:.0f}s（拟真人节奏）——")
                time.sleep(nap)
            else:
                self.rest(3.0, 7.0)
        self.log("=== 批次结束 ===")

    def write_payload(self, spec: dict, res: dict) -> None:
        skus = []
        for s in res["skus"]:
            item = dict(s)
            item.pop("node", None)
            item["jd_title"] = f"{res.get('jd_title') or ''} {item['sku_name']}".strip()
            skus.append(item)
        payload = {
            "batch_date": self.day,
            "product_id": spec["pid"],
            "observed_time": now_iso(),
            "shop": DEFAULT_SHOP,
            "monitor_status": "已确认",
            "jd_title": res.get("jd_title"),
            "note": (f"店内搜索「{res.get('keyword_used') or spec['keyword']}」→ 详情页 → SKU 面板实采。"
                     f"页面标「共{res.get('count')}款」，实际采到 {len(skus)} 个 SKU。"
                     f"详情页 到手价={res.get('detail_deal')} 划线价={res.get('detail_list')}。"
                     f"口径：price=到手价（主口径），list_price=划线价。"),
            "skus": skus,
        }
        path = self.payload_dir / f"{spec['pid']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"  载荷已写 {path.relative_to(ROOT)}")
        r = subprocess.run([PY3, str(ROOT / "src" / "record.py"), str(path), "--force"],
                           capture_output=True, text=True, cwd=str(ROOT))
        for line in (r.stdout or "").strip().splitlines():
            self.log(f"    {line}")
        if r.returncode != 0:
            self.log(f"    ! 写库失败：{(r.stderr or '').strip()[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", help="批量规格 JSON：{day, items:[{pid,name,keyword,tokens,exclude}]}")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--pid", default=None, help="只采某个商品")
    ap.add_argument("--redo", default=None,
                    help="重采指定商品（逗号分隔 pid），忽略断点状态；配合 --pid 可强制单商品重跑")
    ap.add_argument("--day", default=None)
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    day = args.day or spec.get("day") or today()
    items = spec["items"]
    if args.pid:
        items = [i for i in items if i["pid"] == args.pid]

    redo = {p.strip() for p in (args.redo or "").split(",") if p.strip()}
    if redo:
        state_path = ROOT / "state" / f"collect_{day}.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            removed = [p for p in redo if state.pop(p, None) is not None]
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已从断点里移除待重采：{removed or '（本来就没记录）'}")

    c = Collector(day)
    try:
        c.run(items, limit=args.limit)
    finally:
        try:
            c.restore_ime()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
