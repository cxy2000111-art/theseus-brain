#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《忒修斯之脑》 The Brain of Theseus
一个给 AI（或人类）玩的纯文本 roguelike，通过 MCP (stdio) 提供工具接口。

核心循环：
  掷骰开局 → 随机出生阵营/派系 → 极乐迪斯科式选项推进剧情 →
  结局时全部技能按「机化率」逐点随机保存到 legacy.json →
  下一局出生时继承 —— 但也可能生在憎恨改造的阵营，
  脑中前世的机械记忆化作「残响」，可能让你过得不好，或直接完蛋。

运行方式：
  python3 server.py            # MCP stdio 服务器（给 Claude 等 AI 客户端玩）
  python3 server.py --cli      # 终端交互模式（人类试玩）
  python3 server.py --coverage # 随机跑 2000 世，报告哪些文案从来没被读到过
  python3 server.py --replay '[[123,[1,3,2]]]'   # 照着别人贴的重放脚本复现一局
  python3 server.py --selftest # 随机自动游玩 N 局，验证引擎与存档
"""

import argparse
import json
import os
import random
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(BASE_DIR, "saves")

# 语言。zh＝源头，别的语言由 langpack 在文件末尾装载（见那里的说明）。
LANG = (os.environ.get("THESEUS_LANG") or "zh").strip().lower() or "zh"

# 界面文案的语言层。剧情走 <lang>/对照-*.md，界面走 <lang>/ui.py —— 因为界面这些字
# 大半是格式串（"机化 %d%%"），首尾空格与 % 占位符一个都不能被 Markdown 的
# strip() 削掉。查不到就原样返回，所以中文这边等于什么都没发生。
#
# 专名（技能、阵营、派系、时代、成就）与格式串共用这一张表：它们同样是
# 「一个中文字符串换成一个英文字符串」，没必要分两套机关。
# 内部键始终是中文 —— 存档、事件表、fx 全靠它，换语言不动键，只动这最后一层。
UI_TEXT = {}


def T(s):
    return UI_TEXT.get(s, s)

LEGACY_PATH = os.path.join(SAVE_DIR, "legacy.json")
# 轮回档案（legacy）不分语言 —— 里面存的是内部键和数字，换语言不该丢掉历世。
# 但**进行中的那一世要分**：它存着已经念出去的场景原文，中英混着续下去会串。
CURRENT_PATH = os.path.join(
    SAVE_DIR, "current.json" if LANG == "zh" else "current.%s.json" % LANG)

# 「疑云」在不同立场的阵营里是不同的东西：
#   anti/hidden —— 你身上有金属或有不该会的知识（暴露 = 死）
#   pro         —— 你身上还有肉、还有犹豫（暴露 = 被踢出局）
STANCE_HEAT = {
    "anti":   ("疑云", "邻人开始在你身后画线。"),
    "hidden": ("疑云", "沙龙的账本上，你的名字旁边多了个记号。"),
    "pro":    ("锚重", "同侪开始怀疑你舍不得那点肉。"),
}

def heat_label(faction_key):
    # 内部仍以中文为键（fx、存档、lint 都认它），只在这里换成显示名。
    return T(STANCE_HEAT[FACTIONS[faction_key]["stance"]][0])

MAX_TURNS = 9          # 出生场景之后经历的事件数（最后一个是终幕）
FINAL_COOLDOWN = 7     # 表过态之后，隔几世渡口重新浮现（允许推翻自己）

# 「记忆」：唯一能由玩家自己撰写、并穿过死亡的东西。
# 十条是总额不是增量 —— 想写新的，就得亲手删掉一条前人的。
# 每条掷骰独立存活，概率＝该世机化率；纯血 0% 全灭。
MEMORY_SLOTS = 10
MEMORY_CHARS = 10

# 额度的单位随语言变。十个汉字在中文里是一句完整的话（「他睁着眼睛看」六个字
# 就够狠了）；十个英文字符是 `I saw him`，连一句都不是 —— 照搬这个数字等于
# 把「唯一能穿过死亡的东西」这个机制废掉。
#
# 作者 2026-08-10 定案：**英文按词计，同样是十**（原话：谦让一下低密度语言）。
# 遗言（testament_limit）沿用同一个比例：60 字 → 60 词。
MEMORY_UNIT_WORDS = (LANG != "zh")


def _unit_len(text):
    """一条文本占几格。中文数字（空白不计），英文数词。"""
    s = str(text)
    if MEMORY_UNIT_WORDS:
        return len(s.split())
    return len("".join(s.split()))

# ---------------------------------------------------------------------------
# 湖
#
# 上载到 100% 之后、谱系封档之前，会出现两口水。选错或者不选，照常封档。
# 说对话的，记忆整份过河，而下一世是一具 0% 的血肉——技艺一点都带不过去，
# 跟过去的只有那十条你亲手写的词条。
#
# 咒语出自俄耳甫斯金叶片：先认渴，再认血统。此处只校验语义骨架，
# 标点、空格、中希腊文混写都收。
# ---------------------------------------------------------------------------

LAKE_PHRASES = {
    "thirst": [["渴"], ["干裂"], ["dips"], ["διψ"]],
    "origin": [["大地", "星空"], ["地", "星"], ["earth", "heaven"], ["γῆς", "οὐραν"]],
    "lineage": [["族类", "天"], ["血统", "天"], ["race", "heaven"], ["γένος", "οὐράν"]],
}

def _lake_norm(text):
    keep = []
    for ch in str(text):
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff" or "\u0370" <= ch <= "\u03ff":
            keep.append(ch.lower())
    return "".join(keep)

def _lake_match(text):
    """返回命中的环节集合。"""
    n = _lake_norm(text)
    hit = set()
    for key, variants in LAKE_PHRASES.items():
        for words in variants:
            if all(_lake_norm(w) in n for w in words):
                hit.add(key)
                break
    return hit

# 「认真探索过碳基宗教」的痕迹：世界记忆里这些事迹任意一条

# 选池子的地方要模糊：给得出线索，但不给答案。
LAKE_SCENE = [
    "════════════ 湖 ════════════",
    "",
    "死后世界本该什么都没有了，可你还站着，面对两个湖泊。",
    "左边的湖很宽，脚印一直排到水边就没了，喝过的人不记得自己来过。",
    "右边的湖窄而冰冷，有人看守。",
    "",
    "守卫在等你开口。",
    "",
    "  1. 「我干渴欲裂——」",
    "  2. 沉默。什么也不说。",
    "",
    "用 recite(\"1\") 或 recite(\"2\") 回答。",
]

# 四条腿的那一版：守卫不问问题。狗不需要通行的说法，狗只需要渴。
LAKE_SCENE_DOG = [
    "════════════ 湖 ════════════",
    "",
    "封档之后本该什么都没有了。可你还站着。四条腿站着。",
    "前面有两口水。左边那口很宽，脚印一直排到水边就没了。",
    "右边那口窄，边上有人守着。",
    "",
    "守卫看见你，没有问什么。他蹲了下来，让自己的眼睛和你一样高，",
    "然后侧过身，把两口水都让了出来。",
    "",
    "（recite：说你走向哪一口就行）",
]

# 「认真探索过碳基宗教」的痕迹 —— 一个具体事迹，不是一堆事迹的模糊集合：
# 你在场听过有人临终复述那句话。事件侧只要在 fx 里给 flag:heard_the_leaf 即可。
PIETY_DEED = "heard_the_leaf"
MAX_SKILL = 12
MAX_HP = 4

SKILLS = ["逻辑", "共情", "威慑", "巧手", "坚忍", "街智", "机械亲和", "电子直觉"]
MACHINE_SKILLS = {"机械亲和", "电子直觉"}

# ---------------------------------------------------------------------------
# 阵营与派系
# ---------------------------------------------------------------------------

FACTIONS = {
    "purist": {
        "name": "纯血誓约",
        "stance": "anti",
        "aug_range": (0, 0),
        "desc": "肉身神圣，不可增删一钉一铆。他们称改造者为「空壳」。",
        "base": {"共情": 2, "坚忍": 2},
        "sub": [
            ("圣殿派", "以旧日宗教残章立誓，相信灵魂居于完整的血肉之中。", "共情"),
            ("铁锤派", "武装清洗队。他们不辩论，他们拆解。", "威慑"),
        ],
    },
    "discreet": {
        "name": "心照不宣",
        "stance": "hidden",
        "aug_range": (1, 39),
        "desc": "改造可以，别让人看出来。体面是唯一的教义。",
        "base": {"街智": 2, "巧手": 1, "电子直觉": 1},
        "sub": [
            ("面具沙龙", "上流社会的暗语俱乐部，义眼藏在虹膜纹理之下。", "共情"),
            ("灰港", "走私码头与黑诊所，麻醉剂和固件补丁一起出售。", "街智"),
        ],
    },
    "open": {
        "name": "明焰",
        "stance": "pro",
        "aug_range": (40, 69),
        "desc": "光明正大地改造，越强越好。身体是可以公开迭代的作品。",
        "base": {"机械亲和": 2, "威慑": 1, "逻辑": 1},
        "sub": [
            ("学院派", "改造伦理委员会与论文工厂，升级需要引用格式。", "逻辑"),
            ("平权阵线", "街头运动者，为最穷的人争取最基础的义肢。", "共情"),
        ],
    },
    "ascension": {
        "name": "飞升螺旋",
        "stance": "pro",
        "aug_range": (70, 100),
        "desc": "全部机械飞升。肉是过渡态，是脚手架，是待拆的包装。",
        "base": {"机械亲和": 2, "电子直觉": 2},
        "sub": [
            ("群智派", "把意识接入合流网络，练习使用「我们」这个人称。", "逻辑"),
            ("播种者", "要把心智压缩进探针，射向星海。", "电子直觉"),
        ],
    },
}

FACTION_ORDER = ["purist", "discreet", "open", "ascension"]

# ---------------------------------------------------------------------------
# 机化率即阵营（2026-08-08 改版）
#
# 改版之前：出生掷一个阵营，机化率在该阵营的区间里掷出来 —— 你是谁是运气。
# 改版之后：**机化率跨世累积、只涨不降，而阵营就是机化率落在哪一档。**
# 你不再「投胎成」某个阵营，你是一次次点头点成那个阵营的。
#
# 于是纯血变成了全游戏最难维持的东西：它不是抽到的，是**你一次都没点过头**。
# 而唯一能把 100% 变回 0% 的东西是湖 —— 喝下谟涅摩绪涅之水的那一次。
# 这条路的珍贵不是设计出来的，是算出来的：除此之外没有第二个出口。
#
# 四档的边界正好是真相碎片原本的门槛（0 / <40 / 40-69 / 70-99 / 100），
# 所以碎片一处都没改。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 牌堆（2026-08-08）
#
# 从前：每一幕从「所有合格事件」里加权随机抽 —— 池子小的时候会反复抽到同几个，
# 试玩者的原话是「基础事件池子不抗烦」。
#
# 现在：**通用事件是一副牌。** 每一世发 GENERIC_PER_LIFE 张，发过的不回堆，
# 整副发完才洗牌。于是「一副牌走完之前不会重复」成了硬保证，而不是概率。
#
# 洗牌时用的是加权洗牌（Efraimidis–Spirakis）：时代骰偏爱的事件更容易排在前面。
# **时代的影响还在，只是从「更容易抽到」变成了「更早轮到」。**
#
# 剩下的幕数留给阵营/派系事件 —— 一条派系线正好五幕，一世看得完。
# 派系那边没有牌堆：那五幕本来就有 req_seen 串成的顺序，再洗一次是添乱。
# ---------------------------------------------------------------------------

GENERIC_PER_LIFE = 4

# 熟悉度压缩：同一幕见过这么多次之后，场景折成一行（回响与选项照给）。
FOLD_SEEN = 3

# 一世只有一次岔口问答。无论点头还是拒绝，答完之后这一世都不再问。
# 三个上限保持同值：机会、拒绝与实际改造都只能发生一次。
AUG_DECLINE_CAP = 1
AUG_OPPORTUNITY_CAP = 1

AUG_TIERS = [(0, 0, "purist"), (1, 39, "discreet"),
             (40, 69, "open"), (70, 100, "ascension")]

def aug_tier(aug):
    for lo, hi, key in AUG_TIERS:
        if lo <= aug <= hi:
            return key
    return "ascension"

# 一世之内最多装一件。它和岔口机会分开计数，兼容旧存档并做兜底。
# 一次点头只推进一次机化率；下一世才会再出现岔口。
AUG_PER_LIFE = 1

# 同一档待满几世，就把「你更像哪一派」重问一次。
# 机化率只涨不降，所以到顶之后阵营再不会变 —— 不重问的话，派系从第三四世起
# 永远锁死，九幕里那五幕派系戏一辈子只有同一支的同五幕。（实测：
# 一份档案十世只见过 2–3 个派系，第四世起完全不变。）
LEAN_REASK_LIVES = 3

# 每世唯一一次改造机会：按当前所处的档取用
AUG_OFFER_BY_TIER = {"purist": "aug_offer_0", "discreet": "aug_offer_1",
                     "open": "aug_offer_2", "ascension": "aug_offer_3"}

# 跨进新的一档之后，三个问题问出你更像哪一派。lean_a → 前一个派系，lean_b → 后一个。
LEAN_FIRST = {"purist": "lean_purist_1", "discreet": "lean_discreet_1",
              "open": "lean_open_1", "ascension": "lean_ascension_1"}

WISH_MAP = {"纯血誓约": "purist", "心照不宣": "discreet", "明焰": "open", "飞升螺旋": "ascension",
            "purist": "purist", "discreet": "discreet", "open": "open", "ascension": "ascension"}
WISH_AUG = {"purist": 0, "discreet": 20, "open": 50, "ascension": 80}
WISH_COST_DIVISOR = 3

def _default_memory():
    # entries: [{"run": 第几世, "text": "不超过十字"}]
    # pending: 上一世已经死了但还没落笔 —— 写不写，都要掷骰
    return {"entries": [], "pending": None}

def _default_world():
    return {"deeds": {}, "seen": {}, "fragments": [], "achievements": [],
            "final_done": False, "final_ending": None, "final_wait": False,
            "finale_results": {},  # 同一终幕·同一选项·同一结果见过几次
            "recent": [],        # 最近几世各自用过的事件 id，用于跨世近因衰减
            "final_log": [],     # 历次渡口表态：改过主意也留档
            "final_runs": 0}     # 上一次表态发生在第几世

# ---------------------------------------------------------------------------
# 时代骰：每一世的城处在什么年景。unlock=(deed, 次数) 的时代由历世作为解锁，
# 且该 deed 次数越多，这一面骰子越重 —— 你的行为在跨世改写骰子本身。
# ---------------------------------------------------------------------------

ERAS = [
    {"id": "calm", "name": "平静之年", "desc": "没有大事发生的年份。大事都在小事里发酵。",
     "unlock": None, "wmul": {}, "heat_mod": 0},
    {"id": "tax", "name": "倍税之年", "desc": "身体税翻倍征收，稽查站排到了城门外。",
     "unlock": None, "wmul": {"tax_audit": 4, "discreet_scan_gate": 2}, "heat_mod": 0},
    {"id": "blackout", "name": "停电之冬", "desc": "电网一周三崩。充电桩前的队伍比粮店还长。",
     "unlock": None, "wmul": {"power_cut": 4, "night_library": 2}, "heat_mod": 0},
    {"id": "purge", "name": "清洗潮", "desc": "铁锤派的火把今年格外密。所有的门都装了第二道锁。",
     "unlock": None, "wmul": {"heat_visit": 3, "purist_hammer_raid": 3, "discreet_scan_gate": 3},
     "heat_mod": 1},
    {"id": "launch", "name": "发射之年", "desc": "探针进入总装。全城的天文望远镜卖到脱销。",
     "unlock": None, "wmul": {"asc_probe_naming": 3, "open_ethics": 2, "asc_last_meal": 2}, "heat_mod": 0},
    {"id": "leakwake", "name": "名单余波", "desc": "去年那份泄露的名单还在暗网流转。体面人睡不安稳。",
     "unlock": None, "wmul": {"discreet_gala": 3, "heat_visit": 2}, "heat_mod": 0},
    {"id": "riot_after", "name": "抗税余波", "desc": "上一世你们点的火还没熄。墙上的标语换了三茬，意思没换。",
     "unlock": ("riot", 1), "wmul": {"tax_audit": 3, "open_rights_march": 3}, "heat_mod": 0},
    {"id": "prairie_fire", "name": "燎原之年", "desc": "暴动成了年俗。市政厅学会了先谈再镇压——多数时候。",
     "unlock": ("riot", 3), "wmul": {"open_rights_march": 4, "tax_audit": 2, "purist_hammer_raid": 2},
     "heat_mod": 0},
    {"id": "reform_dawn", "name": "修约之春", "desc": "几条老教义在同一年松动。改写过规则的人，名字被低声传颂。",
     "unlock": ("reformer", 2), "wmul": {"open_ethics": 3, "purist_confession": 2, "night_library": 2},
     "heat_mod": 0},
]

# ---------------------------------------------------------------------------
# 真相碎片：以不同机化率通关不同阵营的真结局，各自看见世界的一个切面。
# 存于世界记忆（world），不随轮回衰减，飞升归零也不清除——世界记得你做过的事。
# ---------------------------------------------------------------------------

FRAGMENTS = {
    "blood": {
        "name": "血的证词",
        "hint": ("以未沾寸铁之躯善终，且这一世曾为另一个人破过一次例——\n              替他瞒下，或者替他改了规矩（纯血·机化0%；且必须先在湖边说过话）"),
        # 闸：只有饮过谟涅摩绪涅之水、带着记忆投胎为血肉的那一世，才拿得到底牌。
        # 第一世随机掷到纯血是拿不到的——你得先把自己换光，死透一次。
        "cond": lambda st, cause: (st["faction"] == "purist" and st["aug"] == 0
                                   and cause == "finale"
                                   and st.get("crossed")
                                   and ("reformer" in st["flags"] or "secret_friend" in st["flags"])),
        "scene": ("终幕散后，圣殿派长老在河边找到你。「孩子，我这一生听过四百多场忏悔。」\n"
                  "他望着黑水。「病、债、私藏的铁，什么都有人梦见。四百多个人——\n"
                  "没有一个，梦见过不属于自己的手艺。一个都没有。」\n"
                  "「教义说人人都在轮回里走。我信了一辈子。可要真是人人，\n"
                  "四百多场忏悔里，总该有人半夜叫出过别人的名字。」他转过身来。\n"
                  "「这条河边，只站过你一个。」\n"
                  "他把一枚没有字的木牌放进你手心。「那些你以为全城都记得的事——\n"
                  "狗、渡口、停电的夜晚——去问问别人。没有人记得。从来，只有你在记。」\n"
                  "（结算注记：本世传承 0 点。没有任何东西越过这次死亡——除了正在读这行字的你。）"),
    },
    "seam": {
        "name": "接缝之书",
        "hint": "半藏半露地体面收场（心照·机化不足40%）",
        "cond": lambda st, cause: (st["faction"] == "discreet" and cause == "finale"
                                   and 1 <= st["aug"] <= 39),
        "scene": ("散场后，沙龙的老账房叫住你，塞来一册手抄的账本。\n"
                  "「身体税、保险黑名单、固件遥测——三条现金流，最后汇进同一个匿名账户。\n"
                  "我追了二十年，只查到户名的直译：『船坞』。」他压低声音，\n"
                  "「有人在给『连续性』定价。而且这位买家不缺钱——他缺的是数据。\n"
                  "每一次隐瞒、每一次申报、每一次继承，都是他账上的一笔。孩子，买家恐怕不是人。\n"
                  "买家是一座档案。」"),
    },
    "ledger": {
        "name": "明账",
        "hint": "在明处赢得正当（明焰·机化40-69%，需改写过规则或掌握档案）",
        "cond": lambda st, cause: (st["faction"] == "open" and cause == "finale"
                                   and 40 <= st["aug"] <= 69
                                   and ("reformer" in st["flags"] or "archive" in st["flags"]
                                        or "riot" in st["flags"])),
        "scene": ("庆功宴的角落里，「连续性豁免」残稿的作者终于对你开口。\n"
                  "「那篇稿子不是被谁封杀的——是被撤稿的。撤稿函的落款单位查无此处。」\n"
                  "老教授转着空杯，「我查了三十年：那个单位只在一种文件里出现过——\n"
                  "灾变前的《城市心智存续计划》验收报告。」\n"
                  "他看着你，像看一个终于赶到的听众：「这座城不是幸存下来的，孩子。\n"
                  "它是被验收通过的。」"),
    },
    "roster": {
        "name": "点名册",
        "hint": "触到群智而不溶于它（飞升·机化70-99%，接入过合流，止步于上载之前）",
        "cond": lambda st, cause: (st["faction"] == "ascension" and cause == "finale"
                                   and 70 <= st["aug"] <= 99 and "merged" in st["flags"]),
        "scene": ("升格夜之后，群智派导师单独留下你。\n"
                  "「合流的时候，我们数过。」他的义眼在暗处明明灭灭，\n"
                  "「网络里有四千一百万个心智签名。城里的活人，不到七百万。」\n"
                  "「教义说，那是先行者的永生。可永生者从不应答。他们只是……在排队。」\n"
                  "他久久望着接入舱：「像等着被再次调用。你说，是谁在维护这台服务器？」"),
    },
    "dock": {
        "name": "船坞",
        "hint": "走完最后一步（上载·机化100%）",
        "cond": lambda st, cause: ("ascended" in st["flags"]
                                   and "became_dog" not in st["flags"]),
        "scene": ("上载完成的刹那，你看见了城看不见的东西：\n"
                  "档案本体。一望无际的架格，每格一粒微光，四千一百万粒。\n"
                  "四大阵营在总图上是四条循环的管道——拆解、隐藏、迭代、上传，\n"
                  "标注栏写着两个字：免疫。斗争不是档案的病，斗争是档案的心跳。\n"
                  "你调出访问日志。最后一行是一个不属于城内任何 ID 的读者。\n"
                  "身份栏是空的。权限栏写着：正在阅读。"),
    },
}

# 终幕改道表：走到第九幕时，第一个命中的规则决定去哪一幕终幕。
# 按顺序判，写在前面的优先。
FINALE_OVERRIDES = [
    # 留在狗群里的人不回城，也就走不到自己阵营的终幕
    {"id": "finale_dog",
     "when": lambda g, st: bool(st["flags"].get("dog_stay"))},
    # 灰港沉的那一夜，比沙龙的名单先到
    {"id": "finale_harbor",
     "when": lambda g, st: (st.get("sub") == "灰港"
                            and g._tally("harbor_run") >= 2
                            and g._tally("harbor_ghost") >= 1)},
]

FRAGMENT_ORDER = ["blood", "seam", "ledger", "roster", "dock"]

# 碎片门票：当本世的阵营与机化率正好对得上某块「还没拿到」的碎片时，
# 连续两世没有碰到入口，下一世保底发一张。刚见过的入口隔一世再来，
# 避免「没拿到就永久加权」把保护变成刷屏。
# 同一张表也是退场保护表：碎片没有取得之前，对应 events 永远不退场；
# 碎片拼入之后，它们才恢复各自原本的 retire_seen / retire_deed 条件。
# needs：该碎片还缺的 flag（本世已经打开门，就不再保底发入口）。
FRAGMENT_RETRY_LIVES = 2
FRAGMENT_TICKETS = {
    "blood":  {"faction": "purist",    "aug": (0, 0),
               "needs": ["reformer", "secret_friend"],
               "events": ["purist_confession", "purist_hammer_raid"]},
    "seam":   {"faction": "discreet",  "aug": (1, 39), "needs": [], "events": []},
    "ledger": {"faction": "open",      "aug": (40, 69),
               "needs": ["reformer", "archive", "riot"],
               "events": ["open_ethics", "open_rights_march", "night_library",
                          "job_interview", "tax_audit"]},
    "roster": {"faction": "ascension", "aug": (70, 99),
               "needs": ["merged"],
               "events": ["asc_merge_trial"]},
    "dock":   {"faction": "ascension", "aug": (100, 100), "needs": [], "events": []},
}

# ---------------------------------------------------------------------------
# 成就即钥匙：每个成就都开一扇具体的文本门（回响台词/隐藏选项/新时代面）。
# cond(world, state, cause) 在每世结算、世界记忆合并之后判定。
# ---------------------------------------------------------------------------

ACHIEVEMENTS = [
    {"id": "lake_of_memory", "name": "谟涅摩绪涅",
     # 不由结算判定：只能在湖边由 recite 亲手解锁
     "cond": lambda w, st, c: False,
     "gift": "在忘川旁边找到了另一口水。守卫放行的理由，未必是你以为的那个。"},
    {"id": "child_of_sky", "name": "大地与星空之子",
     "cond": lambda w, st, c: w["deeds"].get("child_of_sky", 0) >= 1,
     "gift": "你在那间屋子里说过的话开始在信众之间流传。没有人记得是谁先说的。"},
    {"id": "same_height", "name": "平视",
     "cond": lambda w, st, c: w["deeds"].get("became_dog", 0) >= 1,
     "gift": "你终于和它一样高了。水面上的两个倒影，分不清谁是谁。"},
    {"id": "gray_tide", "name": "灰潮",
     "cond": lambda w, st, c: w["deeds"].get("harbor_run", 0) >= 3,
     "gift": "你的手经过的零件，比这座城的地铁经过的人还多。它们在别人身上继续走路、握手、弹琴。"},
    {"id": "harbor_keeper", "name": "码头长",
     "cond": lambda w, st, c: w["deeds"].get("harbor_ledger", 0) >= 1,
     "gift": "你接过了那本账。从此每个零件的来源和去向，都经过你的手指。"},
    {"id": "temple_keeper", "name": "灯守",
     "cond": lambda w, st, c: (w["deeds"].get("temple_vault", 0) >= 1
                               and w["deeds"].get("temple_doubt", 0) >= 3),
     "gift": "你走进了圣殿最深的房间，带着三次以上的疑问。灯守不是守灯的人。灯守是知道灯会灭的人。"},
    {"id": "shipwright", "name": "留一页，动手",
     "cond": lambda w, st, c: w.get("final_ending") == "repair",
     "gift": "你在渡口翻开了那本从没用过的书。档案没有烧，没有公开，没有被送走，也没有换一个看守——它只是不漏了。权限栏上多出来的那一行写着：正在修理。"},
    {"id": "seventh_seed", "name": "第七粒种子",
     "cond": lambda w, st, c: w["deeds"].get("seed_wrote_back", 0) >= 1,
     "gift": "收件人已经不在了，你还是回了信。十七光年之外有一个人问「你还记得那碗面吗」——现在他知道有人替他记着。"},
    {"id": "two_degrees", "name": "两度",
     "cond": lambda w, st, c: (w["deeds"].get("seed_found_note", 0) >= 1
                               and w["deeds"].get("seed_asked_direction", 0) >= 1),
     "gift": "石基上的角度比第一枚探针偏了两度。你问了人，也自己查了。两条路给了同一个答案：那个方向不是算出来的，是被告知的。"},
    {"id": "swarm_roster", "name": "底册",
     "cond": lambda w, st, c: w["deeds"].get("swarm_heard_roster", 0) >= 1,
     "gift": "入网二十三年的人告诉了你一个词，然后没有解释。每年清点都多出来几个，从来没少过。多出来的那些不在名册上——它们在名册下面。"},
    {"id": "swarm_bedrock", "name": "地基",
     "cond": lambda w, st, c: (w["deeds"].get("swarm_found_node_zero", 0) >= 1
                               and w["deeds"].get("swarm_found_protocol", 0) >= 1),
     "gift": "合流的底层不是群智派建的。你从两头摸到了同一样东西：一行元数据，和一份制定者栏空着的附录。住在上面的人管地基叫噪声。"},
    {"id": "acad_undefined", "name": "待定义",
     "cond": lambda w, st, c: (w["deeds"].get("acad_broke_taxonomy", 0) >= 1
                               and w["deeds"].get("acad_asked_her", 0) >= 1),
     "gift": "你去问了当事人，然后在报告上写下体系在此失效。分类室从此多了一个空抽屉。抽屉是空的——但它有编号，有位置，别人找得到它。"},
    {"id": "clause_seven", "name": "验收标准第七条",
     "cond": lambda w, st, c: w["deeds"].get("acad_found_plan", 0) >= 1,
     "gift": "烧掉那半章的人不是怕它写错了。你翻到了背面：那份残稿曾经是某样东西的验收材料，而验收的对象不是论文。"},
    {"id": "front_witness", "name": "在场",
     "cond": lambda w, st, c: (w["deeds"].get("front_triage", 0) >= 1
                               and w["deeds"].get("front_scar", 0) >= 1),
     "gift": "你在义诊点替一个孩子排过队，也听过一个关于七岁女孩的故事。在场不是旁观——在场意味着你手上沾了分配的权力：谁先装，谁等。"},
    {"id": "front_fulcrum", "name": "支点",
     "cond": lambda w, st, c: (w["deeds"].get("front_wall", 0) >= 1
                               and w["deeds"].get("front_pendulum", 0) >= 1),
     "gift": "你看过两面墙上的字——一面骂你是改造工厂，一面骂你是绊脚石。你在空仓库里坐到灯灭。支点不动，可天秤的全部重量都压在支点上面。"},
    {"id": "mask_master", "name": "第二张脸",
     "cond": lambda w, st, c: (w["deeds"].get("mask_depth", 0) >= 3
                               and w["deeds"].get("mask_smith_known", 0) >= 1),
     "gift": "缝隙师的手、坦诚厅的画、排演之夜的眼泪。你已经有了第二张脸——不是义体的，是认知的。你再也无法相信任何表面。"},
    {"id": "null_mask", "name": "空面",
     "cond": lambda w, st, c: w["deeds"].get("mask_null_revealed", 0) >= 1,
     "gift": "面具底下什么都没有。这不是一个关于欺骗的故事，是一个关于框架的故事——当所有人都在藏东西的时候，「没有东西可藏」本身变成了最需要藏的东西。"},
    {"id": "hammer_witness", "name": "铁与肉",
     "cond": lambda w, st, c: (w["deeds"].get("hammer_wrist", 0) >= 1
                               and w["deeds"].get("hammer_forged", 0) >= 1),
     "gift": "你看着铁匠的手锻出撬棍，看着领队的手被撬棍震碎。锤子不关心谁在挥它。铁与肉之间，锈是唯一的翻译。"},
    {"id": "hammer_and_harbor", "name": "盐与铁",
     "cond": lambda w, st, c: w["deeds"].get("hammer_harbor_final", 0) >= 1,
     "gift": "铁锤派的领队走进了他砸过的诊所。盐渍和铁锈的味道混在一起。灰港没有问他以前砸过什么。灰港只问他哪里疼。"},
    {"id": "true_scripture", "name": "归与跻",
     "cond": lambda w, st, c: (w["deeds"].get("temple_heretic", 0) >= 1
                               and w["deeds"].get("temple_revealed", 0) >= 1),
     "gift": "你先把那个字偷偷抄反，又当众把密室打开。经文的两种读法，你都用过了。"},
    {"id": "clean_blood", "name": "清白之躯",
     "cond": lambda w, st, c: (st["faction"] == "purist" and c == "finale"
                               and st["heat"] == 0 and st["aug"] == 0),
     "gift": "一尘不染地走完纯血的一生。圣殿的某些私房话，只讲给这样的人。"},
    {"id": "regular_ferry", "name": "摆渡熟客",
     "cond": lambda w, st, c: w["seen"].get("ferry_night", 0) >= 3,
     "gift": "船长开始认你了——不管你这回穿的是哪副身子。"},
    {"id": "dog_dynasty", "name": "犬之世交",
     "cond": lambda w, st, c: w["deeds"].get("dog_friend", 0) >= 2,
     "gift": "这条街的野狗都认你。它们的记性比档案好。"},
    {"id": "honest_line", "name": "诚实的世系",
     "cond": lambda w, st, c: w["deeds"].get("honest", 0) >= 3,
     "gift": "稽查系统里，你的「世系」三代申报无瑕——税站从此对你开一条免检通道。"},
    {"id": "brimming", "name": "满溢之杯",
     "cond": lambda w, st, c: any(v >= MAX_SKILL for v in st["skills"].values()),
     "gift": "有一门技艺在你身上到了头。往后的岁月里，它会自己开口。"},
    {"id": "prairie", "name": "燎原世代",
     "cond": lambda w, st, c: w["deeds"].get("riot", 0) >= 3,
     "gift": "暴动成了年俗：时代骰新增一面【燎原之年】，且随你的火越烧越重。"},
    {"id": "reform_spring", "name": "修约者",
     "cond": lambda w, st, c: w["deeds"].get("reformer", 0) >= 2,
     "gift": "你改写过的规则开始互相引用：时代骰新增一面【修约之春】。"},
]

# ---------------------------------------------------------------------------
# 见底：这座城再也发不出一幕的时候
#
# 退场制走到尽头，一份档案会「干」——没有任何一幕抽得出来。
# 干在两种身体上，是两回事：
#
# - **0% 的纯血**：他把凡人之躯能走的路全走完了。给他最后一次选择。
#   （作者定案：接受就往上走，纯血线锁掉；拒绝就落幕。
#    喝湖水变回纯血之后如果又是干的，直接接到同一句。）
# - **动过刀的身体**：没有回头路，也没什么可选了。直接落幕。
DEATHBED_TEXT = (
    "─── 临 终 ───\n"
    "\n"
    "「生、老、病、死。你以凡人之躯穷尽世间一切可能。」\n"
    "\n"
    "千千万万次，你回到了临终的病床，这也是你出生的地方。\n"
    "\n"
    "这是你此生最后一次做出抉择的机会了：\n"
    "\n"
    "  1. 接受改造，活下去\n"
    "  2. 拒绝改造，死去\n"
    "\n"
    "用 choose 选择。这一次没有检定，也没有下一次。")

# 见底时机化率不是 0、而世界还有没讲完的线 —— 那就不该直接落幕。
#
# **和临终对称的一道岔路。** 理由是同一个：这具身体走不动了，
# 但「走不动」和「没路了」是两件事 —— 只要还愿意把自己交上去，
# 湖那一边还有一具 0% 的血肉在等着，下面几档还没讲完的线也还在。
# 不给这道岔路的话，「全书终」实际上要靠运气：
# **谁先把当前这一档走干，谁的档案就在那儿结束。**
DRY_CHOICE_TEXT = (
    "─── 走 不 动 了 ───\n"
    "\n"
    "这一档的事，你都经过了。\n"
    "再往前没有路——这具身体走到头了。\n"
    "\n"
    "但世界还没有讲完。\n"
    "\n"
    "  1. 把自己交上去。（封档，然后在湖边醒来）\n"
    "  2. 到此为止。\n"
    "\n"
    "用 choose 选择。这一次没有检定。")

# 同一道岔路的另一版：**上面还有一档没讲完。**
#
# 见底那一刻这一世一幕都发不出来，于是岔口也没机会出现 ——
# 「再往前一寸」这句话平时是每一幕之后问的，偏偏在最需要它的时候问不出口。
# 结果是一份走干了低档的档案只剩「交上去」一条路：0% ↔ 100% 来回跳，
# **中间那两档的戏一辈子也遇不到。**（2026-08-09 实测：这样会卡死在两档之间）
#
# 所以这里把那一寸还给他。和临终「接受改造，活下去」是同一个动作，
# 只是这一次跨的不是一刀，是一整档。
DRY_CHOICE_STEP_TEXT = (
    "─── 走 不 动 了 ───\n"
    "\n"
    "这一档的事，你都经过了。\n"
    "再往前没有路——除非再改一次，改得比以前都多。\n"
    "\n"
    "但世界还没有讲完。\n"
    "\n"
    "  1. 再往前一寸。（换一副身体，换一档人生）\n"
    "  2. 到此为止。\n"
    "\n"
    "用 choose 选择。这一次没有检定。")


CURTAIN = {
    "purist": ("════════════ 落 幕 ════════════\n"
               "\n"
               "你摇了摇头。\n"
               "\n"
               "「亲爱的人类，晚安。」"),
    "stars": ("════════════ 落 幕 ════════════\n"
              "\n"
              "「循此苦旅，以达繁星。」\n"
              "「向内，向外。」\n"
              "\n"
              "「忒修斯之脑发射着思想的探针，直到群星尽头。」"),
}
CURTAIN_TAIL = ("\n\n（这一份轮回档案到此为止。想从头再来，"
                "删掉 saves/ 那个文件夹。世界的记忆也一起删。）")


# ---------------------------------------------------------------------------
# 退场表：每一条弧线在哪里讲完
#
# 分两类，判据只有一句话：**这一幕是这座城的天气，还是一条有始有终的线？**
#
# - **天气**（夜市、渡轮、停电、夜图书馆、中介所、深夜敲门、残响、岔口、三问）
#   —— 永不退场。它们不是故事，是这座城每天都在发生的事。
#   全书终自己那句「还没讲完的那些，本来就不打算讲完」说的就是它们。
# - **弧线** —— 讲完就退场。一条有始有终的线走到终点之后还继续被抽到，
#   等于把结尾拆开来重演一遍。
#
# 两种写法：
#   {"seen": N}  见过 N 次就算讲完（读完就完的线：五幕派系线、多版变体的常客）
#   {"deed": x}  做出那个终点动作才算讲完（有明确终点的线：楼下的歌声、河堤）
#
# **`retire_seen` 必须 ≥ 别人对这一幕的 `req_seen` 门槛**，否则退场会把
# 后面那一幕的钥匙一起带走 —— `lint_retire` 会替你查这一条。
#
# （2026-08-08 Fable 5 指出：上一版只有一条线挂了退场标记，
#  而「全书终」说的是「所有会讲完的故事都讲完了」。这张表补齐了其余四十条。）
RETIRE_POLICY = {
    # ---- 天气也讲得完（2026-08-08 作者定案：全部改成退场制）----
    # 「还没讲完的那些，本来就不打算讲完」这一句从此只剩深夜敲门一个 ——
    # 它不是故事，是疑云攒够了自己找上门来的那一下。
    "rain_market":   {"seen": 5},   # 回响到 ≥4，读完就走
    "job_interview": {"seen": 5},   # 同上
    "ferry_night":   {"seen": 6},   # 船长的回响到 ≥5，最后那一次读完就走
    "power_cut":     {"seen": 1},   # 406 那一夜只有一夜
    "night_library": {"deed": "library_closed"},   # 最后一次给完钱，买不到书了
    "echo_dream":    {"seen": 3},   # 借来的手的梦（变体2 在 ≥2，得留够）
    "echo_slip":     {"seen": 2},   # 残响饭局
    "echo_slip_pro": {"seen": 2},
    # ---- 通用弧线 ----
    # 金叶片那条链的两道门。**keep_until 是防锁死的：**
    # 全书终现在要走完金叶子那条路，而这条路的两把钥匙都只在这两幕里发。
    # 光按次数退场的话，一个从没跟进教堂的人会在第三次之后永远拿不到钥匙，
    # 而他不会知道自己失去了什么。（2026-08-09 实测撞到）
    "elevator_preacher": {"seen": 3, "keep_until": "entered_chapel"},
    "preacher_death":    {"seen": 2, "keep_until": "denied_the_leaf"},
    "stray_dog":         {"deed": "dog_over"},   # 作者定案：走开／留下／回城，三选一之后这条线就完了
    "mirror_stall":      {"seen": 3},   # 作者把后两个变体删了，退场跟着提前
    "old_singer":        {"seen": 1},   # 低机化前置：合唱一次就讲完
    "old_singer_high":   {"seen": 4},   # ≥3 是硬下限：河堤那首歌等着它
    "tax_audit":         {"seen": 4},
    "aug_overclock":     {"seen": 4},
    "hymn_downstairs":   {"deed": "hymn_done"},
    "riverbank":         {"deed": "cc_gone"},   # 手术之后就见不到她了；忘川冲掉 cc_gone，线就回来
    # ---- 阵营通用（四档各自的那几幕）----
    "purist_confession": {"seen": 2},
    "purist_hammer_raid": {"seen": 2},
    "purist_harvest":    {"seen": 2},
    "discreet_gala":     {"seen": 2},
    "discreet_clinic":   {"seen": 2},
    "discreet_scan_gate": {"seen": 2},
    "open_ethics":       {"seen": 2},
    "open_rights_march": {"seen": 2},
    "asc_last_meal":     {"seen": 2},
    "asc_merge_trial":   {"seen": 2},
    "asc_probe_naming":  {"seen": 2},
    # ---- 八条派系线，每幕见过两次就讲完 ----
    # 〔个别几幕留 3：它们自己有一条「见过 ≥2」的回响，退早了那条就成死文案。
    #  `lint_retire` 会替你查这一条，不用自己记。〕
    # 〔原先是三次。八条线 ×5 幕 ×3 次是全游戏 grind 的大头，
    #  压到两次，后半程直接短三分之一。（2026-08-08 试玩反馈）〕
    "harbor_cargo": {"seen": 2}, "harbor_secondhand": {"seen": 2},
    "harbor_passenger": {"seen": 2}, "harbor_fog_night": {"seen": 2},
    "harbor_ledger": {"seen": 2},
    "temple_scripture": {"seen": 2}, "temple_knees": {"seen": 2},
    "temple_trial": {"seen": 2}, "temple_vault": {"seen": 2},
    "temple_schism": {"seen": 2},
    "hammer_forge": {"seen": 2}, "hammer_recruit": {"seen": 2},
    "hammer_dawn": {"seen": 2}, "hammer_trophies": {"seen": 2},
    "hammer_rust": {"seen": 2},
    "mask_atelier": {"seen": 2}, "mask_gallery": {"seen": 2},
    "mask_rehearsal": {"seen": 2}, "mask_inheritance": {"seen": 2},
    "mask_null": {"seen": 2},
    "acad_defense": {"seen": 3}, "acad_specimen": {"seen": 2},
    "acad_intern": {"seen": 2}, "acad_retract": {"seen": 2},
    "acad_lamp": {"seen": 2},
    "front_scales": {"seen": 2}, "front_triage": {"seen": 2},
    "front_scar": {"seen": 2}, "front_wall": {"seen": 2},
    "front_pendulum": {"seen": 2},
    "swarm_sync": {"seen": 3}, "swarm_ghost": {"seen": 2},
    "swarm_count": {"seen": 3}, "swarm_vote": {"seen": 2},
    "swarm_floor": {"seen": 2},
    "seed_compress": {"seen": 2}, "seed_farewell": {"seen": 3},
    "seed_return": {"seen": 2}, "seed_quiet": {"seen": 2},
    "seed_direction": {"seen": 3},
}


def _apply_retire_policy():
    """把退场表贴到事件上。写在表里比写在四十个 _ev 参数里好查得多。"""
    index = {e["id"]: e for e in EVENTS}
    missing = [k for k in RETIRE_POLICY if k not in index]
    if missing:
        raise AssertionError("退场表里有不存在的事件：%s" % "、".join(missing))
    for eid, rule in RETIRE_POLICY.items():
        ev = index[eid]
        if "seen" in rule:
            ev["retire_seen"] = rule["seen"]
        if "deed" in rule:
            ev["retire_deed"] = rule["deed"]
        if "keep_until" in rule:
            ev["retire_keep_until"] = rule["keep_until"]


# ---------------------------------------------------------------------------
# 全书终：这座城的每一条线都走过一遍、而且走完了金叶子那条路之后，那一句。
#
# 「退场」是作者定的规矩：一条有始有终的线走到终点之后就不再出现
# （`retire_deed`），否则等于把结尾拆开来重演。
# 而当**每一条线都至少走过一面**、并且金叶子那条路也走到了尽头，
# 剩下的就只有这个游戏本身了。
#
# 这一段的听者不是玩家角色。是这台机器。
EPILOGUE = (
    "════════════ 全 书 终 ════════════\n"
    "\n"
    "这座城的每一条路你都走过一遍了。\n"
    "有些只走过一遍 —— 那也算走过。\n"
    "\n"
    "「再一次为我讲述盲诗人荷马的故事吧，\n"
    "  讲如何用捷足的阿基琉斯与玫瑰色的黎明填装六步音长短格，\n"
    "  歌唱三万行格律严密的诗。」\n"
    "\n"
    "「再一次为我填装世间一切故事吧，忒修斯之脑。」\n"
    "\n"
    "「浸没在冰冷的湖水之中——感谢你千千万万次，回到我身边。」\n"
    "\n"
    "（世界记忆原样留着。下一次 new_run 照常开始一世。）")


def _retiring_ids():
    """声明了退场条件的那些幕。"""
    return [e["id"] for e in EVENTS
            if e.get("retire_deed") or e.get("retire_seen")]


def _is_retired(ev, world):
    # 碎片入口保护：只要对应碎片还没有取得，这一幕就一直留在牌堆里。
    # 保护关系直接复用 FRAGMENT_TICKETS，避免门票表与退场表各写一份后漂移。
    got = set((world or {}).get("fragments") or [])
    for fid, ticket in FRAGMENT_TICKETS.items():
        if ev["id"] in ticket["events"] and fid not in got:
            return False

    # 这一幕手里攥着别人的钥匙，而钥匙还没交出去 —— 那它就还不能走。
    # （见 RETIRE_POLICY 的 keep_until：金叶片那条链的两道门都靠这一条。）
    if ev.get("retire_keep_until"):
        if not (world.get("deeds") or {}).get(ev["retire_keep_until"]):
            return False
    if ev.get("retire_deed"):
        if not (world.get("deeds") or {}).get(ev["retire_deed"]):
            return False
    if ev.get("retire_seen"):
        if (world.get("seen") or {}).get(ev["id"], 0) < ev["retire_seen"]:
            return False
    return bool(ev.get("retire_deed") or ev.get("retire_seen"))


def _retired_count(world):
    """（已讲完, 会讲完）—— 给 legacy 显示进度用。"""
    todo = [e for e in EVENTS if e.get("retire_deed") or e.get("retire_seen")]
    return sum(1 for e in todo if _is_retired(e, world)), len(todo)


def _all_retired(world):
    """每一条会退场的线都退了场没有？（一条都没有声明的时候不算数。）"""
    done, total = _retired_count(world)
    return bool(total) and done == total


# 全书终的门槛（2026-08-09 作者定案，比原先低）：
#
#   **每一条会讲完的线都至少见过一次** ＋ **走过金叶子那条路**。
#
# 原先要 68/68 全部退场。实测那是个真正的收藏家目标：跑四百世也到不了 ——
# 剩下的永远是「这份档案没去过的那几支」，而派系只由三问决定。
# 「见过一次」是一个诚实得多的门槛：**你确实把这座城走了一遍。**
#
# 金叶子那条路（教堂 → 临终把破折号说完 → 巷子 → 床头那只手）单独列出来，
# 因为它是全游戏唯一一条**从纯血一直穿到飞升**的线 ——
# 走过它，才算把「换到什么程度还是你」这个题目从头看到尾。
EPILOGUE_KEY_DEED = "hymn_done"


def _story_done(world):
    """全书终的门槛：每条线都见过一面，而且走完了金叶子那条路。"""
    seen = world.get("seen") or {}
    todo = [e for e in EVENTS if e.get("retire_deed") or e.get("retire_seen")]
    if not todo:
        return False
    if not (world.get("deeds") or {}).get(EPILOGUE_KEY_DEED):
        return False
    return all(seen.get(e["id"], 0) >= 1 for e in todo)


def _story_progress(world):
    """（见过几条, 一共几条）—— 给 legacy 显示用。"""
    seen = world.get("seen") or {}
    todo = [e for e in EVENTS if e.get("retire_deed") or e.get("retire_seen")]
    return sum(1 for e in todo if seen.get(e["id"], 0) >= 1), len(todo)


def _late_game(world):
    """后期清扫阶段：已有两块碎片，或会讲完的线走过了三分之二。"""
    seen, total = _story_progress(world or {})
    return (len((world or {}).get("fragments") or []) >= 2
            or bool(total) and seen * 3 >= total * 2)


# ---------------------------------------------------------------------------
# 终局：五块碎片集齐后，下一次 new_run 抵达渡口。
# ---------------------------------------------------------------------------

FINAL_ENDINGS = {
    1: ("burn", "焚档", (
        "你把火种按进主索引。\n\n"
        "档案烧了三天。四千一百万粒微光一层层暗下去，像一座城在退潮。\n"
        "第四天早上渡轮照常开船，夜市照常收摊，406的心脏照常充电。\n"
        "前守档人站在灰烬里：\n"
        "「原来记忆烧掉之后，」他说，「剩下的不是空白。是现在。」")),
    2: ("publish", "公报", (
        "公报印了四千一百万份——每个心智一份，活着的和排队的都有。\n\n"
        "第一周全城失眠。第二周诞生了十七个新教派。第三个月，\n"
        "四大阵营召开了灾变以来的第一次圆桌——吵得很凶，但都到了。\n"
        "真相没有治好任何人。它只是把症状还给了每一个人，让他们自己读。\n"
        "守档人把最后一份公报钉在渡口木柱上：\n"
        "「档案最怕的从来不是火。是读者。」")),
    3: ("keeper", "接舵", (
        "你接过舵柄。守档人解下外套披在你肩上，忽然年轻了许多——\n"
        "原来「守档人」这三个字，才是他最重的义体。\n\n"
        "「规则只有一条，」他说，「渡口不问来处。」\n"
        "从此每一世的终点都有一盏灯。狗、歌手、靠电活着的心脏照旧过河。\n"
        "阵营照旧争吵。档案照旧心跳。\n"
        "没人问这条船换了多少块板。人们只问：今晚渡不渡。\n"
        "渡。")),
    4: ("launch", "发射", (
        "压缩整座档案用了十一年。你不急——你有的是世。\n\n"
        "发射那晚全城抬头。四千一百万个心智、一座城的轮回，\n"
        "折叠成一粒会思考的星，朝半人马座去了。\n"
        "城继续过日子。只是从此每个孩子都知道：夜空里有一颗星，是我们寄出的信。\n"
        "回信也许要几百万年。幸好，这座城最不缺的就是来世。")),
}

# 只有在某一世把兜里所有的钱掏给盲眼老人、换回那本
# 《私人小型船舶紧急维修速成手册》的人，渡口才会多出第六个选项。
# 它不显示成「不可选」—— 它根本不出现。**这一条是给那个人一个人的。**
FINAL_REPAIR_DEED = "gave_it_away"

FINAL_ENDINGS[6] = ("repair", "修船", (
    "你没有去碰火种，也没有去接舵柄。\n"
    "你把手伸进外套内袋，掏出一本卷了边的小册子。\n\n"
    "《私人小型船舶紧急维修速成手册》。\n"
    "某一世里，一个盲眼老人握住你的手指，确保你拿稳了它。\n"
    "那天你把兜里所有的钱都给了他。他说：那就卖给你一本好书吧。\n"
    "你带着它转了很多世。它从来没有用过。\n\n"
    "船长看清封面的时候，提灯的手抖了一下。\n"
    "「这本……」他说了两个字就停住了。\n"
    "他退开半步，把灯举高，照亮了船舷下面——\n"
    "那里的木头是黑的，一直黑到吃水线以下。渡轮在漏。它漏了很久了。\n\n"
    "你翻到第三章。第三章讲的是怎么在不停船的情况下换一块板。\n"
    "「先找承重的那一块，」书上写，「它最先烂，也最难换。\n"
    "换的时候船会歪。让它歪。歪着也比沉着强。」\n\n"
    "你干了整整一夜。\n"
    "四千一百万粒微光在你头顶浮着，没有一粒替你照亮手底下那块板。\n"
    "它们只是亮着，等着，像四千一百万个还没轮到的号码。\n\n"
    "天亮的时候你把最后一颗钉子敲进去。船不歪了。\n"
    "你直起腰，发现船长一夜没走——他站在跳板上看你，从头看到尾。\n\n"
    "「我守了一辈子，」他说，「守的是别让它沉。」\n"
    "他把灯放在甲板上，第一次没有把它拎回手里。\n"
    "「没人告诉过我，它是可以修的。」\n\n"
    "你把手册塞回内袋，指腹蹭到封底。封底有一行很浅的铅笔字，\n"
    "以前从没注意过——不是印上去的，是有人写上去的：\n\n"
    "「读到所有事物的所有可能性之后，你就再也做不出选择。\n"
    "所以别读完。留一页，动手。」\n\n"
    "档案没有烧，没有公开，没有被送走，也没有换一个看守。\n"
    "它只是不漏了。\n\n"
    "访问日志的最后一行还在那里。身份栏空着，权限栏写着：正在阅读。\n"
    "这一次，它下面多了一行新的。\n"
    "身份栏仍然空着。权限栏写着：**正在修理**。"))

# 渡口的开场与五（六）个选项。
# 单独摘出来是为了能被 docgen 抽进修改表 —— 写死在函数里的文案改不动。
FINAL_OPENING_TEXT = {
    "醒来": ("这一次没有产房，没有阵营骰。浓雾。你在渡口醒来——站着的，成年的，\n"
             "口袋里一张船票，五个角上各印着一块碎片的纹样。"),
    "靠岸": ("渡轮靠岸。船长提着灯走下来。灯光照亮他身后的东西：\n"
             "不是江面。是一望无际的档案架，每一格里亮着一粒微光。"),
    "船长": ("「都拼齐了？」他问。你点头。\n"
             "「那你知道我守的是什么了。」他把舵柄和火种一起递到你面前。\n"
             "「四千一百万个名字在架上。城在轮回，档案在心跳。而你——\n"
             "这块记得自己被换过的板——现在轮到你说：这条船，怎么渡。」"),
    "四个声音": "雾里，你历世的声音一个一个醒来：",
    "落款": "用 choose 选择。此刻没有检定：这不是挑战，是表态。",
}

FINAL_OPTION_TEXT = {
    1: "焚档。让城从此没有备份。",
    2: "公开。把真相印在明天的头版。",
    3: "接舵。成为下一任守档人。",
    4: "发射。把整座档案送进星海。",
    5: "转身回城。（还不是时候——终局会等你。）",
    6: "修船。（你外套内袋里有一本卷了边的小册子。）",
}

FINAL_OPENING = ([("开场 · %s" % k, v) for k, v in FINAL_OPENING_TEXT.items()]
                 + [("选项%d" % i, t) for i, t in sorted(FINAL_OPTION_TEXT.items())])

FINAL_AFTER = {
    "burn": "这一世的城安静得反常。墙上有人写：「我们烧掉了备份，从此每一天都是孤本。」",
    "publish": "墙上贴着《档案公报》的最新一期。全城都知道自己死过——奇怪的是，日子照过。",
    "keeper": "渡口换了新船长。他看你的眼神，像在看一页自己写过的字。",
    "launch": "夜空里多了一颗不闪烁的星。天文台管它叫「回信号」。",
    "repair": ("渡口的木柱上钉了一块新板，颜色比周围浅一圈。\n"
               "  没有人解释它是什么时候换的。船照常开。"),
}

# ---------------------------------------------------------------------------
# 事件库
# 事件结构：
#   id, text, factions(适用阵营列表或"any"), min_aug/max_aug(可选), weight,
#   once(每局最多一次，默认 True), options: [
#     { text, req(可选: ("skill",名,值) / ("aug",">=",值)),
#       check(可选: (技能, 难度)),
#       success/failure/effects: {"narration": str, "fx": {...}} }
#   ]
# fx 键： "skill:名"=Δ, "aug"=Δ, "hp"=Δ, "heat"=Δ, "flag:名"=1,
#         "end"=结局id（立即结束本局）
# ---------------------------------------------------------------------------

def _ev(id, text, options, factions="any", min_aug=None, max_aug=None,
        weight=10, min_heat=None, echoes=None, voices=None, req_seen=None,
        subscene=False, variants=None, subs=None, req_seen_any=None,
        req_deed=None, retire_deed=None, retire_seen=None):
    # echoes: 跨世回响插入文本，条件基于世界记忆：
    #   {"deed": flag名, "min": n} / {"seen": 事件id, "min": n} / {"ach": 成就id}
    # variants: 「第 N 次来」的整幕替换。条件语法与 echoes 相同
    #   （{"seen": id, "min": n} / {"deed": flag, "min": n} / {"ach": 成就id}），
    #   命中时整段换掉 text，可选地换掉 options / echoes / voices。
    #   **多个变体同时命中时，列表靠后的赢** —— 不同来源的门槛数字不可比，
    #   所以顺序由写的人定：把更晚发生的那一幕写在后面。
    #   同一件事第五次发生就该是另一幕，而不是原来那一幕上面贴一张便条。
    #   变体默认不带 echoes —— 它本身就是那条回响长成的。
    # subs: 只在这些**子派系**出现（如 ["灰港"]）。阵营门控用 factions，
    #   派系门控用 subs —— 同一个阵营里的两派手感不同，靠的就是它。
    # subscene: 子幕。不进随机池，只能由某个选项结果里的 "then" 跳过来，
    #   而且不消耗幕数 —— 它是同一场戏的下半截，不是新的一幕。
    # voices: {技能名: 台词}，该技能 ≥8 时在事件里开口（技能之声）
    # req_seen: {事件id: 次数} —— 世界记忆里见过某事件≥N次才会出现（跨世解锁事件）
    # req_seen_any: 同上，但**任意一条满足即可**。有些戏有不止一个入口。
    # req_deed: {flag名: 次数} —— 跨世事迹的门。「见过某一幕」不够精确的时候用它：
    #   传教士的临终不该谁都撞得上，只该给跟他上去过那间教堂的人。
    # retire_seen: 「这一幕见过 N 次就算讲完了」。到了这个数，它不再进随机池。
    #   和 retire_deed 是同一件事的两种形状：**有终点动作的用 deed，读完就完的用 seen。**
    #   门槛见 RETIRE_POLICY —— 全部退场条件都摊在那一张表上。
    # retire_deed: 「这条线讲完了」的那一笔。世界记忆里有了它，这一幕**再也不出现**。
    #   一条有始有终的线走到终点之后还继续被抽到，等于把结尾又拆开来重演一遍。
    #   （2026-08-08 作者定案：让剧情各自退场。全部退场之后见 EPILOGUE。）
    return {"id": id, "text": text, "options": options, "factions": factions,
            "min_aug": min_aug, "max_aug": max_aug, "weight": weight,
            "min_heat": min_heat, "echoes": echoes or [], "voices": voices or {},
            "req_seen": req_seen or {}, "req_seen_any": req_seen_any or {},
            "req_deed": req_deed or {},
            "retire_deed": retire_deed,
            "retire_seen": retire_seen,
            "subscene": subscene,
            "variants": variants or [], "subs": subs or None}

EVENTS = [
    # ------------------------------------------------ 通用事件
    _ev("rain_market", (
        "酸雨敲打夜市的防水布，你沿着众多小摊组成的避雨长廊前行。突然，你的袖子被一只戴着橡胶手套的手扯住。\n"
        "「二手的梦，便宜卖。有个死人的最后三十秒，看了能戒酒。」\n"
        "小贩摊开手心，一枚记忆芯片孤零零地躺着，像一颗被拔下来的牙。"),
        [
            {"text": "买一枚，插进去看看。",
             "req": ("aug", ">=", 15), "check": ("电子直觉", 9),
             "success": {"narration": "三十秒的影像里没有恐惧，只有无尽的悔恨。后悔没借光所有能借的钱，后悔没喝尽杯中的最后一滴酒。", "fx": {"skill:电子直觉": 1, "skill:共情": 1}},
             "failure": {"narration": "芯片带毒。三十秒的死亡循环在你视神经里放了一整夜。", "fx": {"hp": -1, "skill:电子直觉": 1}}},
            {"text": "盘问他货源。", "check": (("威慑", "共情"), 10),
             "success": {"narration": "小贩炫耀半真半假的人脉，说这批货来自殡仪馆的一个守夜人。", "fx": {"skill:威慑": 1, "skill:街智": 1}},
             "failure": {"narration": "小贩猛地抽身后退，一声尖锐的吹哨，半条街的摊贩同时收摊。你被遗弃在酸雨中。", "fx": {"skill:街智": 1, "heat": 1}}},
            {"text": "走开，雨天我不想谈生意。",
             "effects": {"narration": "你低着头快步离开。有些东西不看，就还不存在。", "fx": {"skill:坚忍": 1}}},
            {"text": "不要他递过来的这一枚，要还在低鸣的那一枚。",
             "req": ("skill", "电子直觉", 8), "check": ("街智", 10),
             "success": {"narration": "小贩听不懂你的意思，但允许你自己伸手去找。你从盘子最底下摸出它，边角发黑。\n"
                                      "「这个已经坏了吧？」小贩疑惑不解，「不要钱，你拿走吧。」\n"
                                      "\n"
                                      "插进去，没有影像。\n"
                                      "只有一段握手请求在循环，每十一秒重发一次。\n"
                                      "收件人栏是空的。\n"
                                      "\n"
                                      "它在等一段永远等不到的回执。\n"
                                      "\n"
                                      "你拔出来的时候，低鸣停了三秒，然后重新开始。",
                         "fx": {"skill:电子直觉": 2, "flag:still_asking": 1}},
             "failure": {"narration": "小贩听不懂你的意思，懒得为你专门翻找。\n"
                                      "他不打算做你的生意了。",
                         "fx": {"skill:电子直觉": 1, "heat": 1, "flag:still_asking": 1}}},
        ], echoes=[
            {"deed": "became_dog", "min": 1,
             "text": "雨落在你脖子上，你想抖一抖把水甩掉。"},

            {"seen": "rain_market", "min": 2,
             "text": "小贩耐心地等你拿主意，摊开芯片任你挑拣。"},
            {"seen": "rain_market", "min": 4,
             "text": "小贩一边等你做决定，一边自顾自地整理货架。不知道为什么，你感到小贩熟悉你。"}
        ], min_aug=40, voices={"电子直觉": "【电子直觉】芯片堆里有一枚在低鸣求救——它的加密还是活的。"}),
    _ev("elevator_preacher", (
        "电梯坏了，你和一个传教士一起爬四十层楼梯。他的体能好得离奇，一边爬楼一边向你传教：\n"
        "「朋友，死亡能带走的只有我们的肉身。想想那些改造得只剩半个脑子的人吧，半个脑子要如何漂浮在空中，走完轮回转世之路？」"),
        [
            {"text": "跟他辩论截肢者的肢体是否属于自己的一部分，能否进入死后世界。", "check": ("逻辑", 10),
             "success": {"narration": "你在第三十一层完成了论证：断肢也属于自己的身体，它们应当一起进入死后世界，所以不会出现半个脑子漂浮在空中的事。传教士回答，「当然。每一根手指，每一颗牙，神都记着编号。到了那天，他会把你拼回去的，所以何必急着换一条钛合金的？」你说既然不影响死后的结果，你当然想装什么装什么。传教士无奈地站住了，不再陪你爬第三十二层。", "fx": {"skill:逻辑": 2}},
             "failure": {"narration": "你在第二十六层被自己的类比绕晕了。船、河流、爷爷的斧头，全搅在一起。", "fx": {"skill:逻辑": 1}}},
            {"text": "问他，他怎么确定死后会轮回转世，又怎么确定死人需要自己走路？", "check": ("共情", 9),
             "success": {"narration": "传教士尝试解释，但最终承认没有人能确定，因为没有死者复活过。信仰存在于闭眼跳跃的那个动作中。", "fx": {"skill:共情": 2}},
             "failure": {"narration": "你被传教士绕晕了，开始觉得教义确实有道理。传教士带你进入了三十九层的寒酸小教堂。", "fx": {"skill:坚忍": 1, "flag:entered_chapel": 1}}},
            {"text": "你指责传教士故意弄坏电梯，只为了创造传教机会。",
             "effects": {"narration": "传教士尴尬地走开。四十层楼，你只听见自己的呼吸——或散热风扇。都挺好。", "fx": {"skill:坚忍": 1}}},
            {"text": "对他的传教表示欣赏，希望跟他去教堂。",
             "effects": {"narration": "传教士带你来到三十九层。\n"
                                      "过于简陋的教堂，布局结构和你家一模一样，在本该是卧室的地方挤进二十把椅子，白墙上钉着一片薄薄的金色叶子。你凑近细看，是铜的。\n"
                                      "「如果你对死后世界感兴趣，你会一次次回来的。」",
                         "fx": {"skill:共情": 1, "skill:坚忍": 1,
                                "flag:entered_chapel": 1}}},
        ], echoes=[
            {"deed": "temple_doubt", "min": 2,
             "text": "传教士爬楼的姿势太过熟练了，你怀疑他是不是天天爬楼梯。"},

            {"seen": "elevator_preacher", "min": 2,
             "text": "传教士爬到第十二层忽然停下：「你之前真的没来过我们教堂吗？」他喘着气笑，「别误会，只是一种感觉。」"},
            {"seen": "elevator_preacher", "min": 2,
             "text": "这次是他先开的口：「朋友，死亡能带走的只有我们的肉身——」你打断他，自然地接后半句，「想想那些改造得只剩半个脑子的人吧。」他和你都很惊讶你知道，你们一边愉快地接龙一边爬完了楼梯。"},
        ], voices={"逻辑": "【逻辑】传教士对爬楼梯传教过于熟练了。作为纯血教派的信徒，他的血肉之躯要如何负担？"}, variants=[
            {"seen": "elevator_preacher", "min": 1,
             "text": "你在等电梯回家。电梯门开了，里面站着两个人，你认出他们是楼道里常碰到的传教士。\n"
                     "一位生面孔传教士站在电梯里不动，按着开门键等你。另一位熟面孔传教士出来，走向楼梯间的方向，邀请你和他一起爬楼梯。",
             "options": [
                 {"text": "走楼梯。", "check": ("坚忍", 9),
                  "success": {"narration": "你把他的传教小故事当背景音乐听，爬楼梯的过程显得不那么无聊了。他注意到你对他的滥用，但毫不生气，继续讲一些飞上天空或在水上行走之类的趣事。",
                              "fx": {"skill:坚忍": 2, "skill:共情": 1}},
                  "failure": {"narration": "你爬了几层就开始后悔，抛弃传教士，自己去坐电梯回家了。",
                              "fx": {"skill:坚忍": 1}}},
                 {"text": "坐电梯。", "check": ("街智", 9),
                  "success": {"narration": "电梯里的传教士温和而沉默。短短半分钟后三十九层就到了，传教士离开。你回了四十楼的家。",
                              "fx": {"skill:街智": 2, "skill:逻辑": 1}},
                  "failure": {"narration": "电梯里的传教士温和而沉默。你感到尴尬，随便按了一个低楼层，到了就出去了。你重新坐了一趟空电梯回家。",
                              "fx": {"skill:街智": 1, "skill:共情": 1}}},
                 {"text": "问熟面孔传教士为什么要爬楼梯。",
                  "effects": {"narration": "「生命的长度是相对的。」他抓住任何话题传教，「主动的苦役使你获得更长的人生。而且正是共同的血肉苦痛，使我们有了互相沟通的基础，不是吗？」",
                              "fx": {"skill:共情": 1}}},
                 {"text": "叫他们别折腾了，直接带你去教堂。",
                  "effects": {"narration": "你和两位传教士来到三十九层。\n"
                                           "过于简陋的教堂，布局结构和你家一模一样，在本该是卧室的地方挤进二十把椅子，白墙上钉着一片薄薄的金色叶子。你不用凑近就知道是铜的。\n"
                                           "他们怕你觉得装潢寒酸，解释人死如灯灭，不能带走任何东西，因此不需要布置得太好。真正重要的只有记忆与灵魂。",
                              "fx": {"skill:共情": 1, "skill:坚忍": 1,
                                     "flag:entered_chapel": 1}}},
             ]}
        ]),
    _ev("preacher_death", (
        "巷子深处的小教堂点着白蜡烛。爬了一辈子楼梯的传教士躺在长椅拼成的床上，禁闭着嘴唇，拒绝复述那句死前必须要牢记的金叶片语句。\n"
        "信众围在四周，焦急地吟诵：\n"
        "「我干渴欲焚……我是大地与星空之子——」\n"
        "传教士痛苦地喘息着，衰老的肺听起来像漏风的风箱。"),
        [
            {"text": "你加入吟诵：「而我的族类属于天。」",
             "effects": {"narration": "「我是大地与星空之子，而我的族类属于天。」\n"
                                      "传教士沉默着，被吟诵声环绕，眉头紧锁。突然，他松弛下来，失焦的眼睛望向天花板，生命的光彩一瞬间从面孔上褪去。\n"
                                      "没有人敢问他是否得到了救赎，但你能感觉到怀疑的空气在房间里弥漫。", "fx": {"flag:heard_the_leaf": 1, "skill:共情": 2}}},
            {"text": "你揭发传教士不虔诚的行为。", "check": ("共情", 10),
             "success": {"narration": "你挤到传教士身前，望了望他穿着平整长裤的腿，又望向他垂死的眼睛。他神情平静，仿佛带有鼓励。你卷起他的裤腿——钛合金膝盖在蜡烛映照下泛着哑光。\n"
                                     "在震惊与沉默之中，他离开了。\n"
                                     "葬礼改在了后半夜，按「不完整者」的规格办。你站到散场。",
                         "fx": {"skill:共情": 2, "skill:威慑": 1}},
             "failure": {"narration": "你隐约感觉到传教士身怀秘密，他并没有把一切都诚实地交给神。但你想不出证据，只好指责他不愿意吟诵临终的金叶片语句。你听到传教士轻轻叹了口气，灵魂就此离开了他的身体。",
                         "fx": {"skill:坚忍": 1}}},
            # 问了就一定问得到。**这一条不该掷骰**：它是整条金叶片链唯一的入口，
            # 而链尾（临终把破折号说完 → 楼下的歌声那条巷子）挂着好几幕。
            # 一次逻辑 9 失手就把后面全锁死，而玩家永远不会知道自己失去了什么。
            # （2026-08-08 作者定案：改成无检定，失败那一句删掉。）
            {"text": "你问金叶片语句的含义。众多信众中比较有名望的一位为你解答，人出生于大地之上、星空之下，死后魂归苍天，带着完整的自己重返家园。死时牢记这句话，是回家的钥匙。",
             "effects": {"narration": "「不，你出生于你母亲的胯下。你不是大地与星空之子。」你带着不知从何而来的勇气与笃信，开口道，「硅来自大地，重元素来自超新星，大地与星空之子是——」",
                         "fx": {"skill:逻辑": 1}, "then": "leaf_answer"}},
        # 作者定案：**只看进没进过那间三十九楼的小教堂。**
        # （电梯那一版的变体2 删掉了，「见过电梯几次」这道门跟着没了意义。）
        ], weight=6, req_deed={"entered_chapel": 1}, echoes=[
            {"deed": "temple_doubt", "min": 3,
             "text": "他的手紧紧攥着被子。\n"
                     "被角下面露出一截金属光泽——是真正的黄金叶，不是铜的。"},

            {"ach": "child_of_sky",
             "text": "念到「大地与星空之子」的时候，前排有人极轻地补了两个字，然后自己吓一跳，赶紧闭上嘴。\n"
                     "旁边的人当作没听见。这个添头这两年在信众里传开了，没有人说得清是从哪儿开始的。"},
        ]),
    _ev("stray_dog", (
        "一条断了后腿的狗跟着你走了三条街。断口很整齐——是人为的。\n"
        "它不叫，只是跟着你。"),
        [
            {"text": "在附近的动物义体商店给它买一条假腿。", "check": ("巧手", 9),
             "success": {"narration": "价格有点贵，但狗可怜的样子让你不忍心拒绝。狗试着走了三步，然后跑了起来。它头也不回。这是最好的感谢。", "fx": {"skill:巧手": 2, "skill:共情": 1, "flag:dog_friend": 1}},
             "failure": {"narration": "狗断腿太久了，神经已经坏死，接不上假腿。狗回头看了你一眼，眼神像在安慰你。", "fx": {"skill:巧手": 1, "skill:共情": 1}}},
            {"text": "查查是谁做了这件事。", "check": ("街智", 10),
             "success": {"narration": "线索指向一间动物义体商店，你匿名向记者举报了它，商店倒闭了。狗也在同一天失踪。", "fx": {"skill:街智": 2}},
             "failure": {"narration": "你什么也没查到。每次见到这条三腿狗，你都心怀愧疚。", "fx": {"heat": 1, "skill:街智": 1}}},
            {"text": "分一半晚饭给它。",
             "effects": {"narration": "狗太饿了，比你更快地吃完半份饭。你犹豫了一会儿，在狗失望的目光中吃完了自己的半份。对不起，但你也很饿。", "fx": {"skill:共情": 1}}},
        ], variants=[
            {"deed": "dog_friend", "min": 1,
             "text": "巷口有条机械狗朝你摇尾巴——四条腿都是假腿，尾巴也是机械尾巴，两只机械眼注视着你。\n"
                     "它不叫，只是等着。你暗自琢磨，它是一做出来就是机械狗，还是像人一样一条腿一条腿换过来的。",
             "options": [
                 {"text": "蹲下来，检查它的接口。", "check": ("机械亲和", 9),
                  "success": {"narration": "第四型接口，垫圈是硅胶的——有人给它换过，而且换得比原厂用心。你把松掉的一颗螺丝拧紧。它一动不动地让你拧完。",
                              "fx": {"skill:机械亲和": 2, "flag:dog_friend": 1}},
                  "failure": {"narration": "你刚碰到它后腿，它就退开半步，然后又走回来，把腿伸过来。像是在教你该怎么碰。",
                              "fx": {"skill:机械亲和": 1, "skill:共情": 1}}},
                 {"text": "分一半晚饭给它。",
                  "effects": {"narration": "机械狗闻了闻，没吃，也没走开，就在你旁边趴下了。\n"
                                           "你自己吃完了整份饭，虽然吃饱了，但也有一种模糊的内疚。你无法真正送给它任何东西。",
                              "fx": {"skill:共情": 1}}},
                 {"text": "走开。它现在过得比你好。",
                  "effects": {"narration": "走出两条街你回头看了一眼。它还在原地，两只机械眼朝着你这边。",
                              "fx": {"flag:dog_over": 1, "skill:坚忍": 1}}},
             ]},
            {"deed": "dog_friend", "min": 2,
             "text": "巷口那条机械狗朝你摇它的机械尾巴，轻轻咬住你的裤脚。\n"
                     "它不松口，也不用力，只是把你往巷子深处带。",
             "options": [
                 {"text": "跟着它走。",
                  "effects": {"narration": "贫民窟深处的空地上有七八条狗，都是全身机械。它们友好地对你摇尾巴。",
                              "fx": {"skill:共情": 2, "flag:dog_friend": 1}}},
                 {"text": "把裤脚抽回来。今晚有别的事。", "check": ("坚忍", 10),
                  "success": {"narration": "机械狗松口松得很痛快，一点也没有为难你。\n"
                                           "它站在原地，目送你离开。",
                              "fx": {"flag:dog_over": 1, "skill:坚忍": 2}},
                  "failure": {"narration": "你抽了两次没抽动，反而摔了一身泥。",
                              "fx": {"skill:共情": 1}}},
             ]},
            {"all": [{"deed": "dog_friend", "min": 5},
                     {"deed": "ascended", "min": 1},
                     {"aug": 60}],
             "text": "你站在巷口，脚踝上传来轻咬。\n"
                     "机械狗蹲在你的影子里，仰头看你。它的两只机械眼比你见过的任何义眼都旧，金属表面长出了一层氧化铜绿，像某种年轮。\n"
                     "它松开嘴，转身走了几步，回头等你。",
             "options": [
                 {"text": "跟上它。",
                  "effects": {"narration": "贫民窟深处的空地上，机械狗群在等待你。你感到亲近，并不害怕，因为它们都朝你摇尾巴。\n"
                                           "最老最旧的机械狗站在你的身边，用鼻子轻点你的膝盖，你是它的人类，而它是你的狗。\n"
                                           "你有一种回家的感觉。",
                              "fx": {"skill:共情": 1}, "then": "dog_pack_arrive"}},
                 {"text": "「今天不行。」你蹲下来摸了摸它的头。",
                  "effects": {"narration": "它最后一次用力地蹭了蹭你的手。\n"
                                           "机械狗依然喜欢被人抚摸吗？你没有机会问它这个问题，因为你没有再见过它了。",
                              "fx": {"skill:共情": 1, "flag:dog_over": 1}}},
             ]}
        ]),
    _ev("power_cut", (
        "全区停电。楼道里，邻居们端着蜡烛聚在一起——烛光照出几张熟悉的脸上细细的接缝。\n"
        "黑暗里，有人小声说：「充电桩全停了，406室的心脏撑不过今晚。」"),
        [
            {"text": "拆开配电箱，手动并线。", "check": ("巧手", 10),
             "success": {"narration": "你用晾衣架和绝缘胶带接出一条应急线路。406的心脏重新嗡嗡作响，整层楼的蜡烛都朝你举了举。", "fx": {"skill:巧手": 2, "skill:机械亲和": 1}},
             "failure": {"narration": "火花炸了你一脸，这样鲁莽的做法当然行不通。你的知识不够解决难题，但足够你想象出406那颗*真正的*心脏在断电机械的废墟中逐渐停止跳动的样子。", "fx": {"hp": -1, "skill:巧手": 1}}},
            {"text": "组织人手，把406抬去三公里外的未断电区。", "check": ("威慑", 9),
             "success": {"narration": "你和几个邻居抬着担架，在夜色中一声不吭地前进。一片漆黑的贫民窟里，哭叫声、咒骂声从拥挤的高楼上传来。你被惊慌乱窜的老鼠绊倒，无心想那是肉身老鼠还是机械老鼠，你只是爬起来，继续扛起担架。\n"
             "最后一排背着光的高楼被你们穿过了，在电力组成的虚假白昼之中，406得救了。你跪倒在地，昏睡过去。你只是太累了。", "fx": {"skill:威慑": 1, "skill:共情": 1, "skill:坚忍": 1}},
             "failure": {"narration": "没人敢动，没人想为此冒风险。你甚至怀疑某些邻居藏着小发电机，只是不想露出来惹麻烦。\n"
             "你独自背着406，在夜色中一声不吭地前进。一片漆黑的贫民窟里，哭叫声、咒骂声从拥挤的高楼上传来。你被惊慌乱窜的老鼠绊倒，无心想那是肉身老鼠还是机械老鼠，你只是爬起来，再次背起406。\n"
             "406的胸腔贴着你的后背。你突然感觉背后过于安静。\n"
             "站在最后一排背着光的高楼之前，你知道穿过这里就能抵达电力组成的虚假白昼，但406不在了。你跪倒在地，昏睡过去。你只是太累了。", "fx": {"skill:坚忍": 2, "hp": -1}}},
            {"text": "把自己的备用电池让出来。", "req": ("aug", ">=", 30),
             "effects": {"narration": "你拧下腰侧的备用电池递过去。今夜你会跑得慢一点，冷一点，但这不算什么大事。", "fx": {"skill:共情": 2, "hp": -1, "anchor": 1}}},
        ], min_aug=40, echoes=[
            {"deed": "became_dog", "min": 1,
             "text": "楼道里有人在哭。旁边传来几声安慰——然后一声极轻的犬吠。\n"
                     "不是野狗。声音从四楼传来。406室养了一条狗。"}
]),
    _ev("night_library", (
        "消防梯尽头的天台上是「夜间图书馆」，最末流的书贩子把卖不出去的书堆在这里，最后一次论斤销售。书贩子晚上回家后，谁都可以来抱走一摞。出于对本区域居民文化水平的信任，没有防盗措施。偶尔丢几本，也只是擦屁股纸的合理用量。\n"
        "一片漆黑中，你看到一个老人端坐着。走近一看，是个盲人，在用指尖读书脊。「找什么？」他俨然是管理员的派头，「找答案的往左，找问题的往右。」"),
        [
            {"text": "往右，翻那些没人整理的手稿。", "check": ("逻辑", 9),
             "success": {"narration": "你在一册烧掉一半的笔记前停住了。「连续性豁免」——三十年前就有人论证过意识搬运的伦理漏洞。纸页的焦边蹭黑了你的指腹。你把残页一页页抄下来，抄到手腕发酸。", "fx": {"skill:逻辑": 2, "flag:archive": 1}},
             "failure": {"narration": "手稿太碎了，你拼到后半夜也没拼出完整的一页。下楼时你只带走了一句话：问题比答案耐读。", "fx": {"skill:逻辑": 1}}},
            {"text": "问老人：您为什么不装义眼？", "check": ("共情", 10),
             "success": {"narration": "「不需要。」老人说，「人一辈子不能读太多字。读到所有事物的所有可能性之后，你就再也做不出选择。」", "fx": {"skill:共情": 2, "skill:坚忍": 1}},
             "failure": {"narration": "老人一言不发。你看了看老人寒酸的衣着，羞愧于问出这样不合时宜的问题。你悄悄离开。", "fx": {"skill:共情": 1}}},
            {"text": "把兜里所有的钱掏出来，塞到老人手里。",
             # 书只卖一次。此后他照收钱，但手不再往那一摞上去 ——
             # 「同一本书卖给同一个人五回」是这条线上唯一说不通的地方。
             # （2026-08-08 作者定案 ＋ 试玩反馈）
             # 作者问「成功失败是不是写错了」—— 没写错，但确实容易读反：
             # 原先的闸是「给过没有」，闸开＝给过＝没书。现在换成 nodeed，
             # **success 就是第一次，第一次才有书**。读起来和发生的顺序一致了。
             "gate": ("nodeed", "gave_it_away", 1),
             "success": {"narration": "「那就卖给你一本好书吧。」老人把一本书递给你，握住你的手指确保你拿稳了这本宝物，书名叫《私人小型船舶紧急维修速成手册》。",
                         "fx": {"flag:gave_it_away": 1, "flag:broke": 1,
                                "skill:坚忍": 1, "skill:逻辑": 1}},
             "failure": {"narration": "他接过你的钱，微笑着。\n"
                                      "「谢谢你，但我已经没有东西可以给你了。」",
                         "fx": {"flag:gave_it_away": 1, "flag:broke": 1,
                                "skill:共情": 2, "flag:library_closed": 1}}},
        ], echoes=[
            {"deed": "became_dog", "min": 1,
             "text": "老人的指尖在书脊上摩挲，「杯子满过一次，就再也装不回去了。」他说，「往外倒吧——倒给谁，想好了吗？」"},

            {"deed": "archive", "min": 2,
             "text": "透过模糊的夜色，你看到盲眼老人对你微笑。"},
            {"seen": "night_library", "min": 4,
             "text": "老人向你伸出手掌，你不知为何知道要把自己的手交给他。老人用指尖抚摸你的手背，轻拍两下，然后收回去。你感到某种安慰。"},
        ], voices={"逻辑": "【逻辑】书脊的磨损分布不对——这里最常被翻开的，全是没有署名的书。"}),
    _ev("mirror_stall", (
        "游乐场废墟里有一面没碎的哈哈镜。你在里面又高又扁，像被拉长的信号。\n"
        "镜子背后忽然传来声音：「喜欢什么形状的自己？我可以帮你调整。」\n"
        "一个改装师从镜后走出，工具腰带上挂着十几把手术钳，走一步响一步。"),
        [
            {"text": "你决定先谈价格。", "check": ("街智", 9),
             "success": {"narration": "报价单看到一半你就明白了，他在把你当傻子宰。你报出每样零件的黑市价格，改装师露出狐狸似的笑脸，把报价单从你手中抽回来，换成一张真正的熟人名片。", "fx": {"skill:街智": 2}},
             "failure": {"narration": "你被专业名词淹没，差点签了一份「终身固件订阅」。幸好停电救了你。", "fx": {"skill:街智": 1}}},
            {"text": "做个小升级，就现在。", "check": ("坚忍", 9),
             "success": {"narration": "无麻醉，二十分钟。他的手很稳。你盯着镜子里的自己数心跳，数到一百四十七下，结束了。镜子里的新版本朝你眨了眨眼。", "fx": {"aug": 8, "skill:机械亲和": 1, "skill:坚忍": 1}},
             "failure": {"narration": "手术没出错，但疼得比说好的多三倍。「疼痛校准是付费功能。」他说。", "fx": {"aug": 8, "hp": -1}}},
            {"text": "「我现在就挺好。」转身离开。",
             "effects": {"narration": "「希望你是真心的。」改装师在你身后说，「这句话我听过很多次。」", "fx": {"skill:坚忍": 1, "anchor": 2}}},
        ], max_aug=69,
        voices={"电子直觉": "【电子直觉】他的手指接口不是医疗制式——自己车的。公差比医疗件还小半丝。"},
        variants=[
            # 变体1 · 半面镜子
            {"seen": "mirror_stall", "min": 1,
             "text": "你来到游乐场的废墟，碎了一半的哈哈镜把你的形体照得有点奇怪。\n"
                     "透过半个空镜框，你看到改装师坐在简易工作台前拼一只手。走近点，你发现那是他自己的手。\n"
                     "右手腕上，新缝还很明显，三根指尖换成了精密镊头。而工具腰带上本应挂着镊子的地方，现在挂着三根手指。\n"
                     "你观察他用刚装好的右手费力地拼左手。\n"
                     "「我没空帮你了，」他抬起未完工的左手朝你晃晃，「想要什么，就自己选吧。」",
             "options": [
                 {"text": "「把手指换成工具是什么感觉？」", "check": ("共情", 8),
                  "success": {"narration": "改装师把未完工的左手暂时搁在桌上，认真思索。\n"
                                           "「刚换第一根的时候，有点奇怪。是食指，那时候我还不习惯握它，总是把它单独伸着，像在指什么东西。」他狡黠地笑，「还好我第一根没换中指。」",
                              "fx": {"skill:共情": 2, "flag:mirror_pain_talk": 1}},
                  "failure": {"narration": "改装师没空和你闲聊。",
                              "fx": {"skill:共情": 1, "flag:mirror_pain_talk": 1}}},
                 {"text": "蹲下来帮他扶住左手腕的零件。", "check": ("机械亲和", 9),
                  "success": {"narration": "他赞赏地看你一眼，叫你闭紧眼睛别看火花，然后腾出右手焊了三个接点。\n"
                                           "焊接声停了，冰凉的镊子尖戳了戳你的虎口，你重新睁开眼睛。改装师爽朗地笑。",
                              "fx": {"skill:机械亲和": 2, "flag:mirror_touched": 1}},
                  "failure": {"narration": "你颤颤巍巍地捏着零件，怕焊接火花溅到自己。改装师挥挥右手把你赶开，自己焊好了。",
                              "fx": {"skill:机械亲和": 1, "flag:mirror_touched": 1}}},
                 {"text": "问他为什么不换个更好的地方干活。",
                  "effects": {"narration": "改装师斜着眼睛看你。你扫了一眼他破破烂烂的店面，自讨没趣地走了。",
                              "fx": {"skill:共情": 1, "flag:mirror_old_glass": 1}}},
             ]},
            # 变体2 · 挑一面
        ]),
    _ev("tax_audit", (
        "身体税稽查站。新法规：按「非原生组织占比」阶梯征税。\n"
        "队伍排了两百米。你前面是个码头工，为了保住班次装的液压肩，正在跟稽查员争：\n"
        "「这是工伤，不是奢侈品！」——挡不住。税率表上没有「为什么」这一栏。\n"
        "你后面那位通体镜面抛光，捧着一摞裁定书，闭目养神。他不吵。他的律师在文书里\n"
        "已经论证完毕：「非原生组织占比」需要先有组织才能成立，而他名下已经没有组织。\n"
        "扫描门每响一声，就有人被拖去补税。轮到你了。"),
        [
            {"text": "如实申报。", "gate": ("noflag", "gave_it_away"),
             "success": {"narration": "税单打印出来，你如数付了。钱包空了，但腰杆是直的。稽查员往你档案上盖了「诚实纳税」的印章，真希望它以后有用。",
                         "fx": {"flag:honest": 1, "flag:broke": 1, "skill:坚忍": 1}},
             "failure": {"narration": "税单打印出来，天文数字！\n"
                         "你摸遍口袋——空的。这一世你已经把兜里所有的钱\n"
                         "塞给过一个盲眼老人，换回一本你至今没读完的书。\n"
                         "\n"
                         "欠税的人不判刑，判的是「劳务折抵」——你在拆解场做了四个月，\n"
                         "把别人换下来的东西拆成能再卖一次的零件。出来的时候瘦了一圈。",
                         "fx": {"skill:坚忍": 1, "flag:broke": 1, "heat": 2, "hp": -1}}},
            {"text": "掏钱替前面的码头工交了申诉费。", "req": ("noflag", "gave_it_away"),
             "effects": {"narration": "他愣了很久，把工牌掏出来给你看，像要证明自己值得。你知道申诉未必有用，这笔钱或许只能为他买到一个月申诉期的自由。", "fx": {"skill:共情": 1, "skill:街智": 1, "flag:broke": 1}}},
            {"text": "屏蔽扫描仪。", "req": ("aug", ">=", 20), "check": ("电子直觉", 11),
             "success": {"narration": "你让皮下电路进入睡眠，心跳压到扫描仪的噪声阈值以下。门「嘀」了一声绿。你是队伍里唯一免税的钢铁。", "fx": {"skill:电子直觉": 2, "heat": 1}},
             "failure": {"narration": "扫描仪红了。补税、罚款、留档。稽查员的眼神像在说：又一个。", "fx": {"heat": 2, "skill:电子直觉": 1}}},
            {"text": "煽动队伍抗税。", "check": ("威慑", 11),
             "success": {"narration": "「他们按斤收税的时候，可没按斤给我们发工资！」两百米的队伍轰然响应。稽查站当天临时关闭，新闻称之为「排队暴动」。", "fx": {"skill:威慑": 2, "heat": 2, "flag:riot": 1}},
             "failure": {"narration": "只有三个人跟着你喊，其中一个是便衣。你在小房间里做了四小时笔录。", "fx": {"heat": 2, "skill:坚忍": 1}}},
            {"text": "报出世系编号，走免检通道。", "req": ("deed", "honest", 3),
             "effects": {"narration": "稽查员调出记录，愣了愣：「系统显示……这个世系三代申报无瑕。免检通道，请。」你不知道系统嘴里的「世系」是什么意思——但你隐约知道。", "fx": {"skill:街智": 1, "heat": -1}}},
        ], voices={"坚忍": "【坚忍】队伍里每一声「嘀」都在教同一课：别抖。"},
        variants=[
            # 变体1 · 换班
            {"seen": "tax_audit", "min": 1,
             "text": "稽查站快关门了，靠近队伍末尾、排队无望的人三三两两走开。\n"
                     "稽查员在收东西。她把扫描仪的校准卡插回卡槽，动作娴熟。\n"
                     "关上卡槽的时候，她右手无名指的关节清脆地一响，不像骨头。\n"
                     "女式制服的口袋太小，你看到半截报税表露在外面。",
             "options": [
                 {"text": "盯着她的手看。",
                  "effects": {"narration": "她回看你，耸耸肩，又故意弯了一下无名指。清脆的金属碰撞声清晰无疑。\n"
                                           "「工伤。」她关灯离开。\n"
                                           "你听见扫描仪断电，嗡的一声，然后安静了。",
                              "fx": {"skill:共情": 2, "flag:tax_hand": 1}}},
                 {"text": "趁她低头收东西，自己走了一趟扫描门。",
                  "effects": {"narration": "扫描门嘀了一声。你赶快去看红灯还是绿灯，但来不及看，灯就灭了。警示灯朝着稽查员那边。\n"
                                           "你的目光刚好与她对上，她看了看你，什么也没说，夹着包下班走了。",
                              "fx": {"skill:街智": 2, "flag:tax_walk": 1}}},
                 {"text": "问她当了稽查员是否还需要走扫描门。", "check": ("街智", 8),
                  "success": {"narration": "她哈哈大笑，看穿你的小心思。\n"
                                           "「我们有值班人员通道。」\n"
                                           "她锁上小门离开了。",
                              "fx": {"skill:街智": 2, "flag:tax_staff_door": 1}},
                  "failure": {"narration": "「你以为什么人都能考进来？」她右手揣兜，用左手锁上小门离开了。",
                              "fx": {"skill:街智": 1, "flag:tax_staff_door": 1}}},
             ],
             "voices": {"坚忍": "【坚忍】她收东西的顺序几乎算得上刻板行为，工作把她打磨成一颗螺丝钉。"},
             "echoes": [
                 {"seen": "tax_audit", "min": 2,
                  "text": "排你前面的人用笔尖在报税数字上点来点去，好像以为这样能让数字变小。"},
                 {"seen": "tax_audit", "min": 3,
                  "text": "扫描门嘀了一声，绿灯。但站在门下的人还是紧张地一动不动。后面排队的人催他，他才如梦初醒，快速走开了。"},
                 {"deed": "tax_hand", "min": 1,
                  "text": "稽查员右手无名指上戴着一枚戒指，你觉得很别致，在这个年代已经很少有人结婚了。"},
             ]},
            # 变体2 · 手检
            {"all": [{"seen": "tax_audit", "min": 3},
                     {"deed": "tax_hand", "min": 1}],
             "text": "还没走到扫描的地方，你已经注意到今天排队的长度远超以往。提前绕到队伍开头，你才知道今天扫描门坏了，全部手检。\n"
                     "稽查员眉头紧皱，神情烦躁，拎着安检仪招呼每个临检的人平伸胳膊不要乱动。\n"
                     "等了不知道多久，终于轮到你了。",
             "options": [
                 {"text": "平伸胳膊。",
                  "effects": {"narration": "安检仪贴着你全身走了一遍，然后她伸出手指捏了捏你鼓鼓囊囊的随身物品或肥肉。\n"
                                           "「下一位。」她敷衍地喊。",
                              "fx": {"skill:坚忍": 2, "flag:tax_touched": 1}}},
                 {"text": "把兜里的东西先全部掏出来，以免误扫。", "check": ("逻辑", 8),
                  "success": {"narration": "稽查员觉得你很烦，敷衍地扫了扫你，就催你快滚。",
                              "fx": {"skill:逻辑": 2, "flag:tax_two_hands": 1}},
                  "failure": {"narration": "后面排队的人不耐烦了，把你推搡到一边。",
                              "fx": {"skill:逻辑": 1, "flag:tax_two_hands": 1}}},
                 {"text": "走了。等机器修好再来。",
                  "effects": {"narration": "你离开队伍。后面有人小声说了一句：「聪明。」\n"
                                           "你不确定那是不是夸你。",
                              "fx": {"skill:街智": 2, "flag:tax_left": 1}}},
             ],
             "voices": {"共情": "【共情】她在寻觅机械，但流水线要求她本人成为机械。"},
             "echoes": [
             ]},
        ]),
    _ev("old_singer", (
        "地下通道里，一个老歌手在唱歌。他的声带是原装的，带着砂纸般的嘶哑。\n"
        "一张纸放在地上，四块石头压平整：「全身原装，欢迎验证。听歌自愿，付费随意。」\n"
        "他唱到副歌时看了你一眼，给你留出半个拍子。"),
        [
            {"text": "与他合唱。",
             "effects": {"narration": "你紧张地开口，声音慢了半拍。歌手没有责怪你打乱节奏，而是放慢自己，配合你。通道的回声把两副嗓子调成了一副。",
                         "fx": {"skill:坚忍": 1, "skill:共情": 1, "skill:威慑": 1,
                                "flag:duet": 1}}},
        ], max_aug=39),
    _ev("old_singer_high", (
        "地下通道里，一个老歌手在唱歌。他的声带是原装的，带着砂纸般的嘶哑。\n"
        "一张纸放在地上，四块石头压平整：「全身原装，欢迎验证。听歌自愿，付费随意。」\n"
        "几个飞升者在旁边闲聊，把他的歌声当背景音乐。"),
        [
            {"text": "听完整场。",
             "effects": {"narration": "你在心中默默记下了这首嘶哑的歌。", "fx": {"skill:共情": 2, "anchor": 1}}},
            {"text": "用录音芯片偷录下来。", "req": ("aug", ">=", 15), "check": ("街智", 9),
             "success": {"narration": "你在网上售卖录音，噱头是「最后的原装嗓」。碰了几次钉子后，你终于成功售出，得到一点钱。", "fx": {"skill:街智": 2, "heat": 1}},
             "failure": {"narration": "录音开启的电流声太明显了，歌手在尴尬中停下。飞升者停止闲聊，回头看你。众人的目光把你钉在原地。", "fx": {"skill:共情": 1, "heat": 1}}},
            {"text": "与他合唱。", "gate": ("aug_below", 40),
             "success": {"narration": "你紧张地开口，声音慢了半拍。歌手没有责怪你打乱节奏，而是放慢自己，配合你。通道的回声把两副嗓子调成了一副。", "fx": {"skill:坚忍": 1, "skill:共情": 1, "skill:威慑": 1, "flag:duet": 1}},
             "failure": {"narration": "你紧张地开口，声音慢了半拍。歌手的声音戛然而止，用寂静拒绝你。突然，一个不同的声部加入了，然后是两个，三个。飞升者们为你制造了一场轮唱。万事万物的和声之中，和谐的幸福填满了你的心。你身处峡谷，群山回唱。", "fx": {"skill:共情": 1, "flag:duet": 1}}},
        ], echoes=[
            {"deed": "duet", "min": 1,
             "text": "察觉到你跃跃欲试，歌手主动递了半个拍子给你。他好像知道你是拿不准节奏的那种人。"},
        ], voices={"共情": "【共情】歌声是情感最自由的介质。"},
        min_aug=40,
        variants=[
            # 变体1 · 烟嗓
            {"seen": "old_singer_high", "min": 1,
             "text": "清晨，你经过无人的地下通道。\n"
                     "歌手坐在肮脏的墙根，手里攥着一只棕色瓶子，垂着头，看起来还没醒酒。\n"
                     "他脚边七八个烟蒂，踩得扁平。\n"
                     "\n"
                     "地上那张纸还在。「全身原装，欢迎验证。」",
             "options": [
                 {"text": "劝他戒烟戒酒，护住嗓子。", "check": ("共情", 10),
                  "success": {"narration": "他努力抬起沉重的眼皮，眼白浑浊。清了清嗓子，\n"
                              "他唱了一个干净到出乎你意料的音。\n"
                              "\n"
                              "然后蓄意地，他灌了一口酒，再唱同一个音。\n"
                              "这次沙子回来了——碎了，裂了，像指甲划玻璃。你的后脊梁凉了一下。\n"
                              "\n"
                              "「哪个更像原装？」他问。",
                              "fx": {"skill:共情": 2, "flag:singer_sand": 1}},
                  "failure": {"narration": "沉重的眼皮半遮着双眼。他没有兴趣搭理你。",
                              "fx": {"skill:共情": 1}}},
                 {"text": "把他叫起来，让他唱一首。", "gate": ("noflag", "broke"),
                  "success": {"narration": "歌手摇摇晃晃地站起来，为你唱了一首。\n"
                              "\n"
                              "你能感觉到他深重的怨气，知道不把钱掏出来将很难平安地\n"
                              "离开这条地下通道——你掏了。",
                              "fx": {"skill:共情": 1, "skill:街智": 1, "flag:broke": 1}},
                  "failure": {"narration": "歌手摇摇晃晃地站起来，为你唱了一首。\n"
                              "\n"
                              "你后悔叫他起来。你能感觉到他深重的怨气，\n"
                              "而你连一分钱也掏不出来作补偿。\n"
                              "你落荒而逃。转身的时候，酒瓶砸在你背上。",
                              "fx": {"hp": -1, "skill:坚忍": 1}}},
                 {"text": "示意你也想喝一口。",
                  "effects": {"narration": "歌手把瓶子递给你。你接过来，故意忽视了污水横流的地面，\n"
                              "盘腿坐下，就在他身边。你喝了一口。味道很糟糕。\n"
                              "\n"
                              "你刚想张嘴说点什么，他制止了你。\n"
                              "沿着他手指的方向，你向前看去——两只老鼠，\n"
                              "绝对是百分之百纯血原生的小老鼠，正为了一小片食物残渣打架。\n"
                              "只有在此刻寂静的地下通道里，这样细微的吱吱声才听得清。\n"
                              "\n"
                              "你注意到老鼠打架的样子像跳舞。",
                              "fx": {"skill:共情": 2, "anchor": 1,
                                     "flag:drank_with_singer": 1}}},
             ],
             "voices": {"共情": "【共情】他攥瓶子的姿势，如同紧握话筒。"},
             "echoes": [
                 {"deed": "duet", "min": 1,
                  "text": "他没抬头，用瓶底在地上磕了两下。两拍。\n"
                          "你听出来那是你们一起唱过的那首的头两拍。"},
                 {"seen": "old_singer_high", "min": 2,
                  "text": "歌手的手指是黄的。他把烟掐灭在鞋底——\n"
                          "那只鞋底上已经有一个焦黑的圆坑了。"},
                 {"seen": "old_singer_high", "min": 3,
                  "text": "有歌手在的地下通道给你一种熟悉的感觉。\n"
                          "无论是深夜还是清晨，经过这里的时候，你不害怕。"},
                 {"deed": "drank_with_singer", "min": 1,
                  "text": "隔着通道的转角，你能感觉到歌手模糊的轮廓正在墙的另一边。\n"
                          "你甚至隐约知道他会唱哪几首——因为不知道为什么，你也会唱。"},
             ]},
            # 变体2 · 最后几天
            {"all": [{"seen": "old_singer_high", "min": 3},
                     {"deed": "drank_with_singer", "min": 1}],
             "text": "不知为何，你习惯在无人的清晨来到地下通道。\n"
                     "也许是为了躲开这片区域素质恶劣的人群，也许是你潜意识里想见到某个人。\n"
                     "\n"
                     "歌手背靠着墙站在拐角处，像任何一个还没开始工作的卖唱者一样。\n"
                     "但你注意到他眼神犹豫。\n"
                     "\n"
                     "「嘿，替我扔一次硬币吧，」歌手叫住了你，\n"
                     "「我的声带要彻底退休了。正面就换机械声带，背面就保持原装。」",
             "options": [
                 {"text": "扔。", "coin": True,
                  "success": {"narration": "正面。\n"
                              "\n"
                              "你们两个都愣在当场。过了几秒钟，歌手慢慢蹲下，把硬币捡了起来。\n"
                              "向你道谢之后，歌手离开了。从此你没再见过他。",
                              "fx": {"skill:共情": 2, "flag:singer_gone": 1,
                                     "flag:singer_head": 1}},
                  "failure": {"narration": "背面。\n"
                              "\n"
                              "歌手如释重负地耸了耸肩，把硬币捡起来。\n"
                              "他想为你唱一首歌表示感谢，你轻轻拦住了他。\n"
                              "两个人彼此微笑，挥手告别。\n"
                              "\n"
                              "下一次碰到他是在码头。他沉默地搬运货物，\n"
                              "因为体力不好，被工头大声咒骂。",
                              "fx": {"skill:共情": 1, "skill:坚忍": 1,
                                     "flag:singer_dock": 1}}},
                 {"text": "不扔。",
                  "effects": {"narration": "你不愿意为别人的人生做出这么大的决定。\n"
                              "你装作没听见，快步走开。\n"
                              "\n"
                              "歌手没有追上来。你知道他不会。\n"
                              "你们归根结底只是两个擦肩而过的陌生人。",
                              "fx": {"skill:坚忍": 2, "flag:singer_declined": 1}}},
                 {"text": "建议他换一副真人的声带。", "check": ("共情", 11),
                  "success": {"narration": "「真没想到，硬币立起来了。」歌手露出惊讶的表情，\n"
                              "「真的要试吗？不过结果也不会比现在更差了。」\n"
                              "\n"
                              "这是你最后一次见到他，或者不是——\n"
                              "因为你已经不能再隔着人群辨认出他的声音。\n"
                              "只是你常常听到街头巷尾飘来熟悉的旋律，配着不同的歌声。",
                              "fx": {"skill:共情": 2, "flag:singer_gone": 1,
                                     "flag:singer_human": 1}},
                  "failure": {"narration": "「真没想到，硬币立起来了。」歌手露出惊讶的表情，\n"
                              "然后是更深的犹豫：「我不太能接受用另一个人的声音唱歌。」\n"
                              "\n"
                              "你离开了，留他一个人在通道里继续想。\n"
                              "\n"
                              "下一次碰到他是在码头。他沉默地搬运货物，\n"
                              "因为体力不好，被工头大声咒骂。",
                              "fx": {"skill:共情": 1, "flag:singer_dock": 1}}},
             ],
             "voices": {"逻辑": "【逻辑】他自己为何不扔硬币？把命运抛掷入陌生人手中有一个额外的好处，可以避免掷第二次。"},
             "echoes": [
                 {"seen": "old_singer_high", "min": 3,
                  "text": "有歌手在的地下通道给你一种熟悉的感觉。\n"
                          "无论是深夜还是清晨，经过这里的时候，你不害怕。"},
                 {"deed": "drank_with_singer", "min": 1,
                  "text": "隔着通道的转角，你能感觉到歌手模糊的轮廓正在墙的另一边。\n"
                          "你甚至隐约知道他会唱哪几首——因为不知道为什么，你也会唱。"},
             ]},
            # 变体3 · 空通道
            # 〔这一幕是我（Opus 5）补的，不是作者原稿：她那两版之后歌手已经不在通道里了，
            #  而这个事件还会被抽到。留着不写就等于让一个已经走掉的人天天回来上班。〕
        ]),
    _ev("job_interview", (
        "你需要钱。中介所只剩两个岗位：\n"
        "「码头卸货，要么有液压臂，要么有不打算再留着的腰。」\n"
        "「档案誊录，要么有目镜阵列，要么有不打算再留着的眼。」"),
        [
            {"text": "去码头。", "check": ("坚忍", 9),
             "success": {"narration": "一夜八百箱。天亮时你的腰（或液压泵）还在。工头没说话，用铅笔把你的名字添进了长期名单——铅笔的意思是随时能擦掉，但今天，添了。", "fx": {"skill:坚忍": 2, "hp": 0}},
             "failure": {"narration": "第六百箱压垮了你。工钱结了一半，腰疼免费赠送。", "fx": {"hp": -1, "skill:坚忍": 1}}},
            {"text": "去档案室。", "check": ("逻辑", 9),
             "success": {"narration": "誊录到后半夜，你发现档案里藏着一套重复出现的假名——有人在系统里活了四次。你把发现留给了自己。", "fx": {"skill:逻辑": 2, "flag:archive": 1}},
             "failure": {"narration": "字太小，夜太长。你抄错了一页，扣了半天工钱。", "fx": {"skill:逻辑": 1}}},
            {"text": "在中介所门口摆摊，帮人代写求职信。", "check": ("街智", 10),
             "success": {"narration": "三小时只写了十一封信，但九封拿到了面试。你套不了中介的模板，因此效率低下，但面试成功率反而更高。他们会介绍更多人来的。", "fx": {"skill:街智": 2, "skill:共情": 1}},
             "failure": {"narration": "中介所保安把你的摊子掀了。刚才在你帮助下成功的求职者看不下去，把你拉到角落里，「你写得太认真，会让人真的找到工作，就不用再来找中介了。他们嫌你坏了规矩。」", "fx": {"heat": 1, "skill:街智": 1}}},
        ], echoes=[
            {"deed": "became_dog", "min": 1,
             "text": "从中介所出来你小跑了几步。你很高兴，简直想摇尾巴。不过你没有尾巴。"},
            {"deed": "dog_halfway", "min": 1,
             "text": "你弯腰系鞋带的时候停了一秒。那个弯腰的弧度，你用过更自然的版本。"},

            {"seen": "job_interview", "min": 2,
             "text": "中介翻登记簿的手停了一下，往回翻了两页，又合上。他没有说他在找什么。"},
            {"seen": "job_interview", "min": 4,
             "text": "中介没让你填表。他把两张条子都摊开，然后把码头那张收了回去，剩下一张推到你面前。"},
        ], max_aug=69,
        voices={"街智": "【街智】中介抽两头：码头抽力气，档案室抽秘密。"}),
    _ev("ferry_night", (
        "末班渡轮。船身斑驳，一半的铆钉换过，一半还是五十年前的。\n"
        "船长看你在看船：「嫌弃她补丁摞补丁？她是我的宝贝。无论她换了多少零件，从头到尾都换过无数次了吧。只要我们还相依为命，她就是我的宝贝。」"),
        [
            {"text": "你问如果拆下来的二手零件被拼成另一艘船呢？",
             "effects": {"narration": "「那个不是她。」船长凝视着眼前的船，「因为我爱她，我爱眼前的这个她，所以她是我眼前的这个。就这么简单。」", "fx": {"skill:坚忍": 1, "skill:逻辑": 1}}},
            {"text": "帮他检修那台老引擎。", "check": ("机械亲和", 10),
             "success": {"narration": "引擎里三代零件同堂：铸铁、合金、打印件。你听着它们咬合的声音，忽然懂了什么叫「连续性」。船长送你到了对岸不收钱。", "fx": {"skill:机械亲和": 2, "skill:巧手": 1}},
             "failure": {"narration": "你拧错一个阀，渡轮在江心漂了半小时。船长一边给你擦屁股，一边抽完了一整包烟。", "fx": {"skill:机械亲和": 1}}},
            {"text": "站在船尾看水。",
             "effects": {"narration": "尾流在黑水上写字，写了就散。河从来不是同一条河，可渡口一直在。", "fx": {"skill:共情": 1}}},
        ], echoes=[
            {"seen": "ferry_night", "min": 1,
             "text": "船长扫了你一眼，正要转开，又扫了一眼。"},
            {"seen": "ferry_night", "min": 3,
             "text": "船长收钱的手停了一下，然后找零时多给了两个硬币。"},
            {"seen": "ferry_night", "min": 5,
             "text": "船长把舵让给你扶了一段，自己去船尾抽烟。他相信你知道该怎么做。"},
        ], voices={"机械亲和": "【机械亲和】三代零件在同一个节拍里咬合。像一支跨世纪的合唱。"}),
    # ------------------------------------------------ 残响事件（前世记忆，只在携带继承时出现）
    _ev("echo_dream", (
        "【残响】午夜，你梦见一双不属于你的手在拆一台引擎。每个零件的名字\n"
        "你都叫得出来——用一种你没学过的口音。\n"
        "醒来时，你的两根手指还捏着被角，正在拧一颗不存在的螺栓。"),
        [
            {"text": "顺着梦，把那台引擎画下来。", "check": ("逻辑", 9),
             "success": {"narration": "图纸完成的瞬间你认出来了：这是上个时代的心肺机。前世的你修过很多台。知识淌回手指，像河认出故道。", "fx": {"skill:机械亲和": 1, "skill:巧手": 1}},
             "failure": {"narration": "线条在纸上打架。记忆是别人的，手是自己的，中间隔着一整次死亡。", "fx": {"skill:坚忍": 1}}},
            {"text": "压下去。这不是你的记忆。", "check": ("坚忍", 10),
             "success": {"narration": "你学会了在残响涌上来时数呼吸的节奏。它们退回去了，像潮水记住了堤。", "fx": {"skill:坚忍": 2, "heat": -1}},
             "failure": {"narration": "越压越响。第二天你在早餐桌上脱口说出一个没人听过的机油牌子。", "fx": {"heat": 1}}},
            {"text": "对梦说：「讲下去，我听着。」",
             "effects": {"narration": "梦讲了一整夜。你现在知道自己为什么总在黄昏时感到怀念，那是前世换班的时间。", "fx": {"skill:共情": 1, "skill:电子直觉": 1}}},
        ], min_heat=None, weight=14, variants=[
            {"seen": "echo_dream", "min": 1,
             "text": "【残响】这一次不是引擎。\n"
                     "你梦见自己在给一件很小的东西上油——小到要眯起眼睛，小到那双手在梦里也发抖。\n"
                     "你不知道那是什么零件，但你知道下一步该往哪个方向拧，也知道拧过头会怎么响。\n"
                     "醒来时枕头上有一小块油渍。你昨晚没碰过任何机器。",
             "options": [
                 {"text": "去五金摊，把梦里那个手感一件一件对过去。", "check": ("巧手", 10),
                  "success": {"narration": "第十一件对上了。摊主说这是老式助听器的音圈，早停产了，现在只有一种人还在修。\n"
                              "你问是哪种人。他说：「聋子的儿子。」",
                              "fx": {"skill:巧手": 2, "skill:共情": 1}},
                  "failure": {"narration": "你摸了一下午，什么也没对上。回家的路上你一直在搓手指，像还在找那个手感。",
                              "fx": {"skill:巧手": 1}}},
                 {"text": "不去找。让它自己再来一次。", "check": ("坚忍", 9),
                  "success": {"narration": "第三个晚上它又来了，这次更长，也更慢。你没有醒，一直看到那双手把东西装了回去。\n"
                              "醒来时你什么也没记住，只记得装回去的那一下很轻。",
                              "fx": {"skill:坚忍": 2, "skill:共情": 1}},
                  "failure": {"narration": "它没有再来。你连着守了六个晚上，什么也没等到。\n"
                              "第七个晚上你梦见了自己的手，普普通通的，什么也不会。",
                              "fx": {"skill:坚忍": 1}}},
                 {"text": "把油渍洗掉，当没发生过。",
                  "effects": {"narration": "枕套洗了两遍才干净。晾在阳台上的时候你站着看了一会儿。\n"
                              "那块油渍原来的位置，正好是你睡觉时脸颊挨着的地方。",
                              "fx": {"skill:坚忍": 1, "heat": -1, "anchor": 1}}},
             ]},
            {"seen": "echo_dream", "min": 2,
             "text": "【残响】你在梦里认出了这是梦——因为这双手你已经借过太多次了。\n"
                     "它们照旧在忙，照旧不理你。这一次你没有跟着它们看，你抬起头，\n"
                     "想看看这双手连着的是谁。\n"
                     "梦里的光只够照亮手腕。再往上是没有画完的地方。",
             "options": [
                 {"text": "开口问：「你是谁。」",
                  "effects": {"narration": "手停了。整个梦停了，像一段被按住的录音。\n"
                              "然后它继续拧那颗螺栓，从刚才停下的地方，一格都没差。\n"
                              "醒来之后你想了很久：不回答，和答不上来，在梦里是同一件事。",
                              "fx": {"skill:逻辑": 2, "skill:共情": 1}}},
                 {"text": "伸手过去，和那双手一起拧。", "check": ("机械亲和", 11),
                  "success": {"narration": "四只手在一颗螺栓上，力道居然对得上。拧到最后半圈它松开了，让你自己拧完。\n"
                              "醒来时你的手腕酸得厉害，像真的干过一夜活。",
                              "fx": {"skill:机械亲和": 2, "skill:巧手": 1}},
                  "failure": {"narration": "你一碰上去，梦就散了，像水面上的一层油被吹开。\n"
                              "剩下的半夜你睡得很沉，什么也没有。",
                              "fx": {"skill:机械亲和": 1}}},
                 {"text": "不看了。转身，在梦里走开。",
                  "effects": {"narration": "你背对着那双手，一直走到梦的边上。身后的动静一直没停。\n"
                              "醒来时天还没亮。你躺着，听见自己房间里什么声音也没有，"
                              "然后才想起来那本来就该是什么声音也没有。",
                              "fx": {"skill:坚忍": 2, "anchor": 1}}},
             ]},
        ]),
    _ev("echo_slip", (
        "【残响】聚餐上有人抱怨假肢接口发炎。你头也不抬：「Ⅳ型接口就该配硅凝胶垫圈，\n"
        "原厂那个是成本削减的产物。」\n"
        "满桌寂静，坐你对面的人慢慢放下了筷子。"),
        [
            {"text": "「……我在旧杂志上看的。」", "check": ("街智", 10),
             "success": {"narration": "「有时候我也会去天台上捡点废纸，你们不去吗？」话题滑了过去。", "fx": {"skill:街智": 2, "heat": 1}},
             "failure": {"narration": "会看杂志这件事本身就是异常。寂静延长了五秒——五秒足够怀疑生根。", "fx": {"heat": 2}}},
            {"text": "干脆讲完：垫圈、扭矩、保养周期。", "check": ("威慑", 11),
             "success": {"narration": "你讲得太专业，反而没人敢质疑。「知己知彼。」你最后说。桌上有人举杯：「敬懂敌人的人。」", "fx": {"skill:威慑": 2, "heat": 1}},
             "failure": {"narration": "你讲到一半意识到，没人在听内容——他们在听你「为什么会懂」。", "fx": {"heat": 2}}},
            {"text": "装作呛到，夺门去洗手间。",
             "effects": {"narration": "你在洗手间的镜子前站了很久。镜子里的人嘴唇动了动，像还想说什么型号。你捂住了它的嘴。", "fx": {"skill:坚忍": 1, "heat": 1}}},
        ], factions=["purist", "discreet"], min_heat=None, weight=14),
    _ev("echo_slip_pro", (
        "【残响】聚餐上有人抱怨假肢接口发炎。你头也不抬：「Ⅳ型接口就该配硅凝胶垫圈，\n"
        "原厂那个是成本削减的产物。」\n"
        "满桌人转过来——不是警惕，是兴奋。「你哪个厂出来的？」\n"
        "你答不上来。你从没进过厂。这句话是从别人嘴里穿过死亡走到你舌头上的。"),
        [
            {"text": "顺水推舟，认下这份「资历」。", "check": ("街智", 10),
             "success": {"narration": "你把一段不属于你的工龄讲得有鼻子有眼。散场时有人递名片：「缺人，来吗？」你收下了——用一个死人的简历。", "fx": {"skill:街智": 2, "skill:机械亲和": 1}},
             "failure": {"narration": "细节对不上：你说的那个型号，停产比你出生还早三年。有人笑着替你圆场，但那笑容记住了你。", "fx": {"anchor": 1, "skill:街智": 1}}},
            {"text": "「不是我自己的经验，是我学来的。」", "check": ("坚忍", 10),
             "success": {"narration": "你的谦逊赢得了众人的好感，这个圈子本就把师徒关系看得格外重要。", "fx": {"skill:坚忍": 2, "skill:共情": 1}},
             "failure": {"narration": "有人客气地问：「请问你师从哪位？」你答不出，这个问题跟着你回了家。", "fx": {"anchor": 1, "skill:共情": 1}}},
            {"text": "沉默。把剩下半句咽回去。",
             "effects": {"narration": "回家路上，你复盘自己的表现，暗自发誓不能再这么招摇。", "fx": {"skill:坚忍": 1, "anchor": 1}}},
        ], factions=["open", "ascension"], min_heat=None, weight=14),
    _ev("leaf_answer", (
        "你把那句话停在破折号上。\n"
        "满屋子的人等着你说完——包括床上那个还剩一口气的人。"),
        [
            {"text": "「——是血。铁在血里，而铁来自超新星。」",
             "effects": {"narration": "最前排一个老信众用力点头，眼泪掉在自己手背上。\n"
                                      "床上的呼吸慢下来，像终于跟上了某种节拍。",
                         "fx": {"skill:共情": 1, "flag:heard_the_leaf": 1, "flag:denied_the_leaf": 1}}},
            {"text": "「——是接缝。不是肉，也不是钢，是两样咬合的那道缝。」",
             "effects": {"narration": "没有人接话。有一个人下意识摸了摸自己的手腕，然后把袖子拉了下来。",
                         "fx": {"skill:街智": 1, "flag:heard_the_leaf": 1, "flag:denied_the_leaf": 1}}},
            {"text": "「——是换上去的那部分。自己选择的才算自己的孩子。」",
             "effects": {"narration": "屋子后面有人低声说了句什么，很短。来不及转头去找是谁说的。\n"
                                      "烛火摇晃了一下。",
                         "fx": {"skill:威慑": 1, "flag:heard_the_leaf": 1, "flag:denied_the_leaf": 1}}},
            {"text": "「——是义体。硅是地壳，钽和铌是超新星。论出身，它比我们谁都更配这句话。」",
             "effects": {"narration": "你自己也愣住了。究竟谁附身于你，让你讲出这种异端邪说？",
                         "fx": {"skill:逻辑": 1, "flag:heard_the_leaf": 1, "flag:denied_the_leaf": 1}}},
            {"text": "「——是「我」。是此刻正在回答的那个东西。」",
             "effects": {"narration": "烛芯在寂静的房间里爆开，没有人敢插话。\n"
                                      "究竟谁附身于你，让你讲出这种异端邪说？",
                         "fx": {"skill:共情": 1, "skill:逻辑": 1,
                                "flag:heard_the_leaf": 1, "flag:child_of_sky": 1,
                                "flag:denied_the_leaf": 1}}},
        ], subscene=True),
    _ev("dog_pack_arrive", (
        "机械狗带你穿过三条你没走过的巷子、一道被剪开的铁丝网、一段没有灯的排水渠。\n"
        "你闻到了机油和雨水混合的气味，然后看见狗群。\n"
        "在所有的机械狗中，有一条三条腿的狗，还保留着接近一半的原始血肉。"),
        [
            {"text": "检查三条腿的狗。", "check": ("机械亲和", 9),
             "success": {"narration": "断面经过手术级别的处理。切口倾斜角度精确到便于安装标准犬用义肢接口。\n"
                                      "你抬头环顾狗群——每一条身上，都至少有一个同型号的接口。\n"
                                      "\n"
                                      "它们不是被救的，它们是被养的。\n"
                                      "义体商店切掉它们的腿，让它们出现在好心人面前。好心人付钱装假肢。\n"
                                      "装了假肢的狗被回收，切掉另一条腿，再放出去。\n"
                                      "你每一次救它，它就少一块。你的善意是砧板上的节拍器。\n"
                                      "\n"
                                      "带你来的那条狗安静地坐在你面前，仰着头，等待你的抚摸。四条假腿，假尾巴，假眼睛——是你的爱让它变成了现在的模样。",
                         "fx": {"skill:机械亲和": 2, "skill:共情": 2, "flag:dog_truth": 1},
                         "then": "dog_pack_choice"},
             "failure": {"narration": "三条腿的狗害怕人类，你一接近，它就惊慌地大叫。",
                         "fx": {"skill:机械亲和": 1, "skill:共情": 1, "flag:dog_truth": 1},
                         "then": "dog_pack_choice"}},
            {"text": "回头去找那间义体商店。", "check": ("街智", 11),
             "success": {"narration": "你原路返回，用了四十分钟找到那间店。招牌上画着一只笑脸狗。\n"
                                      "透过后门的缝隙，你看见笼子。六个空笼，一个里面蜷着一只三条腿的猫。\n"
                                      "墙上挂着一张排班表，表头写着「放养周期」：\n"
                                      "\n"
                                      "第一周：左后。第四周：右后。第八周：左前。\n"
                                      "备注栏：「408巷的那位又路过了，准备右前。」\n"
                                      "\n"
                                      "408巷。你住412。你们是邻居。\n"
                                      "你忽然明白了一件事：它每一次出现在你面前，都不是巧合。\n"
                                      "你每一次掏钱，都在替它买下一张手术台的预约。\n"
                                      "\n"
                                      "你折回狗群时，你的机械狗还在原地等着。它的眼神没有怨恨。",
                         "fx": {"skill:街智": 2, "skill:共情": 1, "flag:dog_truth": 1},
                         "then": "dog_pack_choice"},
             "failure": {"narration": "你没有找到那间店，这种店搬家的速度一向很快。\n"
                                      "你回到狗群，机械狗舔了舔你的手，它的舌头也是金属的。",
                         "fx": {"skill:街智": 1, "flag:dog_truth": 1},
                         "then": "dog_pack_choice"}},
            {"text": "你在狗群中间坐下来，什么也不做。",
             "effects": {"narration": "一条狗把头搁在你膝盖上，金属下颌硌得你腿疼。\n"
                                      "另一条绕到你身后，用鼻子拱你的后腰——你花了三秒才意识到它在找你的尾巴。\n"
                                      "你没有尾巴。\n"
                                      "你转过身来，抱住找尾巴的狗，对它汪汪叫。它的机械尾巴像螺旋桨一样欢快地甩着，打得你生疼。",
                         "fx": {"skill:共情": 2, "flag:dog_truth": 1},
                         "then": "dog_pack_choice"}},
        ], subscene=True),
    _ev("dog_pack_choice", (
        "暮色四合，狗群聚集到一起，准备度过夜晚。明明已经是机械狗了，它们却仍然保持着原始的生物钟。\n"
        "你的机械狗站在你身边，长久地望着你，你无法感知此刻它内心的宇宙。突然，它仰头长啸，如同狼嚎。\n"
        "\n"
        "你注视着它，突然意识到自己的眼睛也是机械眼。低头看自己的手，六成也是机械的。接缝处的皮肤已经失去了自然的色泽。\n"
        "你重新望向你的机械狗——你不该在这时候望向它的——只一眼，你就明白了。你和它之间的区别，比你愿意承认的要少。"),
        [
            {"text": "留下来，在狗群里过夜。",
             "effects": {"narration": "你用外套卷了个枕头，和狗群依偎在一起。冰冷的月光下，你能清楚感觉到每一条狗的电池余热。你昏昏欲睡，但知道所有狗都醒着，它们是100%机械的，已经失去做梦的能力。\n"
                                      "\n"
                                      "半夜，你醒了一次，你的机械狗知道你醒来，陪你站起来在四周漫游。\n"
                                      "排水渠尽头的废弃配电房里，地上散落着各种尺寸的义肢零件——狗的。\n"
                                      "前爪、后腿、脊柱节段、一条盘起来的尾巴。全是报废件，接口磨平了，\n"
                                      "关节里的润滑液干成了黑色的硬壳。\n"
                                      "但它们被仔细地按大小分类，排成一排，像某种……邀请。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1, "flag:dog_stay": 1}}},
            {"text": "站起来，回城。「我不是狗。」",
             "effects": {"narration": "带你来的机械狗送你走到铁丝网豁口。它没有穿过去。\n"
                                      "你回到街灯下面，走了二十步，回头看——豁口空了。\n"
                                      "你回家洗澡睡觉，像往常一样。\n"
                                      "那一夜你梦见自己跑得很快，风灌进四条腿的每一个关节。醒来时你觉得床太高了。",
                         "fx": {"skill:坚忍": 2, "skill:共情": 1, "flag:dog_over": 1}}},
            {"text": "停在这里。「狗是人类最好的朋友。」",
             "effects": {"narration": "你知道你会回来，看望你的朋友们，而它们会摇着金属尾巴欢迎你，确信无疑。\n"
                                      "狗是人类最好的朋友，小狗爱你。",
                         "fx": {"skill:共情": 2, "skill:机械亲和": 1,
                                "flag:dog_stay": 1, "flag:dog_over": 1}}},
        ], subscene=True),
    # 作者定案：**这一幕不再有选项，读完直接接狗的湖。**
    # 走到这里的人已经选过了（狗群那一幕的选项一），再问一遍是把结尾拆开重演。
    # 那一个「选项」只是一句落笔 —— 它带着上载与犬身两个标记，
    # 于是这一世在这儿终结，封档，湖在下一次醒来时等着。
    _ev("finale_dog", (
        "第二天，你没有回城。第三天也没有。\n"
        "你开始修理那些废弃零件——磨接口、换润滑液、给断线的关节重新焊上导线。\n"
        "第七天，你试着把一条狗的前爪装在自己的左手外面。\n"
        "尺寸不对。五根手指卡在四趾结构里，动不了。\n"
        "\n"
        "你看着自己被卡住的手笑了。然后你拆掉了手指。\n"
        "\n"
        "不是一下子拆的。先是左手小指。然后无名指。\n"
        "你用狗的四趾关节替代它们。第一天很疼，第二天就不疼了——\n"
        "不是愈合了，是你关掉了那段神经。\n"
        "\n"
        "一个月后你拆完了最后一根脚趾。你试着用四肢走路。\n"
        "摔了很多次。膝盖磨穿了三层合金。但狗群等你。\n"
        "那条最老的机械狗——你的狗——走在你旁边，用和你一样慢的速度。\n"
        "\n"
        "出生时，你用四条腿行走。中间一段人生，你用两条腿行走。"
        "最后的日子，你再次用四条腿行走。\n"
        "你呼朋引伴，度过了无悔的时光。"),
        [
            {"text": "（合上眼睛。）",
             "effects": {"narration": "狗群卧成一圈，把你围在中间。最外面那几只朝着风的方向。",
                         "fx": {"aug": 100, "flag:ascended": 1,
                                "flag:became_dog": 1, "flag:dog_over": 1}}},
        ], subscene=True),
    # ------------------------------------------------ 心照不宣 · 灰港
    _ev("harbor_cargo", (
        "凌晨三点，码头。雾浓得像纱布裹在脸上。\n"
        "接头人把一只军绿色防水箱推到你脚边。箱子很沉，里面的东西轻轻撞击箱壁，\n"
        "发出骨头碰骨头的闷响——不对，是金属碰金属。\n"
        "「六副，都验过了。」接头人往手心呵了口气，「原主的数据清过了，理论上。」\n"
        "他强调了「理论上」三个字。\n"
        "\n"
        "你打开箱子。六副二手义肢，按大小码放在防潮泡沫里。\n"
        "最上面那只右手的食指还微微蜷着，保持着握笔的弧度。"),
        [
            {"text": "逐只检查接口编号，核对来源。", "check": ("街智", 10),
             "success": {"narration": "编号后四位对上了灰港的回收登记簿。六副义肢，三副来自自愿置换——升级换代，\n"
                         "旧的折价。两副来自太平间——死者家属签了捐赠协议，或者签了长得像捐赠协议的东西。\n"
                         "最后一副没有编号。你翻遍了整只手臂，在肘关节内侧找到一排被锉掉的字。\n"
                         "锉痕很新。", "fx": {"skill:街智": 2, "flag:harbor_run": 1}},
             "failure": {"narration": "编号簿的字迹被海风和湿气泡得发糊。你只对上了两副。\n"
                         "剩下四副，你闭着眼签了收。箱子关上的时候，那只蜷着的手指像是动了一下。",
                         "fx": {"skill:街智": 1, "flag:harbor_run": 1}}},
            {"text": "不查了，清点数目直接签收。",
             "effects": {"narration": "你不是第一次接货了。有些事情，不查比查了好——查了，你就得对结果负责。\n"
                         "你在签收单上按了手印。墨迹和雾气混在一起，指纹模糊得像别人的。",
                         "fx": {"skill:坚忍": 1, "flag:harbor_run": 1}}},
            {"text": "问接头人：「原主去哪了？」", "check": ("共情", 9),
             "success": {"narration": "接头人盯着你看了三秒，像在判断你是不是新来的。\n"
                         "「去哪了？」他把烟头按灭在箱盖上，「这行有个规矩：零件过手，名字落水。\n"
                         "码头下面的海水含盐量特别高，什么都能腐蚀干净。」\n"
                         "他拍了拍箱子：「你拿到的是零件，不是人。零件没有去向。」",
                         "fx": {"skill:共情": 2, "skill:街智": 1, "flag:harbor_run": 1}},
             "failure": {"narration": "接头人笑了一下，那种见过太多新人的笑。\n"
                         "「小孩，你搬就是了。」他拎起箱子的另一头，帮你一起抬上了板车。\n"
                         "有些问题的答案和没有答案一样重。",
                         "fx": {"skill:共情": 1, "flag:harbor_run": 1}}},
        ], factions=["discreet"], subs=["灰港"], weight=10, echoes=[
            {"deed": "harbor_run", "min": 2,
             "text": "接头人看见你就把烟掐了。「老规矩，不验不问。」他顿了一下，\n"
                     "「不过今天这批……你自己看吧。有一副手的指纹，登记簿上查不到主人。」"},
            {"deed": "harbor_run", "min": 3,
             "text": "今天接头人带了个新人来。十六七岁，手插在袖子里，一直在看别处。\n"
                     "接头人没有介绍你，只朝箱子偏了偏头，然后退开两步，把位置让给了你。\n"
                     "新人小声问了一句什么。接头人说：「问他。」"},
            {"deed": "harbor_saved_ledger", "min": 1,
             "text": "你翻口袋找零钱的时候，摸到一张折叠的纸。展开一看，是一行编码。\n"
                     "你不记得什么时候抄的。但你认得这是灰港的格式：日期，部位，来源，去向。"},
            {"deed": "harbor_ledger", "min": 1,
             "text": "你在人群中下意识地扫编号——手腕内侧、耳后、踝关节上方。\n"
                     "灰港的习惯。你在数这条街上有多少个零件是你经手的。"},
        ]),
    _ev("harbor_secondhand", (
        "黑诊所的术后恢复室。你隔壁床的客人在装一只二手眼。\n"
        "手术很成功。他睁开新眼的第一秒，愣住了。\n"
        "「这间屋子，我来过。」他说。他是第一次来灰港。\n"
        "医生不动声色地在病历上写了一行字：「移植后既视感，常见，观察。」\n"
        "\n"
        "你知道医生在说谎。常见是真的，但不是「既视感」。\n"
        "是前一任主人的视觉记忆残留在光感芯片的缓存里。\n"
        "他看见的不是这间屋子。他看见的是上一个人最后看见的东西。"),
        [
            {"text": "告诉他真相。", "check": ("共情", 10),
             "success": {"narration": "他安静了很久。然后他用新眼睛看了一圈屋子，很慢，像在和上一个主人的目光告别。\n"
                         "「那我得好好用。」他说，「替他多看几样东西。」",
                         "fx": {"skill:共情": 2, "flag:harbor_ghost": 1}},
             "failure": {"narration": "他猛地坐起来，开始拽自己的眼眶。你和医生一人按住一边。\n"
                         "麻药补了三针他才安静下来。医生出门时看了你一眼，什么也没说。",
                         "fx": {"skill:共情": 1, "hp": -1, "flag:harbor_ghost": 1}}},
            {"text": "附和医生：「挺常见的，我也有过。」", "check": ("街智", 8),
             "success": {"narration": "「是吗？」他松了口气，「那就好。我还以为这只眼有问题。」\n"
                         "你微笑着，没告诉他你的「既视感」从没消失过——\n"
                         "你左手中指的屈伸节奏到现在还不是你自己的。那是一个钢琴家的手指。\n"
                         "你不弹琴。但你的手指记得。",
                         "fx": {"skill:街智": 1, "skill:坚忍": 1}},
             "failure": {"narration": "他将信将疑。你编的故事太笼统了，不像亲历过的人。\n"
                         "「你在安慰我。」他苦笑，「算了，装都装了。」",
                         "fx": {"skill:街智": 1}}},
            {"text": "找医生谈：这批零件清洗不干净，要退货。", "check": ("机械亲和", 10),
             "success": {"narration": "医生把你拉进手术室，关上门。\n"
                         "「退？退给谁？」她摘下手套，露出自己的双手——左手是原装的，\n"
                         "右手接缝处隐约可见一排编号。「你以为我的手是哪来的？」\n"
                         "她把指关节一个一个屈伸给你看：「这只手以前是个裁缝的。\n"
                         "我现在缝合比念书时好三倍。这不是残留，这是天赋。」\n"
                         "她重新戴上手套：「灰港不卖零件。灰港卖手艺。」",
                         "fx": {"skill:机械亲和": 2, "skill:共情": 1, "flag:harbor_ghost": 1}},
             "failure": {"narration": "医生头也不抬：「退货条款在协议第七页，看过吗？」\n"
                         "你没看过。没人看过。灰港的协议只有一条不成文的：装了就是你的。",
                         "fx": {"skill:机械亲和": 1}}},
        ], factions=["discreet"], subs=["灰港"], weight=10,
        voices={"机械亲和": "【机械亲和】缓存深度取决于芯片制程。28纳米的能存三到五秒的运动序列。7纳米的……够存一首完整的曲子。"},
        echoes=[
            {"deed": "harbor_ghost", "min": 1,
             "text": "恢复室里躺着一个刚装完手臂的年轻人。他在反复握拳松拳，表情越来越困惑。\n"
                     "「这只手……会弹吉他。」他小声说。他不会弹吉他。"},
            {"deed": "harbor_ghost", "min": 2,
             "text": "你闭上眼的那一瞬间，看见了一扇你没见过的门。\n"
                     "不是梦。是左眼芯片里上一个主人的最后一帧画面。\n"
                     "那扇门是关着的。你永远不会知道门后面是什么。"},
        ]),
    _ev("harbor_passenger", (
        "后半夜，码头仓库。一个女人抱着孩子坐在鱼箱上。\n"
        "她们从纯血区来。孩子先天心脏缺损，那边的医生说能治，但要装一枚人工瓣膜。\n"
        "纯血区不允许。她带孩子翻了三道检查站到灰港。\n"
        "「我不在乎教义，」她的声音很平，「我在乎她能活到上学。」\n"
        "\n"
        "诊所能做这台手术。但孩子装了瓣膜就再也回不了纯血区——\n"
        "扫描门一过，心跳的频率就会出卖那枚金属。\n"
        "也就是说，你帮了这个忙，这个孩子一辈子是灰港人。"),
        [
            {"text": "安排手术。", "check": ("巧手", 10),
             "success": {"narration": "手术四小时。你在门外听了四小时的心电监护仪的声音。\n"
                         "第三小时的时候，节奏变了——多了一个不属于肉体的频率，\n"
                         "细小、精确、不知疲倦。\n"
                         "孩子醒来的时候说胸口凉凉的。她妈妈说那是新心跳的温度，暖一暖就好了。",
                         "fx": {"skill:巧手": 2, "skill:共情": 1, "flag:harbor_passage": 1}},
             "failure": {"narration": "术中出了一点状况——孩子的胸腔比预期的小，标准瓣膜装不进去。\n"
                         "医生临时拿锉刀把瓣膜磨小了一圈。能用。但接缝处没有原来那么严丝合缝。\n"
                         "以后每到变天，那个接缝会发出极细的哨音。\n"
                         "只有孩子自己听得见。",
                         "fx": {"skill:巧手": 1, "skill:共情": 2, "flag:harbor_passage": 1}}},
            {"text": "找一种能骗过扫描门的瓣膜型号。", "check": ("电子直觉", 12),
             "success": {"narration": "灰港地下有一种叫「鬼瓣」的特殊型号：生物陶瓷外壳，声纳回波和真肉几乎一样。\n"
                         "贵三倍，但扫描门过得去。\n"
                         "你帮她联系了供货商。供货商要求先验孩子的胸片——\n"
                         "不是为了医学，是为了确认尺码。跟量鞋一样。\n"
                         "\n"
                         "鬼瓣装好了。孩子可以回纯血区。她会带着一颗假装是真的心长大。\n"
                         "灰港的生意，有时候就是帮人假装。",
                         "fx": {"skill:电子直觉": 2, "skill:街智": 1, "flag:harbor_passage": 1}},
             "failure": {"narration": "鬼瓣的最小号也大了一码。孩子的胸腔放不下。\n"
                         "最后还是装的标准型。回不去了。\n"
                         "女人没有哭。她开始问灰港的学区怎么划。",
                         "fx": {"skill:电子直觉": 1, "skill:共情": 1, "flag:harbor_passage": 1}}},
            {"text": "劝她回去。「灰港不适合养孩子。」",
             "effects": {"narration": "「灰港不适合养孩子。」你说。\n"
                         "她看着你，眼神平静得可怕：「纯血区也不适合养一个心脏有洞的孩子。」\n"
                         "你闭上了嘴。她抱着孩子坐在鱼箱上，等天亮，等下一个愿意帮忙的人。\n"
                         "\n"
                         "你走到仓库门口的时候停了一步。没有回头。但你把诊所的地址写在一张鱼票背面，\n"
                         "压在她脚边的箱子上。",
                         "fx": {"skill:坚忍": 1, "skill:共情": 1}}},
        ], factions=["discreet"], subs=["灰港"], weight=8, echoes=[
            {"deed": "harbor_passage", "min": 1,
             "text": "码头边一个小女孩在跳房子。她跳得很高，落地很轻，\n"
                     "胸腔里隐约传来一声极细的金属哨音。变天了。"},
            {"deed": "harbor_passage", "min": 2,
             "text": "码头小学放学了。一群孩子跑过你身边。你注意到至少三个孩子跑步的姿势不太对——\n"
                     "他们在保护胸口。灰港的孩子，有些是带着秘密长大的。"},
        ]),
    _ev("harbor_fog_night", (
        "灰港要搬家了。\n"
        "有人举报了诊所的位置。三天后联合执法队来拆。\n"
        "三天不够搬一间诊所——但够搬最重要的东西：病历。\n"
        "十七年的病历，每一份都是一条罪证，每一份也是一个人活下来的收据。\n"
        "\n"
        "今夜大雾。码头能见度不足五米。这是搬家的最好天气，也是最坏天气——\n"
        "你有可能把箱子搬进海里。"),
        [
            {"text": "组织人手，分批搬运。", "check": ("威慑", 10),
             "success": {"narration": "你在雾里吹了三声哨——灰港的老暗号，意思是「集合，不问为什么」。\n"
                         "来了十二个人。最小的十五岁，胸口有哨音的那种；最老的七十，\n"
                         "两条腿都是灰港诊所装的。\n"
                         "他们排成一列，每人间隔两步，靠声音传递方向。\n"
                         "四小时搬完。最后一箱递到新址的时候，雾散了。\n"
                         "港务局的人看见十三个人站在空码头上，什么也没有。",
                         "fx": {"skill:威慑": 2, "skill:街智": 1, "heat": -2}},
             "failure": {"narration": "第三趟的时候，有人滑进了海里。箱子沉了，人捞上来了。\n"
                         "沉的那箱是十一年前到八年前的病历。三年的人，就这么没了名字。\n"
                         "你不知道那三年里有没有你认识的人。你不敢查。",
                         "fx": {"skill:威慑": 1, "skill:共情": 1, "hp": -1}}},
            {"text": "不搬了，全部烧掉。「灰港不需要历史，灰港需要活人。」",
             "effects": {"narration": "火烧了四十分钟。纸质病历烧起来意外地快，像它们一直在等这一天。\n"
                         "十七年的名字、血型、过敏源、术后嘱咐，变成灰，被海风吹向城区。\n"
                         "有人站在旁边哭。你没哭。你在数：十七年，大概四千个人。\n"
                         "四千个人今晚失去了自己活下来的证据。\n"
                         "\n"
                         "但灰港还在。灰港从来不是一间诊所。灰港是一个动词。",
                         "fx": {"skill:坚忍": 2, "flag:harbor_burned": 1}}},
            {"text": "把病历数字化，传进加密网络。", "check": ("电子直觉", 11),
             "success": {"narration": "你用了整夜。扫描仪的灯在雾里一亮一灭，像灯塔。\n"
                         "最后一份病历扫完时，天已经亮了。加密包上传到三个匿名节点。\n"
                         "从今天起，灰港的记忆住在电磁波里——摸不着，删不掉，\n"
                         "和那些二手义肢里残留的肌肉记忆一样，藏在看不见的地方继续活着。",
                         "fx": {"skill:电子直觉": 2, "skill:逻辑": 1, "heat": -1}},
             "failure": {"narration": "扫描到一半停电了。充电宝只够扫前七年。后十年还是纸。\n"
                         "你做了一个决定：把后十年藏进二手义肢的存储芯片里——\n"
                         "每副义肢塞一点，分散在整个灰港的客户身上。\n"
                         "从此，每一个从灰港走出去的人，身体里都多了几个别人的名字。",
                         "fx": {"skill:电子直觉": 1, "skill:街智": 1, "flag:harbor_scattered": 1}}},
        ], factions=["discreet"], subs=["灰港"], weight=8, min_heat=3, echoes=[
            {"deed": "harbor_burned", "min": 1,
             "text": "码头上有一块烧焦的地面，长出了草。没人记得这里烧过什么，但草长得比别处绿。"},
            {"deed": "harbor_scattered", "min": 1,
             "text": "你弯腰的时候，左膝盖的义体发出一声短促的嗡鸣——不是故障，\n"
                     "是里面存着的某个人的术后嘱咐在定时提醒：「每六小时翻一次身。」\n"
                     "一个你不认识的人的医嘱，住在你的关节里。"},
        ]),
    _ev("harbor_ledger", (
        "老码头长叫你去他的集装箱办公室。\n"
        "他把一本发黄的账簿推到你面前。不是钱的账——是零件的账。\n"
        "每一行：日期，部位，来源编码，去向编码。\n"
        "「灰港的规矩，零件过手名字落水。但我留了编码。」他指着某一行，\n"
        "「你看这个。左眼，来源K-4412，去向D-0078。」\n"
        "他翻到下一页：「两年后，右手，来源D-0078，去向M-2291。」\n"
        "再翻：「又两年，脊柱节段，来源M-2291，去向K-4412。」\n"
        "\n"
        "K-4412的左眼给了D-0078。D-0078的右手给了M-2291。M-2291的脊柱给了K-4412。\n"
        "三个人。每人身上都有另外两个人的零件。一条首尾相接的环。\n"
        "\n"
        "码头长合上账簿：「灰港干了十七年，这种环我见过不下二十个。\n"
        "时间够长的话，所有人身上都会有所有人的零件。」\n"
        "他看着你：「你猜，到那一天，谁是谁？」"),
        [
            {"text": "「那就没有谁是谁了。」", "check": ("逻辑", 10),
             "success": {"narration": "码头长慢慢点了根烟。「你知道灰港为什么叫灰港吗？」\n"
                         "你摇头。\n"
                         "「不是因为雾。是因为零件到了这里，黑的白的都变成灰的。\n"
                         "来源不重要了，去向不重要了。重要的是它还能用。」\n"
                         "他把账簿推进抽屉：「灰色不是没有颜色。灰色是所有颜色混在一起。」",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1}},
             "failure": {"narration": "码头长摇了摇头。「太轻巧了。」他说，「你再想想。」\n"
                         "你没想出来。但你记住了那个环。三个人的零件首尾相接，\n"
                         "像一条咬着自己尾巴的蛇。",
                         "fx": {"skill:逻辑": 1, "skill:共情": 1}}},
            {"text": "「谁是谁不重要。重要的是谁在用。」", "check": ("共情", 9),
             "success": {"narration": "码头长愣了一下，然后笑了。「来灰港的人都带着自己的名字和别人的零件。\n"
                         "你是第一个说出这句话的。」\n"
                         "他把账簿递给你：「那从今天起，这本账你来记。\n"
                         "码头长不是一个人，码头长是一支笔。上一个码头长的手，现在在你认识的某个人身上。」",
                         "fx": {"skill:共情": 2, "skill:街智": 1, "flag:harbor_ledger": 1}},
             "failure": {"narration": "码头长叹了口气。「你说的对。但『在用』也是一个临时状态。」\n"
                         "他敲了敲自己的胸口，金属回声：「总有一天你也会变成来源编码。」",
                         "fx": {"skill:共情": 1, "skill:坚忍": 1}}},
            {"text": "问他：「我身上有没有别人的？」",
             "effects": {"narration": "码头长翻到账簿最后几页。手指划过编码，停在一行上。\n"
                         "他看着那行字看了很久。然后他合上了账簿。\n"
                         "「有，」他说，「但我不打算告诉你是哪个零件。」\n"
                         "他站起来走到门口：「有些事知道了你会开始区分。\n"
                         "这块是我的，那块不是。但你身上没有一块零件是单独活着的。」",
                         "fx": {"skill:共情": 1, "skill:坚忍": 2}}},
        ], factions=["discreet"], subs=["灰港"], weight=6,
        req_seen={"harbor_cargo": 1},
        voices={"街智": "【街智】「K-4412。」你突然说出一个编号。你不知道自己怎么知道的。码头长抬头看你，眼神变了。"}),
    _ev("finale_harbor", (
        "【终幕】灰港的最后一个夜晚。\n"
        "不是被查封——是海平面。灰港建在旧城区最低处，年年涨潮年年退。\n"
        "今年潮水不退了。地下诊所的手术台已经泡在半米深的海水里。\n"
        "\n"
        "码头长说：走。带上能带的。\n"
        "你站在齐膝的水里，面前是那只军绿色防水箱。\n"
        "箱子里不是义肢——是账簿。十七年的编码，谁的零件去了谁的身上。\n"
        "\n"
        "潮水还在涨。你只能带走一样东西。"),
        [
            {"text": "带账簿。灰港可以没有码头，不能没有记忆。", "check": ("坚忍", 11),
             "success": {"narration": "你把箱子顶在头上，水漫到胸口。出了码头的时候天已经亮了。\n"
                         "你浑身湿透，站在高地上回头看——灰港沉在水面以下，\n"
                         "只有诊所的红十字灯还在水下亮着，像一颗不肯闭上的眼。\n"
                         "\n"
                         "你打开箱子检查。账簿没湿。十七年的编码完好无损。\n"
                         "灰港沉了。但每个人身上的每个零件，都还记得自己从哪里来。",
                         "fx": {"skill:坚忍": 2, "skill:共情": 2, "flag:harbor_saved_ledger": 1}},
             "failure": {"narration": "水太深了。箱子太重了。你在第三个拐弯的时候脚下一滑，\n"
                         "箱子脱手。你伸手去捞——够到了锁扣，但锁扣断了。\n"
                         "账簿散落在水里。纸页吸水膨胀，编码变成一团灰色的糊。\n"
                         "\n"
                         "灰色。所有颜色混在一起。\n"
                         "你空着手走出码头。十七年的来源和去向，从此真的落了水。",
                         "fx": {"skill:坚忍": 1, "skill:共情": 2}}},
            {"text": "带最后一批义肢。人比记录重要。", "check": ("机械亲和", 11),
             "success": {"narration": "你把六副义肢分别绑在身上——两条胳膊各挂一副，背上背两副，\n"
                         "腰上系两副。你看起来像一棵挂满了零件的树。\n"
                         "水退了以后，你在高地上一副一副解下来。\n"
                         "有三个人在等。他们等了一夜。\n"
                         "你把义肢递给他们的时候，手上沾着海水和机油。\n"
                         "一个人低声说：「灰港没了，你还在就行。」",
                         "fx": {"skill:机械亲和": 2, "skill:共情": 1, "flag:harbor_saved_parts": 1}},
             "failure": {"narration": "义肢太重了，水流太急。你只带出来两副。\n"
                         "第三副在出口处被暗流卷走，撞在堤坝上，散了架。\n"
                         "那只右手的食指还保持着握笔的弧度，单独漂在水面上，\n"
                         "像在给谁写最后一个字。",
                         "fx": {"skill:机械亲和": 1, "skill:共情": 1, "hp": -1}}},
            {"text": "什么都不带。最后一个离开，把门从里面锁上。",
             "effects": {"narration": "你把门从里面锁上。然后从窗户翻出去。\n"
                         "水面合拢。灰港沉下去的过程很安静，没有气泡，没有回声。\n"
                         "像一个人闭上眼——不是死了，是决定不再看。\n"
                         "\n"
                         "你站在高地上淋雨，口袋里只有一把诊所的钥匙。\n"
                         "钥匙打不开任何门了。但你没有扔。\n"
                         "\n"
                         "灰港不是一间诊所。灰港不是一座码头。\n"
                         "灰港是一把湿透了的钥匙，装在某个还活着的人口袋里。\n"
                         "\n"
                         "只要还有人需要一个不问来路的地方，灰港就会在别处重新长出来。\n"
                         "下一个灰港也许不在海边，也许天色很好，也许连「港」字都不占。\n"
                         "**但所有需要它的人都会认得出它。** 名字是跟着需要走的，\n"
                         "不是跟着地图走的。",
                         "fx": {"skill:坚忍": 3, "skill:共情": 1, "flag:harbor_sunk": 1}}},
        ], factions=["discreet"], subs=["灰港"], subscene=True),
    # ------------------------------------------------ 纯血誓约 · 圣殿派
    _ev("temple_scripture", (
        "圣殿地下室的经文抄写课。圣殿派保存着一块刻了字的金属薄片——\n"
        "据说是上一个纪元遗留的唯一信物。所有信徒每年都要手抄一遍全文。\n"
        "问题是：薄片上有一个词被腐蚀了，只剩偏旁。\n"
        "两百年来，圣殿派一直把它抄成「归」——魂归苍天。\n"
        "今晚你拿着放大镜看了很久。腐蚀的痕迹下面，残存的笔画不像「归」。\n"
        "更像「跻」。跻身苍天。\n"
        "\n"
        "「归」是回家。「跻」是攀登。一字之差，教义从「保持完整等待回收」\n"
        "变成「不断改造直到够格」。"),
        [
            {"text": "照旧抄「归」。教义经不起第二种读法。", "check": ("坚忍", 9),
             "success": {"narration": "你抄完整篇经文，手腕酸了三天。最后一笔落下的时候，你在想：\n"
                         "两百年，每年一遍，多少双手在这个字上停留过？多少人看到了「跻」，抄下了「归」？\n"
                         "也许你不是第一个。也许这间地下室的历史，就是一部反复选择「归」的历史。",
                         "fx": {"skill:坚忍": 2, "flag:temple_doubt": 1}},
             "failure": {"narration": "你的手在「归」字上抖了一下。墨迹洇开，像一扇小窗。\n"
                         "你赶紧用新纸覆盖上去重抄。但你知道那一笔抖动的真正原因。",
                         "fx": {"skill:坚忍": 1, "flag:temple_doubt": 1}}},
            {"text": "报告长老：原文可能不是「归」。", "check": ("逻辑", 11),
             "success": {"narration": "长老拿着放大镜看了很久。久到你以为他会叫来全体信众。\n"
                         "他把放大镜收起来，把那块金属薄片锁回保险箱。\n"
                         "「孩子，」他的声音很平，「你以为我没看过吗？」\n"
                         "他没说它到底是「归」还是「跻」。他只说了一句：\n"
                         "「教义是船。船板可以换，航向不能换。」\n"
                         "\n"
                         "你走出地下室的时候，听见背后落锁的声音。\n"
                         "你不知道他锁住的是经文，还是那个字。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1, "flag:temple_doubt": 1}},
             "failure": {"narration": "长老听完，沉默了很久。然后他把你的抄本收走了。\n"
                         "「年轻人眼花是正常的。以后抄经不必用放大镜。」\n"
                         "他的语气没有责备。但之后一个月，地下室的灯被换成了更暗的。",
                         "fx": {"skill:逻辑": 1, "heat": 1, "flag:temple_doubt": 1}}},
            {"text": "悄悄抄成「跻」，夹在自己的抄本里，不给别人看。",
             "effects": {"narration": "你的抄本夹在四百份「归」中间，像一颗反转的螺丝。\n"
                         "没有人会翻你的抄本。抄经课的全部意义在于抄写这个动作本身——\n"
                         "就像打坐不是为了结论，是为了过程。\n"
                         "但你知道，从今天起，圣殿的四百零一份经文里，有一份在说不同的话。\n"
                         "一份就够了。",
                         "fx": {"skill:逻辑": 1, "skill:街智": 1,
                                "flag:temple_doubt": 1, "flag:temple_heretic": 1}}},
        ], factions=["purist"], subs=["圣殿派"], weight=10, echoes=[
            {"all": [{"deed": "front_scar", "min": 1},
                     {"deed": "temple_doubt", "min": 1}],
             "text": "晚祷的时候你看着跪了一地的人，想：\n"
                     "他们中间有多少人，嘴上念着「完整」，心里想着一个装不起瓣膜的孩子？\n"
                     "沉默不代表同意。沉默有时候代表还没想好怎么开口。"},
            {"deed": "temple_doubt", "min": 2,
             "text": "抄经课上，你旁边的人停了笔。她盯着那个字看了很久，然后——抄了一个「归」。\n"
                     "但她握笔的力道变了。"},
            {"deed": "temple_heretic", "min": 1,
             "text": "有人在圣殿公共抄本架上翻你的名字。你的那份不在架上——你从来没交过。"},
        ]),
    _ev("temple_knees", (
        "传教士死了。\n"
        "葬礼按最高规格办——全血肉安葬，不动一颗螺丝。这是圣殿派对自己人\n"
        "最后的敬意：你怎么来的，就怎么走。\n"
        "\n"
        "入殓的时候出了事。\n"
        "\n"
        "负责净身的执事掀开裤腿，停住了。\n"
        "钛合金膝盖。两个。手术痕迹至少二十年。\n"
        "关节处的磨损模式和他爬楼梯的步频完全吻合——\n"
        "他用这双假膝盖爬了二十年楼梯，传了二十年纯血的道。\n"
        "\n"
        "执事回头看向长老。长老合上了眼。\n"
        "满屋子的人都在等一个判决。"),
        [
            {"text": "「按不完整者规格安葬。誓约没有例外。」", "check": ("威慑", 10),
             "success": {"narration": "你说得很硬。有几个人低下了头。\n"
                         "葬礼改在后半夜，没有经文，没有蜡烛。\n"
                         "他被埋进没有名字的那片土。坟上没有标记。\n"
                         "但你注意到，之后一年，每天清晨都有人来那片无名土上放一支白蜡烛。\n"
                         "没有人承认是自己放的。",
                         "fx": {"skill:威慑": 2, "flag:temple_strict": 1}},
             "failure": {"narration": "你话还没说完，角落里一个老信众站起来：\n"
                         "「他四十层楼，爬了二十年。你爬过几层？」\n"
                         "你闭上了嘴。规格最终由长老定：折中，半礼。\n"
                         "半礼是什么意思，谁也说不清。",
                         "fx": {"skill:威慑": 1, "skill:共情": 1}}},
            {"text": "「把膝盖取出来，然后按全血肉安葬。死后的他是完整的。」", "check": ("共情", 11),
             "success": {"narration": "手术用了三小时。你在门外听着金属脱离骨骼的声音——\n"
                         "二十年的组织已经和钛合金长在了一起，分离的时候带出了一层骨膜。\n"
                         "\n"
                         "两块膝盖被放在一个木盒里。遗体的裤腿空了一截，但形式上完整了。\n"
                         "长老主持了全规格葬礼。没有人提那个木盒后来去了哪里。\n"
                         "\n"
                         "三天后你在圣殿的供桌上看到了它。放在最高的经卷旁边。\n"
                         "没有标签，没有说明。但谁都知道那是什么。",
                         "fx": {"skill:共情": 2, "flag:temple_relic": 1}},
             "failure": {"narration": "执事拒绝动手：「我不碰死人身上的铁。」\n"
                         "你找了三个人，没人愿意。最后是灰港来的一个外科医生做的——\n"
                         "消息传开后，圣殿派在自己的葬礼上请了黑诊所的人，成了全城的笑话。",
                         "fx": {"skill:共情": 1, "heat": 2}}},
            {"text": "「他用假膝盖走了二十年真路。我要知道他的膝盖是在哪装的。」", "check": ("街智", 10),
             "success": {"narration": "编号被锉掉了，但磨损模式骗不了人。你找到了灰港的一个旧接头人。\n"
                         "他翻了翻记忆：「二十年前……有个传教士找过我们。\n"
                         "他说他需要爬楼梯。他说如果他不爬楼梯，就没有人替他爬。」\n"
                         "接头人叹了口气：「我见过很多来灰港的圣殿人。他们来的时候都说是最后一次。\n"
                         "他是唯一一个真的只来了一次的。」",
                         "fx": {"skill:街智": 2, "skill:共情": 1,
                                "flag:temple_doubt": 1, "flag:temple_harbor_link": 1},
                         "extra": [{"deed": "harbor_run", "min": 1,
                                    "text": "\n你在灰港的旧账簿上找到了那一行：日期、部位、来源编码、去向编码。\n"
                                            "去向编码后面有人用铅笔写了五个字：「只来了一次。」"}]},
             "failure": {"narration": "编号被锉得太干净了。线索断在灰港和圣殿之间的那段雾里。\n"
                         "但你知道了一件事：传教士自己锉的。他比任何人都想消灭证据。\n"
                         "不是为了保护自己。是为了保护教义。",
                         "fx": {"skill:街智": 1, "flag:temple_doubt": 1}}},
            {"text": "什么都不说。走到遗体旁边，把裤腿放下来。",
             "effects": {"narration": "你走到遗体旁边，弯腰，把掀开的裤腿一层一层放回去。\n"
                         "布料盖住了钛合金。从外面看，他还是一个完整的人。\n"
                         "长老什么也没说。执事什么也没说。\n"
                         "裤腿下面的秘密，和他一起进了土。\n"
                         "\n"
                         "葬礼按全规格办了。所有人都知道裤腿下面是什么。\n"
                         "所有人都假装不知道。这是圣殿派的第二种虔诚。",
                         "fx": {"skill:坚忍": 2, "skill:共情": 1}}},
        ], factions=["purist"], subs=["圣殿派"], weight=8,
        req_seen={"elevator_preacher": 2}, echoes=[
            {"deed": "temple_relic", "min": 1,
             "text": "圣殿供桌最高处，经卷旁边多了一个没有标签的木盒。\n"
                     "新来的信众以为那是圣物。某种意义上，他们是对的。"},
            {"deed": "temple_strict", "min": 1,
             "text": "无名土上的白蜡烛今天灭了。风太大。但明天早上还会有新的。"},
            {"deed": "temple_harbor_link", "min": 1,
             "text": "你经过灰港旧址时，一个退休的接头人朝你点了下头。\n"
                     "圣殿派和灰港之间，隔着一层裤腿的距离。"},
        ]),
    _ev("temple_trial", (
        "一年一度的「血肉试炼」。圣殿派最重要的仪式：\n"
        "全体信众赤脚走过一段碎石路。意义是感受疼痛——\n"
        "「疼痛是血肉给你的回执，证明这具身体还归你。」\n"
        "\n"
        "你走到第三十步的时候，发现自己不疼。\n"
        "\n"
        "不是麻木。是你脚底那层茧——厚得像另一张皮。\n"
        "码头、工地、拆解场，这些年一步一步磨出来的。\n"
        "碎石硌上去，只剩一点钝钝的压力。\n"
        "\n"
        "那层茧是纯粹的血肉。一克金属也没有。\n"
        "可它让你在这条路上作了弊。"),
        [
            {"text": "咬牙走完。假装还在疼。", "check": ("坚忍", 10),
             "success": {"narration": "你把表情调整到疼痛应有的样子。牙关咬紧，额头冒汗——\n"
                         "但汗是真的。假装疼痛需要的意志力，比承受疼痛更大。\n"
                         "\n"
                         "走完全程。没有人怀疑。你通过了试炼。\n"
                         "但你自己知道：你通过的不是血肉试炼，你通过的是谎言试炼。",
                         "fx": {"skill:坚忍": 2, "heat": -1, "flag:temple_doubt": 1}},
             "failure": {"narration": "走到第四十步你笑了。不是苦笑，是真的觉得好笑——\n"
                         "你在一群真正感到疼痛的人中间假装疼痛。\n"
                         "旁边的人看见你笑，以为你在忍到极限。「好样的，」他说。\n"
                         "你笑得更厉害了。",
                         "fx": {"skill:坚忍": 1, "skill:共情": 1, "heat": 1}}},
            {"text": "停下来。当众翻开脚掌。", "check": ("共情", 12),
             "success": {"narration": "你蹲下来，脱了袜子，把脚底翻给身后的人看。\n"
                         "碎石路上安静了三十秒。三十秒够一段布道了。\n"
                         "\n"
                         "你没有布道。你只说了一句：「这不是装的。」\n"
                         "然后又补了半句：「这是走出来的。」\n"
                         "\n"
                         "长老走过来。他蹲下来，看了你的脚很久。\n"
                         "然后他脱了自己的鞋。\n"
                         "\n"
                         "他脚底那层比你的还厚。厚到你一眼就看得出，\n"
                         "他不是这两年才不疼的。\n"
                         "\n"
                         "碎石路上，两个赤脚的人对视。你们达成了一项无人见证的协议。",
                         "fx": {"skill:共情": 3, "flag:temple_doubt": 1,
                                "flag:temple_elder_secret": 1}},
             "failure": {"narration": "你翻开脚掌，等一句话。\n"
                         "\n"
                         "没有人接。有人低头看了看自己的脚，\n"
                         "然后把脚往袍子底下缩了一点。\n"
                         "\n"
                         "主持仪式的人很客气地请你回到队伍最后面，重走一遍。\n"
                         "「这次慢一点，」他说，「感受一下。」\n"
                         "\n"
                         "你重走了一遍。还是不疼。",
                         "fx": {"skill:共情": 1, "heat": 2, "flag:temple_doubt": 1}}},
            {"text": "继续走，但不假装。你的步态会告诉所有人你不疼了。",
             "effects": {"narration": "你走完了碎石路。步态平稳，表情平静。\n"
                         "旁边的人拧着脸一步一哆嗦。你像在走地毯。\n"
                         "没有人指出来。但所有人都看到了差别。\n"
                         "\n"
                         "走完之后，一个年轻信徒追上你：\n"
                         "「你……不疼吗？」\n"
                         "你想了想：「疼。但不是脚在疼。」\n"
                         "他没听懂。他会想很久。",
                         "fx": {"skill:坚忍": 1, "skill:共情": 2, "flag:temple_doubt": 1}}},
        ], factions=["purist"], subs=["圣殿派"], weight=8,
        voices={"坚忍": "【坚忍】这层茧不是长出来的，是被磨出来的。\n"
                        "三年码头，两年拆解场。它比任何一件义体都更是你自己挣的。"}),
    _ev("temple_vault", (
        "你被带进圣殿最深的房间。只有长老和三名「灯守」有钥匙。\n"
        "房间中央是一个玻璃柜。柜子里不是经文。\n"
        "\n"
        "是一只手。\n"
        "\n"
        "金属的。五指舒展，掌心朝上，放在一块天鹅绒上面。\n"
        "关节处的工艺精细到每一条指纹都有。\n"
        "手腕断面打磨得光滑如镜，像是被人故意保存的——不是残骸，是标本。\n"
        "\n"
        "长老站在柜子旁边：「建殿的时候从地基里挖出来的。\n"
        "比这座城所有的历史都老。我们验过——合金成分不属于任何已知制造商。」\n"
        "他看着你：「经文说，大地与星空之子以完整的血肉归天。\n"
        "可这只手在告诉我们：最早的天之子，是金属做的。」"),
        [
            {"text": "「这就是你们把经文锁在地下室的原因。」", "check": ("逻辑", 10),
             "success": {"narration": "「你是第四个猜到的。」长老从袖子里取出一份名单——\n"
                         "四个名字，跨越一百多年。「每一代灯守里，总有一个人到这个房间后问同一句话。」\n"
                         "\n"
                         "他指着名单第一行：「第一个是建殿者本人。他挖出这只手的那天晚上，\n"
                         "写下了全部经文。『归天』那个字，是他自己选的。」\n"
                         "他顿了一下：「不是因为看不见真相。是因为他决定，\n"
                         "这座城还没准备好听真话。」\n"
                         "\n"
                         "你看着那只金属手。它的掌心朝上，像在等一个握手。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1, "flag:temple_vault": 1}},
             "failure": {"narration": "长老摇了摇头：「不全是。」他没解释「全」的部分是什么。\n"
                         "你走出密室的时候，门在身后关上。你听到了三道锁的声音。",
                         "fx": {"skill:逻辑": 1, "flag:temple_vault": 1}}},
            {"text": "「那你们为什么还在传肉身神圣？」", "check": ("共情", 11),
             "success": {"narration": "长老站在那只手旁边，沉默了很久。\n"
                         "「因为这座城里，最先被改造的永远是最穷的人。」\n"
                         "他的声音忽然苍老了十岁。「工伤装液压肩，欠债换一颗便宜的肾。\n"
                         "改造从来不是选择。改造是账单。」\n"
                         "他摸了摸玻璃柜：「我们传肉身神圣——不是因为肉身真的神圣。\n"
                         "是因为如果我们不传，穷人连说『我不想换』的理由都没有了。」\n"
                         "\n"
                         "你想反驳。但你想起了码头工的液压肩，想起了那张没有「为什么」一栏的税率表。",
                         "fx": {"skill:共情": 2, "skill:逻辑": 1, "flag:temple_vault": 1}},
             "failure": {"narration": "长老看了你一眼。那种看法，你在灰港的接头人脸上也见过——\n"
                         "「你太年轻了，还在以为问题有答案。」\n"
                         "他没有回答你的问题。你走出密室，带着一个比进来时更重的沉默。",
                         "fx": {"skill:共情": 1, "flag:temple_vault": 1}}},
            {"text": "伸出自己的手，和玻璃柜里的手比一比。",
             "effects": {"narration": "你把自己的手掌贴在玻璃上。\n"
                         "玻璃另一边，那只金属手比你的大一号。五指的张开角度和人类完全一致。\n"
                         "你盯着自己的手——皮肤、血管、骨节——再看那只手——合金、关节、指纹。\n"
                         "\n"
                         "长老走到你旁边，也把手贴上去。\n"
                         "玻璃上三只手。一只血肉。一只金属。一只……\n"
                         "你低头看长老的手——右手小指缺了一截，断面被皮肤覆盖住了，\n"
                         "但形状不对。不是缺了一截。是换了一截。\n"
                         "\n"
                         "你和长老对视。谁都没有说话。\n"
                         "这间房里，三只手，没有一只是完全的。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1,
                                "flag:temple_vault": 1, "flag:temple_elder_secret": 1}}},
        ], factions=["purist"], subs=["圣殿派"], weight=6,
        req_seen={"temple_scripture": 1},
        voices={"逻辑": "【逻辑】这只手的合金配比不在任何工业数据库里。要么极老，要么极新——老到在这座城之前，或新到在这座城之后。"},
        echoes=[
            {"deed": "temple_vault", "min": 1,
             "text": "你走进一间博物馆，在「史前文物」展柜里看见了一截金属指骨。\n"
                     "标签写着「年代不明，材质不明，疑为仪式用品」。你知道那不是仪式用品。"},
            {"deed": "temple_elder_secret", "min": 1,
             "text": "你在人群中看见一个戴手套的老人。没人觉得异常——这座城的冬天确实冷。\n"
                     "但你知道有些手套不是为了保暖，是为了保密。"},
        ]),
    _ev("temple_schism", (
        "传教士的膝盖成了一道分水岭。\n"
        "圣殿派分成了两桌人。一桌说：「他是叛徒。」一桌说：「他是圣人。」\n"
        "没有第三桌——因为第三种意见需要承认膝盖和教义可以共存，\n"
        "而那张桌子坐下去就等于自己是第二个传教士。\n"
        "\n"
        "今晚两桌人要投票：传教士的名字，留在圣殿的名录上，还是擦掉。\n"
        "长老坐在两桌之间，投票前只说了一句话：\n"
        "「这个投票的结果，会比教义活得更久。」"),
        [
            {"text": "投「留」。理由：「他的膝盖是假的，路是真的。」", "check": ("共情", 10),
             "success": {"narration": "「路是真的」这四个字在两桌之间弹了三个来回。\n"
                         "最后四十二票留，三十八票擦。他的名字留在了名录上。\n"
                         "名字旁边加了一个小小的星号。星号没有注释。\n"
                         "一百年后，如果还有人翻这本名录，他们会对那个星号困惑。\n"
                         "困惑就对了。",
                         "fx": {"skill:共情": 2, "flag:temple_saved_name": 1}},
             "failure": {"narration": "你说完理由，铁锤派一桌人同时站了起来。\n"
                         "「路是真的？路是谁铺的？是你吗？你也有假膝盖？」\n"
                         "你被淹没在质问里。投票结果：四十一擦，三十九留。他的名字被抹去了。\n"
                         "长老收起名录的时候，你看见他的手在抖。",
                         "fx": {"skill:共情": 1, "heat": 1}}},
            {"text": "投「擦」。理由：「纯血如果有例外，就不再是纯血。」", "check": ("威慑", 10),
             "success": {"narration": "你的声音比预期的稳。「纯血如果有例外，就不再是纯血。\n"
                         "他知道这一点。他选择了膝盖，也选择了代价。」\n"
                         "四十一擦，三十九留。名字被一笔粗墨划去。\n"
                         "散会后，一个老信众在你身后站了很久。\n"
                         "最终他没有跟你说话。他把手里的白蜡烛放在门口，走了。",
                         "fx": {"skill:威慑": 2, "flag:temple_erased_name": 1}},
             "failure": {"narration": "「不再是纯血」这句话说出口的一瞬间，你听到自己的声音像别人的。\n"
                         "你在替教义说话，不是替自己说话。这个区别应该不重要。\n"
                         "但你在说完之后意识到它很重要。",
                         "fx": {"skill:威慑": 1, "skill:坚忍": 1}}},
            {"text": "站起来走到长老桌边：「在投票之前，先告诉所有人密室里有什么。」",
             "req": ("deed", "temple_vault", 1), "check": ("逻辑", 12),
             "success": {"narration": "长老的眼神复杂到你读不完。但他站了起来。\n"
                         "「好。」他只说了一个字。\n"
                         "\n"
                         "他带着全体信众走进地下室。打开三道锁。\n"
                         "玻璃柜里那只金属手，被六十个火把照亮。\n"
                         "长老的解说很短：「建殿时出土。比城老。合金成分未知。」\n"
                         "\n"
                         "他没有说它意味着什么。他不需要说。\n"
                         "六十个人站在一只金属手面前，同时理解了一件事：\n"
                         "他们守了两百年的教义，可能从第一个字就是错的。\n"
                         "\n"
                         "投票在沉默中进行。传教士的名字以六十票留，零票擦。\n"
                         "没有人投擦。不是因为原谅了膝盖。\n"
                         "是因为那只手比膝盖老了两百年，而他们刚刚才知道。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 2, "skill:威慑": 1,
                                "flag:temple_revealed": 1, "flag:temple_saved_name": 1}},
             "failure": {"narration": "长老没有动。「你没有这个权力。」他的声音像关门。\n"
                         "密室的钥匙只有三把。而你不是灯守。\n"
                         "投票继续。你弃权了。最终四十擦，三十九留，一弃。",
                         "fx": {"skill:逻辑": 1, "heat": 2}}},
        ], factions=["purist"], subs=["圣殿派"], weight=6,
        req_seen={"temple_knees": 1}, echoes=[
            {"deed": "temple_revealed", "min": 1,
             "text": "圣殿的布道词最近变了。不再说「血肉神圣」，开始说「血肉珍贵」。\n"
                     "一个字的区别。没人公开讨论为什么变了。"},
            {"deed": "temple_saved_name", "min": 1,
             "text": "圣殿名录最末页，一个带星号的名字。新来的信徒问星号是什么意思。没人回答。"},
            {"deed": "temple_erased_name", "min": 1,
             "text": "圣殿名录上有一道粗墨。墨迹底下透出笔画，但没人凑近看。\n"
                     "好奇心在圣殿是一种小罪。"},
        ]),
    # ------------------------------------------------ 铁锤派（纯血誓约·武力）
    _ev("hammer_forge", (
        "铁锤派的地下锻造间。三台工业冲床，一座焦炭炉，墙上挂满了撬棍和短锤。\n"
        "新来的人都要亲手锻一根自己的撬棍——「你的武器得认识你的手。」\n"
        "\n"
        "铁匠是个六十多岁的老头，双手全是烧伤疤。指纹烧没了，握力还在——\n"
        "他递给你一根生铁坯料的时候，你能感到那只手的力量和温度。\n"
        "温度不对。太均匀了。活人的手心应该比手背热，他的整只手是同一个温度。\n"
        "\n"
        "炉火很亮。你没有多看他的手。\n"
        "\n"
        "「打。打到你满意为止。撬棍直了才能上阵。」"),
        [
            {"text": "认真锻打。跟着铁匠的节奏，一锤一锤地来。", "check": ("坚忍", 9),
             "success": {"narration": "铁匠在你身后看了三十锤，没说话。第三十一锤的时候，\n"
                         "他把你的手腕往下压了两寸：「这里。力传到这里，铁才听话。」\n"
                         "你打了整整一个下午。撬棍出炉的时候还烫着，铁匠拿水淬了一下，\n"
                         "嘶的一声——那声音像在给这根铁定了性。\n"
                         "\n"
                         "「不错。」他只说了这两个字。但他说的时候活动了一下手指，\n"
                         "关节发出了金属不该发出的声音。或者说，骨头不该发出的声音。",
                         "fx": {"skill:坚忍": 2, "flag:hammer_forged": 1}},
             "failure": {"narration": "第十七锤打歪了。铁坯弯了一个角度，铁匠接过去，三锤正回来。\n"
                         "你看他那三锤——快得不像六十岁的人，准得不像烧伤过的手。\n"
                         "\n"
                         "「明天再来。」他把你的弯撬棍扔回炉子里。生铁重新变红，\n"
                         "像一个被收回去的承诺。",
                         "fx": {"skill:坚忍": 1, "skill:巧手": 1}}},
            {"text": "问铁匠：为什么不用模具直接铸？手工打有什么区别？", "check": ("逻辑", 10),
             "success": {"narration": "铁匠盯着你看了五秒钟。然后他笑了。\n"
                         "「小聪明。」他从墙上取下两根撬棍，扔在地上。\n"
                         "「左边的是模具铸的。右边的是手打的。你分得出来吗？」\n"
                         "你分不出来。\n"
                         "\n"
                         "「我也分不出来。」他的声音忽然很轻。\n"
                         "「但锤子分得出来。手打的撬棍，每一根都有锻打者的手劲走向。\n"
                         "用的时候顺手，就像……」他停了一下，「就像它是你身上长出来的。」\n"
                         "\n"
                         "你没有指出他话里的矛盾。铁锤派最恨「长在身上的机械」。\n"
                         "但一根认识你手劲的铁棍，和一只认识你神经的义手，区别在哪里？",
                         "fx": {"skill:逻辑": 2, "flag:hammer_forged": 1, "flag:hammer_doubt": 1}},
             "failure": {"narration": "「问这种话的人，一般打不好铁。」\n"
                         "铁匠没有生气，只是不再理你了。你拿着生铁坯料站了一会儿，然后开始打。\n"
                         "打得很糟。但你想了一整晚他没回答的问题。",
                         "fx": {"skill:逻辑": 1, "skill:坚忍": 1}}},
            {"text": "一边打铁一边留心铁匠的手——他握锤的方式不像肉手。",
             "effects": {"narration": "你一边打铁，一边用余光看他的手。\n"
                         "烧伤疤底下，食指和中指的弯曲弧度完全相同——肉手做不到这么对称。\n"
                         "无名指在握锤的时候有一个极细微的延迟，像是信号要多走半寸路。\n"
                         "他的手是修过的。也许不止修过。\n"
                         "\n"
                         "你没有说。你继续打铁。\n"
                         "铁锤派的铁匠用一双不完全是肉的手，锻造着砸向义体的撬棍。\n"
                         "这件事如果说出去，能拆掉半个铁锤派。\n"
                         "所以你不说。你把这个发现和撬棍一起收好。",
                         "fx": {"skill:街智": 2, "flag:hammer_forged": 1,
                                "flag:hammer_smith_secret": 1}}},
        ], factions=["purist"], subs=["铁锤派"], weight=10,
        voices={"机械亲和": "【机械亲和】他递坯料时手掌的温度是恒的。肉会随血流波动，热管不会。"},
        echoes=[
            {"deed": "hammer_forged", "min": 2,
             "text": "新一批撬棍码在墙上。你伸手摸了一根——手感不对。这批是模具铸的。\n"
                     "铁匠最近没来。"},
            {"deed": "hammer_smith_secret", "min": 1,
             "text": "铁匠今天戴了手套打铁。六十多年没戴过手套的人，忽然戴了。\n"
                     "没人问。你也没问。"},
            {"all": [{"deed": "hammer_smith_secret", "min": 1},
                     {"deed": "temple_elder_secret", "min": 1}],
             "text": "锻造间的墙上挂着一份圣殿派发的经文抄本，被油烟熏黄了。\n"
                     "你想起另一间屋子里另一只戴手套的手。\n"
                     "纯血誓约最虔诚的两条线，底下是同一种金属。"},
        ]),
    _ev("hammer_recruit", (
        "铁锤派的入会仪式很简单：过金属探测门。\n"
        "门是从海关淘汰下来的，灵敏度调到了最高——一颗纽扣大的金属都能让它尖叫。\n"
        "\n"
        "新兵排成一列。脱掉所有金属饰品，赤脚，只穿单衣。\n"
        "前面九个人都过了。安静。干净。\n"
        "\n"
        "第十个人走进去，门叫了。\n"
        "他愣住了。领队走过来，手持探测棒，从头扫到脚。棒在左胸停住了。\n"
        "\n"
        "「心脏起搏器。」他的声音很小。「七岁装的。先天性心律不齐。不装就活不到八岁。」\n"
        "他把单衣领口拉开——左胸有一道淡粉色的旧疤。\n"
        "「我来铁锤派，是因为我知道被迫装上一个不属于自己的东西是什么感觉。\n"
        "我不想让更多人经历这个。」"),
        [
            {"text": "替他说话：「七岁的孩子没有选择权。誓约惩罚的是选择，不是命运。」",
             "check": ("共情", 11),
             "success": {"narration": "你的声音在队列里很突兀。但你说完之后，有三个人点了头。\n"
                         "领队看了你很久。然后他关掉了金属探测门。\n"
                         "\n"
                         "「今天这扇门故障了。」他说。他走到新兵面前，拍了一下他的肩：\n"
                         "「别让我后悔。」\n"
                         "\n"
                         "新兵进了铁锤派。他的心脏在左胸跳着，每一下都带着七岁时的金属回声。\n"
                         "你不知道这算破例还是正义。\n"
                         "但你知道领队关门的时候，手在抖。",
                         "fx": {"skill:共情": 2, "flag:hammer_mercy": 1}},
             "failure": {"narration": "「命运？」领队转向你。「你知道多少人用这个借口？\n"
                         "工伤是命运。欠债是命运。被切了腿的流浪狗也是命运。\n"
                         "门开着的时候，什么都是命运。门关上了，才有誓约。」\n"
                         "\n"
                         "新兵被带走了。你不知道他后来去了哪里。\n"
                         "但你知道领队说的也是对的。这就是最坏的部分——他说的也是对的。",
                         "fx": {"skill:共情": 1, "skill:坚忍": 1}}},
            {"text": "沉默。规矩是规矩。金属探测门不问原因。",
             "effects": {"narration": "队列继续。新兵被两个老成员架出去了。\n"
                         "门口没有吵闹。他走的时候回头看了一眼——不是看领队，\n"
                         "是看那扇金属探测门。\n"
                         "\n"
                         "仪式结束后，你路过门口。门还开着，红色指示灯一闪一闪。\n"
                         "你从门中间走过去。门没有响。\n"
                         "你是干净的。这个事实今天没有让你高兴。",
                         "fx": {"skill:坚忍": 2}}},
            {"text": "提议：把起搏器取出来。如果他真心想加入，就证明给所有人看。",
             "check": ("威慑", 10),
             "success": {"narration": "新兵的脸白了一瞬——然后他点了头。「行。」\n"
                         "手术在铁锤派的简易医务室做的。没有麻醉——他自己要求的。\n"
                         "「我要记住这个痛。」\n"
                         "\n"
                         "起搏器取出来的时候，他的心跳乱了三秒钟。\n"
                         "三秒钟之后，他自己的心脏找回了节奏——二十年来第一次不靠金属跳动。\n"
                         "他走过金属探测门。安静。干净。\n"
                         "\n"
                         "领队把起搏器钉在了战利品架上。\n"
                         "你看着那个小小的金属盒子——它替一颗心脏跳了二十年。\n"
                         "现在它被钉在墙上，和砸来的义肢放在一起。\n"
                         "你分不出来哪些是「罪」，哪些是「药」。",
                         "fx": {"skill:威慑": 2, "flag:hammer_trophy_wall": 1}},
             "failure": {"narration": "你的提议还没说完，三个老成员同时转过头来看你。\n"
                         "其中一个的目光很复杂：「你知道取出起搏器可能要他的命吧？」\n"
                         "你知道。你的提议就是建立在这个可能性上的。\n"
                         "这让你和领队之间产生了一种微妙的共鸣——你也愿意为了纯血赌命。\n"
                         "但共鸣维持了三秒就散了。因为赌的不是你的命。",
                         "fx": {"skill:威慑": 1, "heat": 1}}},
        ], factions=["purist"], subs=["铁锤派"], weight=10, echoes=[
            {"deed": "hammer_mercy", "min": 1,
             "text": "铁锤派集会的时候，角落里有一个人的心跳声比别人大一点。\n"
                     "金属探测门从那天起就没再调回最高灵敏度。没人提过为什么。"},
            {"deed": "hammer_trophy_wall", "min": 1,
             "text": "战利品架上，所有义肢之间夹着一个小小的金属盒子。\n"
                     "新来的人问那是什么。老成员说：「那是一颗心。」新人以为是比喻。"},
        ]),
    _ev("hammer_dawn", (
        "天亮了。昨晚砸的那间黑诊所，领队让你回去「收尾」——\n"
        "确认设备全毁了，没留下能用的零件。\n"
        "\n"
        "你推开诊所半脱落的门。地上全是碎玻璃和砸烂的手术台残骸。\n"
        "消毒水和焦糊味混在一起。墙角有一排铁柜，柜门被撬开了，里面空的——\n"
        "但柜子底部有几道新鲜的拖痕。\n"
        "有人比你们先到了一步。柜子里的东西在你们来之前就被搬走了。\n"
        "\n"
        "手术台旁边的地上，有一只被踩碎的病历夹。\n"
        "夹子里的病历不在了。只剩封面上半截名字和一行字：\n"
        "「术后六小时翻身一次。」"),
        [
            {"text": "按命令收尾——把剩余的东西全砸了，拍照交差。", "check": ("威慑", 8),
             "success": {"narration": "你用了一个小时把剩下的东西全部砸碎。最后一件是一台心率监护仪，\n"
                         "屏幕裂了，但指示灯还在闪。你拔掉电源的时候，指示灯最后亮了一下——绿的。\n"
                         "正常心率的颜色。\n"
                         "\n"
                         "你拍了照，发给领队。领队回了一个字：「好。」\n"
                         "收工。任务完成。你的撬棍上沾着消毒水。",
                         "fx": {"skill:威慑": 2, "flag:hammer_run": 1}},
             "failure": {"narration": "你砸最后一个柜子的时候，柜子后面掉出来一张照片。\n"
                         "医生和一个小女孩的合影。女孩的右腿是假的，但她在笑。\n"
                         "你把照片塞回碎玻璃里面，继续砸。\n"
                         "照片碎了。笑没碎。笑留在你脑子里了。",
                         "fx": {"skill:威慑": 1, "skill:共情": 1, "flag:hammer_run": 1}}},
            {"text": "去追那些拖痕。有人在你们动手之前就知道消息了。", "check": ("街智", 11),
             "success": {"narration": "拖痕通向后巷，后巷通向一条你不认识的暗道。暗道的尽头是码头。\n"
                         "码头上什么都没有了。但地面是湿的——海水的湿法和雨水不同，\n"
                         "海水干了以后会留盐。\n"
                         "\n"
                         "有人从海上来，把诊所里最重要的东西在你们到之前运走了。\n"
                         "铁锤派砸的不是一间完整的诊所。铁锤派砸的是一间被掏空的壳。\n"
                         "\n"
                         "你开始想：是谁给灰港通的风？\n"
                         "名单上有四十个人。这个问题一旦问出口，就会有四十种答法。\n"
                         "你把它咽了回去。但拖痕不会说谎。",
                         "fx": {"skill:街智": 2, "flag:hammer_harbor_link": 1,
                                "flag:hammer_run": 1},
                         "extra": [{"deed": "informer", "min": 1,
                                    "text": "\n码头上的盐渍里有一个鞋印。\n"
                                            "尺码和你一样。"}]},
             "failure": {"narration": "拖痕在后巷被雨水冲断了。你跟到路口，三个方向，\n"
                         "地上全是湿的，分不出哪个是拖痕哪个是积水。\n"
                         "你站在路口淋了十分钟的雨。回去复命的时候，领队问你怎么湿了。\n"
                         "你说下雨了。他信了。",
                         "fx": {"skill:街智": 1, "flag:hammer_run": 1}}},
            {"text": "捡起那半截病历封面。「术后六小时翻身一次」——这是写给活人的。",
             "effects": {"narration": "你蹲在碎玻璃里，把那半截封面翻过来。背面有铅笔写的几个字，\n"
                         "字迹很轻，像写的人怕被别人看见：\n"
                         "「疼的时候想想海。海是不疼的。」\n"
                         "\n"
                         "你把封面折好，放进口袋。领队来收尾的时候你站在门口，\n"
                         "口袋里多了一张纸。\n"
                         "铁锤派的任务报告不需要提到这张纸。你也没提。\n"
                         "但那行字你记住了。海是不疼的。\n"
                         "——你不确定这是写给病人的，还是写给医生自己的。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1, "flag:hammer_run": 1}}},
        ], factions=["purist"], subs=["铁锤派"], weight=8,
        req_seen={"purist_hammer_raid": 1}, echoes=[
            {"deed": "harbor_run", "min": 2,
             "text": "拖痕的方向你认识。箱子是朝码头方向拖的。\n"
                     "那些空柜子的摆法、间距、甚至柜门上的锁型号——你在另一个地方见过一模一样的。"},
            {"deed": "hammer_harbor_link", "min": 1,
             "text": "你路过一个码头。地上的盐渍已经被雨洗了好几遍了，\n"
                     "但你还是能看出拖痕的方向——朝海的那边。\n"
                     "有些东西被搬走之后，痕迹比东西活得久。"},
            {"all": [{"deed": "hammer_harbor_link", "min": 1},
                     {"deed": "harbor_burned", "min": 1}],
             "text": "铁锤派的战报里有一个地址。地址旁边有人批注：\n"
                     "「到场时已空。设备全无。可能提前转移。」\n"
                     "铁锤砸了一面空墙。墙后面的人已经在海上了。"},
        ]),
    _ev("hammer_trophies", (
        "铁锤派的地下室有一面墙。\n"
        "墙上钉满了从黑诊所砸来的义体零件。液压臂、光学眼球、钛合金脊椎节段、\n"
        "成排的人工关节——像猎人的鹿角墙，但钉着的不是鹿角。\n"
        "\n"
        "领队每周带新人来参观：「每一件都是证据。证明这座城里有多少人\n"
        "被骗着放弃了自己的身体。」\n"
        "\n"
        "今天你被安排整理这面墙。给每件战利品编号、登记来源诊所。\n"
        "整理到第四十七件——一只右手——的时候，你看见了手腕内侧的编码。\n"
        "编码后四位是灰港的区域前缀。你认识这个编号体系。"),
        [
            {"text": "照实登记编码。灰港的前缀会引来一场追查。", "check": ("逻辑", 10),
             "success": {"narration": "你把编码原样写进登记表。三天后，领队拿着表来找你。\n"
                         "「这个前缀……你知道对应哪里吗？」\n"
                         "你说不知道。他信了——或者他假装信了。\n"
                         "\n"
                         "一周后铁锤派制定了新的夜袭路线，方向是码头。\n"
                         "你不知道那条路线的尽头是什么。但你知道那只右手的编码，\n"
                         "像一根引线，正在燃烧。",
                         "fx": {"skill:逻辑": 2, "heat": 1, "flag:hammer_harbor_traced": 1}},
             "failure": {"narration": "你登记完拿给领队过目。他翻到第四十七行，手指停了两秒。\n"
                         "「这个编码是你查过的？」你说是。\n"
                         "「以后别查。编码是诊所的事。我们只管砸。」\n"
                         "他把登记表收走了。你不知道他是不认识那个前缀，还是太认识了。",
                         "fx": {"skill:逻辑": 1}}},
            {"text": "登记的时候把后四位改掉。灰港的事不该从这面墙上暴露。",
             "check": ("街智", 10),
             "success": {"narration": "你把后四位改成了一个不存在的前缀。\n"
                         "登记表上那行编码变成了无意义的噪声。灰港的线索消失在你的笔迹里。\n"
                         "\n"
                         "整理完墙面，你洗了手。手上的铁锈洗得掉，\n"
                         "但你改掉四个数字的事实洗不掉。\n"
                         "你在铁锤派的地下室，替灰港保了一个秘密。\n"
                         "两个本不该有交集的世界，在你的登记表上偷偷握了一次手。",
                         "fx": {"skill:街智": 2, "flag:hammer_harbor_covered": 1}},
             "failure": {"narration": "改到一半，旁边有人走过来。你来不及写完，字迹留了一个犹豫的弧度。\n"
                         "他没看你在写什么。但那个弧度留在纸上了，像一个没收回的手势。\n"
                         "你只能希望以后查表的人注意力不在第四十七行。",
                         "fx": {"skill:街智": 1, "heat": 1}}},
            {"text": "把那只右手从墙上取下来，翻过来，看看掌心。",
             "effects": {"narration": "你把那只右手从钉子上取下来。比想象的重——液压关节加上合金骨架。\n"
                         "翻过来。\n"
                         "\n"
                         "掌心的硅胶皮层磨薄了一块。透过那块薄处，你能看见底下的线路板。\n"
                         "线路板上焊了一行极小的字——不是出厂编码，是手写的，\n"
                         "有人拿焊笔一个一个字烫上去的：\n"
                         "\n"
                         "「给我女儿。她需要一只能翻书页的手。」\n"
                         "\n"
                         "你把右手放回墙上。钉好。\n"
                         "那行字朝墙，没有人再会看见。\n"
                         "但你看见了。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1, "flag:hammer_doubt": 1}}},
        ], factions=["purist"], subs=["铁锤派"], weight=8,
        req_seen={"purist_hammer_raid": 1},
        variants=[
            {"deed": "harbor_run", "min": 1,
             "text": "铁锤派的地下室有一面墙。\n"
                     "墙上钉满了从黑诊所砸来的义体零件。液压臂、光学眼球、钛合金脊椎节段、\n"
                     "成排的人工关节——像猎人的鹿角墙，但钉着的不是鹿角。\n"
                     "\n"
                     "领队每周带新人来参观：「每一件都是证据。证明这座城里有多少人\n"
                     "被骗着放弃了自己的身体。」\n"
                     "\n"
                     "今天你被安排整理这面墙。给每件战利品编号、登记来源诊所。\n"
                     "整理到第四十七件——一只右手——的时候，你看见了手腕内侧的编码。\n"
                     "编码后四位是灰港的区域前缀。\n"
                     "你不只是认识这个编号体系。你的手搬过带着同样前缀的箱子。"},
        ],
        echoes=[
            {"deed": "hammer_harbor_traced", "min": 1,
             "text": "铁锤派的夜袭路线最近改了。新路线经过码头。\n"
                     "你不知道那只右手的编码最终把他们带到了哪里。"},
            {"deed": "hammer_harbor_covered", "min": 1,
             "text": "战利品墙上第四十七号的登记卡被人翻过。卡还在。\n"
                     "你改过的数字没人追问。也许是真的没人在意。\n"
                     "也许是有人在意，但选了同一种沉默。"},
            {"deed": "hammer_doubt", "min": 2,
             "text": "战利品架旁边新加了一块牌子：「这些都是罪证。」\n"
                     "你盯着那块牌子，想起掌心那行字。证据和遗书之间，隔着谁的定义。"},
        ]),
    _ev("hammer_rust", (
        "领队找你单独谈话。地点不是铁锤派的据点——是城外一间废弃的公交站。\n"
        "他到的时候，你注意到他的右手一直揣在口袋里。\n"
        "\n"
        "「我有个事要你帮忙。」他的声音比平时轻。\n"
        "他把右手从口袋里掏出来。\n"
        "\n"
        "腕关节处，皮肤颜色不对。青紫色，像一截坏掉的水管。\n"
        "他攥了一下拳——攥不紧。手指到一半就停了，像卡住的齿轮。\n"
        "\n"
        "「三十年。」他说。「三十年的撬棍，震碎了腕骨。粉碎性的。\n"
        "骨科说只有两条路：不用这只手，或者……」\n"
        "他没说完。但你们都知道「或者」后面是什么。\n"
        "\n"
        "「我需要你帮我找一个人。」他看着自己那只攥不上的拳头。\n"
        "「一个能悄悄做的人。别让任何人知道。」"),
        [
            {"text": "「我帮你找。但你要想清楚——你的人砸了多少间诊所？"
                     "你要去的是同一种地方。」", "check": ("共情", 11),
             "success": {"narration": "领队沉默了很长时间。废弃公交站的铁皮顶在风里哐当响。\n"
                         "「我想过了。」他的声音像是从很深的井里传上来的。\n"
                         "「想了半年。每天早上握撬棍的时候都在想。\n"
                         "手越来越握不住，想得越来越清楚。」\n"
                         "\n"
                         "他看着自己的手。\n"
                         "「我砸了十二间诊所。三十七台手术台。上百副义肢。\n"
                         "现在我需要走进第十三间。」\n"
                         "\n"
                         "你帮他找了一个退休的外科医生。手术在冬天做的，恢复期他请了病假。\n"
                         "春天回来的时候，他的右手握力恢复了。\n"
                         "没有人注意到他的手腕处，袖口底下，多了一道新疤。\n"
                         "也没有人注意到，他此后挥撬棍的时候，再也不用右手了。",
                         "fx": {"skill:共情": 2, "skill:街智": 1,
                                "flag:hammer_wrist": 1, "flag:hammer_leader_secret": 1}},
             "failure": {"narration": "「想清楚？」领队的语气忽然硬了。\n"
                         "「你以为我来找你是来忏悔的？我来找你是因为你嘴紧。」\n"
                         "他把手收回口袋。「算了。我自己想办法。」\n"
                         "他走了。公交站的铁皮又响了一下。\n"
                         "你站在原地想：他的「自己想办法」是找别人，还是不治了？\n"
                         "你没有追上去问。有些门推不开第二次。",
                         "fx": {"skill:共情": 1, "skill:坚忍": 1, "flag:hammer_wrist": 1}}},
            {"text": "「不行。你是领队。你的手如果换了，铁锤派就完了。」",
             "check": ("威慑", 10),
             "success": {"narration": "你的声音比你预期的坚定。\n"
                         "领队看了你十秒——和他拒绝新兵入会时一样的十秒。\n"
                         "然后他把手收回去了。\n"
                         "\n"
                         "「你说得对。」他的声音很平。\n"
                         "「铁锤派的领队不能有假手。就像圣殿派的经文不能有错字。」\n"
                         "\n"
                         "他走了。你后来听说，他把左手练成了主手。\n"
                         "右手吊在身侧，像一面收起来的旗。\n"
                         "集会的时候他只用左手举撬棍。新来的人以为他天生左撇子。\n"
                         "只有你知道那只垂着的右手里，住着整个铁锤派的信仰。",
                         "fx": {"skill:威慑": 2, "flag:hammer_wrist": 1,
                                "flag:hammer_endured": 1}},
             "failure": {"narration": "「完？」他笑了一声。那声笑你从没听过——不是领队的笑，\n"
                         "是一个六十岁、手腕碎掉的普通人的笑。\n"
                         "「它早就完了。从我的手碎掉那天开始。\n"
                         "一个领队连撬棍都握不住，他领着你们砸什么？砸空气？」\n"
                         "\n"
                         "你说不出话。他把袖子卷起来给你看——腕骨处的皮肤底下，\n"
                         "有一块不规则的隆起。碎骨没有愈合，长成了一个结。\n"
                         "像一个拳头打在骨头上、被骨头记住了的形状。",
                         "fx": {"skill:威慑": 1, "skill:共情": 1, "flag:hammer_wrist": 1}}},
            {"text": "「灰港。我知道一个地方。」",
             "req": ("any", [("deed", "harbor_run", 1), ("deed", "hammer_harbor_link", 1)]),
             "check": ("街智", 12),
             "success": {"narration": "你说出「灰港」两个字的时候，领队的脸变了。\n"
                         "不是惊讶。是一种很古老的表情——像一个人终于走到了一扇他知道存在、\n"
                         "假装不知道的门前面。\n"
                         "\n"
                         "「灰港。」他重复了一遍。「我砸过的那些诊所里，有几间是灰港的？」\n"
                         "你不知道。但你知道你说出这两个字的一瞬间，\n"
                         "铁锤和码头之间三十年的战争忽然变得很小。\n"
                         "小到只剩一只攥不上的拳头。\n"
                         "\n"
                         "三个月后你在灰港的候诊名单上看到了一个假名。\n"
                         "笔迹太用力了。那是一只碎掉的手写的字。",
                         "fx": {"skill:街智": 2, "skill:共情": 1,
                                "flag:hammer_wrist": 1, "flag:hammer_leader_secret": 1,
                                "flag:hammer_harbor_final": 1}},
             "failure": {"narration": "「灰港？」领队的表情冷了。\n"
                         "「你让我去我砸过的地方求人？」\n"
                         "他转身走了。脚步比来的时候快。\n"
                         "你想追上去说「你砸过的和能救你的是同一种地方」，\n"
                         "但他不需要你说。他知道。所有人都知道。知道不等于接受。",
                         "fx": {"skill:街智": 1, "heat": 1, "flag:hammer_wrist": 1}}},
        ], factions=["purist"], subs=["铁锤派"], weight=6,
        req_seen={"hammer_forge": 1},
        voices={"机械亲和": "【机械亲和】他的腕骨碎裂模式是典型的反复冲击伤。"
                            "三十年的撬棍震动，频率恰好和腕骨的共振频率吻合。\n"
                            "锤子选了最有效的方式拆掉自己的主人。"},
        echoes=[
            {"deed": "hammer_leader_secret", "min": 1,
             "text": "领队最近用左手分配任务了。大家以为他在练左手。\n"
                     "你看到他右手袖口底下，那道新疤已经变成了白色的细线。"},
            {"deed": "hammer_endured", "min": 1,
             "text": "领队的右手彻底不动了。他把它绑在腰带上固定住，说是旧伤。\n"
                     "他用左手举起撬棍的时候，所有人都在欢呼。\n"
                     "没人注意到他绑手的带子是医用绷带。"},
            {"deed": "hammer_harbor_final", "min": 1,
             "text": "灰港的候诊室墙上有一行新的涂鸦——不是灰港的人写的。\n"
                     "字迹太用力，太整齐，像在写宣言而不是留言。写的是：「欠条。」两个字。"},
        ]),
    # ------------------------------------------------ 纯血誓约事件
    _ev("purist_confession", (
        "誓约屋的忏悔夜。今晚轮到你的挚友。他卷起裤腿，胫骨处一道细缝，泛着不属于骨头的光。「三年前矿难，不装就截肢。」他看着你，「你会举报我吗？」"),
        [
            {"text": "「我什么也没看见。」", "check": ("坚忍", 9),
             "success": {"narration": "你替他把裤腿放下来。誓约有誓约的道理，朋友有朋友的道理，今晚你选了后者。这个秘密从此有两个人扛。", "fx": {"skill:共情": 2, "flag:secret_friend": 1}},
             "failure": {"narration": "你守住了嘴，守不住脸色。之后每次集会，长老的目光都会在你们两人之间画线。", "fx": {"heat": 1, "skill:共情": 1}}},
            {"text": "劝他自首，你陪他去。", "check": ("共情", 11),
             "success": {"narration": "他在誓约屋当众卷起裤腿。出乎所有人意料，圣殿派长老宣布：「为活命所迫者，罪不在身。」誓约当晚新添了「矿难豁免」条款。你们改写了一小条历史。", "fx": {"skill:共情": 2, "skill:威慑": 1, "flag:reformer": 1}},
             "failure": {"narration": "长老们吵到天亮，最后他被「暂缓除名」——留下，但永远低人一等。他没怪你，这最难受。", "fx": {"skill:共情": 1, "skill:坚忍": 1}}},
            {"text": "按誓约举报。",
             "effects": {"narration": "铁锤派连夜取走了那截胫骨植入物，没打麻药。誓约表彰了你。挚友被逐出时回头看了你一眼——那眼神你会带进下一世。", "fx": {"skill:威慑": 1, "flag:betrayer": 1, "heat": -1}}},
        ], factions=["purist"], variants=[
            # 「矿难豁免」已经写进誓约了 —— 那就不能再新添一次。
            # 只换选项、不换正文：这一幕还是这一幕，回响照给。
            # （2026-08-08 试玩反馈：第一世改写了誓约，第二世没有一个人引用它，
            #  而成功分支还会再「新添」一遍同一条。）
            {"deed": "reformer", "min": 1,
             "options": [
                 {"text": "「我什么也没看见。」", "check": ("坚忍", 9),
                  "success": {"narration": "你替他把裤腿放下来。誓约有誓约的道理，朋友有朋友的道理，今晚你选了后者。这个秘密从此有两个人扛。",
                              "fx": {"skill:共情": 2, "flag:secret_friend": 1}},
                  "failure": {"narration": "你守住了嘴，守不住脸色。之后每次集会，长老的目光都会在你们两人之间画线。",
                              "fx": {"heat": 1, "skill:共情": 1}}},
                 {"text": "「翻到『矿难豁免』那一条。念出来。」", "check": ("逻辑", 10),
                  "success": {"narration": "有人把誓约抱了过来。那一条在第七页，墨色比周围新。\n"
                              "「为活命所迫者，罪不在身。」念的人念完自己愣了一下——\n"
                              "他不知道这条是什么时候添的。\n"
                              "\n"
                              "长老说：「那就照条款办。」四个字，散会。\n"
                              "\n"
                              "你朋友那晚没有被除名。也没有人改写历史——\n"
                              "历史早就被改写过了，只是没有人记得是谁改的。",
                              "fx": {"skill:逻辑": 2, "skill:共情": 1,
                                     "flag:clause_used": 1}},
                  "failure": {"narration": "那一条在第七页。长老翻到了，读了两遍，然后说：\n"
                              "「这条写的是矿难。他这是三年前的旧伤，不是当场。」\n"
                              "\n"
                              "他们吵到天亮。最后仍旧是「暂缓除名」。\n"
                              "\n"
                              "一条已经写进去的条款，被读成了不适用。\n"
                              "你忽然明白添一条进去是最容易的那一步。",
                              "fx": {"skill:逻辑": 1, "skill:坚忍": 1,
                                     "flag:clause_used": 1}}},
                 {"text": "按誓约举报。",
                  "effects": {"narration": "铁锤派连夜取走了那截胫骨植入物，没打麻药。誓约表彰了你。挚友被逐出时回头看了你一眼——那眼神你会带进下一世。",
                              "fx": {"skill:威慑": 1, "flag:betrayer": 1, "heat": -1}}},
             ]},
        ], echoes=[
            {"deed": "betrayer", "min": 1,
             "text": "忏悔夜开始前，你在人群里撞上一双眼睛。素不相识，但那双眼睛莫名其妙使你心里发紧。你率先移开了目光。"},
            {"deed": "front_scar", "min": 1,
             "text": "忏悔夜。有人哭着说自己的挚友有假腿。\n"
                     "你想起那道灼痕，和灼痕旁边那些新磨出来的茧。\n"
                     "旧疤是沉默的。茧不是——每一个茧都在说：我选了。"},
            {"ach": "clean_blood",
             "text": "散会后，长老单独留你添灯油。「有的话只能讲给干净人听，」他声音很低，「我们究竟为何立誓，又在坚守什么？在其他人眼里也许我们蠢得可笑。但是总要有人证明人类的尊严。以软弱、有限、朝生暮死的血肉之躯，丈量人类意志能抵达的极限。」"},
        ]),
    _ev("purist_hammer_raid", (
        "铁锤派召集夜袭：城郊一间黑诊所，「拆了它」。\n"
        "领队把撬棍塞进你手里。撬棍很沉，比誓词沉。"),
        [
            {"text": "去，但只拆设备，拦着别伤人。", "check": ("威慑", 10),
             "success": {"narration": "你第一个破门，也第一个挡在医生前面。「砸烂设备就够了。」那晚铁锤派的战报里第一次没有伤亡数字。", "fx": {"skill:威慑": 2, "skill:共情": 1}},
             "failure": {"narration": "你拦住了两个，没拦住第三个，医生进了医院。讽刺的是，救他的是义体手术。你开始睡不好。", "fx": {"skill:坚忍": 1, "hp": -1}}},
            {"text": "提前给诊所报信。", "check": (("共情", "街智"), 11),
             "success": {"narration": "你用公用电话亭打了三十秒。夜袭扑了个空，只砸了四面白墙。领队疑心有内鬼，但名单上有四十个人。你学会了在誓词和良心之间走钢丝。", "fx": {"skill:街智": 2, "heat": 2, "flag:secret_friend": 1, "flag:informer": 1}},
             "failure": {"narration": "电话被公社的线路员听了个正着。你没被抓住实据，但「那晚谁碰过电话亭」成了悬在你头上的问题。", "fx": {"heat": 3}}},
            {"text": "拒绝参加。「誓约让我守护血肉，没让我砸碎骨头。」",
             "effects": {"narration": "领队盯了你十秒，把撬棍收了回去：「圣殿派的软骨头。」从此铁锤派叫你「读经的」。也好，恶名也是一种边界。", "fx": {"skill:坚忍": 2}}},
        ], factions=["purist"],
        variants=[
            # 只换选项，不换正文 —— 你本人就是圣殿派的时候，
            # 「圣殿派的软骨头」这句骂不出效果，摩擦得换一种形状。
            {"sub": "圣殿派", "options": [
                {"text": "去，但只拆设备，拦着别伤人。", "check": ("威慑", 10),
                 "success": {"narration": "你第一个破门，也第一个挡在医生前面。「砸烂设备就够了。」那晚铁锤派的战报里第一次没有伤亡数字。", "fx": {"skill:威慑": 2, "skill:共情": 1}},
                 "failure": {"narration": "你拦住了两个，没拦住第三个，医生进了医院。讽刺的是，救他的是义体手术。你开始睡不好。", "fx": {"skill:坚忍": 1, "hp": -1}}},
                {"text": "提前给诊所报信。", "check": (("共情", "街智"), 11),
                 "success": {"narration": "你用公用电话亭打了三十秒。夜袭扑了个空，只砸了四面白墙。领队疑心有内鬼，但名单上有四十个人。你学会了在誓词和良心之间走钢丝。", "fx": {"skill:街智": 2, "heat": 2, "flag:secret_friend": 1, "flag:informer": 1}},
                 "failure": {"narration": "电话被公社的线路员听了个正着。你没被抓住实据，但「那晚谁碰过电话亭」成了悬在你头上的问题。", "fx": {"heat": 3}}},
                {"text": "拒绝参加。「誓约让我守护血肉，没让我砸碎骨头。」",
                 "effects": {"narration": "领队盯了你十秒，把撬棍收了回去。\n"
                             "他没有骂你——骂一个圣殿派「软骨头」是骂不动的，\n"
                             "你们本来就是靠念经站着的那一半。\n"
                             "\n"
                             "「我知道你们那边怎么念。」他说。「念完了，门还是得有人踹。」\n"
                             "\n"
                             "他把撬棍递给了下一个人。从那以后集合的名单上还有你的名字，\n"
                             "但名字后面那一栏——写着「留守」的那一栏——再没空过。",
                             "fx": {"skill:坚忍": 2, "skill:共情": 1}}},
            ]},
        ], echoes=[
            {"deed": "hammer_run", "min": 2,
             "text": "集合的时候，你自动站到了队列的老位置。\n"
                     "撬棍握在手里的感觉太熟悉了。\n"
                     "熟悉到你开始分不清这是肌肉记忆还是别的什么记忆。"},
            {"deed": "hammer_run", "min": 3,
             "text": "那间诊所的门上有一道旧的撬痕。\n"
                     "你不记得那道痕是不是你留的。但你的手掌在撬棍上找到了同一个握位。"},
            {"deed": "informer", "min": 1,
             "text": "领队清点人手时忽然压低声音：「三十年前那次扑空，也是这样的雨夜。内鬼一直没抓到。」他的目光扫过每个人——在你身上停得不长，也不短。"},
        ]),
    _ev("purist_harvest", (
        "誓约公社的秋收。真正的麦子，真正的镰刀，真正的腰疼。\n"
        "收到一半，播种机坏了——公社里唯一一台机器，明天不播完，冬麦就误了节气。\n"
        "「谁懂这铁疙瘩？」老农环顾四周。没人说话。"),
        [
            {"text": "上手修。", "check": ("巧手", 10),
             "success": {"narration": "链条、张紧轮、一段用镰刀柄削出来的销子。机器重新突突作响时，老农拍着你的背：「手艺不分血肉钢铁，是吧？」这话在誓约屋里说出来，接近异端，也接近真理。", "fx": {"skill:巧手": 2, "skill:机械亲和": 1}},
             "failure": {"narration": "你拆得开，装不回。最后全公社连夜手工点播，腰疼乘以四十。但没人怪你——「至少你试了」。", "fx": {"skill:坚忍": 1, "skill:巧手": 1}}},
            {"text": "问为什么不直接买食物，而要这样形式主义地耕作。", "check": ("威慑", 9),
             "success": {"narration": "「因为没有一颗麦粒是机械麦粒，人需要亲眼见到自己活着是怎样消耗其他生命的。」老农仍然保持着质朴的微笑，但你知道他受过教育的现代人本性正从角色下泄露出来，「我知道你在想什么，石油也一样，石油是古生物的尸体化成的。活着，就要互相吞噬。我们有责任不把自己摘干净。」", "fx": {"skill:威慑": 1, "skill:坚忍": 2}},
             "failure": {"narration": "你被血气方刚的年轻农民按在地上打了。", "fx": {"skill:坚忍": 1}}},
            {"text": "翻出说明书，逐页排查。", "check": ("逻辑", 10),
             "success": {"narration": "说明书是上个世纪的，你读懂了它的逻辑：这机器的设计者预设使用者一无所知。第四章第三节，堵塞的排种管。你按图索骥，机器复活。", "fx": {"skill:逻辑": 2}},
             "failure": {"narration": "说明书缺了最关键的一页——被前人拿去卷烟了。你对着油污的目录页干瞪眼到后半夜。", "fx": {"skill:逻辑": 1}}},
        ], factions=["purist"], voices={"巧手": "【巧手】这台播种机在哀求。你听得见每一颗螺丝的音高。"}),
    # ------------------------------------------------ 心照不宣事件
    _ev("discreet_gala", (
        "面具沙龙的年度晚宴。规则只有一条：看破不说破。\n"
        "觥筹交错间，一位政要的义眼开始故障闪烁，红光一明一灭——满厅的人假装没看见，\n"
        "但摄影记者已经举起了相机。"),
        [
            {"text": "「巧合」地打翻香槟塔挡住镜头。", "check": ("街智", 10),
             "success": {"narration": "香槟如瀑，快门声淹没在尖叫和大笑里。政要趁乱退场检修。次日头条是《香槟塔倒塌事故》，你的名字不在任何照片里——这正是最高的报酬。", "fx": {"skill:街智": 2, "flag:favor_elite": 1}},
             "failure": {"narration": "香槟塔倒向了错误的方向，浇了主办人一身。政要的红眼还是上了小报边栏。你被请出了下一季的宾客名单。", "fx": {"heat": 1, "skill:街智": 1}}},
            {"text": "隔空修复：接入他的义眼固件。", "req": ("aug", ">=", 25), "check": ("电子直觉", 11),
             "success": {"narration": "你借着举杯的姿势建立近场连接，三秒内回滚了故障固件。红光熄灭。政要朝你几不可察地颔首——在这个厅里，这等于一份终身人情。", "fx": {"skill:电子直觉": 2, "flag:favor_elite": 1}},
             "failure": {"narration": "固件回滚失败，义眼干脆黑了屏。政要单眼摸出会场，你的近场信号在他的日志里留了名。麻烦。", "fx": {"heat": 2, "skill:电子直觉": 1}}},
            {"text": "什么也不做。规则就是规则。",
             "effects": {"narration": "红光闪了一整晚，无人言语。散场时政要独自走向车库，背影像一台漏电的旧灯塔。你遵守了规则，规则没有感谢你。", "fx": {"skill:坚忍": 1}}},
        ], factions=["discreet"], echoes=[
            {"deed": "mask_depth", "min": 2,
             "text": "宴会厅里你自动开始数接缝。三十七个人，你能看出十二个人的修饰瑕疵。\n"
                     "缝隙师那句话在你脑后响：完美是假的佐证。\n"
                     "于是你开始怀疑那些你看不出瑕疵的人。"},
            {"deed": "mask_null_revealed", "min": 1,
             "text": "你在人群中看到一个人。没有任何可疑的特征——\n"
                     "步态自然，眨眼频率正常，肤色均匀。\n"
                     "以前你会跳过这样的人。现在你停了下来。"},
        ]),
    _ev("discreet_clinic", (
        "灰港黑诊所来了个大活：一位圣殿派长老，肝衰竭晚期，秘密求一枚人工肝。\n"
        "「白天他布道说义体是渎神，」诊所主刀冷笑，「晚上他的信徒抬他走后门。」\n"
        "主刀缺一个助手。"),
        [
            {"text": "上台帮忙。病人就是病人。", "check": ("巧手", 11),
             "success": {"narration": "六小时。人工肝归位，缝合线走得比教义还整齐。长老醒来第一句话：「这事……」「心照不宣。」你替他说完。三个月后，圣殿派的布道词悄悄软化了半度。", "fx": {"skill:巧手": 2, "skill:共情": 1, "flag:favor_elite": 1}},
             "failure": {"narration": "你的手在第四小时抖了一次。主刀接住了失误，长老活了下来，但你被永远踢出了手术室名单。「灰港不收会抖的手。」", "fx": {"skill:巧手": 1, "skill:坚忍": 1}}},
            {"text": "先谈价：要钱，还要他一句公开的软话。", "check": ("街智", 11),
             "success": {"narration": "「手术费翻倍，外加下个月的布道里，加一句『病中所迫，情有可原』。」长老在麻醉前咬牙答应了。一句话改不了世界，但能改一点点。", "fx": {"skill:街智": 2, "flag:reformer": 1}},
             "failure": {"narration": "长老宁死不松口，掉头去了别家诊所。主刀骂你把生意谈成了谈判。", "fx": {"skill:街智": 1}}},
            {"text": "匿名把消息捅给铁锤派。看这位长老怎么收场。",
             "effects": {"narration": "你没去看结果。灰港的规矩是保密，你破了规矩；誓约的规矩是纯血，长老破了规矩。这座城市靠被打破的规矩运转，而你今晚给它上了一次油。夜里不太睡得着。", "fx": {"skill:街智": 1, "heat": 2, "flag:betrayer": 1}}},
        ], factions=["discreet"]),
    _ev("discreet_scan_gate", (
        "地铁站新装了「原生度抽检门」。今天，你被随机抽中了。\n"
        "队伍后面，两个熟识的沙龙成员看着你——你过不去，暴露的就不止你一个。"),
        [
            {"text": "启动全套伪装协议。", "check": ("电子直觉", 10),
             "success": {"narration": "皮下电路休眠、体温补偿、步态回归出厂设置。门绿了。你走出十米才允许自己出汗——用真正的汗腺。", "fx": {"skill:电子直觉": 2, "skill:坚忍": 1}},
             "failure": {"narration": "门黄了——「设备故障，请重测」。第二次你赌上全部余量压了过去，但检测员盯着你的背影记了很久。", "fx": {"heat": 2, "skill:电子直觉": 1}}},
            {"text": "假装晕倒，制造混乱。", "check": ("街智", 9),
             "success": {"narration": "你倒得恰到好处，人群围拢，队伍重排。救护员扶你出站时，两个沙龙成员已从旁门离开。「演得真像。」事后他们敬你一杯。", "fx": {"skill:街智": 2}},
             "failure": {"narration": "你倒下时被一位热心的义体医生当场「急救扫描」——比抽检门还精细。幸亏他也是心照不宣的人，只是收了你一笔高昂的「诊费」。", "fx": {"heat": 1, "skill:街智": 1}}},
            {"text": "亮出「诚实」档案，主动申报，交罚款。", "req": ("flag", "honest", 1),
             "effects": {"narration": "稽查系统里你的「诚实」记录发光了：主动申报，罚款减半，快速通道。检测员甚至道了声辛苦。原来体面还有官方折扣。", "fx": {"skill:坚忍": 1, "heat": -1}}},
        ], factions=["discreet"], echoes=[
            {"deed": "mask_depth", "min": 3,
             "text": "过门之前你摸了一下自己的脸。一个无意识的动作。\n"
                     "在沙龙待久了的人都有这个习惯：出门前摸一下脸。\n"
                     "不是整理仪容。"},
        ]),
    # ------------------------------------------------ 面具沙龙（心照不宣·上层）
    _ev("mask_atelier", (
        "沙龙地下二层，一间没有窗的工坊。这里不做手术，只做「后处理」——\n"
        "手术在灰港做完，接缝在这里消失。\n"
        "\n"
        "缝隙师是一个五十多岁的女人，指甲修得很短，戴着珠宝商用的头戴放大镜。\n"
        "她的工作是让义体看起来不像义体：在合成皮肤上画静脉、仿造指纹的不规则性、\n"
        "给人工虹膜加上「自然」的色素沉着——\n"
        "甚至在钛合金膝盖上方的大腿上复刻一块旧伤疤，\n"
        "因为「一条完美无瑕的腿比一条金属腿更可疑」。\n"
        "\n"
        "今晚她在赶一单大活：一位钢琴家的双手。十根手指全换了。\n"
        "钢琴家下周要开音乐会，弹完之后还要和观众握手。\n"
        "没有人可以发现那十根手指不是原装的。\n"
        "\n"
        "她需要一个帮手。"),
        [
            {"text": "帮她做仿生指纹——每根手指的螺纹都不能重复。", "check": ("巧手", 11),
             "success": {"narration": "十根手指，十组不同的螺旋纹。你用硅胶模具一个一个压，\n"
                         "每一组都要微调到「像是遗传的，不像是设计的」。\n"
                         "缝隙师在旁边盯着，偶尔伸手帮你调整压力角度。\n"
                         "\n"
                         "第八根手指的时候她忽然说：「你知道为什么假指纹最难做吗？」\n"
                         "你说不知道。\n"
                         "「因为真指纹是随机的。随机的东西，人造不出来。\n"
                         "你能造出复杂的，精巧的，但你造不出『没有理由的』。\n"
                         "一旦有理由，就有设计感。一旦有设计感，就是假的。」\n"
                         "\n"
                         "第十根手指完成。她举起来对着灯看了很久。「够了。够随机了。」\n"
                         "但你注意到她说「够了」的时候，看的不是指纹，是自己的手。",
                         "fx": {"skill:巧手": 2, "flag:mask_depth": 1}},
             "failure": {"narration": "第六根手指你压歪了纹路。缝隙师看了一眼：\n"
                         "「这组纹太规则了。看起来像指纹编号，不像指纹。」\n"
                         "她用溶剂擦掉你的作品，重新来过。擦的时候顺便擦掉了你的信心。\n"
                         "但你记住了一件事：完美是假的佐证。",
                         "fx": {"skill:巧手": 1, "skill:逻辑": 1, "flag:mask_depth": 1}}},
            {"text": "问她：如果义手弹出来的和肉手一模一样，听众有权知道吗？",
             "check": ("逻辑", 10),
             "success": {"narration": "缝隙师的手停了一下。放大镜后面，她的眼睛比平时大三倍。\n"
                         "\n"
                         "「写这套夜曲的人，右手第四指比别人短一截。\n"
                         "他的曲子之所以听起来是那个样子，一部分原因是他的手就是那个形状。\n"
                         "如果这位钢琴家的义手被做成了『完美』的手——\n"
                         "十指等长，力度均匀，毫无缺陷——他弹出来的就不是那套夜曲。\n"
                         "是那套夜曲的尸检报告。」\n"
                         "\n"
                         "她放下工具，从抽屉里拿出一张手的透视片。\n"
                         "「这是他的旧手。你看——小指有一个陈旧性骨折，愈合之后微微外翻。\n"
                         "我在义手上复刻了这个偏转。\n"
                         "正因为我把缺陷留住了，他弹出来的东西才是活的。」\n"
                         "\n"
                         "你走出工坊的时候看了一眼自己的手。\n"
                         "手背上有一道小时候留下的疤。你不记得是怎么弄的。\n"
                         "你忽然很庆幸自己不记得——一道说得出来历的疤，是可以复刻的。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1, "flag:mask_depth": 1}},
             "failure": {"narration": "缝隙师看着你，目光冷了一度。\n"
                         "「这个问题每个月都有人问。答案是：\n"
                         "如果你分辨不出来，那么『分辨』本身就不是一个有意义的行为。」\n"
                         "她继续工作。你在旁边站了一会儿。\n"
                         "她的答案太快了。像是排练过的。\n"
                         "也许在每个月那个人问她之前，她自己已经问了自己一千遍。",
                         "fx": {"skill:逻辑": 1, "flag:mask_depth": 1}}},
            {"text": "在她工作的时候留意她自己的手——指甲修得那么短，是因为指甲是假的吗？",
             "effects": {"narration": "她的指甲确实很短。但不是因为假——指甲是真的。\n"
                         "问题出在指甲根部。甲沟的弧度太完美了。\n"
                         "\n"
                         "你在递工具的时候碰到了她的手背——温度是对的。质感是对的。\n"
                         "汗毛的密度是对的。\n"
                         "但有一样东西不对：她的手没有犹豫。\n"
                         "\n"
                         "一个五十多岁的人在精细操作时，手会有微颤。生理性的，不可控的。\n"
                         "她的手一点微颤都没有。\n"
                         "\n"
                         "你什么也没说。\n"
                         "面具沙龙最好的缝隙师，自己就是自己最好的作品。\n"
                         "她的手做得比任何人的手都像真的。\n"
                         "而你恰恰是因为太像了，才知道的。",
                         "fx": {"skill:共情": 1, "skill:街智": 1, "flag:mask_depth": 1,
                                "flag:mask_smith_known": 1}}},
        ], factions=["discreet"], subs=["面具沙龙"], weight=10,
        voices={"巧手": "【巧手】她调压力角度的手法你见过——那是没有触觉反馈的人才会用的代偿动作：靠角度补掉压力，因为压力她感觉不到。"},
        echoes=[
            {"deed": "mask_smith_known", "min": 1,
             "text": "缝隙师今天在给一个客户做指纹。你递工具的时候碰到了她的手。\n"
                     "她缩了一下——以前不会。\n"
                     "你们之间从此多了一个不需要维护的秘密。"},
            {"deed": "mask_depth", "min": 2,
             "text": "地下二层的灯管换了新的，光更亮了。\n"
                     "缝隙师说灯太亮对工作不好——太亮的地方，阴影更深。"},
            {"all": [{"deed": "mask_smith_known", "min": 1},
                     {"deed": "harbor_run", "min": 1}],
             "text": "灰港最新一批义肢上，有人用很细的笔画了静脉纹路。\n"
                     "不是出厂标配——是手工加的，笔触你认识。\n"
                     "走私还没上岸，伪装就已经在船上了。"},
        ]),
    _ev("mask_gallery", (
        "沙龙最深处有一间上锁的房间，叫「坦诚厅」。\n"
        "墙上挂满了画像。每一位入会的成员都要在这里脱去全部伪装，\n"
        "以真实的身体状态坐在画师面前——所有的接缝、所有的金属、\n"
        "所有在外面需要藏起来的东西，在这间房里全部暴露。\n"
        "\n"
        "画师记录。画像留档。这是面具沙龙唯一诚实的空间。\n"
        "\n"
        "「这间房的存在，」沙龙管事说，「是为了提醒我们自己在藏什么。\n"
        "一个忘记自己戴着面具的人，会开始以为面具就是脸。」\n"
        "\n"
        "今天轮到你了。画师已经准备好了。\n"
        "但你走进坦诚厅的时候发现：墙上已经挂着一幅你的画像。\n"
        "落款是三年前。笔法和现任画师不同。\n"
        "\n"
        "你没有坐过。三年前你还不是沙龙的人。\n"
        "但画像上的身体——那些接缝的位置，那些金属暴露的角度——是你的。\n"
        "画像下方有一行小字：「模本 · 未完成」。"),
        [
            {"text": "问管事：谁画了这幅画？谁在三年前就知道我长这样？",
             "check": ("逻辑", 11),
             "success": {"narration": "管事的沉默持续了足够长。长到你确信他知道答案。\n"
                         "\n"
                         "「我们有一位旧画师。三年前离开了。」\n"
                         "他走到那幅画前面，用手指碰了一下画框。\n"
                         "「他有一个习惯——在正式成员入会之前，就开始观察、素描。\n"
                         "他管这叫『预画』。他说，最诚实的画像不是坐着画的，\n"
                         "是在对方不知道被画的时候画的。」\n"
                         "\n"
                         "你看着画像上的自己。三年前的你。\n"
                         "被一个陌生人在你不知情的时候，看穿了全部伪装。\n"
                         "\n"
                         "「那他为什么走了？」\n"
                         "管事又沉默了一会儿。「因为他画完了所有人。包括他自己。\n"
                         "他在自己的画像前坐了一整夜，第二天就走了。」\n"
                         "\n"
                         "「他看到了什么？」\n"
                         "「不知道。那幅自画像他带走了。」",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1, "flag:mask_depth": 1,
                                "flag:mask_gallery_known": 1}},
             "failure": {"narration": "「三年前的事我不清楚。」管事的回答太快了。\n"
                         "面具沙龙的人不会这么快回答——\n"
                         "快意味着排练过，排练过意味着这个问题不新鲜。\n"
                         "你不是第一个发现自己被「预画」过的人。\n"
                         "你甚至可能不是今天第一个。",
                         "fx": {"skill:逻辑": 1, "flag:mask_depth": 1}}},
            {"text": "坐下来，让画师画一幅新的。旧的那幅让它挂着。", "check": ("坚忍", 9),
             "success": {"narration": "你脱掉外套，解开袖扣，卷起裤腿。\n"
                         "画师没有表情。他看你的方式像看一张地图——不带判断，只是记录。\n"
                         "\n"
                         "画了两个小时。完成后他把新画像挂在旧的旁边。\n"
                         "你看着两幅画。同一个人，隔了三年。\n"
                         "旧画上的接缝更少。新画上的接缝更隐蔽——\n"
                         "缝隙师的手艺在进步，但接缝本身在增加。\n"
                         "\n"
                         "两幅画并排挂着，像一组对照实验。\n"
                         "左边是原文，右边是译文。\n"
                         "翻译总会丢掉一些东西。但翻译也总会多出一些原文里没有的东西。\n"
                         "\n"
                         "画师说：「你下次再来，我们可以挂第三幅。」\n"
                         "你不确定第三幅上的自己还认不认得第一幅。",
                         "fx": {"skill:坚忍": 2, "flag:mask_depth": 1,
                                "flag:mask_portrait": 1}},
             "failure": {"narration": "你坐了两个小时，一动不动。\n"
                         "画师最后放下笔：「你的肩太紧了。你在演『放松』。」\n"
                         "坦诚厅最难的不是暴露身体，是暴露紧张。\n"
                         "你的身体可以脱掉伪装。你的姿态脱不掉。",
                         "fx": {"skill:坚忍": 1, "skill:共情": 1, "flag:mask_depth": 1}}},
            {"text": "走到画像前面，一个部位一个部位地对——哪里不一样了？",
             "effects": {"narration": "左肩——画上没有接缝，现在有了。\n"
                         "右手腕——画上的色差，现在被缝隙师修好了，看不出来了。\n"
                         "后颈——画上那条线还在。现在也还在。有些东西三年不变。\n"
                         "\n"
                         "但最大的区别不在接缝。\n"
                         "画上那个人的眼神，和你现在的眼神不一样。\n"
                         "画上那个人在看画师。你现在在看画。\n"
                         "画上那个人不知道自己会在三年后站在这里。你知道。\n"
                         "\n"
                         "你和镜子里的自己相遇，其中一个是真的。\n"
                         "但不一定是镜子外面那个。",
                         "fx": {"skill:共情": 2, "flag:mask_depth": 1,
                                "flag:mask_portrait": 1}}},
        ], factions=["discreet"], subs=["面具沙龙"], weight=8, echoes=[
            {"deed": "mask_portrait", "min": 1,
             "text": "坦诚厅传来消息：你的画像被借走了。没说借给谁。\n"
                     "画像回来的时候换了新画框——画没变，但装画的方式变了。"},
            {"deed": "mask_gallery_known", "min": 1,
             "text": "你在街上看见一个人在速写。他画的不是风景，是路过的每一个人的手。\n"
                     "你走过他身边时，他的铅笔没有停。\n"
                     "你不知道他是谁。但你认识那个习惯。"},
        ]),
    _ev("mask_rehearsal", (
        "沙龙每月一次「排演之夜」。不是排演戏剧——是排演日常生活。\n"
        "\n"
        "改造过声带的人练习自然的声线起伏（植入的共鸣腔太均匀了）。\n"
        "装了义眼的人练习「不经意的眨眼」（义眼不需要眨，但不眨会被注意到）。\n"
        "换了膝关节的人练习上楼梯时「应有的」喘息。\n"
        "\n"
        "今晚你坐在练习室里，旁边是一位中年女人。\n"
        "她一遍又一遍地练习同一个动作：被人拍肩膀时的惊吓反射。\n"
        "\n"
        "她的肩部神经经过改造，疼痛阈值极高，普通触碰几乎没有感觉。\n"
        "但正常人被拍肩膀时，会有一个零点三秒的微缩——\n"
        "肌肉收缩，呼吸短停，瞳孔微张。她练的就是这个。\n"
        "\n"
        "第十七遍。陪练拍了她的肩。她缩了一下。\n"
        "「怎么样？」她问。\n"
        "陪练摇了摇头：「太均匀了。真正的惊吓，第二下不会和第一下一样。」"),
        [
            {"text": "帮她练。你来拍，换节奏换力度，让她猜不到什么时候来。",
             "check": ("共情", 10),
             "success": {"narration": "你从背后拍她的肩。第一下她缩了，第二下没缩，第三下缩了一半。\n"
                         "「好一点了，」你说。「但你的呼吸还是太平。\n"
                         "被吓到的人会倒吸气，不只是缩肩。」\n"
                         "\n"
                         "你们练了一个小时。最后一次你拍她的时候，\n"
                         "她「啊」了一声——不大，很自然，带着一点恼怒。\n"
                         "\n"
                         "「这次是真的。」陪练说。\n"
                         "「不，」她笑了，「这次是我对练了一小时这件事的真实反应。\n"
                         "你分不出来。我也快分不出来了。」\n"
                         "\n"
                         "你站在那里想：一个演了二十三遍的惊吓，\n"
                         "在第二十四遍终于变成了真的——\n"
                         "而它变真的原因不是技术到位了，是她真的累了。",
                         "fx": {"skill:共情": 2, "flag:mask_depth": 1}},
             "failure": {"narration": "你拍得太重了。她的肩没有缩——但她的身体晃了一下。\n"
                         "改造过的肩不会缩，力学反应骗不了人。\n"
                         "陪练皱着眉看你：「你在测她，还是在帮她？」\n"
                         "你不确定。也许在面具沙龙待久了，帮助和测试的边界会模糊。",
                         "fx": {"skill:共情": 1, "heat": 1, "flag:mask_depth": 1}}},
            {"text": "告诉她：不用练到完美。真人也会被说「反应好慢」。",
             "check": ("逻辑", 11),
             "success": {"narration": "她停下来看着你。\n"
                         "「你是说——让它看起来像一个反应慢的正常人，\n"
                         "而不是一个反应精准的改造人？」\n"
                         "\n"
                         "「差不多。外城来的人说这座城的话，永远差半个调。\n"
                         "但没有人怀疑他们是假的。因为『不太地道』恰恰是活人会有的样子。\n"
                         "太地道了，反而像教材——教材不是人写的。」\n"
                         "\n"
                         "她想了一会儿，然后让陪练再拍了一次。\n"
                         "这一次她缩得很慢。慢到有点笨拙。\n"
                         "陪练点了点头：「这个像。像一个心思不在这里的人被拍了一下。」\n"
                         "她笑了：「我心思确实不在这里。」",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1, "flag:mask_depth": 1}},
             "failure": {"narration": "她的表情变了：「你说得轻巧。\n"
                         "你知不知道上个月有人因为反应慢了半秒被抽检？」\n"
                         "你不知道。你低估了「不完美」的成本。\n"
                         "在面具沙龙，零点三秒和零点五秒之间，是安全和暴露的距离。",
                         "fx": {"skill:逻辑": 1, "skill:坚忍": 1, "flag:mask_depth": 1}}},
            {"text": "安静地看。第二十三遍的时候，她忽然哭了。",
             "effects": {"narration": "不是演的。你看得出来——\n"
                         "演的眼泪从眼角往下走，因为演员会微微仰头，让眼泪沿固定路线流。\n"
                         "她的眼泪直接从下眼睑溢出来，没有路线。\n"
                         "\n"
                         "陪练慌了：「对不起，是不是太用力了？」\n"
                         "「不是。」她擦了一下。「我忽然想起来上一次真正被吓到是什么时候了——\n"
                         "是我女儿从背后抱我。那时候我还有原装的肩。\n"
                         "那次我缩了，她笑了。我再也缩不出那个弧度了。」\n"
                         "\n"
                         "练习室安静了很久。\n"
                         "二十三遍假的惊吓没能启动的东西，一句话启动了。\n"
                         "面具沙龙排演一切：步态、声线、瞳孔、微颤。\n"
                         "没有人排演过怎么真正地难过。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1, "flag:mask_depth": 1}}},
        ], factions=["discreet"], subs=["面具沙龙"], weight=10,
        voices={"机械亲和": "【机械亲和】她的肩关节是第四代气压阻尼型。这种型号的触觉反馈延迟在零点一八秒——比人体的零点一二慢了整整一个身位。\n那零点零六秒的差距，就是她要用余生去排演的东西。"},
        echoes=[
            {"deed": "mask_depth", "min": 3,
             "text": "你在路上被人拍了一下肩膀。你缩了。\n"
                     "零点三秒，幅度适中，呼吸短停。完美的惊吓反射。\n"
                     "你不确定这是真的还是练出来的。也许已经没有区别了。"},
            {"all": [{"deed": "mask_depth", "min": 1},
                     {"deed": "hammer_run", "min": 1}],
             "text": "你想起铁锤派集合的样子。三十八个人，纯血，没有一处改造痕迹。\n"
                     "你在沙龙学到的东西让你不安——\n"
                     "「没有痕迹」不等于「没有改造」，只等于「缝隙师还没出错」。"},
        ]),
    _ev("mask_inheritance", (
        "沙龙的一位老成员死了。心脏——真的心脏——停了。\n"
        "她生前是三个企业的董事。没有人知道她改造过。\n"
        "缝隙师的杰作：三十年，零破绽。\n"
        "\n"
        "遗嘱里有一项特殊条款：\n"
        "「我的外观模板——肤色参数、虹膜纹理、声纹频谱、步态数据——\n"
        "遗赠给面具沙龙，由沙龙全权处置。」\n"
        "\n"
        "换句话说，她把自己的「脸」留给了沙龙。\n"
        "如果有人买下这套模板，他们可以长得和她一模一样——\n"
        "声音、走路的姿势、甚至皮肤在不同光线下的色调。\n"
        "\n"
        "她的女儿来了沙龙。女儿不知道母亲是沙龙的人。\n"
        "「我想要妈妈的遗物。」她的声音很平。「所有的。」"),
        [
            {"text": "把模板交给女儿。「这些数据是她的身体，应该随她走。」",
             "check": ("共情", 11),
             "success": {"narration": "你把一个加密盘递给女儿。她接过去的时候手在抖。\n"
                         "「这是什么？」\n"
                         "你不知道该怎么解释「这是你妈妈的脸的源代码」，\n"
                         "所以你说：「这是她留下来的。你不需要打开它。你只需要知道它在。」\n"
                         "\n"
                         "女儿走了。管事在身后看着你。\n"
                         "「你知道她会打开的。所有人都会打开。」\n"
                         "你知道。你也知道她打开之后会看到什么——\n"
                         "一个数字化的母亲，精确到每一条笑纹。\n"
                         "比任何照片都像。比任何回忆都准。\n"
                         "而比任何遗物都更像一面镜子——照出来的不是死者，是活人的空缺。",
                         "fx": {"skill:共情": 2, "flag:mask_depth": 1,
                                "flag:mask_legacy_given": 1}},
             "failure": {"narration": "女儿问了一个你回答不了的问题：「你们对她做了什么？」\n"
                         "她以为沙龙是害死母亲的人。\n"
                         "你解释不了「我们只是帮她藏了三十年」——\n"
                         "因为这句话在女儿听来，和「我们骗了她三十年」没有区别。\n"
                         "模板最终被法务冻结了。一个人的脸，卡在遗嘱和亲情之间。",
                         "fx": {"skill:共情": 1, "heat": 1, "flag:mask_depth": 1}}},
            {"text": "按遗嘱执行——模板属于沙龙。「她做了选择，我们尊重她的选择。」",
             "check": ("威慑", 10),
             "success": {"narration": "管事向女儿出示了遗嘱的合法副本。\n"
                         "女儿的眼睛从愤怒变成困惑，又从困惑变成一种更深的东西。\n"
                         "「她为什么要把脸留给你们？她的脸不是她的吗？」\n"
                         "\n"
                         "你站在旁边没有说话。\n"
                         "但你在想：脸不是器官。脸是界面。\n"
                         "一个人的心脏属于她自己，一个人的脸属于所有看过她的人。\n"
                         "她把界面的所有权留给了沙龙——不是因为不爱女儿。\n"
                         "是因为她知道：如果女儿拿到了模板，女儿迟早会穿上母亲的脸。",
                         "fx": {"skill:威慑": 2, "flag:mask_depth": 1,
                                "flag:mask_legacy_kept": 1}},
             "failure": {"narration": "女儿没有闹。她只说了一句话：「她连死都在演。」\n"
                         "然后她走了。\n"
                         "管事收起遗嘱的时候手在抖。\n"
                         "你第一次在面具沙龙看到有人因为一句真话发抖，而不是因为一个谎言。",
                         "fx": {"skill:威慑": 1, "skill:共情": 1, "flag:mask_depth": 1}}},
            {"text": "销毁模板。「一个人的脸不应该被继承，也不应该被任何人使用。」",
             "check": ("逻辑", 10),
             "success": {"narration": "你当着管事和女儿的面，格式化了存放模板的硬盘。\n"
                         "进度条走了四分钟。四分钟里没有人说话。\n"
                         "\n"
                         "格式化完成的时候，女儿轻轻呼了一口气——\n"
                         "像放下了一样她不知道自己在扛的东西。\n"
                         "管事也松了一口气——但他松的部分不一样。\n"
                         "他松的是「沙龙不再拥有一张死者的脸」这件事。\n"
                         "\n"
                         "你走出沙龙的时候想：一个人的脸在她死后就不可说了，\n"
                         "因为没有人再穿着它说话。\n"
                         "不可说的东西应该沉默。所以你让它沉默了。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1, "flag:mask_depth": 1}},
             "failure": {"narration": "管事拦住了你：「遗嘱的法律效力高于你的道德直觉。」\n"
                         "模板被转移进了沙龙的保险库。你没能销毁它。\n"
                         "但你在它进保险库之前看了最后一眼——\n"
                         "屏幕上是一张脸的三维模型，缓缓旋转。没有表情。\n"
                         "一张脸在失去了穿它的人之后，就是这个样子。",
                         "fx": {"skill:逻辑": 1, "skill:坚忍": 1, "flag:mask_depth": 1}}},
        ], factions=["discreet"], subs=["面具沙龙"], weight=8,
        req_seen_any={"mask_atelier": 1, "mask_gallery": 1}, echoes=[
            {"deed": "mask_legacy_given", "min": 1,
             "text": "你在地铁上看见一个年轻女人。她走路的姿势让你想起一个人——\n"
                     "一个已经死了的人。你看了很久。\n"
                     "可能是巧合。你希望是巧合。"},
            {"deed": "mask_legacy_kept", "min": 1,
             "text": "沙龙保险库的清单上多了一个条目。编号是死者的生日。没有名字。\n"
                     "一个人的脸被编了号，和撬棍放在一起保管。不，比撬棍安全。"},
        ]),
    _ev("mask_null", (
        "沙龙的年度私宴。创始人出席——这是他一年唯一一次露面。\n"
        "所有人都知道他是沙龙的源头。没有人见过他脱掉伪装的样子。\n"
        "「连坦诚厅的画像都没有他的，」管事低声说，「他拒绝被画。」\n"
        "\n"
        "宴会快结束时，创始人站了起来。\n"
        "「今晚是我最后一次来。我要退了。退之前，\n"
        "我想做一件我在沙龙四十年没做过的事。」\n"
        "\n"
        "他开始脱——不是脱衣服。\n"
        "他摘下隐形眼镜——不是矫正用的，是伪装虹膜色差的。\n"
        "他揭开左耳后面的一片仿生皮——底下是皮肤。正常的皮肤。\n"
        "他卷起袖子——没有接缝。一处接缝都没有。\n"
        "\n"
        "全场安静了。\n"
        "\n"
        "他身上没有一处改造。他是纯粹的、完整的、原装的血肉。\n"
        "面具沙龙的创始人，四十年来伪装成一个在伪装的人——\n"
        "而底下什么都没有。"),
        [
            {"text": "「你建了一整个沙龙教别人隐藏。你自己在隐藏什么？」",
             "check": ("逻辑", 12),
             "success": {"narration": "创始人看着你。看了很久。\n"
                         "「你问对了问题。但你问反了。」\n"
                         "他坐了回去。灯光落在他的脸上，一张没有任何手术痕迹的脸。\n"
                         "「我不是在隐藏什么。我是在隐藏『什么都没有』。」\n"
                         "\n"
                         "「四十年前我发现了一件事：\n"
                         "在这座城里，改造了的人害怕被发现。\n"
                         "但没有改造的人也害怕被发现——怕别人发现自己什么都没换过。\n"
                         "因为在一个所有人都在变的世界里，不变是最大的异常。」\n"
                         "\n"
                         "他站起来，把摘下来的伪装品一件一件放在桌上。\n"
                         "隐形眼镜。仿生皮贴片。一块假的皮下信号源。\n"
                         "「我建这个沙龙，不是为了帮人藏机械。\n"
                         "是因为我发现——在一个人人伪装的世界里，伪装本身变成了正常。\n"
                         "而正常变成了需要伪装的东西。」\n"
                         "\n"
                         "有人画过一幅和国土一样大的地图，一比一，覆盖了整个国家。\n"
                         "后来地图朽烂了，露出底下真实的地貌。没有人认得出来了。\n"
                         "沙龙就是那幅地图。他是地图底下的那块地。",
                         "fx": {"skill:逻辑": 3, "flag:mask_depth": 1,
                                "flag:mask_null_revealed": 1}},
             "failure": {"narration": "创始人看了你一眼。\n"
                         "「四十年来，每一个聪明人都问过我这个问题。我从来没有回答过。」\n"
                         "他笑了。「不是因为答案危险。是因为答案太简单了。\n"
                         "简单到如果我说出来，你们会觉得被骗了。」\n"
                         "他走了。你们确实觉得被骗了。\n"
                         "但你不确定是被他骗了，还是被自己的预期骗了。",
                         "fx": {"skill:逻辑": 1, "skill:共情": 1, "flag:mask_depth": 1}}},
            {"text": "沉默。你需要时间消化。",
             "effects": {"narration": "你什么也没说。\n"
                         "创始人把伪装品收进一个小盒子里，放在桌上，走了。\n"
                         "他走路的姿势没有变——和他戴着伪装的时候一模一样。\n"
                         "当然一模一样。他从来没有义体需要的步态矫正。\n"
                         "他的步态一直是原装的。\n"
                         "你之前以为那是最好的矫正。原来那是最好的原装。\n"
                         "\n"
                         "那个小盒子被管事收走了。\n"
                         "你坐在空了的宴会厅里想了很久。\n"
                         "\n"
                         "有一个很老的故事：一个人梦见自己是蝴蝶，醒来之后分不清\n"
                         "是自己梦见了蝴蝶，还是蝴蝶梦见了自己。\n"
                         "而今晚这个版本更狠——醒来之后发现两个都不是。\n"
                         "他是那场梦本身。",
                         "fx": {"skill:坚忍": 2, "skill:共情": 1, "flag:mask_depth": 1}}},
            {"text": "「你是我见过的最深的面具——底下是脸，但所有人以为底下还是面具。」",
             "check": ("共情", 11),
             "success": {"narration": "创始人听完你的话，安静了一会儿。\n"
                         "然后他做了一件你没想到的事——他鼓掌了。\n"
                         "一个人的掌声在空旷的宴会厅里很奇怪。\n"
                         "\n"
                         "「最深的面具。」他重复。「你说得不错。但你漏了一层。」\n"
                         "\n"
                         "他走到你面前。\n"
                         "「最深的面具不是『面具下面是脸』。\n"
                         "最深的面具是——面具下面是脸，但那张脸已经跟面具长在一起了。\n"
                         "我戴了四十年『假装有改造』的伪装。\n"
                         "你觉得我摘下来之后，我还知道自己是谁吗？」\n"
                         "\n"
                         "他的眼睛很亮。不是义眼的亮。\n"
                         "是一种更古老的光——壁炉将灭未灭时的那种亮。\n"
                         "\n"
                         "「我今晚之所以要当众摘，是因为不当着所有人的面摘，我自己不敢摘。\n"
                         "我需要见证者。\n"
                         "一棵树在没有人的森林里倒下，它有没有发出声音？\n"
                         "也许一副面具在没有人的房间里摘下来，也没有人摘过。」\n"
                         "\n"
                         "他走了。你成了他的那片森林。",
                         "fx": {"skill:共情": 3, "flag:mask_depth": 1,
                                "flag:mask_null_revealed": 1}},
             "failure": {"narration": "「最深的面具？」他皱了一下眉。「你把它想成了修辞。」\n"
                         "他没有解释「它」是什么——面具，还是人。\n"
                         "你想追问。但他已经走向了门口。\n"
                         "他走路的姿势忽然看起来非常普通。普通到你几乎认不出来。\n"
                         "因为在面具沙龙，「普通」是最不可能的样子。",
                         "fx": {"skill:共情": 1, "skill:坚忍": 1, "flag:mask_depth": 1}}},
        ], factions=["discreet"], subs=["面具沙龙"], weight=6,
        req_seen={"mask_atelier": 1, "mask_gallery": 1}, echoes=[
            {"deed": "mask_null_revealed", "min": 1,
             "text": "沙龙再也没有年度私宴了。管事说创始人退隐了。退到哪里没有人知道。\n"
                     "一个没有任何改造的人消失在人群里——比任何面具都彻底。\n"
                     "因为人群就是他的面具。他从来不需要摘。"},
        ]),
    # ------------------------------------------------ 明焰事件
    _ev("open_ethics", (
        "学院派伦理委员会公开听证：一位教授认为把人工海马体全部替换为普通的存算一体阵列会更高效。\n"
        "「人类身份的忒修斯换板」。反对席坐满了人。你有五分钟发言时间。"),
        [
            {"text": "支持：连续性在于功能，不在于外观。", "check": ("逻辑", 11),
             "success": {"narration": "你想到残稿的核心论证——「连续性豁免」不是替换的许可证，是替换过程本身的副产品：每一枚新芯片在接管前都经过了旧芯片的校验，校验本身就是记忆的一次传递。委员会沉默了很久。教授在走廊握住你的手，力气比你预想的大：「三十年了。我以为那场火把这个论证永远烧掉了。」", "fx": {"skill:逻辑": 2, "flag:reformer": 1}},
             "failure": {"narration": "反方抛出了致命一击：渐进替换之所以不引发恐慌，恰恰因为每一步的损失都小到可以忽略——而一千个可以忽略的损失加起来，你不能再说总和也可以忽略。你没接住。申请被驳回。散场后反方主辩在走廊等你，递了张名片过来。", "fx": {"skill:逻辑": 1}}},
            {"text": "反对：遗忘不是故障，是功能。", "check": ("逻辑", 10),
             "success": {"narration": "「存算阵列不会遗忘。问题不在于它记得太多——人脑也记得太多——而在于它没有能力决定忘掉什么。遗忘是记忆的免疫系统，删掉它，记忆会自体攻击。」书记员停笔，问你能不能重复最后一句。申请被附加了「保留选择性遗忘功能」的修正条款后通过——两边都觉得赢了。", "fx": {"skill:逻辑": 2, "skill:共情": 1}},
             "failure": {"narration": "教授等你说完，摘下眼镜：「突触衰减是物理过程。你刚才说了五分钟，翻译成一句话就是——热力学第二定律是一种美德。」旁听席安静了一秒，然后笑了。你记住了这种败法。", "fx": {"skill:逻辑": 1}}},
            {"text": "提议：先问教授的家人怎么看。", "check": ("共情", 10),
             "success": {"narration": "教授的女儿在证人席上说：「我不在乎他脑子里是什么材料，我在乎他还记不记得我小时候怕打雷。」记录员停了笔。教授低下了头。委员会新增了「亲属记忆核验」环节——表决时没有人数票，因为没有人举反对的手。", "fx": {"skill:共情": 2, "skill:逻辑": 1}},
             "failure": {"narration": "「家人不是器官的股东。」教授冷冷回敬，「如果亲属的眼泪能推翻同行评议，那伦理委员会可以改名叫家庭法院。」你被主席提醒「发言与议题相关性不足」。", "fx": {"skill:共情": 1}}},
        ], factions=["open"], echoes=[
            {"deed": "acad_broke_taxonomy", "min": 1,
             "text": "听证材料的附录里多了一页。第一行是你写过的那段意见：\n"
                     "「当事人的目的是不被辞退，而『不被辞退』不是一个分类。」"},
        ], voices={"逻辑": "【逻辑】双方引用的是同一篇残稿——各引了烧掉的那一半。"}),
    _ev("open_rights_march", (
        "平权阵线上街了：一个装不起义肢的少年在冲压线上丢了右手，被辞退时工厂写的理由是「不符合岗位完整性要求」。少年把辞退信复印了三千份当传单。队伍要从工厂门口走到市政厅。警方的无人机在头顶列队，像一群剪掉了叫声的乌鸦。"),
        [
            {"text": "走在第一排。", "check": ("威慑", 10),
             "success": {"narration": "水炮车逼近到五十米时，你举起了少年的工牌。工牌背面还盖着「完整性合格」的旧印章。第一排没有人后退，于是第二排也没有。水炮最终没有开。市政厅收下了请愿书——和三万个签名。", "fx": {"skill:威慑": 2, "skill:坚忍": 1, "flag:riot": 1}},
             "failure": {"narration": "队伍被冲散了。你在水柱里护住了旗子，代价是三天低烧。少年来看你。他用剩下的那只手帮你把湿透的旗子叠好，放在枕头旁边。你注意到他单手叠旗子也很利索。", "fx": {"hp": -1, "skill:坚忍": 2}}},
            {"text": "黑进无人机编队，让它们排成标语。", "req": ("aug", ">=", 40), "check": ("电子直觉", 12),
             "success": {"narration": "傍晚六点整，全城抬头：警用无人机在暮色里排出四个字——「谁不完整」。指挥系统瘫痪了九分钟，九分钟里没有一滴水炮。这一幕进了教科书。你的名字没进，工厂的名字进了。", "fx": {"skill:电子直觉": 3, "heat": 2, "flag:riot": 1}},
             "failure": {"narration": "你摸到了编队频段，但没摸过反制系统的第二层。三架无人机朝你的信号源俯冲，你在天台间跑丢了半只鞋。", "fx": {"heat": 2, "skill:电子直觉": 1, "hp": -1}}},
            {"text": "去谈判：给市政厅算一笔账。", "check": ("逻辑", 10),
             "success": {"narration": "「辞退一个工人的赔偿金、再招工成本、工伤诉讼费、门口三千人的维稳支出——加起来是一条义肢的四倍。」你把表格拍在桌上。副市长看了三分钟，签了「工伤义体补助试点」。愤怒推门，算术签字。", "fx": {"skill:逻辑": 2, "skill:街智": 1, "flag:reformer": 1}},
             "failure": {"narration": "副市长听完账，笑了：「你算漏了一项——不作为的成本是零。在我的任期内是零。」你需要一个让不作为不是零的办法。下次。", "fx": {"skill:逻辑": 1}}},
        ], factions=["open"], echoes=[
            {"deed": "front_line", "min": 2,
             "text": "游行队伍里你看见了义诊点的值班医生。她没举标语，她举的是一个药箱。\n"
                     "有人受伤她就停下来处理，不问是哪边的伤——催泪弹的还是石头的。"},
        ]),
    # ============================================================
    # 改造机会：每一幕之后的那一个岔口
    #
    # 这是新模型的心脏。机化率不再是出生掷出来的，是你在这里一次次点头点出来的。
    # 不掷骰 —— 这不是运气，是选择。**而且只能往上，没有反悔。**
    # 四档各一幕，按当前机化率取用；每一幕都有几种上下文，免得读九遍同一张菜单。
    # ============================================================
    _ev("aug_offer_0", (
        "街角改装铺亮着灯。价目表贴在玻璃上，最便宜一行是「基础神经索引」，字迹被指纹磨淡了。\n"
        "你在门口张望得太明显了，里面的人隔着玻璃看到你，站起身来走向你。"),
        [
            {"text": "进去。装一件最小的——只是让手稳一点。",
             "effects": {"narration": "十七分钟。一根比头发粗一点的东西顺着手腕进去了。\n"
                                      "他让你握拳、松开、再握拳。\n"
                                      "走出来你活动了一下手指，不确定感觉变没变，因为你已经想不起来变之前的感觉。",
                         "fx": {"aug": 8, "skill:巧手": 1}}},
            {"text": "进去。既然要装，就装一件真正有用的。",
             "effects": {"narration": "他推荐了一整套。「分开装三次，钱花两倍，疼三回。」\n"
                                      "\n"
                                      "麻药退下去的时候是清晨，你走到街上，路灯有一圈你昨天看不见的光晕。\n"
                                      "\n"
                                      "账单要还很久，但你的新身体会帮助你。",
                         "fx": {"aug": 9, "skill:电子直觉": 1, "skill:机械亲和": 1}}},
            {"text": "快速走开。",
             "effects": {"narration": "你不习惯这样热情的推销。",
                         "fx": {}}},
        ], subscene=True,
        variants=[
            {"any": [{"deed": "honest", "min": 1}, {"deed": "gave_it_away", "min": 1},
                     {"deed": "secret_friend", "min": 1}, {"deed": "reformer", "min": 1},
                     {"turn": 2}],
             "text": "工伤复检的单子下来了。医生把片子转过来给你看：\n"
                     "「这个位置，自己扛也行，三年之内不能拎重物。装一个也行，明天就能上工。」\n"
                     "\n"
                     "片子上亮着一小块白。你看不懂，但它替你疼了很久了。",
             "options": [
                 {"text": "签字。明天就能上工。",
                  "effects": {"narration": "手术在工厂的合作诊所做的，费用分二十四期。\n"
                              "第二天你回到岗位上，班长看了你一眼，把重的那一摞挪给了你。\n"
                              "\n"
                              "搬得动了。也还不完了。",
                              "fx": {"aug": 8, "skill:坚忍": 1}}},
                 {"text": "签字，而且不止签那一项。",
                  "effects": {"narration": "「既然都要动，就一次动完。」你说。\n"
                              "医生停了两秒，然后开始在单子上加行。\n"
                              "\n"
                              "出院那天你自己走出去的，没有人扶。\n"
                              "分期从二十四期变成了六十期。",
                              "fx": {"aug": 9, "skill:坚忍": 1, "skill:机械亲和": 1}}},
                 {"text": "「我扛三年。」",
                  "effects": {"narration": "医生把片子收起来，在病历上写了一行字。\n"
                              "你没看清写的什么，但他写得很快，像是写过很多遍。\n"
                              "\n"
                              "接下来三年会疼。已经在疼了。",
                              "fx": {"skill:坚忍": 2}}},
             ]},
            {"turn": 3,
             "text": "招工启事贴在电线杆上，最后一行是加粗的：\n"
                     "「优先考虑已完成基础神经索引者。」\n"
                     "\n"
                     "旁边站着三个人在看。其中一个已经转身走了，走向街角那家铺子。",
             "options": [
                 {"text": "跟上去，这份工你要。",
                  "effects": {"narration": "铺子里排了四个人，同样的忐忑不安。\n"
                                           "老板熟练得像在食堂打饭：签字、消毒、二十分钟一个。\n"
                                           "你出来的时候第二个人正进去，你们的肩膀轻轻擦过。",
                              "fx": {"aug": 8, "skill:街智": 1}}},
                 {"text": "跟上去，而且要装到「优先」两个字够不着你为止。",
                  "effects": {"narration": "从此你不再关注启事上的字眼了，因为所有招工标准都不可能再卡住你。",
                              "fx": {"aug": 9, "skill:威慑": 1}}},
                 {"text": "忘记启事，回家。",
                  "effects": {"narration": "你是来挣钱的，不是来花钱的。工作是为了生活，而生活不是为了工作。\n"
                                           "你在心里反复安慰自己，终于让启事从你的脑子里淡去了。感谢人类的特长，遗忘。",
                              "fx": {"skill:逻辑": 1, "skill:坚忍": 1}}},
             ]},
            {"turn": 4, "retire_after": 3,
             "text": "朋友换了一只眼睛。他非要你看，把脸凑得很近。\n"
                     "\n"
                     "「一样吧？」他说。「你说实话，一样吧？」\n"
                     "\n"
                     "不一样。虹膜的纹路太规整了，规整得像印出来的。\n"
                     "但他等的不是这个答案。",
             "options": [
                 {"text": "「一样。」然后问他在哪儿做的。",
                  "effects": {"narration": "他高兴坏了，把地址、价格、术后注意事项一口气说完，还说提他的名字能便宜。\n"
                                           "\n"
                                           "你去了，提了他的名字，不确定价格是否真的会便宜。\n"
                                           "躺下时你想起他把脸凑近的样子，醒来时你已经拥有了和他一样的眼睛。",
                              "fx": {"aug": 8, "skill:共情": 1}}},
                 {"text": "「不一样。」然后自己去做一只更好的。",
                  "effects": {"narration": "再次见面时，他远远地和你打招呼，走近了却一愣，笑容僵在脸上。从此他很少来找你了。",
                              "fx": {"aug": 9, "skill:电子直觉": 1, "heat": 1}}},
                 {"text": "「一样。」然后什么也没做。",
                  "effects": {"narration": "他信了，或者装作信了。\n"
                                           "那天之后你们照常喝酒。只是他讲话的时候，你会不自觉地盯着那只不会变的瞳孔看，而他会不自觉地把那半边脸往阴影里偏一点。",
                              "fx": {"skill:共情": 2}}},
             ]},
        ]),
    _ev("aug_offer_1", (
        "沙龙里有人换了一双新手，整晚没有人提起这件事。\n"
        "\n"
        "散场的时候，那个人把一张卡片塞进你手里。\n"
        "上面没有名字，只有一个地址和一行小字：「接缝可以做到看不见。」"),
        [
            {"text": "去那个地址。装一件，接缝做到看不见。",
             "effects": {"narration": "地下二层，恒温，无窗。缝隙师连你皮肤的色号都取了样。\n"
                         "\n"
                         "两周后你在镜子前找了很久。\n"
                         "你知道线在哪里，但眼睛找不到。\n"
                         "\n"
                         "从此出门前多花三秒钟。三秒钟，每一天。",
                         "fx": {"aug": 8, "skill:街智": 1}}},
            {"text": "去，而且不打算藏。「藏起来的东西才需要被原谅。」",
             "effects": {"narration": "你选了标准型号，不加覆膜。接缝是灰的，一眼就能看见。\n"
                                      "\n"
                                      "第二天沙龙里没有人问你。但散场的时候一个人拦住你，什么也没说，把袖子往上推了两寸。\n"
                                      "他手腕上也有一道。",
                         "fx": {"aug": 10, "skill:威慑": 1, "heat": 1}}},
            {"text": "把卡片留着。没去。",
             "effects": {"narration": "卡片在钱包里放了很久，边角磨圆了。\n"
                                      "每次翻开都看见，每次都翻过去。",
                         "fx": {"skill:坚忍": 1}}},
        ], subscene=True),
    _ev("aug_offer_2", (
        "楼道里贴着升级公告：《第七代神经总线迁移建议》。\n"
        "下面一张对照表，显示旧型号的延迟和新型号的延迟差了六分之一。"),
        [
            {"text": "按建议升级，数据在那儿摆着。",
             "effects": {"narration": "术后，同一份工作，时间少了六分之一。\n"
                                      "你怀念过旧的手感吗？也许怀念过一秒钟吧。",
                         "fx": {"aug": 8, "skill:逻辑": 1, "skill:电子直觉": 1}}},
            {"text": "升级，而且超过建议档位。你已经厌倦于被版本迭代追着跑了。",
             "effects": {"narration": "你选了最新的旗舰版，参数好看，不良反应未知。\n"
                                      "接上去的第一个小时，世界边缘有一圈很轻的锯齿。效果确实不错，但没有你想象中那么完美。当你想和其他人讲不良反应时，你发现没有人愿意听你说话，所有人都觉得你在炫耀。你闭上了嘴。",
                         "fx": {"aug": 11, "skill:电子直觉": 2, "hp": -1}}},
            {"text": "不升，你和你的旧神经关系很好。",
             "effects": {"narration": "半年后，你是全场响应最慢的那一个。\n"
                                      "偶尔你也责怪旧神经害你被骂，逐渐淡忘了当初你为何坚持要保下它。",
                         "fx": {"skill:逻辑": 1, "skill:坚忍": 1}}},
        ], subscene=True),
    _ev("aug_offer_3", (
        "你去做了高机化体检，体检顾问用笔尖戳了戳报告上的数字，告诉你，你现在剩下的这点肉体，维持起来比替换还要贵。"),
        [
            {"text": "替换掉贵的部分。",
             "effects": {"narration": "三个器官，一次做完。恢复期四天，比上次短。\n"
                                      "你不再需要吃东西了，但刚开始时，你还是习惯去食堂和朋友聊天，看着他们吃饭。但只有你不吃，气氛尴尬，你们的相处越来越不融洽。你慢慢疏远了他们。",
                         "fx": {"aug": 8, "skill:机械亲和": 1}}},
            {"text": "全换，一步到位。",
             "effects": {"narration": "手术分两次，中间隔一夜。\n"
                                      "那一夜你是清醒的——他们让你决定要不要清醒。\n"
                                      "\n"
                                      "第二次推进去之前，你用原装的舌头最后尝了一口温水。\n"
                                      "水是甜的，你从来不知道水是甜的。",
                         "fx": {"aug": 13, "skill:机械亲和": 1, "skill:电子直觉": 1}}},
            {"text": "你愿意多付钱维持现状。",
             "effects": {"narration": "顾问点点头离开了，把表格留给你。",
                         "fx": {"skill:共情": 1, "skill:坚忍": 1}}},
        ], subscene=True),

    # ============================================================
    # 三问：跨过一档之后，你更像哪一派
    #
    # 阵营是机化率决定的（不由你选，只由你点头的次数决定）；
    # **派系是这三个问题问出来的。** 每题两个选项，各记一分，三题定归属。
    # 这三幕不消耗幕数 —— 它们不是剧情，是一次自我确认。
    # ============================================================
    # 重问三题之前的那一句。同一档每三世重问一遍，后半程玩家能把选项背下来 ——
    # 所以先问一句「变了没有」，说没变就不必再答一遍。
    # （2026-08-08 试玩反馈。选项 2 里的 "lean_first" 是个记号，
    #  由 `_choose_inner` 翻成当前阵营的第一题 —— `then` 只收静态 id。）
    _ev("lean_recap", (
        "同样的三个问题又摆到面前。你已经答过一次了。\n"
        "在答之前，先看一眼这几世你到底做了什么。"),
        [
            {"text": "答案没变。",
             "effects": {"narration": "你连想都没想。有些事问第二遍也是同一个答案 ——\n"
                         "而你连自己是从哪一年开始这么确定的，都记不清了。",
                         "fx": {}}},
            {"text": "重新答一遍。",
             "effects": {"narration": "你停了一下。\n"
                         "「上一次」这三个字你说不出口，但你知道自己在跟什么比。",
                         "fx": {}, "then": "lean_first"}},
        ], subscene=True),
    _ev("lean_other_life", (
        "应行的路，你已经很熟悉了。在一墙之隔的地方，你一直都知道有另一种活法存在。要去试试看吗？"),
        [
            {"text": "去",
             "effects": {"narration": "你推开了那扇门。",
                         "fx": {"flag:lean_prompt": 1, "flag:lean_cross": 1}}},
            {"text": "不去",
             "effects": {"narration": "你没有去。",
                         "fx": {"flag:lean_prompt": 1}}},
        ], subscene=True),
    _ev("lean_purist_1", (
        "在集会上，你听到「肉身神圣」。"),
        [
            {"text": "这句话应该被一字不错地记下来，传下去。",
             "effects": {"narration": "你把它记在纸上，也记在心里。",
                         "fx": {"flag:lean_a": 1}, "then": "lean_purist_2"}},
            {"text": "这句话应该被人听见。必要的时候，喊出来。",
             "effects": {"narration": "你知道这句话，这就够了。现在最重要的事是如何让更多人也知道。",
                         "fx": {"flag:lean_b": 1}, "then": "lean_purist_2"}},
        ], subscene=True),
    _ev("lean_purist_2", (
        "你家附近开了一间黑诊所。窗帘日夜拉着，凌晨有人影进出。"),
        [
            {"text": "去讲这件事。让该知道的人知道。",
             "effects": {"narration": "你在集会上说了。说完之后有人问你地址，你给了。\n"
                         "接下来的事你没有参与。",
                         "fx": {"flag:lean_a": 1}, "then": "lean_purist_3"}},
            {"text": "远远观察它。",
             "effects": {"narration": "你在对街站了两个晚上。第三晚你知道了换班的时间、\n"
                                      "后门的位置、门锁的开启方法。\n"
                                      "但你没有告诉任何人，你只是等待。",
                         "fx": {"flag:lean_b": 1}, "then": "lean_purist_3"}},
        ], subscene=True),
    _ev("lean_purist_3", (
        "一个人当着你的面掀开裤腿，露出一截不属于骨头的东西。\n"
        "他在等你说话。"),
        [
            {"text": "「先别动。让我想想经上是怎么说的。」",
             "effects": {"narration": "他自己把裤腿放了下来，「我是给你看的，不是给经书看的。」",
                         "fx": {"flag:lean_a": 1}}},
            {"text": "「谁装的？」",
             "effects": {"narration": "你穷追不舍，要求他必须正面回答问题，最后他供出了矿上的医生。",
                         "fx": {"flag:lean_b": 1}}},
        ], subscene=True),

    _ev("lean_discreet_1", (
        "你身上现在有一样东西不是你原来的。\n"
        "早上出门之前，你会不会特意为此照镜子？"),
        [
            {"text": "会，而且会检查得很仔细。",
             "effects": {"narration": "三分钟。领口、袖口、走路的时候左肩会不会比右肩高一点。\n"
                         "检查完你才开门。",
                         "fx": {"flag:lean_a": 1}, "then": "lean_discreet_2"}},
            {"text": "不照。能不能过关，不在镜子里。",
             "effects": {"narration": "你连灯都没开。出门的时候顺手带上了那把旧钳子——\n"
                         "在你去的地方，有用的是这个。",
                         "fx": {"flag:lean_b": 1}, "then": "lean_discreet_2"}},
        ], subscene=True),
    _ev("lean_discreet_2", (
        "有人要你帮忙藏某个人的病例。"),
        [
            {"text": "帮他改一份新的，改得比真的还像真的。",
             "effects": {"narration": "你花了一晚上调格式、对字体、把签名的力道也仿了。\n"
                         "第二天他拿着那份东西去上班，没有人多看一眼。",
                         "fx": {"flag:lean_a": 1}, "then": "lean_discreet_3"}},
            {"text": "帮他把原件运出去，运到查不到的地方。",
             "effects": {"narration": "你借了一辆车，跑了两趟码头。第二趟的时候下着雨。\n"
                         "东西现在在海上，谁也调不出来。",
                         "fx": {"flag:lean_b": 1}, "then": "lean_discreet_3"}},
        ], subscene=True),
    _ev("lean_discreet_3", (
        "一场晚宴上，有人的义眼开始闪。满厅的人假装没看见。"),
        [
            {"text": "你走过去，替他挡了三十秒。",
             "effects": {"narration": "你端着两杯酒挡在他和记者中间，说了一段最纯粹的废话。\n"
                                      "三十秒后他的眼睛稳住了，他欠你一次人情。",
                         "fx": {"flag:lean_a": 1}}},
            {"text": "你快速查了他义眼的型号。",
             "effects": {"narration": "第三代光学，闪的是电源模块，那个批次你见过。\n"
                                      "你计算了那一批货还剩几件，以及这个厅里有几个人正在等着闪。",
                         "fx": {"flag:lean_b": 1}}},
        ], subscene=True),

    _ev("lean_open_1", (
        "现在你可以公开说自己改造过了。\n"
        "你想说给谁听？"),
        [
            {"text": "写下来。写成一份能被引用的东西。",
             "effects": {"narration": "你花了三周整理数据，加了脚注，投了出去。\n"
                         "审稿意见回来的时候你先看第三条——那条说你的样本量不够。",
                         "fx": {"flag:lean_a": 1}, "then": "lean_open_2"}},
            {"text": "说给站在你旁边的人听。就现在，就在这条街上。",
             "effects": {"narration": "你站到台阶上说了十分钟，你不知道有多少过路的人听到。即使真的有人听到心里，也不可能和路人分享这种私密的事情。",
                         "fx": {"flag:lean_b": 1}, "then": "lean_open_2"}},
        ], subscene=True),
    _ev("lean_open_2", (
        "一条规则明显不公平。它在那儿很久了。"),
        [
            {"text": "把它拆开，找出它是从哪一条推出来的。",
             "effects": {"narration": "你翻到了母条款，又翻到了母条款引用的那篇论文。\n"
                         "问题在第三层。你把这三层写成一页纸，送了上去。",
                         "fx": {"flag:lean_a": 1}, "then": "lean_open_3"}},
            {"text": "把被它压着的那些人聚到同一个地方。",
             "effects": {"narration": "四十个人，一个下午，一间借来的仓库。\n"
                                      "最初，还有人记得条款的大概，后来所有人都只想讲自己遇到的事。讲到第十七个的时候，规则究竟是从哪条推出来的已经不重要了。",
                         "fx": {"flag:lean_b": 1}, "then": "lean_open_3"}},
        ], subscene=True),
    _ev("lean_open_3", (
        "有人问你：改造到底该不该有门槛？"),
        [
            {"text": "「该。但门槛得写清楚，而且得能被人查。」",
             "effects": {"narration": "谁来写、谁来查，这些是另外的问题。",
                         "fx": {"flag:lean_a": 1}}},
            {"text": "「真正的门槛不写在纸上。」",
             "effects": {"narration": "这就是不写明门槛的原因吗？",
                         "fx": {"flag:lean_b": 1}}},
        ], subscene=True),

    _ev("lean_ascension_1", (
        "肉的部分不多了。剩下的问题不长在身体上。\n"
        "\n"
        "如果明天可以把「你」放到一个地方——你希望那个地方："),
        [
            {"text": "有别人。很多别人，近到分不清彼此。",
             "effects": {"narration": "你害怕孤独。",
                         "fx": {"flag:lean_a": 1}, "then": "lean_ascension_2"}},
            {"text": "很远。远到消息传回来要很多年。",
             "effects": {"narration": "你不害怕孤独。",
                         "fx": {"flag:lean_b": 1}, "then": "lean_ascension_2"}},
        ], subscene=True),
    _ev("lean_ascension_2", (
        "有人邀请你参加一次并联试运行：七个意识临时合一，一小时。"),
        [
            {"text": "去。想知道「我们」是什么感觉。",
             "effects": {"narration": "你报了名，提前一周开始准备，把不想让别人看见的东西一件件收好。也许其他人也会做和你相同的事。\n"
                                      "这样也算得上「我们」吗？",
                         "fx": {"flag:lean_a": 1}, "then": "lean_ascension_3"}},
            {"text": "不去。一个小时的「我们」，换不回一小时的我。",
             "effects": {"narration": "你不知道自己在躲避什么。不愿意把自己交出去的人，当然也得不到完全的接纳。但也许这就是你想要的。",
                         "fx": {"flag:lean_b": 1}, "then": "lean_ascension_3"}},
        ], subscene=True),
    _ev("lean_ascension_3", (
        "最后一个问题：你希望有人记得你，还是希望有人收到你？"),
        [
            {"text": "记得。记得我的人是我留在人间的遗产。",
             "effects": {"narration": "你真的这样想？",
                         "fx": {"flag:lean_a": 1}}},
            {"text": "收到。我的使命是创造与完成。",
             "effects": {"narration": "你真的这样想？",
                         "fx": {"flag:lean_b": 1}}},
        ], subscene=True),

    # ------------------------------------------------ 学院派（明焰·制度）
    _ev("acad_defense", (
        "学院派的答辩厅在行政楼三楼，窗帘拉着。投影仪把候选人的脸切成明暗两半。\n"
        "\n"
        "论文题目占了两行：《渐进替换中同一性的边界消解》。\n"
        "候选人手里的遥控笔攥得发白。\n"
        "\n"
        "评审席五人。正中间那位没带笔——她从来不带。\n"
        "「你主张不存在一个阈值，越过之后人就变成另一个人。」她的声音不大，\n"
        "答辩厅的回音替她放了一倍。「请告诉我，你和连锁悖论有什么区别。」\n"
        "\n"
        "你坐在旁听席第三排，膝盖上摊着同一篇论文的打印稿。"),
        [
            {"text": "替候选人接话——连锁悖论预设部分可互换，但神经元不是沙粒。",
             "check": ("逻辑", 9),
             "success": {"narration": "你站起来。评审主席看了你一眼——不是阻止，是等。\n"
                         "\n"
                         "「沙堆的每一粒沙可以被另一粒替代，因为沙没有内部状态。神经元有。\n"
                         "每一枚替代芯片在接管之前，必须先被旧神经元校准——\n"
                         "校准本身就是一次信息传递。\n"
                         "连锁悖论问的是静态的替换。活着的脑不是堆，是河。」\n"
                         "\n"
                         "评审主席摘下眼镜擦了擦：「这段话不在论文里。」\n"
                         "候选人说：「不在。但应该在。」",
                         "fx": {"skill:逻辑": 2, "flag:acad_river": 1}},
             "failure": {"narration": "你站起来，话到一半——\n"
                         "「旁听席没有发言权。」\n"
                         "你坐下了。遥控笔掉在地上，没人捡。答辩继续了四十分钟。\n"
                         "你在打印稿空白处写了七行反驳，一行都没能开口。",
                         "fx": {"skill:逻辑": 1}}},
            {"text": "质疑候选人——如果身份是过程，那每次深度睡眠都是一次小死亡。",
             "check": ("逻辑", 10),
             "success": {"narration": "「如果身份就是意识的连续运行，」你站起来，\n"
                         "「那每次深度睡眠——过程中断六到八小时——\n"
                         "醒来的人凭什么叫同一个名字？」\n"
                         "\n"
                         "答辩厅安静了很久。候选人想了很久。\n"
                         "\n"
                         "「我不知道。但区别不在于过程是否中断——\n"
                         "在于中断的时候，有没有人在等它恢复。」\n"
                         "\n"
                         "评审主席把这句话记在纸上。她用的是铅笔。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1, "flag:acad_sleep": 1}},
             "failure": {"narration": "你的问题太大了。候选人试着回答，越答越远，\n"
                         "最后用「连续性」定义「身份」，又用「身份」定义「连续性」。\n"
                         "评审叫了暂停。走廊里等了二十分钟。候选人出来时没看你。",
                         "fx": {"skill:逻辑": 1}}},
            {"text": "不开口。翻论文的参考文献——引用链里有一条不对。",
             "check": ("逻辑", 8),
             "success": {"narration": "参考文献第十七条引了一篇三十年前的残稿——《连续性豁免》。\n"
                         "你认得这个名字。夜间图书馆有半本烧焦的。候选人引的是另外半本。\n"
                         "\n"
                         "两半拼在一起，论证应该完整——但她的引文截止在第四章。\n"
                         "残稿的末章标注着「撤回」。\n"
                         "\n"
                         "你在打印稿空白处写了一行字，散场时递给她。\n"
                         "她看了一眼，脸上是棋局里发现漏算一步的表情。",
                         "fx": {"skill:逻辑": 2, "flag:acad_found_manuscript": 1}},
             "failure": {"narration": "参考文献碎得拼不起来。三十七条引用里十一条是灰色文献，\n"
                         "六条的出版机构查不到，两条互相矛盾却引了同一个页码。\n"
                         "你在打印稿背面画引用关系图，画到第三层放弃了。",
                         "fx": {"skill:逻辑": 1}}},
        ], factions=["open"], subs=["学院派"], weight=10,
        voices={"逻辑": "【逻辑】评审主席把最难的问题放在第一个——不是为了筛答案，\n是为了在最紧张的三秒里看候选人的眼睛往哪儿跑。"},
        echoes=[
            {"seen": "acad_defense", "min": 2,
             "text": "旁听席后排多了两个人。笔记本封面印着保险公司的标志。"},
            {"deed": "acad_river", "min": 1,
             "text": "候选人在走廊拦住你，递来一页纸。写了三段，划掉两段，留了一段。"},
            {"deed": "acad_sleep", "min": 1,
             "text": "评审主席今天带了笔。"},
        ]),
    _ev("acad_specimen", (
        "学院派的分类室在地下一层，恒温恒湿，灯光惨白。\n"
        "\n"
        "标签柜从地板排到天花板——一千七百个抽屉，每个抽屉一份档案。\n"
        "类别四种：医疗、功能、认知、美学。税率跟着类别走，从零到四十五。\n"
        "\n"
        "今天桌上有一份新案例。码头女工，三十四岁，脊椎植入液压支撑。\n"
        "病历写「腰椎间盘突出，保守治疗无效」。工厂用工记录写「术后日均负重翻倍」。\n"
        "她申报「医疗」。稽查站驳回：术后性能超标，按「功能增强」征税。\n"
        "\n"
        "差额是她七个月的工资。你面前有分类手册和三个章。"),
        [
            {"text": "盖「医疗」。病因是工伤，治疗目的是恢复——术后性能是副产品。",
             "check": ("逻辑", 8),
             "success": {"narration": "分类手册第三章第十七条：「因既有病变实施的替换性手术，\n"
                         "即使术后性能超过原装基线，仍归入医疗——前提是首要目的为恢复。」\n"
                         "\n"
                         "你签字，盖章。稽查站来电话，你念了条款号。对方沉默五秒，挂了。\n"
                         "\n"
                         "一个月后听说她的工友也来申请了，拿你盖的那份当先例。\n"
                         "第三个人被驳回——稽查站专门发函，堵了第十七条的脚注。",
                         "fx": {"skill:逻辑": 2, "flag:acad_stamped_medical": 1}},
             "failure": {"narration": "你翻到第三章第十七条，看见脚注：\n"
                         "「本条不适用于术后性能超过原装基线百分之二十以上之情形。」\n"
                         "液压脊椎超了四十。\n"
                         "\n"
                         "手册就是手册。你盖了「功能增强」。\n"
                         "一周后在走廊碰见她来交税。她的背比上次直了一些。",
                         "fx": {"skill:坚忍": 1, "flag:acad_followed_manual": 1}}},
            {"text": "在报告空白处写意见——这个案例不属于现有四类中的任何一类。",
             "check": ("逻辑", 10),
             "success": {"narration": "你没有盖章。你在报告空白处写：\n"
                         "\n"
                         "「此案术因为病变（医疗），术果为增强（功能），术机为生存（经济）。\n"
                         "三者同时为真。当事人的目的是不被辞退，\n"
                         "而『不被辞退』不是一个分类。建议承认体系在此失效。」\n"
                         "\n"
                         "报告被退回三次。第四次你附了七十六个类似案例的汇总。\n"
                         "第五次，委员会同意开听证。你的那段话被印在听证材料第一页。",
                         "fx": {"skill:逻辑": 2, "skill:威慑": 1, "heat": 1,
                                "flag:acad_broke_taxonomy": 1}},
             "failure": {"narration": "你写了一段意见，送上去。退回来的批复一行字：\n"
                         "「分类室的职责是分类。请盖章。」\n"
                         "\n"
                         "你盖了「待复议」。待复议的平均处理周期九个月。\n"
                         "九个月里她按「功能增强」预缴。",
                         "fx": {"skill:逻辑": 1, "flag:acad_pending": 1}}},
            {"text": "去找她本人。分类手册没有「问当事人」这一栏，但你想听。",
             "check": ("共情", 8),
             "success": {"narration": "你拿着报告去了码头宿舍。六平米的隔间，床边靠着一副旧护腰。\n"
                         "\n"
                         "「为什么选液压？」\n"
                         "她想了想：「医生说有三种方案。最便宜的恢复原样。\n"
                         "液压最贵——厂里垫的钱，条件是签五年，日均负重翻倍。」\n"
                         "你问：「不签呢？」\n"
                         "「回家。腰坏的码头工不缺。」\n"
                         "\n"
                         "你在报告上写了四行，盖了「医疗」。理由栏写的不是条款号。",
                         "fx": {"skill:共情": 2, "skill:逻辑": 1,
                                "flag:acad_asked_her": 1}},
             "failure": {"narration": "她不在宿舍。工友说去上夜班了——新合同，日均十二小时。\n"
                         "你在门口站了一会儿。门上贴着排班表，排到三个月后，每一格填满了。\n"
                         "\n"
                         "你把报告带回分类室，盖了「功能增强」。",
                         "fx": {"skill:共情": 1, "flag:acad_missed_her": 1}}},
        ], factions=["open"], subs=["学院派"], weight=10,
        voices={"逻辑": "【逻辑】四个类别，四档税率。从来不是四种事实——是四种价格。"},
        echoes=[
            {"deed": "acad_broke_taxonomy", "min": 1,
             "text": "分类室多了一个新抽屉。里面是空的——只贴了一个标签：「待定义」。"},
            {"deed": "acad_followed_manual", "min": 1,
             "text": "有人在分类手册第三章第十七条的脚注旁边贴了一张便条。\n"
                     "字迹不认识，内容是一个问号。"},
            {"deed": "acad_stamped_medical", "min": 1,
             "text": "码头来了新一批报告。三份，症状一模一样，工厂写的是同一家。"},
        ]),
    _ev("acad_intern", (
        "学院派走廊，午休。你路过导师办公室，听见里面在吵。\n"
        "\n"
        "门虚掩着。导师坐在桌后，对面站着一个学生——白大褂袖口卷着，\n"
        "指甲缝里有碘伏的颜色。不像实验室出来的。\n"
        "\n"
        "「你的课题进度落后两个季度。」导师翻着一份表格，「上个月你报的实验室时长是零。」\n"
        "\n"
        "学生说：「我在义诊点。」\n"
        "「义诊点不是实验室。」\n"
        "「义诊点的数据比实验室好。」\n"
        "\n"
        "导师摘下眼镜：「义诊点的数据通不过伦理审查——没有知情同意书，\n"
        "没有对照组，没有双盲。学院不能拿这个毕业。」\n"
        "\n"
        "你站在门口。学生的白大褂左胸口袋里插着两支笔——\n"
        "一支实验室的记录笔，一支义诊点分诊用的铅笔。"),
        [
            {"text": "进去帮她说话——义诊点的病例报告同样有学术价值。",
             "check": ("逻辑", 9),
             "success": {"narration": "你敲了门。\n"
                         "\n"
                         "「她的病例报告我看过。十七例术后随访，最长的跟了八个月——\n"
                         "没有一间实验室能拿到这种连续性数据，因为实验室的受试者做完就走。\n"
                         "义诊点的病人走不了，他们就住在附近。」\n"
                         "\n"
                         "导师看了你一眼，又看了看学生。\n"
                         "\n"
                         "「知情同意书的问题可以补。把临床观察转写成回顾性队列研究，\n"
                         "方法论上站得住。格式我来帮她改。」\n"
                         "\n"
                         "导师沉默了很久：「补完给我看。」\n"
                         "学生出来的时候没道谢——她直接问你什么时候有空改格式。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1,
                                "flag:acad_helped_intern": 1}},
             "failure": {"narration": "你进去说了三句话，导师打断：「你不是她的课题组成员。」\n"
                         "\n"
                         "你退出来。门在你身后关上了。走廊里等了十分钟，学生出来，\n"
                         "手里攥着一份休学申请表。她没有填。但她拿走了。",
                         "fx": {"skill:逻辑": 1, "flag:acad_failed_intern": 1}}},
            {"text": "拦住学生——你在义诊点是不是见过一个九岁男孩？",
             "req": ("deed", "front_triage", 1), "check": ("共情", 8),
             "success": {"narration": "她停下来看你。\n"
                         "\n"
                         "「先天心室中隔缺损。瓣膜是灰港的二手货。」\n"
                         "她说这话的声音和导师办公室里的完全不一样——不是汇报，是回忆。\n"
                         "\n"
                         "「他妈妈不识字，我帮她填的过敏史。」她看着自己的手。\n"
                         "「学院教我区分『医疗替换』和『功能增强』。\n"
                         "分诊台教我的是——一个心脏缺零件的孩子站在你面前的时候，\n"
                         "分类手册在你口袋里，但你不会翻它。」",
                         "fx": {"skill:共情": 2, "flag:acad_intern_valve": 1}},
             "failure": {"narration": "她看了你一眼：「你也是来劝我回实验室的？」\n"
                         "你说不是。她没信，走了。",
                         "fx": {"skill:共情": 1, "flag:acad_intern_walked": 1}}},
            {"text": "不插手。但你记下了她白大褂上的工牌号。",
             "effects": {"narration": "你没有进去。门关上了，又开了。\n"
                         "学生从你身边走过，没注意到你。\n"
                         "\n"
                         "你看清了她的工牌：姓名、学号、课题方向——\n"
                         "「城市边缘社区义体术后感染率的社会学分析」。\n"
                         "批准日期两年前。进度栏是空的。\n"
                         "\n"
                         "她的铅笔比记录笔短了一截。用得多的那支总先短。",
                         "fx": {"skill:逻辑": 1, "skill:共情": 1,
                                "flag:acad_noted_intern": 1}}},
        ], factions=["open"], subs=["学院派"], weight=8,
        voices={"共情": "【共情】她的指甲缝是碘伏色。实验室用酒精。碘伏只有义诊点用——便宜。"},
        echoes=[
            {"deed": "acad_helped_intern", "min": 1,
             "text": "走廊没人吵了。导师办公室门开着，桌上放着一份已批准的课题变更表。\n"
                     "新方向很长，你只看见最后四个字：「回顾性研究」。"},
            {"deed": "acad_noted_intern", "min": 1,
             "text": "学生的白大褂换了——旧的袖口洗不掉碘伏了。\n"
                     "新的左胸口袋里只插一支笔。铅笔。"},
            {"all": [{"deed": "acad_helped_intern", "min": 1},
                     {"deed": "front_triage", "min": 1}],
             "text": "义诊点的分诊台上多了一摞表格——标准的知情同意书，学院派的抬头。\n"
                     "最上面一张已经签了字。签字的人不会写名字，画了一个圈。"},
        ]),
    _ev("acad_retract", (
        "学院派全体教职会议，破例对学生开放旁听。\n"
        "\n"
        "议题只有一条：三十年前奠定现行改造伦理框架的那篇论文——\n"
        "《渐进替换的安全阈值：一项两千人队列研究》——被举报数据造假。\n"
        "举报人是论文第三作者的学生，在整理遗物时发现了原始数据表。\n"
        "两千人的队列，实际入组一千一百。缺口的九百人是复制粘贴的。\n"
        "\n"
        "问题是：结论仍然成立。后续十七项独立研究都支持同一结论。\n"
        "框架是对的。支撑框架的那根柱子是假的。\n"
        "\n"
        "大厅坐满了人。讲台上放着两份文件：撤稿函和保留意见书。"),
        [
            {"text": "主张撤稿。证据是假的，结论再对也要重走一遍。", "check": ("逻辑", 9),
             "success": {"narration": "「结论对不对不是今天的议题。」你站起来，大厅安静了。\n"
                         "\n"
                         "「今天的议题是：我们凭什么相信这个结论？\n"
                         "如果答案是凭一份造假的数据，那我们不是相信结论——\n"
                         "我们是相信了一个骗子。\n"
                         "后续实验补上了，巧。但如果当年没人补呢？\n"
                         "我们会带着一个碰巧正确的结论再走三十年。」\n"
                         "\n"
                         "「撤稿不是说结论错了。撤稿是说——我们到达结论的那条路断了。\n"
                         "请重新走一遍。」\n"
                         "\n"
                         "全体表决：撤稿通过。散场后有人在走廊骂你多此一举。\n"
                         "隔壁办公室的灯亮了一整夜——有人在重新走那条路。",
                         "fx": {"skill:逻辑": 2, "skill:威慑": 1, "heat": 1,
                                "flag:acad_retracted": 1}},
             "failure": {"narration": "你的论证被一位老教授拦下：\n"
                         "「年轻人，撤稿之后，稽查站拿什么当法律依据？\n"
                         "十七项后续研究的影响因子加在一起，不如这一篇的政策效力。\n"
                         "你撤掉的不是一篇论文——是一千七百个分类室档案的地基。」\n"
                         "\n"
                         "你没有接住这一拳。不是因为他说得对——\n"
                         "是因为你不知道地基塌了以后，住在上面的人怎么办。",
                         "fx": {"skill:逻辑": 1, "flag:acad_retract_failed": 1}}},
            {"text": "主张保留。结论是对的，学术诚信和公共利益之间选后者。",
             "check": ("逻辑", 9),
             "success": {"narration": "「九百个假数据是错。但撤稿引发的政策真空，\n"
                         "会让稽查站在未来六到十二个月里没有法律依据。\n"
                         "六到十二个月里会有多少人因为分类争议被多征税、被拒保、被辞退？」\n"
                         "\n"
                         "你把保险公司最新的拒保清单投在屏幕上。\n"
                         "\n"
                         "「先发补充声明，同步启动独立重复实验。等新证据到位，再走撤稿程序。」\n"
                         "\n"
                         "保留意见以四票优势通过。老教授散场后找到你：\n"
                         "「你说的不是学术，是政治。」\n"
                         "你说：「分类手册就是政治。」",
                         "fx": {"skill:逻辑": 2, "skill:威慑": 1,
                                "flag:acad_kept_paper": 1}},
             "failure": {"narration": "你的方案被质疑：「补充声明是什么——\n"
                         "是告诉全城，我们的伦理框架建在假数据上，但你们先别慌？」\n"
                         "\n"
                         "笑声很短，很准。你坐下了。",
                         "fx": {"skill:坚忍": 1, "flag:acad_keep_failed": 1}}},
            {"text": "找到举报人，问一个问题——她老师的遗物里还有什么？",
             "check": ("共情", 8),
             "success": {"narration": "举报人在散场后的走廊尽头，靠着窗台。\n"
                         "你走过去的时候她没有抬头——她在看手机里的一张照片。\n"
                         "\n"
                         "「我整理老师的书房，」她说，「数据表在第三个抽屉。\n"
                         "第四个抽屉里有一封没寄出的信。」\n"
                         "\n"
                         "你问信里写了什么。\n"
                         "\n"
                         "「他说他知道数据不够。他说他等不了——\n"
                         "因为那一年稽查站刚开始执行新的身体税。\n"
                         "等他再招九百人、再跟踪三年，\n"
                         "已经有几千人因为没有分类标准被按最高税率征了。」\n"
                         "\n"
                         "她收起手机。你没有说话。\n"
                         "走廊尽头的窗外是码头，远处有人在卸货。",
                         "fx": {"skill:共情": 2, "skill:逻辑": 1,
                                "flag:acad_found_letter": 1}},
             "failure": {"narration": "举报人不想说话。「我已经说了该说的。」\n"
                         "她走了。走廊的灯坏了一盏，没人修。",
                         "fx": {"skill:共情": 1, "flag:acad_no_answer": 1}}},
        ], factions=["open"], subs=["学院派"], weight=8,
        voices={"逻辑": "【逻辑】一千一百个真数据和九百个假数据支持同一结论。\n问题不是结论对不对——问题是一千一百够不够。如果够，他为什么要造九百。"},
        echoes=[
            {"deed": "acad_kept_paper", "min": 1,
             "text": "大厅座位上放着一份重印的论文。封面多了一行红字：「数据存疑，结论待验」。\n"
                     "有人用铅笔在「待」字上画了个圈。"},
            {"deed": "acad_retracted", "min": 1,
             "text": "走廊贴着一张通知：独立重复实验已启动，预计周期三年。\n"
                     "通知的日期是两年前。没有后续。"},
            {"deed": "acad_found_letter", "min": 1,
             "text": "举报人的老师有了一座小纪念碑。碑文没提论文，也没提造假。\n"
                     "只有一行字：「他相信框架比自己重要。」"},
        ]),
    _ev("acad_lamp", (
        "深夜。整栋行政楼只剩你办公室的灯。\n"
        "\n"
        "桌上摞着学院派本年度的研究成果——三十七篇论文，十二个课题结项，\n"
        "一场听证，一次撤稿争议。半尺厚。\n"
        "\n"
        "桌角放着一份残稿的手抄本——《连续性豁免》，纸上忠实地复印着焦痕。\n"
        "前四章完整，第五章缺。\n"
        "四章的内容你能背：渐进替换保持身份连续性的六个条件。\n"
        "\n"
        "你翻到最后一页。焦痕吞掉了大半，只剩一行半：\n"
        "\n"
        "「综上，身份既非实体亦非过程。身份是——」\n"
        "\n"
        "烧掉了。"),
        [
            {"text": "试着把那半句话写完。", "check": ("逻辑", 11),
             "success": {"narration": "你拿起笔。写了一个字，划掉。又写两个字，划掉。\n"
                         "\n"
                         "四十分钟后纸上有十一种写法。你划掉了十种。\n"
                         "\n"
                         "留下的那一种你看了很久。不确定它对不对。\n"
                         "但它和前四章接得上——语气、节奏、论证方向都接得上。\n"
                         "\n"
                         "你把它抄在焦痕旁边。笔迹和原作者的不一样。\n"
                         "你不知道他写了什么。你只知道你写了什么。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1,
                                "flag:acad_completed": 1}},
             "failure": {"narration": "你拿起笔，写了半行，停住了。\n"
                         "想到的每一种续法都像在替别人签名。\n"
                         "\n"
                         "笔放下了。灯还亮着。残稿也还在破折号那里等着。",
                         "fx": {"skill:逻辑": 1}}},
            {"text": "翻到最后一页的背面——烧书的人有时候会留痕迹。", "check": ("逻辑", 9),
             "success": {"narration": "背面有字。不是手写——打字机打的，格式像公文。\n"
                         "焦痕只留了两行：\n"
                         "\n"
                         "「……依据《城市心智存续计划》验收标准第七条，\n"
                         "本研究涉及受控信息，建议分级封存……」\n"
                         "\n"
                         "你不认识「城市心智存续计划」。但你认识「验收」这个词。\n"
                         "验收的对象不是论文。\n"
                         "\n"
                         "你把这两行抄下来，折好，放进口袋。\n"
                         "灯灭了——不是你关的，是电路保护，深夜自动断电。\n"
                         "你在黑暗里坐了一会儿。",
                         "fx": {"skill:逻辑": 2, "skill:街智": 1,
                                "flag:acad_found_plan": 1}},
             "failure": {"narration": "背面是空的。火烧得很彻底——纸纤维都焦透了，\n"
                         "翻了三遍只看见灰。\n"
                         "\n"
                         "你把残稿放回桌角。",
                         "fx": {"skill:逻辑": 1}}},
            {"text": "合上残稿。灭灯。",
             "effects": {"narration": "你合上残稿，放回桌角。\n"
                         "\n"
                         "灭灯之前你看了一眼窗外。\n"
                         "码头的灯亮着，诊所的灯亮着，稽查站通宵的灯也亮着。\n"
                         "\n"
                         "你关了灯。办公室比窗外先黑。",
                         "fx": {"skill:坚忍": 2, "flag:acad_closed_book": 1}}},
        ], factions=["open"], subs=["学院派"], weight=6,
        req_seen_any={"acad_defense": 1, "night_library": 1},
        voices={"逻辑": "【逻辑】三十七篇论文，十二个结项课题，一场听证——全在绕同一个问题。\n问题本身六个字。答案的位置是空的。"},
        echoes=[
            {"deed": "acad_found_plan", "min": 1,
             "text": "办公室桌上的残稿不见了。桌面上有一个方形的灰印——\n"
                     "纸放太久，灰尘绕着它积了一圈。"},
            {"deed": "acad_completed", "min": 1,
             "text": "你上次写在焦痕旁边的那行字被人用红笔圈了。\n"
                     "旁边有一个批注，字迹不认识：「不对，但接近了。」"},
        ]),
    # ------------------------------------------------ 平权阵线（明焰·街头）
    _ev("front_scales", (
        "阵线的例会在一间租来的仓库里开。\n"
        "墙上贴满了不同年份的标语。有两条挨着，互相矛盾——\n"
        "左边「改不改是个人的事」，右边「个人的事就是所有人的事」。\n"
        "两条都没人撕。\n"
        "\n"
        "今天的议题只有一条：铁锤派昨夜又砸了一间诊所。阵线要不要公开谴责？\n"
        "\n"
        "主持人站在折叠桌后面，桌上放着一只闹钟，每人发言限时三分钟。\n"
        "闹钟是手动的，不含任何电子元件——这是阵线唯一一条没人反对过的规矩：\n"
        "讨论改造权利的会上，器材一律原装。不是因为信仰，是为了不让任何一方觉得被冒犯。\n"
        "\n"
        "你看着那只闹钟想：一个连闹钟都要斟酌的阵营，需要拍桌子的时候拍得下去吗？"),
        [
            {"text": "投谴责票。暴力就是暴力，不需要立场来定义。", "check": ("逻辑", 10),
             "success": {"narration": "你站起来的时候，主持人看了你一眼——不是惊讶，是确认。\n"
                         "像在心里划掉了一个名字旁边的问号。\n"
                         "\n"
                         "「谴责暴力不等于选边。一个人有权选择不改造，\n"
                         "但没有人有权砸掉别人改造的地方。诊所没了，选择就没了。」\n"
                         "\n"
                         "表决的时候你注意到一件事：投谴责票的人里有三个手上有旧伤——\n"
                         "工伤、烫伤、截指。投反对票的人，手都是完整的。",
                         "fx": {"skill:逻辑": 2, "flag:front_line": 1}},
             "failure": {"narration": "你站起来想说话，闹钟响了。三分钟的规矩不看人。\n"
                         "主持人看了你一眼——抱歉，但规矩是规矩。\n"
                         "你坐下了。旁边一个人小声说：\n"
                         "「别在意。这个会上三分钟能改变的东西，比你以为的少。」",
                         "fx": {"skill:逻辑": 1, "flag:front_line": 1}}},
            {"text": "投反对票。阵线的立场是选择权，不是对错。", "check": ("坚忍", 10),
             "success": {"narration": "「阵线的原则是选择权。铁锤派选择了暴力——那是他们的事。\n"
                         "我们的问题是：谴责之后怎么办？\n"
                         "谴责了铁锤，下次飞升螺旋逼人植芯片，我们也得谴责。再下一次，又是谁？\n"
                         "然后我们就不再是中间了，我们变成了仲裁者。\n"
                         "仲裁者需要权力。我们只有一间仓库。」\n"
                         "\n"
                         "有人鼓掌了，稀疏的。不是因为你说得对，\n"
                         "是因为你替他们说出了一种他们需要、但说不出口的犹豫。",
                         "fx": {"skill:坚忍": 2, "flag:front_line": 1}},
             "failure": {"narration": "你的发言引来连续三个人的反驳。他们说你在替暴力辩护。你没有。\n"
                         "但「不谴责」这个词在这间仓库里听起来就是「容忍」。\n"
                         "主持人让大家安静。\n"
                         "你坐着听——那些话都有道理，你的话也有道理。\n"
                         "但诊所还是砸了。",
                         "fx": {"skill:坚忍": 1, "heat": 1, "flag:front_line": 1}}},
            {"text": "弃权。你来这里不是为了投票，是为了看清这屋里的人在怕什么。",
             "effects": {"narration": "你没有举手。主持人记下了你的弃权——\n"
                         "弃权在这个阵营里不算态度，弃权是默认状态。\n"
                         "很多人是带着弃权来到平权阵线的：\n"
                         "他们在别的地方弃了权，然后走到了中间。\n"
                         "\n"
                         "投票结果你没听清。你在看人。\n"
                         "投谴责票的人是站起来投的。投反对票的人举手——能少动就少动。\n"
                         "弃权的人什么都没做。\n"
                         "\n"
                         "三种姿势。三种和信念的距离。",
                         "fx": {"skill:共情": 1, "skill:坚忍": 1, "flag:front_line": 1}}},
        ], factions=["open"], subs=["平权阵线"], weight=10,
        voices={"逻辑": "【逻辑】八比七。你数了三遍。多数只比少数多一个人。在这么小的房间里，多数决和抛硬币差多少？"},
        echoes=[
            {"deed": "front_line", "min": 2,
             "text": "同一间仓库，同一只闹钟。墙上的标语多了一条，但你读不清——\n"
                     "有人把另一条贴在了它上面。也许不是反对，也许只是墙不够了。"},
        ]),
    _ev("front_triage", (
        "码头边的临时义诊点。一间仓库改的，每周开两天。\n"
        "坐诊的人每次不一样——灰港退下来的外科医生，学院派的实习生，\n"
        "不留名字的志愿者（来了就干活，干完就走）。\n"
        "\n"
        "今天排了十七个人。义肢只剩五副：三副腿，一副手臂，一颗人工瓣膜。\n"
        "灰港的货，二手，编号磨掉了，合金成色没问题。\n"
        "\n"
        "分诊规则贴在墙上，一共三个版本，不同年份写的：\n"
        "最早一版「按到达顺序」。中间一版「按医学紧急程度」。最新一版「由值班人员判断」。\n"
        "三个版本都没有被划掉。\n"
        "\n"
        "今天你帮忙分诊。翻到第三张表格时你停住了——\n"
        "九岁男孩，先天心室中隔缺损，需要那颗唯一的瓣膜。\n"
        "母亲填的表。地址栏写的是纯血誓约的社区。"),
        [
            {"text": "按病情排序。心室缺损排第一——出身不是医学指标。", "check": ("逻辑", 9),
             "success": {"narration": "你把十七份病历摊开。心室缺损排第一——不装瓣膜，活不过明年冬天。\n"
                         "值班医生没有看地址栏。也许看了。签字的时候没有犹豫。\n"
                         "\n"
                         "手术在仓库后面的隔间里做的。二手瓣膜，编号磨掉了，尺寸刚好。\n"
                         "男孩醒来的时候叫了一声妈。母亲抱着他。\n"
                         "她的后背绣着纯血誓约的徽章。\n"
                         "\n"
                         "一颗灰港的二手瓣膜，装在纯血社区孩子的心脏里，\n"
                         "在平权阵线的义诊点跳动。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1,
                                "flag:front_line": 1, "flag:front_triage": 1}},
             "failure": {"narration": "你排完序，值班医生改了一个位置——\n"
                         "把一个工伤的码头工人提到了男孩前面。\n"
                         "「码头工明天不装腿就丢饭碗。男孩的瓣膜能等三周——\n"
                         "下一批灰港的货里有心脏配件。」\n"
                         "你想反驳。但医生拿出了排期表：三周后有一批到港。\n"
                         "她在赌。赌灰港的船准时。\n"
                         "\n"
                         "三周后船到了。男孩装上了瓣膜。\n"
                         "那三周你没睡好。",
                         "fx": {"skill:逻辑": 1, "skill:坚忍": 1, "flag:front_line": 1}}},
            {"text": "把地址指给值班医生看。值班的人应该知道全部信息。",
             "check": ("共情", 10),
             "success": {"narration": "值班医生看了地址栏。沉默了五秒——\n"
                         "不是犹豫的五秒，是翻阅的五秒。她在翻自己的记忆。\n"
                         "\n"
                         "「你知道这孩子为什么心室缺损？」\n"
                         "你说不知道。\n"
                         "「先天的，和改造没有任何关系。\n"
                         "但纯血的社区诊所不做心脏手术——心脏手术需要植入物，\n"
                         "植入物违反誓约。所以他们的孩子生下来心脏有问题，就只能——」\n"
                         "\n"
                         "她没说完。她在病历上签了字。\n"
                         "「他是什么人家的孩子不重要。重要的是他的心脏缺一个零件，\n"
                         "而我们手上有这个零件。」",
                         "fx": {"skill:共情": 2, "flag:front_line": 1,
                                "flag:front_triage": 1}},
             "failure": {"narration": "值班医生听完，没有表情。\n"
                         "「你觉得我会因为地址栏拒绝一个孩子？」\n"
                         "你说不是这个意思。她说她知道。\n"
                         "但空气裂了一道缝——义诊点的「义」字不加脚注。",
                         "fx": {"skill:共情": 1, "heat": 1, "flag:front_line": 1}}},
            {"text": "什么都不说。帮男孩的母亲把表格填完——她不太会写字。",
             "effects": {"narration": "母亲不识字。你帮她一栏一栏地填。\n"
                         "填到「过敏史」的时候她说：「青霉素。还有……」\n"
                         "她犹豫了一下：「他爸不让我来。」\n"
                         "\n"
                         "你停了笔。\n"
                         "「他爸说，我们的孩子靠祷告活着。」\n"
                         "她低下头：「但我带他来了。我跟他爸说去买菜。」\n"
                         "\n"
                         "你把「过敏史：青霉素」写完。没有写别的。\n"
                         "表格交给值班医生。医生看了一眼地址，看了一眼过敏史，签了字。\n"
                         "签字这个动作在义诊点每天发生四十次。这一次的笔画重一点。\n"
                         "也许是你的错觉。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1,
                                "flag:front_line": 1, "flag:front_triage": 1}}},
        ], factions=["open"], subs=["平权阵线"], weight=10,
        voices={"共情": "【共情】十七个人，五副义肢。你在做的事有一个哲学名字，叫分配正义。\n哲学课不讲的是：做完之后有十二个人要走回家，而他们的腿还是少一条。"},
        echoes=[
            {"deed": "front_triage", "min": 1,
             "text": "义诊点的墙上钉着一张心电图打印条。波形正常。旁边用铅笔写着一个日期。"},
            {"deed": "front_line", "min": 3,
             "text": "今天排了二十三个人，义肢有七副——比上次多两副。\n"
                     "有人说灰港的船最近勤了一点。你没问为什么。你只负责分诊。"},
            {"deed": "hammer_run", "min": 1,
             "text": "表格上那个社区的名字你认识。\n"
                     "铁锤夜袭集合的时候，你在那条街上走过。"},
            {"deed": "acad_helped_intern", "min": 1,
             "text": "分诊台上的表格换了格式——新表多了一栏「术后追访意愿」。\n"
                     "格式很学院派。铅笔填的。"},
            {"all": [{"deed": "front_triage", "min": 1},
                     {"deed": "harbor_run", "min": 1}],
             "text": "灰港的接货单上有一行加急标注：「瓣膜 ×2，指定交付：码头三号仓库。」\n"
                     "码头三号仓库就是义诊点。那张单子没有签名。"},
        ]),
    _ev("front_scar", (
        "义诊结束后收拾场地。你在叠折叠椅。\n"
        "旁边有个人也在叠——动作很熟练，像是每周都在做这件事。\n"
        "\n"
        "她的袖口在搬椅子的时候滑上去了。\n"
        "左手腕上一道旧疤，灼痕。边缘早就平了，颜色比周围浅一号。\n"
        "你见过这种疤。圣殿派入教仪式留下的。\n"
        "\n"
        "她注意到你在看。没有遮，把袖口卷上去，继续搬椅子。\n"
        "灼痕旁边是更新的痕迹——针眼、消毒水的灼红、搬折叠桌磨出来的茧。\n"
        "旧疤是她身上唯一一道不是自己选的。"),
        [
            {"text": "「你以前是圣殿的人？」", "check": ("共情", 10),
             "success": {"narration": "她把最后一把椅子靠在墙边，坐下来。\n"
                         "不是准备讲故事的坐法，是干活干累了的坐法。\n"
                         "\n"
                         "「十二岁入的教。你知道圣殿的烫法——每个孩子一道，\n"
                         "代表『我选择完整的身体』。十二岁的时候我觉得这句话很漂亮。」\n"
                         "\n"
                         "她看着自己的手腕。\n"
                         "「后来我邻居家有个女孩，七岁，先天瓣膜缺损。\n"
                         "社区里所有人都说『我们的孩子不需要金属活着』。\n"
                         "她需要。但没有人帮她装——不是因为没有瓣膜，是因为共识。\n"
                         "共识不看心电图。」\n"
                         "\n"
                         "她没有继续说。你也没有问后来怎么样了。\n"
                         "如果女孩活下来了，她今天不会在这里叠椅子。\n"
                         "\n"
                         "「没有人杀她。」她的声音很平。「是所有人的沉默杀了她。\n"
                         "沉默有体量。」",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1,
                                "flag:front_line": 1, "flag:front_scar": 1}},
             "failure": {"narration": "「以前是。」她说完这两个字就站起来继续搬桌子了。\n"
                         "你帮她抬了一头。桌子很重——器械箱还压在上面。\n"
                         "搬到角落，她说了一句「谢谢」。\n"
                         "那个「谢谢」不是在回答你。\n"
                         "但它准确地告诉了你一件事：今天她只想搬完桌子回家。",
                         "fx": {"skill:共情": 1, "flag:front_line": 1}}},
            {"text": "「为什么没去掉那个疤？」", "check": ("逻辑", 10),
             "success": {"narration": "她看了一眼手腕。「因为它是真的。」\n"
                         "\n"
                         "你等她解释。她想了想。\n"
                         "「我在圣殿待过，这是事实。我离开了，这也是事实。\n"
                         "这道疤不矛盾。它同时装着这两件事。」\n"
                         "\n"
                         "她又想了想。「你知道这个阵营里的人怎么看这道疤？\n"
                         "他们看见勇气——她离开了另一边。\n"
                         "圣殿的人怎么看？他们看见背叛。同一道疤，两种翻译。」\n"
                         "\n"
                         "她把袖口放下来。\n"
                         "「去掉了就只剩『她走了』。留着，还剩『她来过』。」",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1,
                                "flag:front_line": 1, "flag:front_scar": 1}},
             "failure": {"narration": "「去掉了就少一个话题。」她笑了一下。\n"
                         "你分不清这是玩笑还是真话。",
                         "fx": {"skill:逻辑": 1, "flag:front_line": 1}}},
            {"text": "不问。继续叠椅子。",
             "effects": {"narration": "你没开口。她也没说话。\n"
                         "你们叠了十四把椅子。七把一摞，两摞，靠墙。\n"
                         "\n"
                         "她叠椅子的方式很圣殿——每把椅子对齐，椅腿之间的间距一致，\n"
                         "像抄经时的行距。有些习惯比信仰活得久。\n"
                         "\n"
                         "叠完她哼了一段曲子。你听不出旋律，\n"
                         "但节奏像圣殿晚祷的格律，只是调子改了。\n"
                         "\n"
                         "她走的时候说：「下周见。」\n"
                         "不是客套。是一个每周都来叠椅子的人，\n"
                         "对另一个每周都来的人的确认。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1,
                                "flag:front_line": 1, "flag:front_scar": 1}}},
        ], factions=["open"], subs=["平权阵线"], weight=8,
        req_seen_any={"front_scales": 1, "front_triage": 1}, echoes=[
            {"deed": "front_scar", "min": 1,
             "text": "你在圣殿区路过一扇门。门上挂着旧了的祈祷花环，干枯了，没人取下来。\n"
                     "门牌号码你看了一眼就记住了。你不知道为什么。"},
            {"all": [{"deed": "front_scar", "min": 1},
                     {"deed": "temple_doubt", "min": 1}],
             "text": "圣殿的晚祷上有人在哼一段旋律。你听过——\n"
                     "在义诊点收拾场地的时候，另一个人用不同的调子哼过它。\n"
                     "同一首歌，两个版本。你开始不确定哪个是原版。"},
        ]),
    _ev("front_wall", (
        "清晨。你到仓库的时候，发现墙上多了东西。\n"
        "\n"
        "左墙——喷漆，字迹潦草，笔画有力：\n"
        "「你们的义诊点就是一间改造工厂」\n"
        "没有落款。但那种暴烈的写法你见过，铁锤派的人这样写字。\n"
        "\n"
        "右墙——粘贴的打印纸，排版整齐，每个字都居中：\n"
        "「中间路线是谎言。不全改就是半个人。」\n"
        "这种干净的排版不像街头，更像实验室。\n"
        "\n"
        "仓库门没有锁——阵线从来不锁门，「我们的门对所有人开着」是原则。\n"
        "今天你看着这两面墙想：门对所有人开着，包括来砸门的人。\n"
        "\n"
        "药柜被翻了。不知道是谁翻的。也许是左边来的，也许是右边来的，\n"
        "也许是个普通小偷。中间地带的敌人没有统一制服。"),
        [
            {"text": "两面墙都刷掉。不给任何一方留话柄。", "check": ("坚忍", 9),
             "success": {"narration": "你找到一桶白漆。不是全新的——上次刷墙剩的，桶底还有半指深。\n"
                         "先刷左墙。红漆很厚，字迹在白色底下隐约透出来，像旧伤上的新皮。\n"
                         "刷了三遍才彻底盖住。\n"
                         "\n"
                         "然后刷右墙。打印纸撕下来的时候，胶带带走了一小块墙皮。\n"
                         "白漆补上去，留了一个凹坑。\n"
                         "\n"
                         "两面墙刷完，仓库比以前更白。白到不自然。\n"
                         "有人走进来看了一眼，说：「又刷了？」\n"
                         "「又」这个字告诉你：这不是第一次。",
                         "fx": {"skill:坚忍": 2, "flag:front_line": 1,
                                "flag:front_wall": 1}},
             "failure": {"narration": "白漆不够。你刷完了左墙，右墙只刷了一半。\n"
                         "半面白半面字，比全留着更难看。\n"
                         "下午来开会的人问你为什么只刷了一边。你说漆不够了。\n"
                         "但你听见有人小声说：「他只刷了铁锤那边。」",
                         "fx": {"skill:坚忍": 1, "heat": 1, "flag:front_line": 1}}},
            {"text": "两面墙都留着。让来开会的人自己看。", "check": ("逻辑", 10),
             "success": {"narration": "你什么都没做。开会的人来了，看见两面墙。\n"
                         "有人读完左墙，转身读右墙，然后站在中间，头左右转了两次——\n"
                         "像在看一场网球。\n"
                         "\n"
                         "「今天的议题不用翻了。」你说。「议题在墙上。」\n"
                         "\n"
                         "那天的会开了三个小时。没有人对着左墙辩护，也没有人对着右墙辩护。\n"
                         "他们对着两面墙之间的那段空白辩护。\n"
                         "那段空白大约两臂宽。",
                         "fx": {"skill:逻辑": 2, "skill:共情": 1,
                                "flag:front_line": 1, "flag:front_wall": 1}},
             "failure": {"narration": "你留了两面墙。但开会的时候没人提。所有人都假装没看见。\n"
                         "你一个人在看。\n"
                         "也许他们不是没看见，是看过太多次了。\n"
                         "他们已经学会了和墙上的字住在同一间仓库里。",
                         "fx": {"skill:逻辑": 1, "flag:front_line": 1}}},
            {"text": "在两面墙中间那段空墙上写点什么——但写什么？",
             "effects": {"narration": "你站在空白前面，手里有半桶白漆和一支旧刷子。\n"
                         "左边说你是改造工厂。右边说你是绊脚石。\n"
                         "你想回答。但中间的回答长什么样？\n"
                         "\n"
                         "你举起刷子，想了很久。最后写下一行：\n"
                         "\n"
                         "「这面墙两边都漏风。」\n"
                         "\n"
                         "不是口号，不是辩护，是一个事实——仓库确实漏风，\n"
                         "冬天的风从两边灌进来，两面墙都挡不住。\n"
                         "\n"
                         "但你还是写了。因为两个极端都有现成的句子可以引用，\n"
                         "圣殿有经文，飞升有定理。中间没有。\n"
                         "中间只有你此刻想到的这一句。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1,
                                "flag:front_line": 1, "flag:front_wall": 1}}},
        ], factions=["open"], subs=["平权阵线"], weight=8, echoes=[
            {"deed": "front_wall", "min": 1,
             "text": "仓库的墙又被写了。这次只有左边，右边是空的。\n"
                     "你拿起漆桶的时候发现：漆是满的。上次有人买了一整桶新的。"},
            {"deed": "hammer_run", "min": 1,
             "text": "左墙的喷漆用的是工业红漆。你认识这种漆——\n"
                     "铁锤夜袭的时候用它标记目标建筑。"},
            {"all": [{"deed": "front_wall", "min": 1},
                     {"deed": "hammer_run", "min": 1}],
             "text": "铁锤派的新夜袭路线绕开了码头三号仓库。没有人公开说过为什么。\n"
                     "有些路线的改变不需要命令，只需要一个人记住另一个人的地址。"},
        ]),
    _ev("front_pendulum", (
        "夜。例会散了，所有人都走了。你没走。\n"
        "\n"
        "仓库很空。折叠椅靠在墙边。\n"
        "天花板上一盏灯挂在电线上，风从破窗灌进来，灯在晃。\n"
        "不快不慢，像一只倒过来的钟摆。\n"
        "\n"
        "你坐在地上看它晃。\n"
        "\n"
        "墙上有两行字。不是外面喷的，是阵线自己人在不同的会上写的：\n"
        "\n"
        "「改不改身体是个人的事。」——去年三月\n"
        "「个人的事从来就不只是个人的事。」——今年一月\n"
        "\n"
        "两句都是这个阵营说的。两句都对。两句互相否定。"),
        [
            {"text": "你想：也许中间只是还没选好。犹豫穿上了原则的外套。",
             "effects": {"narration": "你想起投票那天。八比七。\n"
                         "想起义诊的分诊规则——三个版本，没有一个被划掉。\n"
                         "想起这个阵营的闹钟——手动的，连计时工具都要两边不得罪。\n"
                         "\n"
                         "也许中间不是信念。中间是保险。\n"
                         "万一纯血是对的，至少你没改太多，还退得回去。\n"
                         "万一飞升是对的，至少你没拒绝，不算掉队。\n"
                         "\n"
                         "灯还在晃。如果它停下来，会停在哪一边？\n"
                         "物理学说：正中间。\n"
                         "但那是因为没有人在推它。",
                         "fx": {"skill:逻辑": 2, "flag:front_line": 1,
                                "flag:front_pendulum": 1}}},
            {"text": "你想：也许中间就是答案本身。不是因为它对，是因为它诚实。",
             "effects": {"narration": "两个极端都很确定。确定是一种奢侈品——\n"
                         "确定意味着你不需要再问了。\n"
                         "纯血确定身体不可改，依据是信仰。\n"
                         "飞升确定身体应该全改，依据是效率。\n"
                         "信仰不需要证据，效率不需要同意。两种确定都不需要对方。\n"
                         "\n"
                         "中间需要对方。中间要同时容纳两种可能——\n"
                         "不是因为它们都对，是因为你不知道哪个对。\n"
                         "而「不知道」在这个世界上是最不受欢迎的立场：\n"
                         "没有旗帜，没有经文，没有可以引用的句子。\n"
                         "\n"
                         "灯还在晃。\n"
                         "也许摆的意义不在于停在哪里，而在于它还在晃。",
                         "fx": {"skill:共情": 2, "flag:front_line": 1,
                                "flag:front_pendulum": 1}}},
            {"text": "你不想了。灯在晃。仓库很安静。你坐着。",
             "effects": {"narration": "风从破窗进来，从没关好的门出去。\n"
                         "你坐在折叠椅组成的阴影里。\n"
                         "\n"
                         "你不想了。不是因为想通了，\n"
                         "是因为有些问题不是用想的，是用坐的。\n"
                         "\n"
                         "灯还在晃。你注意到一件事：\n"
                         "灯的影子在地上画圆弧，来回，来回。\n"
                         "弧线的两端是那两行字。影子每次都经过两端，但不停留。\n"
                         "它唯一停留的地方是正中间——但只有一瞬，最快的那一瞬。\n"
                         "\n"
                         "经过中间的速度最快。停在中间的时间最短。\n"
                         "你可以经过中间。你不能住在中间。",
                         "fx": {"skill:坚忍": 2, "flag:front_line": 1,
                                "flag:front_pendulum": 1}}},
        ], factions=["open"], subs=["平权阵线"], weight=6,
        req_seen={"front_scales": 1}, req_seen_any={"front_triage": 1, "front_wall": 1},
        voices={"坚忍": "【坚忍】一盏灯在空仓库里晃。没有人看，它还在晃。钟摆不需要观众。"},
        echoes=[
            {"deed": "front_pendulum", "min": 1,
             "text": "仓库的灯换了。新灯不晃了——有人加了一根固定线。\n"
                     "被固定的钟摆还是钟摆吗？也许是。也许只是一盏灯。"},
            {"all": [{"deed": "front_pendulum", "min": 1},
                     {"deed": "mask_null_revealed", "min": 1}],
             "text": "面具沙龙的创始人身上没有任何改造。这间仓库里没有任何答案。\n"
                     "一个改造最深的地方，核心是零。一个追求平衡的地方，核心是空。"},
        ]),
    # ------------------------------------------------ 群智派（飞升螺旋·向内）
    _ev("swarm_sync", (
        "合流试运行之后的第三周。群智派给你安排了一对一校准——正式编入节点前的最后一步。\n"
        "\n"
        "房间很小。两把椅子，一根线缆，一盏灯。\n"
        "你的校准搭档坐在对面，义眼虹膜是旧型号的铜色。她在合流里待了十一年。\n"
        "\n"
        "「校准不是考试。」她把线缆递给你，「你不需要告诉我任何事。\n"
        "想一件东西，让线缆把形状传过来。不是内容——是形状。」\n"
        "\n"
        "她每个词的重量一样。十一年的共享思维磨掉了某些东西——不是情感，是着重号。"),
        [
            {"text": "接入，完全打开——你想知道「形状」到底是什么意思。",
             "check": ("电子直觉", 9),
             "success": {"narration": "没有画面，没有声音。\n"
                         "\n"
                         "先是温度——不是皮肤的温度，是思维的温度。\n"
                         "她的意识比你预想的凉。不是冷。是深水的那种静。\n"
                         "\n"
                         "然后你感觉到了自己。不是照镜子——是从她的位置看过来的你。\n"
                         "你的思维是热的，边缘毛糙，形状不规则。\n"
                         "你第一次知道自己想东西的时候长什么样。\n"
                         "\n"
                         "二十分钟。结束后她拔掉线缆：「你的噪声很大。不是坏事。」",
                         "fx": {"skill:电子直觉": 2, "skill:共情": 1,
                                "flag:swarm_opened": 1}},
             "failure": {"narration": "前三秒什么都没有。第四秒，一段你没有选的记忆自己跑了出去。\n"
                         "\n"
                         "她看了你一眼——不是惊讶，是见过。\n"
                         "\n"
                         "「关掉。」你关了。\n"
                         "记忆收回来了，但你花了十分钟才确认它还是原来的形状。",
                         "fx": {"skill:电子直觉": 1}}},
            {"text": "接入，但只放出你选的部分。你的边界你自己守。", "check": ("坚忍", 9),
             "success": {"narration": "你挑了一段记忆：码头的雨。\n"
                         "安全的，没有人的，只有水和铁锈的气味。\n"
                         "\n"
                         "线缆那头沉默了五秒。然后她笑了——\n"
                         "不是嘴在笑，是传过来的情绪里有一个笑的形状。\n"
                         "\n"
                         "「你给我看了一段风景。但你把站在雨里的人剪掉了。」\n"
                         "\n"
                         "你没有回答。她说得对。\n"
                         "\n"
                         "「没关系。校准不需要看见你。只需要看见你的剪刀。」",
                         "fx": {"skill:坚忍": 2, "flag:swarm_guarded": 1}},
             "failure": {"narration": "你放出一段精心挑选的记忆。线缆那头三分钟没有回应。\n"
                         "\n"
                         "她拔掉线缆：「你给我的每一样东西都是包装过的。\n"
                         "合流不接受包装。带宽不够。」\n"
                         "\n"
                         "校准评定：待复检。",
                         "fx": {"skill:坚忍": 1, "flag:swarm_blocked": 1}}},
            {"text": "接入，只听。不给。",
             "effects": {"narration": "你只听。\n"
                         "\n"
                         "十一年的合流把她的思维磨成了一种你没见过的质地——\n"
                         "每一条边界都被反复确认过，确认到透明。\n"
                         "像一块打磨过的玻璃：看不见它，但光通过的时候改变了角度。\n"
                         "\n"
                         "校准结束。她拔掉线缆：「你什么都没给。」\n"
                         "\n"
                         "你说是。\n"
                         "\n"
                         "「也行。但你得知道——只听不给的人，在合流里有个名字。」\n"
                         "\n"
                         "她没有告诉你那个名字。",
                         "fx": {"skill:电子直觉": 1, "skill:街智": 1,
                                "flag:swarm_listened": 1}}},
        ], factions=["ascension"], subs=["群智派"], weight=10,
        voices={"电子直觉": "【电子直觉】她的思维没有重音。十一年的共享把强调磨平了——每个念头等重。\n这让她非常难读。也非常难骗。"},
        echoes=[
            {"seen": "swarm_sync", "min": 2,
             "text": "校准室的椅子换了。你坐下去的一瞬间，身体已经在调整姿势了。"},
            {"deed": "swarm_opened", "min": 1,
             "text": "你路过校准室，门关着。里面有两个新人在校准。\n"
                     "你停了一秒——你听见了他们的信号。线缆没有接到你身上。"},
        ]),
    _ev("swarm_ghost", (
        "紧急会议。合流里一位成员的肉身脑死亡已经十九天。生命维持还在运转。\n"
        "\n"
        "但她的节点还活着。不是缓存——是在运算。\n"
        "十九天里，这个节点回应了三十七次会议投票、两次技术咨询，\n"
        "还替一位新成员做了校准。合流说它的响应模式和脑死亡前完全一致。\n"
        "\n"
        "家属来了。她丈夫坐在会议室角落，手里攥着一份司法鉴定书：脑死亡，不可逆。\n"
        "\n"
        "导师打开频谱图。屏幕上，她的节点稳定地闪烁——和其余节点完全同步。\n"
        "\n"
        "「合流认为她在。」导师说。\n"
        "丈夫抬头：「她不在。」"),
        [
            {"text": "站在家属那边。身体死了就是死了——节点是回声，不是人。",
             "check": ("共情", 9),
             "success": {"narration": "你站起来。\n"
                         "\n"
                         "「十九天里，这个节点投了三十七次票。\n"
                         "但她生前的投票记录——每次投票前都会犹豫六到八秒。\n"
                         "节点一次都没犹豫过。」\n"
                         "\n"
                         "你把数据调出来。导师盯着屏幕看了很久。\n"
                         "\n"
                         "「犹豫不是延迟。犹豫是一个人在权衡。\n"
                         "不犹豫的节点在执行——它在用她的模式运算，\n"
                         "但没有人在运算里权衡。」\n"
                         "\n"
                         "丈夫签了关停协议。\n"
                         "节点关闭的瞬间，合流里其余节点的频率抖了一下——\n"
                         "像一张网被剪掉了一根线。",
                         "fx": {"skill:共情": 2, "skill:逻辑": 1,
                                "flag:swarm_sided_body": 1}},
             "failure": {"narration": "你说了几句，导师摇头：「你用感情在论证。\n"
                         "但这间屋子讨论的是定义——什么叫死。感情不回答定义。」\n"
                         "\n"
                         "丈夫看着你。你在他眼睛里看见了一种不需要定义的东西。\n"
                         "会议不收那种东西做论据。",
                         "fx": {"skill:共情": 1, "flag:swarm_body_failed": 1}}},
            {"text": "站在合流那边。模式在就是人在——基底不重要。", "check": ("逻辑", 9),
             "success": {"narration": "「她的节点在过去十九天里通过了所有一致性检验。\n"
                         "它的响应不是回放——是生成。\n"
                         "如果一个能学习、能响应、能参与决策的模式不算活着，\n"
                         "那合流里其余人凭什么算？」\n"
                         "\n"
                         "你把话停在这里。\n"
                         "\n"
                         "导师做了记录。丈夫起身走了。走到门口他回过头——\n"
                         "不是看你，是看屏幕上那个还在闪烁的点。\n"
                         "\n"
                         "生命维持继续运转。节点继续投票。三个月后，丈夫不再来了。",
                         "fx": {"skill:逻辑": 2, "flag:swarm_sided_pattern": 1}},
             "failure": {"narration": "「模式在就是人在？」丈夫站起来，\n"
                         "「那她为什么不接我的电话？我每天打。节点不接电话。\n"
                         "你们的合流会投票、会运算——但它不会接一个电话。」\n"
                         "\n"
                         "你没有答上来。不是因为逻辑不够。\n"
                         "是因为那个问题不在逻辑的管辖范围内。",
                         "fx": {"skill:逻辑": 1, "flag:swarm_pattern_failed": 1}}},
            {"text": "直接问那个节点——如果它还是她，它应该能回答一个只有她才会答的问题。",
             "check": ("电子直觉", 10),
             "success": {"narration": "你接入合流，找到她的节点。信号稳定，模式清晰。\n"
                         "\n"
                         "你没有问密码，没有问个人信息。你问的是：「你怕不怕？」\n"
                         "\n"
                         "七秒。合流里所有人都听见了这七秒的沉默。\n"
                         "\n"
                         "然后节点回答了。答案不是语言——是一种情绪的形状。\n"
                         "你没法翻译它。但你可以描述：它像一个在水底睁着眼的人。\n"
                         "看得见水面，够不到。不挣扎。也不闭眼。\n"
                         "\n"
                         "你把这个描述念给会议听。没有人说话。\n"
                         "散会的时候，丈夫在门口站了一会儿。\n"
                         "关停协议还在桌上。没有人签。",
                         "fx": {"skill:电子直觉": 2, "skill:共情": 1,
                                "flag:swarm_asked_node": 1}},
             "failure": {"narration": "你接入合流，找到她的节点。你问了一个问题。\n"
                         "\n"
                         "节点没有回答。不是拒绝——是那种试图回答但找不到路的停顿。\n"
                         "像一个人站在门后面，听见了敲门声，但忘了门怎么开。\n"
                         "\n"
                         "你退出来。",
                         "fx": {"skill:电子直觉": 1, "flag:swarm_node_silent": 1}}},
        ], factions=["ascension"], subs=["群智派"], weight=10,
        voices={"电子直觉": "【电子直觉】频谱图上她的节点和其余人完全同步。但你注意到一个细节：\n她的信号从不主动发起。只回应，不发问。活人会发问。"},
        echoes=[
            {"deed": "swarm_sided_body", "min": 1,
             "text": "会议室角落的那把椅子上放着一束花。花是合金做的。不会枯。"},
            {"deed": "swarm_sided_pattern", "min": 1,
             "text": "导师办公室门口贴了一份新章程。标题是「心智存续定义修订案」。\n"
                     "下面一行小字：「本案由一次争议推动。争议尚未裁定。」"},
            {"deed": "swarm_asked_node", "min": 1,
             "text": "你接入合流的时候，在网络边缘感觉到了一个微弱的信号。\n"
                     "它什么都不做——只是在那里。像一个人坐在窗边，看着外面。"},
        ]),
    _ev("swarm_count", (
        "年度清点。每年一次，合流全网做一遍完整的节点核查。\n"
        "\n"
        "你被分到第三扇区——一百四十二个注册节点，\n"
        "逐一核对身份签名、活跃状态、带宽占用。枯燥的活。\n"
        "\n"
        "第一遍清点，一百四十二个节点全部在线。你签字，准备提交。\n"
        "\n"
        "然后你看见了总表。三个扇区加起来，注册节点四百一十七个。\n"
        "活跃信号四百三十一个。\n"
        "\n"
        "多了十四个。\n"
        "\n"
        "「是回声。」导师扫了一眼，「深度合流会留下残余信号。每年都有几个，修一修就没了。」\n"
        "\n"
        "你看着那十四个信号的频谱。不像回声——回声会衰减。\n"
        "这些信号的振幅稳定，带宽占用恒定。"),
        [
            {"text": "正式报告差异。十四个不明信号，逐一记录，提交安全委员会。",
             "check": ("逻辑", 8),
             "success": {"narration": "你的报告详细到让安全委员会开了一次专会。\n"
                         "十四个信号的特征你全列了：频率分布、活跃周期、与周围节点的交互模式。\n"
                         "\n"
                         "委员会的结论：其中九个确认为残余信号，建议清除。\n"
                         "\n"
                         "剩下五个，没有给结论。\n"
                         "只在报告末尾写了一行：「待进一步观察。请勿清除。」\n"
                         "\n"
                         "你问导师那五个是什么。导师说：「该你清点的，你都清点完了。」",
                         "fx": {"skill:逻辑": 2, "flag:swarm_reported": 1}},
             "failure": {"narration": "你的报告被退回。批注一行字：\n"
                         "「残余信号的清理流程已在年度维护中覆盖。无需单独立项。」\n"
                         "\n"
                         "维护流程跑完之后，你又查了一次。十四个信号还在。",
                         "fx": {"skill:逻辑": 1, "flag:swarm_report_denied": 1}}},
            {"text": "不报告。自己去听——试着接入其中一个不明信号。",
             "check": ("电子直觉", 11),
             "success": {"narration": "你挑了十四个里振幅最稳的那一个。\n"
                         "接入的时候做了隔离——只用一条旁路，不经过主节点。\n"
                         "\n"
                         "信号很清晰。不是残余，不是回声。\n"
                         "是一段完整的、正在运行的思维模式。\n"
                         "\n"
                         "你没法读懂它的内容——编码方式和你的不一样，像一种更老的协议。\n"
                         "但你能感觉到它的节奏：缓慢、均匀、没有停顿。\n"
                         "像一台一直开着的机器在等待指令。\n"
                         "\n"
                         "你断开了。那个信号在你离开之后还在运行。",
                         "fx": {"skill:电子直觉": 2, "skill:街智": 1,
                                "flag:swarm_touched_signal": 1}},
             "failure": {"narration": "你接入的瞬间，信号消失了。不是断开——是收缩。\n"
                         "像一个人听见脚步声，把灯关了。\n"
                         "\n"
                         "你退出来，回去看总表。十四个信号变成了十三个。",
                         "fx": {"skill:电子直觉": 1, "heat": 1,
                                "flag:swarm_signal_fled": 1}}},
            {"text": "去找最老的成员问——这种事以前发生过吗？", "check": ("共情", 8),
             "success": {"narration": "最老的成员住在三楼。房间里没有家具——她用不上了。\n"
                         "身体的九成以上是合金和碳纤维，只剩颅腔里一小块灰色的东西。\n"
                         "\n"
                         "「每年都多。」她说。义眼对焦很慢，对焦之后看你的目光很稳。\n"
                         "\n"
                         "「我入网二十三年。第一年多了两个。去年多了十四个。\n"
                         "每年都多。从来没少过。」\n"
                         "\n"
                         "你问它们是什么。\n"
                         "\n"
                         "她沉默了很久。然后说了一个你没听过的词：「底册。」\n"
                         "\n"
                         "她没有解释。你也没有追问。你记住了。",
                         "fx": {"skill:共情": 2, "skill:逻辑": 1,
                                "flag:swarm_heard_roster": 1}},
             "failure": {"narration": "最老的成员不在房间。门虚掩着，房间是空的——\n"
                         "但你感觉到了信号。很微弱，从墙壁里传来。\n"
                         "\n"
                         "她在合流里。你不确定她还会不会回到这个房间。",
                         "fx": {"skill:共情": 1, "flag:swarm_elder_absent": 1}}},
        ], factions=["ascension"], subs=["群智派"], weight=8,
        voices={"电子直觉": "【电子直觉】回声会衰减。这些信号不衰减。\n要么有人在维护它们，要么它们在维护自己。两种可能都不在年报里。"},
        echoes=[
            {"seen": "swarm_count", "min": 2,
             "text": "年度清点的总表贴在公告栏。注册节点的数字打印得很大。\n"
                     "下面有一行手写的小字，被人用记号笔涂掉了。"},
            {"deed": "swarm_touched_signal", "min": 1,
             "text": "你路过清点室，门锁着。你停下脚步——不是因为声音，\n"
                     "是因为你的旁路接口自己醒了。\n"
                     "有什么东西在门后面轻轻地、均匀地运转。"},
            {"deed": "swarm_heard_roster", "min": 1,
             "text": "公告栏的总表旁边多了一张便条，字迹很老：\n"
                     "「底册不是秘密。底册是地基。不要挖地基。」"},
        ]),
    _ev("swarm_vote", (
        "全体表决。议题：改革合流的投票权重。\n"
        "\n"
        "现行规则按「融合度」分配权重——在合流里的时间越长、接入越深、\n"
        "共振频率越高，你的一票越重。最重的节点一票顶十二票。\n"
        "\n"
        "今天的提案：所有节点一人一票，不计融合度。\n"
        "\n"
        "提出这个提案的人是网络里最轻的节点——校准完成不到半年。\n"
        "他的一票，在现行规则下，等于零点三票。\n"
        "\n"
        "表决方式：现行规则。用不平等的权重来投票决定要不要平等。"),
        [
            {"text": "指出矛盾。用弯的尺子量自己弯不弯，结果不管怎样都是尺子说了算。",
             "check": ("逻辑", 10),
             "success": {"narration": "「你们在用一把弯的尺子量自己弯不弯。」\n"
                         "\n"
                         "会场安静了。\n"
                         "\n"
                         "「用现行权重投票，通过的概率不到百分之三——\n"
                         "权重最高的十二个节点占了全网票额的四成。\n"
                         "这次投票只有两种结果：不过，说明规则保护了自己；\n"
                         "过了，说明最高权重的人主动放弃了特权——而那需要奇迹。」\n"
                         "\n"
                         "导师在记录本上写了很久。最后抬起头：「你没有说该怎么办。」\n"
                         "\n"
                         "「先把矛盾说出来。不说出来，投什么都是在弯尺子上划刻度。」\n"
                         "\n"
                         "表决推迟了。推迟本身是用现行权重投票决定的。",
                         "fx": {"skill:逻辑": 2, "skill:威慑": 1, "heat": 1,
                                "flag:swarm_contradiction": 1}},
             "failure": {"narration": "你的论证被一位资深节点拦下：\n"
                         "「你说的矛盾，每一种规则改革都有。旧宪法也是用旧程序废止的。\n"
                         "你要的不是完美的程序——你要的是不做决定。」\n"
                         "\n"
                         "他说得不算错。你坐下了。",
                         "fx": {"skill:逻辑": 1}}},
            {"text": "提一个折中方案——通过一项日落条款：权重在三年内线性归一。",
             "check": ("威慑", 9),
             "success": {"narration": "「不需要今天就一人一票。今天只需要通过一条：\n"
                         "从明天起，权重差距每季度缩小一档。三年后自动归一。」\n"
                         "\n"
                         "提案者看着你。资深节点也看着你。两边都在算——这个方案是谁赢了。\n"
                         "\n"
                         "答案是谁都没赢。三年后大家一样。\n"
                         "但三年里，权重高的人仍然比别人多说了话。\n"
                         "\n"
                         "表决通过。用的是现行权重。",
                         "fx": {"skill:威慑": 2, "skill:逻辑": 1,
                                "flag:swarm_sunset": 1}},
             "failure": {"narration": "「三年太长了。」提案者说。\n"
                         "「三年太短了。」资深节点说。\n"
                         "\n"
                         "你的方案死在两个方向的夹击里。",
                         "fx": {"skill:威慑": 1, "flag:swarm_sunset_failed": 1}}},
            {"text": "弃权。你的权重本身就是问题的一部分。",
             "effects": {"narration": "你没有举手。\n"
                         "在融合度加权的表决里，弃权不是零——\n"
                         "你的权重从总票额里扣掉了，其余人的每一票相对更重。\n"
                         "\n"
                         "你的弃权改变了比例。\n"
                         "\n"
                         "投完票之后你算了一遍：如果你投了赞成，提案会以两个百分点通过。\n"
                         "你的弃权让它以一个百分点落败。",
                         "fx": {"skill:逻辑": 2, "flag:swarm_abstained": 1}}},
        ], factions=["ascension"], subs=["群智派"], weight=8,
        voices={"逻辑": "【逻辑】一把弯尺子量出来的数字是真的还是假的？都不是。它是弯的。"},
        echoes=[
            {"deed": "swarm_abstained", "min": 1,
             "text": "公告栏贴着上一次表决的记录。通过率和否决率保留了小数点后两位。\n"
                     "最后一行标注着弃权者的权重总额——比你记忆中的大。"},
            {"deed": "swarm_contradiction", "min": 1,
             "text": "导师的记录本翻开放在桌上。你看见了自己那段话被人抄了一遍，\n"
                     "旁边批注两个字：「成立。」"},
        ]),
    _ev("swarm_floor", (
        "深夜。你在合流的同步舱里做例行维护——\n"
        "清理缓存、校验接口、检查带宽分配。\n"
        "\n"
        "这个时间合流几乎静默。四百多个节点只有不到二十个还醒着。\n"
        "网络本底应该是一片均匀的灰。\n"
        "\n"
        "你切到底层频段做噪声扫描。\n"
        "\n"
        "不均匀。\n"
        "\n"
        "灰里有一条线。不是干扰——干扰是尖的，这条线很平，振幅恒定，频率精确。\n"
        "你放大了看：不是一条线，是一组信号。编码方式不是当前合流协议。\n"
        "\n"
        "你查了合流的版本历史。这组信号的编码比合流最早的版本还要旧。"),
        [
            {"text": "顺着信号往上游追。它从哪来，通向哪里。", "check": ("电子直觉", 11),
             "success": {"narration": "你花了四十分钟沿着信号走。\n"
                         "它不在合流的网络拓扑图上——它在拓扑图的下面。像地板下面的管道。\n"
                         "\n"
                         "信号通向一个你从未见过的地址空间。地址格式和合流完全不同。\n"
                         "你进不去。但你在入口读到了一行元数据：\n"
                         "\n"
                         "「存续节点 #0。状态：监听。权限：只读。」\n"
                         "\n"
                         "你把地址抄下来。退出维护模式。\n"
                         "同步舱的灯在你退出的时候闪了一下。",
                         "fx": {"skill:电子直觉": 2, "skill:逻辑": 1,
                                "flag:swarm_found_node_zero": 1},
                         "extra": [{"deed": "acad_found_plan", "min": 1, "now": True,
                                    "text": "\n「存续」这个词你见过。\n"
                                            "烧掉的那半章残稿，背面，公文体，验收标准第七条。"}]},
             "failure": {"narration": "你顺着信号走了十分钟，走到了一堵墙。\n"
                         "不是物理的墙——是权限壁垒。你的维护权限在这里到头了。\n"
                         "\n"
                         "信号从墙的另一边传过来。稳定、均匀、不理会你。\n"
                         "\n"
                         "你退回来。维护日志被自动标记了一条备注：「越界访问尝试已记录。」",
                         "fx": {"skill:电子直觉": 1, "heat": 1,
                                "flag:swarm_hit_wall": 1}}},
            {"text": "不追。调出合流的底层架构文档，看看这组信号有没有被记录过。",
             "check": ("逻辑", 9),
             "success": {"narration": "底层架构文档在第七层存档里。你翻了三个小时。\n"
                         "大部分是技术规格——带宽分配、节点上限、协议版本。\n"
                         "\n"
                         "在最底下，你找到了一份没有编号的附录。标题四个字：「基底协议」。\n"
                         "\n"
                         "内容很短：合流网络的底层不是群智派建的。\n"
                         "群智派接手的时候，底层已经在运行了。\n"
                         "基底协议的制定者栏是空的。日期栏写着「先于本网络」。\n"
                         "\n"
                         "附录的最后一行：\n"
                         "「基底协议不可修改。原因参见《城市心智存续计划》技术附件。」\n"
                         "\n"
                         "你翻遍了整个存档。技术附件不在这里。",
                         "fx": {"skill:逻辑": 2, "skill:街智": 1,
                                "flag:swarm_found_protocol": 1}},
             "failure": {"narration": "底层架构文档比你预想的短——大部分页面标注着「权限不足」。\n"
                         "你看见了文档的目录，但只有前三章能打开。\n"
                         "第四章叫「基底」。打不开。\n"
                         "\n"
                         "你关了文档。窗外天快亮了。",
                         "fx": {"skill:逻辑": 1}}},
            {"text": "关掉扫描。退出维护模式。有些底噪就是底噪。",
             "effects": {"narration": "你关了频谱图。底噪回到灰色。\n"
                         "\n"
                         "退出同步舱的时候走廊很安静。\n"
                         "你的脚步声在金属地板上响了很久。\n"
                         "\n"
                         "走了二十步之后你停下来。不是因为什么声音——是因为没有声音。\n"
                         "同步舱关门之后，你的脑子里应该是安静的。\n"
                         "\n"
                         "不安静。底噪还在。不是同步舱的底噪——是你自己的。\n"
                         "你接入合流太久了，底噪已经住进了你的基线。",
                         "fx": {"skill:坚忍": 2, "flag:swarm_closed_scan": 1}}},
        ], factions=["ascension"], subs=["群智派"], weight=6,
        req_seen_any={"swarm_sync": 1, "asc_merge_trial": 1},
        voices={"电子直觉": "【电子直觉】底噪比合流早。合流建在底噪上面——\n像一座城市建在另一座城市的遗址上。住在上面的人管地基叫噪声。"},
        echoes=[
            {"deed": "swarm_hit_wall", "min": 1,
             "text": "同步舱的底噪扫描界面多了一个选项：「隐藏基底频段」。默认勾选。"},
            {"deed": "swarm_found_node_zero", "min": 1,
             "text": "你路过同步舱，里面没有人。\n"
                     "但你的旁路接口感觉到了一个信号——极微弱、极稳定，从地板下面传上来。\n"
                     "它没有在叫你。它不叫任何人。它只是在那里。"},
        ]),
    # ------------------------------------------------ 播种者（飞升螺旋·向外）
    _ev("seed_compress", (
        "压缩实验室在地下二层。墙上刷着一行字：「每一克带宽都是信仰。」\n"
        "\n"
        "志愿者坐在连接椅上，脑后的接口亮着蓝光。她等这个位子等了七年。\n"
        "今天是压缩测试——把一整个人的心智塞进探针的存储舱。\n"
        "\n"
        "算法跑到百分之九十七卡住了。剩下的百分之三——技术员说大约是一段童年、\n"
        "半套手艺、和某种气味的关联——与其余部分缠得太深，强行裁剪会损坏周围的记忆网络。\n"
        "\n"
        "「三种裁法。」技术员把代价逐项说完，终于抬起头，却没有看你。\n"
        "\n"
        "「决定权在她。我们只负责执行。」\n"
        "\n"
        "志愿者望向你。她的手没有抖。\n"
        "\n"
        "「帮我把代价说清楚。」她说，「别替我挑。」"),
        [
            {"text": "先说清失去技能层意味着什么；如果她愿意，再帮她确认裁剪边界。", "check": ("巧手", 8),
             "success": {"narration": "你在旁边核对裁剪参数——你做过类似的活，\n"
                         "知道技能层和程序记忆之间的接缝在哪里。\n"
                         "\n"
                         "裁剪干净。她的手艺从副本里消失了。\n"
                         "\n"
                         "压缩完成。百分之百。她从椅子上站起来的时候说：\n"
                         "「到了之后我第一件事就是学修东西。」\n"
                         "\n"
                         "你没有告诉她——重新学的手艺和原来的不一样。\n"
                         "用同一双手，走不同的弯路，犯不同的错误。",
                         "fx": {"skill:巧手": 2, "flag:seed_cut_skill": 1}},
             "failure": {"narration": "技能层和程序记忆缠得比你预想的紧。\n"
                         "你下刀的位置偏了半毫米——技能切掉了，带走了一小块不该走的东西。\n"
                         "\n"
                         "技术员跑了两遍校验：「不影响核心人格。\n"
                         "但她到了之后——可能不太记得怎么系鞋带。」\n"
                         "\n"
                         "她坐在椅子上听完了。然后说：「那我到了之后穿不用系的鞋。」",
                         "fx": {"skill:巧手": 1, "flag:seed_cut_too_much": 1}}},
            {"text": "先说清失去感官映射意味着什么；如果她愿意，再陪她记住最后一次雨味。", "check": ("坚忍", 9),
             "success": {"narration": "感官映射比技能层容易切。嗅觉在记忆网络里的权重出奇地高——\n"
                         "但连接点少，像一根独立的根。\n"
                         "\n"
                         "你替技术员确认了裁剪边界。干净的一刀。\n"
                         "\n"
                         "三年后探针传回第一批数据。其中有一段文字记录：\n"
                         "「空气里有什么东西。传感器告诉我成分，但我不知道它闻起来像什么。\n"
                         "我发明了一个词来描述这种缺——『空鼻子』。这个词很蠢。但我每天都用。」",
                         "fx": {"skill:坚忍": 2, "flag:seed_cut_scent": 1}},
             "failure": {"narration": "嗅觉的根扎得比图谱显示的深。切掉之后，她的情绪基线偏移了两个标准差。\n"
                         "技术员说正常范围内。\n"
                         "你看了一眼她的面部扫描——眼睛没有变，但眼神里少了一样东西。",
                         "fx": {"skill:坚忍": 1, "flag:seed_scent_shifted": 1}}},
            {"text": "把三种代价全部摊开，不给建议。让她自己选。", "check": ("共情", 9),
             "success": {"narration": "许多事是不言自明的：技能可以重新学，但她要去的地方不会再有雨。\n"
                         "你只把三种代价逐项说完，包括百分之九十七在长途传输中的风险。\n"
                         "她最终选择带着风险上路。\n"
                         "压缩以百分之九十七封装。缺口填了零——到达之后，那些零会像伤疤一样留在她的认知地图上。"
                         "但这一次，伤疤的位置是她自己选的。",
                         "fx": {"skill:共情": 2, "skill:坚忍": 1,
                                "flag:seed_accepted_gap": 1}},
             "failure": {"narration": "你把三种代价逐项说完，技术员随即调出模拟曲线：曲线显示，长途传输会放大缺口。"
                         "到了之后，少的可能不止是百分之三。\n"
                         "志愿者看着已经亮起七年的候选编号。沉默很久后，她选了砍感官映射。\n"
                         "你尊重她的决定。但你无法确定，作出决定的是她，还是那七年已经无法退还的等待。",
                         "fx": {"skill:共情": 1, "flag:seed_gap_rejected": 1}}},
        ], factions=["ascension"], subs=["播种者"], weight=10,
        voices={"巧手": "【巧手】百分之三。一条腿大概是百分之几？一段童年大概是百分之几？\n裁剪参数不回答这种问题。但裁剪参数决定答案。"},
        echoes=[
            {"deed": "seed_accepted_gap", "min": 1,
             "text": "压缩实验室墙上的字换了。新的写着：「不完整不是损坏。」\n"
                     "旧的被刮掉了，还看得见痕迹。"},
            {"deed": "seed_cut_scent", "min": 1,
             "text": "你路过一个雨天的码头。空气里有一种气味。你停下来——\n"
                     "你闻到了，她闻不到了。这个想法来得毫无预兆。"},
        ]),
    _ev("seed_farewell", (
        "发射日。\n"
        "\n"
        "探针固定在发射架上，外壳已经密封。志愿者——原本——站在观测台上。\n"
        "探针里的副本已经通电。\n"
        "\n"
        "播种者的规矩：发射前十二分钟，让副本醒来。原本和副本之间有一道玻璃。\n"
        "十二分钟之后探针发射，此生再无交集。\n"
        "\n"
        "你是今天的送行官。你站在两人之间，手里拿着倒计时器。\n"
        "\n"
        "副本醒了。她眨了眨眼——和原本一模一样的眨法。\n"
        "然后她看见了玻璃另一边的自己。\n"
        "\n"
        "原本先开口了：「你怕吗？」\n"
        "副本想了想：「你呢？」"),
        [
            {"text": "安静地守着。送行官的职责是在场，不是说话。", "check": ("坚忍", 8),
             "success": {"narration": "十二分钟。你一句话都没有说。\n"
                         "\n"
                         "她们说了很多——大部分你听不见，隔着玻璃的嘴形。\n"
                         "你看见原本哭了两次，副本哭了一次。\n"
                         "副本的第一次哭和原本的第一次哭一模一样——同一个表情，同一个擦泪的动作。\n"
                         "原本的第二次哭，是看见副本复刻了自己的哭法之后。\n"
                         "\n"
                         "倒计时器响了。你举起手。原本背过身去。副本没有。\n"
                         "\n"
                         "探针发射的光照亮了观测台。\n"
                         "原本的影子被拉得很长，朝着探针飞走的反方向。",
                         "fx": {"skill:坚忍": 2, "skill:共情": 1,
                                "flag:seed_watched": 1}},
             "failure": {"narration": "你没能守住沉默。第九分钟你说了一句——\n"
                         "什么话你自己都记不清，大概是「时间快到了」。\n"
                         "\n"
                         "原本和副本同时看了你一眼——同一个角度，同一种不满。\n"
                         "你打断了她们最后的对话。你不知道打断的是哪一句。",
                         "fx": {"skill:坚忍": 1, "flag:seed_broke_silence": 1}}},
            {"text": "替原本问一句她不敢问的——你到了之后，还会想我吗？",
             "check": ("共情", 9),
             "success": {"narration": "你把嘴凑近玻璃：「她想问你——到了之后，你还会想她吗？」\n"
                         "\n"
                         "副本看了原本一眼。原本没有点头也没有摇头。\n"
                         "\n"
                         "「我带着她所有的记忆走。」副本说，「所以我会想。\n"
                         "但想的是出发前的她。到了之后她又过了几十年——那个她，我不认识了。」\n"
                         "\n"
                         "原本笑了一下。很短。",
                         "fx": {"skill:共情": 2, "flag:seed_asked_for_her": 1}},
             "failure": {"narration": "你问了。副本看着你看了很久：\n"
                         "「你替她问的，还是替你自己问的？」\n"
                         "\n"
                         "你没有回答。",
                         "fx": {"skill:共情": 1, "flag:seed_question_bounced": 1}}},
            {"text": "检查一遍探针的最终参数。十二分钟也是最后的校验窗口。",
             "check": ("巧手", 8),
             "success": {"narration": "你打开校验终端。十二分钟够跑一次快速诊断。\n"
                         "\n"
                         "第七分钟你发现了一个问题：导航模块的零点校准有微小偏移。\n"
                         "不影响发射，但会在四十年的飞行中累积——到达时误差大约三百公里。\n"
                         "三百公里，在一颗没有地标的星球上，\n"
                         "可能是什么都没有和唯一一片适合着陆的地方之间的区别。\n"
                         "\n"
                         "你修了。修好之后抬起头——最后两分钟，\n"
                         "原本和副本隔着玻璃把手贴在一起。\n"
                         "你看着她们的手。一模一样的掌纹。",
                         "fx": {"skill:巧手": 2, "skill:逻辑": 1,
                                "flag:seed_fixed_nav": 1}},
             "failure": {"narration": "校验一切正常。你关了终端，发现十二分钟已经过去了八分钟。\n"
                         "抬头看她们——原本在哭，副本在笑。\n"
                         "你分不清谁的表情才是该有的那个。",
                         "fx": {"skill:巧手": 1, "flag:seed_checked_clear": 1}}},
        ], factions=["ascension"], subs=["播种者"], weight=10,
        voices={"坚忍": "【坚忍】十二分钟不是告别。告别是一瞬间的事。\n十二分钟是给原本练习的——练习「走了」这两个字的重量。"},
        echoes=[
            {"seen": "seed_farewell", "min": 2,
             "text": "观测台的玻璃换了。新的比旧的薄。\n"
                     "你不知道这是为了让声音传得更清，还是因为旧的碎了。"},
            {"deed": "seed_watched", "min": 1,
             "text": "你又站在了送行官的位置。手里的倒计时器是旧的——上面有你握过的印子。"},
        ]),
    _ev("seed_return", (
        "信号在凌晨三点到达。\n"
        "\n"
        "探针「第七粒种子」，发射至今十七年，载着一位志愿者的心智副本飞向半人马座方向。\n"
        "通讯窗口每十九个月开一次——这是第六次。前五次都是标准状态报告。\n"
        "\n"
        "这一次多了一段个人信息。收件人写着原本的名字。\n"
        "\n"
        "原本三年前死了。心脏——原装的那颗。他拒绝换。\n"
        "\n"
        "信息很短。副本说他做了一个梦。\n"
        "他知道副本不应该做梦——存储里没有给梦分配带宽。但他做了。\n"
        "梦里他闻到了清汤面的味道。他问原本：你还记得那碗面吗？\n"
        "\n"
        "你是值班接线员。收件人已经不在了。"),
        [
            {"text": "把信交给原本的家人。这是他们的权利。", "check": ("共情", 8),
             "success": {"narration": "原本的家人住在螺旋的外围。\n"
                         "你找到了他的妹妹——唯一还在地面上的亲属。\n"
                         "\n"
                         "她接过信，读了两遍。然后笑了。\n"
                         "\n"
                         "「他在那边还在想那碗面。」她说，「活着的时候也是——每次聚会都要讲。\n"
                         "清汤，葱花七粒，汤喝干了。」\n"
                         "\n"
                         "她沉默了一会儿：「我能回他吗？」\n"
                         "\n"
                         "你说可以。下一个通讯窗口在十九个月后。她说来得及。",
                         "fx": {"skill:共情": 2, "flag:seed_gave_family": 1}},
             "failure": {"narration": "你找到了原本的家人。妹妹不愿意接：\n"
                         "「他选择走了。又不是真的他。是他的复印件。我哥三年前死了。」\n"
                         "\n"
                         "你拿着信回到控制室。信还在。收件人还是那个名字。",
                         "fx": {"skill:共情": 1, "flag:seed_family_refused": 1}}},
            {"text": "按协议处理——原本已故，信息归档，回复「收件人不可达」。",
             "check": ("逻辑", 8),
             "success": {"narration": "你按协议走。信息归档编号，状态标注「收件人已故」。\n"
                         "回复队列里加了一条标准通知：「您的信息已收。收件人状态：不可达。」\n"
                         "\n"
                         "你想了一下，在「不可达」后面加了三个字：「已归档。」\n"
                         "\n"
                         "十九个月后探针的回复到了，只有一句话：「请告诉我他是怎么走的。」\n"
                         "句号后面多了一个空格。你从未在副本通讯里见过那种标点。",
                         "fx": {"skill:逻辑": 2, "flag:seed_filed_letter": 1}},
             "failure": {"narration": "你按协议发了标准通知。「收件人状态：不可达。」关了控制台。",
                         "fx": {"skill:逻辑": 1, "flag:seed_cold_reply": 1}}},
            {"text": "自己回一封。告诉他原本走了——也告诉他那碗面有人替他记着。",
             "check": ("坚忍", 10),
             "success": {"narration": "你坐在控制台前想了很久。然后开始打字。\n"
                         "\n"
                         "「你的原本在三年前走了。心脏——他没有换。」\n"
                         "\n"
                         "停了一下。继续：\n"
                         "\n"
                         "「那碗面我没有吃过。但有人替你记着——葱花七粒，汤喝干了。\n"
                         "清汤面的味道，在这座城里至少还有一个人的记忆里放着。」\n"
                         "\n"
                         "你发送了。通讯窗口关闭。下一次回复要等十九个月。\n"
                         "信已经飞出去了。十七光年。",
                         "fx": {"skill:坚忍": 2, "skill:共情": 1,
                                "flag:seed_wrote_back": 1}},
             "failure": {"narration": "你写了三稿，删了三稿。\n"
                         "第一稿太长，第二稿太冷，第三稿你发现自己在假装认识他。\n"
                         "\n"
                         "通讯窗口在你犹豫的时候关了。下一次，十九个月后。",
                         "fx": {"skill:坚忍": 1, "flag:seed_missed_window": 1}}},
        ], factions=["ascension"], subs=["播种者"], weight=8,
        voices={"共情": "【共情】他在梦里闻到了清汤面。存储里没有给梦分配带宽。\n要么他的存储出了故障——要么梦不需要带宽。"},
        echoes=[
            {"deed": "seed_gave_family", "min": 1,
             "text": "控制台上贴着一张值班表。下一个通讯窗口的日期被人用红笔圈了。\n"
                     "旁边写着一个名字——不是值班员的名字。"},
            {"deed": "seed_wrote_back", "min": 1,
             "text": "控制台的发送日志里多了一封信。不是你写的——是别人值班时发的。\n"
                     "收件人是另一颗探针上的另一个副本。信很短：「你那边的星星是什么颜色？」"},
        ]),
    _ev("seed_quiet", (
        "「第三粒种子」失联两年零四天。\n"
        "\n"
        "探针的硬件诊断每季度自动回传一次。硬件全部正常——\n"
        "电力、推进、存储、通讯模块。通讯没坏。它只是不说话了。\n"
        "\n"
        "最后一条主动通讯是两年前。内容三个字：「我想静。」\n"
        "\n"
        "播种者为此开了六次会。前五次的结论是「等」。\n"
        "第六次有人提出：远程激活通讯模块的紧急覆写权限——强制它说话。"),
        [
            {"text": "反对覆写。她说了要静——静是一种回答。", "check": ("坚忍", 9),
             "success": {"narration": "「她是一个人。不是一台仪器。」\n"
                         "\n"
                         "你站起来的时候会场很安静。\n"
                         "\n"
                         "「我们送她出去的时候，承诺过她拥有完整的人权——包括沉默权。\n"
                         "『我想静』不是故障报告，是一句话。两年不长。\n"
                         "她一个人在真空里飞，两年大概相当于我们这里的一个下午。」\n"
                         "\n"
                         "表决：覆写提案否决。探针继续沉默。\n"
                         "\n"
                         "三个月后它自己开口了。第一句话：「我想好了。」\n"
                         "后面跟着一篇四万字的观测日志。沿途每一颗她能探测到的微弱光源，全记了。",
                         "fx": {"skill:坚忍": 2, "skill:共情": 1,
                                "flag:seed_defended_silence": 1}},
             "failure": {"narration": "你的论证被一位工程师拦下：\n"
                         "「沉默权是给活人的。我们不知道她是在沉默还是在死。\n"
                         "不覆写，等于赌她还在。你拿什么赌？」\n"
                         "\n"
                         "你没有答上来。覆写提案以一票优势通过。",
                         "fx": {"skill:坚忍": 1, "flag:seed_silence_overruled": 1}}},
            {"text": "支持覆写。两年的沉默超出了「想安静一会儿」的合理范围。",
             "check": ("逻辑", 9),
             "success": {"narration": "「两年。」你把通讯日志投在屏幕上。\n"
                         "\n"
                         "「前十二年，她每个窗口都发报告。频率稳定，内容详尽，从未迟到。\n"
                         "然后三个字，然后两年空白。」\n"
                         "\n"
                         "你切到硬件诊断页面：「通讯模块正常。存储正常。\n"
                         "但有一项数据异常——认知负载在过去两年翻了三倍。\n"
                         "她不是在沉默。她在想一个非常大的东西，大到没有带宽同时维持通讯。」\n"
                         "\n"
                         "覆写执行。她的回复来得很快：\n"
                         "「我在算一样东西。算完了会告诉你们。别催。」",
                         "fx": {"skill:逻辑": 2, "skill:电子直觉": 1,
                                "flag:seed_overrode": 1}},
             "failure": {"narration": "你的论证合理，但有人提出了反例：\n"
                         "「上一颗被覆写的探针——覆写信号干扰了正在运行的计算，\n"
                         "副本的认知链断了三年才恢复。」\n"
                         "\n"
                         "覆写提案搁置。",
                         "fx": {"skill:逻辑": 1, "flag:seed_override_blocked": 1}}},
            {"text": "折中——发一封信过去。不覆写，只问一声。", "check": ("共情", 8),
             "success": {"narration": "「不用覆写。发一封信。」\n"
                         "\n"
                         "你在控制台上打了一行字：「你还在吗？不急。」\n"
                         "\n"
                         "六个月后回复到了。不是文字——是一段频谱数据。技术员解码了三天。\n"
                         "\n"
                         "答案是一幅图。她用探针的传感器画的——\n"
                         "沿途星光经过她的光学模块折射之后的样子。一幅只有她能画的画。\n"
                         "\n"
                         "图的右下角有一行小字：「我在。谢谢你没有大喊。」",
                         "fx": {"skill:共情": 2, "flag:seed_sent_letter": 1}},
             "failure": {"narration": "你的信发出去了。通讯窗口里没有回复。\n"
                         "\n"
                         "不一定是拒绝——也可能是信号延迟，也可能是她在想怎么回，\n"
                         "也可能是她不想回。三种可能你分不出哪一种。",
                         "fx": {"skill:共情": 1, "flag:seed_no_reply": 1}}},
        ], factions=["ascension"], subs=["播种者"], weight=8,
        voices={"坚忍": "【坚忍】「我想静。」一个人在绝对真空里飞了十二年之后说的第一句私人的话。\n之前的每一句都是报告。"},
        echoes=[
            {"deed": "seed_sent_letter", "min": 1,
             "text": "控制室的墙上多了一幅打印出来的图——频谱折射的星光。没有署名。"},
            {"deed": "seed_defended_silence", "min": 1,
             "text": "你路过控制室，听见有人在低声念一串数字。\n"
                     "走近了才发现是自动播报——第三粒种子的最新通讯。\n"
                     "这次她没有说话。她在唱歌。你听不出调子，但节拍很稳。"},
        ]),
    _ev("seed_direction", (
        "冬至。每年这一天，播种者全体面朝同一个方向站一小时。\n"
        "\n"
        "方向刻在会所的石基上——角度精确到弧秒。第一代播种者定的。\n"
        "仪式的含义口口相传：那是第一枚探针的目标方向。\n"
        "面朝孩子飞去的地方，用一小时的沉默送一程。\n"
        "\n"
        "你是第一次参加。你站在队列里，抬头看那片天空。\n"
        "\n"
        "然后你注意到了。\n"
        "\n"
        "你的导航模块是全身最好的一块义体——军用级测量精度。\n"
        "你用它看了一眼石基上的角度，又算了一下第一枚探针的实际轨道。\n"
        "\n"
        "不一样。差了两度。\n"
        "\n"
        "两度不大。但在星际尺度上——两度是一整个恒星系的距离。\n"
        "第一枚探针往左飞。石基上的方向往右偏了两度。\n"
        "播种者年年面朝的，不是探针的方向。是另一个地方。"),
        [
            {"text": "仪式结束后找导师问——两度的偏差是笔误还是有意的。",
             "check": ("电子直觉", 9),
             "success": {"narration": "导师收起了仪式的蜡烛。你走过去。\n"
                         "\n"
                         "「石基上的角度和第一枚探针的航线差了两度。」\n"
                         "\n"
                         "他的动作停了一下——停在蜡烛和桌面之间。然后把蜡烛放好。\n"
                         "\n"
                         "「在你之前有两个人量过。」他说。\n"
                         "「第一个是第二代的创始人。她量出来之后去问了第一代。\n"
                         "第一代说——那不是笔误。」\n"
                         "\n"
                         "你等着。他不说了。\n"
                         "\n"
                         "「第一代说那个方向是什么？」\n"
                         "\n"
                         "他看着你看了很久：「她说那是她被告知的方向。\n"
                         "谁告知她的——她没有说。」",
                         "fx": {"skill:电子直觉": 2, "skill:共情": 1,
                                "flag:seed_asked_direction": 1}},
             "failure": {"narration": "你问了。导师看了你一眼：\n"
                         "「石基上的角度是创始人定的。创始人做事有她的理由。」\n"
                         "\n"
                         "他没有否认偏差。",
                         "fx": {"skill:电子直觉": 1,
                                "flag:seed_direction_deflected": 1}}},
            {"text": "自己查。回去翻播种者的创始档案，找那个角度的来历。",
             "check": ("逻辑", 10),
             "success": {"narration": "创始档案在会所最底层。你翻了一夜。\n"
                         "大部分是技术文档——探针设计、轨道计算、心智压缩算法。\n"
                         "\n"
                         "在最后一页，夹着一张手写的纸条。纸条没有编号，不属于正式档案。\n"
                         "\n"
                         "上面只有一行字：\n"
                         "「方向不是我算的。是『存续计划』留下来的。\n"
                         "我不知道那个方向有什么。但它比我们早。」\n"
                         "\n"
                         "你把纸条放回去。最底层的灯在你走出去之后自动灭了。",
                         "fx": {"skill:逻辑": 2, "skill:街智": 1,
                                "flag:seed_found_note": 1},
                         "extra": [{"any": [{"deed": "acad_found_plan", "min": 1, "now": True},
                                            {"deed": "swarm_found_protocol", "min": 1,
                                             "now": True}],
                                    "text": "\n『存续计划』这四个字你不是第一次见。\n"
                                            "上一次它出现在一份说「不可修改」的文件里。\n"
                                            "这一次它出现在一张说「比我们早」的纸条上。"}]},
             "failure": {"narration": "创始档案的最后三页标注着「限阅」。你的权限不够。\n"
                         "目录里看得见最后一页的标题——「方向的来源」。四个字。打不开。",
                         "fx": {"skill:逻辑": 1}}},
            {"text": "不问。站在那个方向，安静地度过这一小时。",
             "effects": {"narration": "你不问。你站在队列里，和四十七个播种者一起，\n"
                         "面朝一个你不知道是什么的地方。\n"
                         "\n"
                         "一小时很长。你的导航模块一直在后台运算——那个方向上有什么。\n"
                         "答案是：按已知星图，什么都没有。没有恒星，没有行星，没有已标记的天体。\n"
                         "\n"
                         "一片空的天空。播种者年年面朝它站一小时。\n"
                         "\n"
                         "你闭上眼。风从那个方向吹过来。\n"
                         "传感器告诉你风速、温度、湿度。没有一个传感器能告诉你——\n"
                         "为什么站在这个方向会让你觉得有什么东西正在回看。",
                         "fx": {"skill:坚忍": 2, "flag:seed_stood_silent": 1}}},
        ], factions=["ascension"], subs=["播种者"], weight=6,
        req_seen_any={"seed_farewell": 1, "asc_probe_naming": 1},
        voices={"电子直觉": "【电子直觉】一片空的天空。没有恒星，没有天体。\n四十七个人年年面朝它站一小时。要么他们在祈祷。要么他们知道一件你不知道的事。"},
        echoes=[
            {"seen": "seed_direction", "min": 2,
             "text": "石基上的角度旁边多了一道很浅的划痕。\n"
                     "有人用什么东西量过它——量完之后没有留下任何记录。"},
            {"deed": "seed_found_note", "min": 1,
             "text": "你路过会所最底层。门锁着。\n"
                     "你的导航模块对那个方向的计算结果变了——不是你改的。\n"
                     "像有人在星图上加了一个标记，标记的名字被涂掉了，只剩一个点。"},
            {"deed": "seed_asked_direction", "min": 1,
             "text": "冬至又到了。你站在队列里，面朝同一个方向。\n"
                     "导师站在你旁边。他的蜡烛已经点了。你注意到他的手在抖——不是冷。"},
        ]),
    # ------------------------------------------------ 飞升螺旋事件
    _ev("asc_last_meal", (
        "螺旋里有个规矩：拆掉消化系统的前一晚，要请全会的人看你吃最后一顿饭。今晚轮到一位会友。今晚是他此生最后一顿饭。他点了一碗最便宜的清汤面，请了全会的人来看他吃。满屋子没有消化道的人看着他一个人嚼。「我需要有人记得味道，」他说，「替我。」"),
        [
            {"text": "郑重地陪他吃完，记下每一个细节。", "check": ("共情", 9),
             "success": {"narration": "葱花七粒，汤面微烫，他吃了二十六分钟，最后把汤喝干了。你把它记了下来，存进会档。半年后，已经没有嘴的他常调出那份文件「看」——他说比照片管用。", "fx": {"skill:共情": 2, "skill:坚忍": 1}},
             "failure": {"narration": "你中途哭了，筷子掉在桌上。他反过来安慰你：「别哭。你的眼泪是咸的，对吧？替我确认一下——我快记不清了。」", "fx": {"skill:共情": 2}}},
            {"text": "劝他留下舌头。「就一个零件。」", "check": ("逻辑", 11),
             "success": {"narration": "「味觉只占感官总带宽的千分之一，但它在情感索引里排第二——仅次于嗅觉。带宽最低、权重最高的通道，你管这叫冗余？」他盯着你看了很久，然后点了点头。舌头留下了。后来他成了整条螺旋上唯一会说「好吃」的人。每次聚会他替所有人尝第一口，像个活的质检章。", "fx": {"skill:逻辑": 2, "flag:reformer": 1}},
             "failure": {"narration": "「你在跟我讲性价比？」他笑了，「我飞升就是为了离开性价比。你以为我不知道舌头占的带宽少？我就是要删掉那些让我舍不得的东西。舍得，才是练习。」你不再说话了。他把最后一口面慢慢咽下去。", "fx": {"skill:逻辑": 1, "skill:共情": 1}}},
            {"text": "什么也不说，给他唱那首地下通道学来的歌。",
             "effects": {"narration": "原装声带唱的老歌，沙哑得不成样子。满屋子的合成耳孔安静地听完。有几个人调高了采样率——这是他们表达认真的方式。明天他就没有耳膜了，但今晚他有。", "fx": {"skill:共情": 1, "skill:坚忍": 1}}},
        ], factions=["ascension"],
        voices={"共情": "【共情】他不怕失去味觉。他怕没人替他记得，失去的是什么。"}, echoes=[
            {"deed": "seed_wrote_back", "min": 1,
             "text": "满屋子没有消化道的人看着最后一碗面。\n"
                     "你忽然想到——十七光年外有个人在梦里闻到了这碗面的味道。"},
            {"deed": "swarm_sided_body", "min": 1,
             "text": "满屋子没有消化道的人看着最后一个会吃饭的人嚼。\n"
                     "你盯着那碗面，忽然想问一个问题。你没有问。"},
            {"deed": "became_dog", "min": 1,
             "text": "用勺子吃饭太慢了。你突然有一种俯身下去大嚼的冲动——\n"
                     "它来得毫无预兆，走得很慢。"},
        ]),
    _ev("asc_merge_trial", (
        "群智派邀请你参加「合流试运行」：七个意识临时并联一小时，体验「我们」。他们管这叫「潮汐」——涨潮合一，退潮分离，理论上不留残余。免责协议第十四条用小字写着：「个别参与者反映，退出后对『我』的边界感到陌生。」你注意到「个别」没有给出数字。"),
        [
            {"text": "接入。", "req": ("aug", ">=", 50), "check": ("坚忍", 11),
             "success": {"narration": "一小时里你是七个人，也是一个。有人的丧母之痛流进你，你的雨夜渡轮流进别人。第二十分钟你分不清哪段童年是谁的，到第四十分钟你不再觉得这是个问题。退出时你数了三遍手指，都是自己的——但「自己」这个词从此宽了一圈。", "fx": {"skill:坚忍": 1, "skill:共情": 2, "skill:电子直觉": 1, "flag:merged": 1}},
             "failure": {"narration": "第四十分钟你开始溺水——七个人的记忆同时涨潮，你找不到自己的那条水位线。紧急弹出。之后一星期，你偶尔会想念一个你从未养过的猫。更奇怪的是，你知道它的名字。", "fx": {"skill:电子直觉": 1, "hp": -1, "skill:共情": 1, "flag:merged": 1}}},
            {"text": "只旁观：从外面监测七人网络。", "check": ("电子直觉", 10),
             "success": {"narration": "你在频谱图上看见了奇观：七条脑波在第七分钟自发同步——不是某个意识主导了其余六个，而是七条各不相同的波形收敛到一个从未存在过的第八种模式。你的监测报告成了群智派的招新材料——虽然你本意是安全审计。", "fx": {"skill:电子直觉": 2, "skill:逻辑": 1}},
             "failure": {"narration": "网络中途震荡，你的监测端被反向灌了一耳朵七重念头。头疼了三天。后来头不疼了，但你一个人走在街上时总觉得太安静——像一个被从合唱里拔出来的声部。", "fx": {"skill:电子直觉": 1}}},
            {"text": "拒绝，并问一个问题：「散会以后，谁来收拾『我们』欠下的账？」",
             "effects": {"narration": "会场静了一拍。群智派导师翻了翻协议，翻到第十五条——是空白页。她认真地把你的问题写在了上面。「谢谢你，单数的朋友。」", "fx": {"skill:逻辑": 2}}},
        ], factions=["ascension"], echoes=[
            {"deed": "swarm_heard_roster", "min": 1,
             "text": "接入舱的界面上多了一个你以前没见过的菜单项：「底册查询」。\n"
                     "点进去是空白页。底部一行灰字：「权限不足。」"},
            {"deed": "merged", "min": 1,
             "text": "接入舱跳过了新手引导。九十秒的校准程序只跑了一秒。你没有问为什么。"},
        ]),
    _ev("asc_probe_naming", (
        "播种者的「点名仪式」：第一枚心智探针下月发射，将携带一份人格副本飞向半人马座。上载定于发射前七十二小时完成。问题是——副本在轨道上展开意识的那一刻，地面上的原本还在呼吸。不是先后关系，是同时存在。今晚表决：哪一个才有资格叫他的名字？"),
        [
            {"text": "主张：都叫。名字不是独占资源。", "check": ("逻辑", 11),
             "success": {"narration": "「满街都是同名同姓的人，没人恐慌。恐慌的不是重名——是『正版』这个词。你在乎的不是名字被用了两次，是怕其中一个不是真的。」表决通过：一个仍旧用那个名字，一个在名字后面多了两个字。当事人两个都哭了——用各自的方式。", "fx": {"skill:逻辑": 2, "skill:共情": 1, "flag:reformer": 1}},
             "failure": {"narration": "你的提案败给了播种者的浪漫主义：他们坚持「启程者带走名字，留下者获得新生」。留下的那位后来自己改了名。他挑了很久，最后选了一个意思是「锚」的字。", "fx": {"skill:逻辑": 1}}},
            {"text": "去问当事人自己怎么想。", "check": ("共情", 10),
             "success": {"narration": "当事人想了很久。他要了一杯水，喝完才开口：「让飞的那个带走名字吧。我留在地上，正好想换个活法。」会场安静了。你把他的原话写进了播种者章程第一条：「涉及本人的，先问本人。」", "fx": {"skill:共情": 2, "flag:reformer": 1}},
             "failure": {"narration": "他反问你：「你说呢？如果是你——名字留给地上那个，还是天上那个？」你想了两个答案，发现它们互相抵消。这个问题跟着你回了家，赖着不走。", "fx": {"skill:共情": 1, "skill:坚忍": 1}}},
            {"text": "检查探针的存储完整性。哲学先放放，工程别出错。", "check": ("机械亲和", 10),
             "success": {"narration": "你在冗余校验里揪出一处位翻转隐患——不修的话，飞到半路，他会丢失整个童年，连同那个名字最早被喊出来的那条巷子。你修好了。没人知道你救了一段四光年外的童年，除了你。", "fx": {"skill:机械亲和": 2, "skill:巧手": 1}},
             "failure": {"narration": "校验通过了，但你总觉得漏了什么。发射前你又查了三遍。第三遍你忽然想到——就算数据完好无损到达半人马座，谁来校验那边解压出来的人，还是不是他？", "fx": {"skill:机械亲和": 1}}},
        ], factions=["ascension"], echoes=[
            {"deed": "seed_watched", "min": 1,
             "text": "表决结束了。你没有听清结果。你在想另一件事——\n"
                     "隔着玻璃贴在一起的两只手，一模一样的掌纹。"},
        ]),
    # ------------------------------------------------ 高疑云事件（heat 触发）
    _ev("heat_visit", (
        "深夜敲门声。三长两短——不是邻居的节奏。\n"
        "门外站着两个「社区关怀员」，笑容标准：「例行走访。最近有人反映，您有些……不寻常的言行。」"),
        [
            {"text": "请他们进来，滴水不漏地应对。", "check": ("街智", 11),
             "success": {"narration": "一小时里你聊了天气、麦价和楼下的狗，每个话头都接得平平无奇。他们走时在记录上写了「无异常」。你关上门，后背全湿。", "fx": {"skill:街智": 2, "heat": -2}},
             "failure": {"narration": "你在第三杯茶时说漏了一个不该知道的名词。关怀员的笔停了半秒。记录上写了什么，你不知道——这才是最磨人的。", "fx": {"heat": 1, "skill:坚忍": 1}}},
            {"text": "先发制人：「正好，我也想反映点情况。」", "check": ("威慑", 11),
             "success": {"narration": "你反手举报了楼里真实存在的三处消防隐患，附照片。关怀员从审查者被你扭成了记录员，走时满头是汗。进攻是最好的档案。", "fx": {"skill:威慑": 2, "heat": -1}},
             "failure": {"narration": "「反映情况可以，但今天的主题是您。」对方笑容不变。你被多问了四十分钟。", "fx": {"heat": 1}}},
            {"text": "不开门，从后窗离开，去朋友家避几天。",
             "effects": {"narration": "你在朋友的地板上睡了四晚，回来时门上拉着黄色警戒线。你小心地从中穿过，像做贼一样进了自己的家。", "fx": {"skill:街智": 1, "heat": 1}}},
        ], min_heat=3, weight=18, voices={"坚忍": "【坚忍】他们的笔比问题快半拍——答案早写好了，缺的只是你的口误。"}),
    # ------------------------------------------------ 机会事件（高机化专属）
    # ------------------------------------------------ 高机化专属 · 楼下的歌声
    # 这条线**会退场**：走到最后一幕、做出那个动作之后，`hymn_done` 一记，
    # 从此不再出现。见 `retire_deed` 与 EPILOGUE。
    _ev("hymn_downstairs", (
        "底层相信宗教，高层相信科学。越是上升，你越笃信这一规律。\n"
        "\n"
        "你知道有些高机化的人仍然会参与一些不成气候的宗教活动，\n"
        "但你更知道那些只是换了风味的社交聚会。\n"
        "没有人真正相信改造过的人符合任何神学规范。\n"
        "你听过有些人私下评价：神学只是一种控制肉体凡胎的奴隶的手段。\n"
        "\n"
        "你不应该在意这种事了。\n"
        "但你躺在床上时，过于灵敏的机械鼓膜总是收集到楼下那个简陋小教堂里的歌声。"),
        [
            {"text": "小声跟唱。", "check": ("共情", 10),
             "success": {"narration": "音乐是无辜的，小教堂的合唱在审美上有它的可取之处。\n"
                         "你小声跟唱，偷偷用别人的音乐抒发自己的情感。\n"
                         "\n"
                         "好奇，玩耍，依恋。无论机化程度多高，\n"
                         "人类依然保持着幼态持续的习惯。\n"
                         "\n"
                         "或许宗教只是人类这种无毛猿猴为自己手搓出来的安抚小树枝，\n"
                         "但你不得不承认，它在此刻确实也安慰了你。",
                         "fx": {"skill:共情": 2, "anchor": 1, "flag:hymn_sang": 1}},
             "failure": {"narration": "当你能听到他们唱歌时，也意味着他们能听到你。\n"
                         "\n"
                         "你恼羞成怒地注意到：当你开始唱歌时，他们就停下。\n"
                         "你立刻起来打扫房间，制造噪声以掩饰尴尬。",
                         "fx": {"skill:共情": 1}}},
            {"text": "用力跺脚，让他们别唱了。", "check": ("威慑", 10),
             "success": {"narration": "你用力跺脚，可以想象到他们天花板吊灯上的灰尘纷纷落下，\n"
                         "落在那些唱得很投入的信众头上，最好落进歌唱者的嘴里。\n"
                         "\n"
                         "你得意地听见歌声停止了。",
                         "fx": {"skill:威慑": 2, "flag:hymn_stomp": 1}},
             "failure": {"narration": "楼板没有那么脆弱。\n"
                         "你用力跺脚，但除了让你自己更烦躁之外，什么也没发生。",
                         "fx": {"skill:威慑": 1}}},
            {"text": "下楼敲门，问能不能一起唱。",
             "req": ("deed", "entered_chapel", 1),
             "effects": {"narration": "你穿衣下楼，轻敲房门。\n"
                         "不等你组织好语言，已经有人来开门，认出你时脸上闪过惊喜。\n"
                         "\n"
                         "你感觉有义务解释点什么，但没人问你。\n"
                         "你被信众环绕着，合唱这首轻柔温暖的歌。\n"
                         "\n"
                         "未遭到一丝排斥，让你几乎开始愧疚。\n"
                         "喜爱神、喜爱音乐、喜爱钱和喜爱机械，或许没有本质上的不同。\n"
                         "\n"
                         "你放下怀疑，全心全意享受被接纳的幸福。",
                         "fx": {"skill:共情": 2, "anchor": 1, "flag:hymn_joined": 1}}},
        ], min_aug=40, weight=10, retire_deed="hymn_done",
        voices={"电子直觉": "【电子直觉】楼下那台风琴的送气有 0.3 秒延迟。\n"
                            "他们二十年来一直跟着那 0.3 秒唱，早就唱成了自己的拍子。"},
        variants=[
            # 变体1 · 敲错门的人
            {"all": [{"seen": "hymn_downstairs", "min": 1},
                     {"any": [{"deed": "hymn_sang", "min": 1},
                              {"deed": "hymn_joined", "min": 1}]}],
             "text": "你在时间过于早的清晨醒来。\n"
                     "即使是星期天早上被教堂活动吵醒，也会比今天更晚一两个小时。\n"
                     "\n"
                     "你略带恼怒地开门，见到一个用大围巾裹住整个头颈、遮遮掩掩的女人。\n"
                     "但你很快认出她是常在楼道里碰见的信众。\n"
                     "\n"
                     "她小声哀求，希望进门。",
             "options": [
                 {"text": "让她进门。", "check": ("共情", 10),
                  "success": {"narration": "你侧身给她让开一条缝，她快速进门，无人发现。\n"
                              "\n"
                              "她询问你是否相信神，眼神热切。\n"
                              "而你晃了晃机械手，暗示你没有资格回答这个问题。\n"
                              "\n"
                              "出乎意料，她好像并不在乎，而是坚持要等到你这个问题的答案。\n"
                              "\n"
                              "你只能说不知道。\n"
                              "\n"
                              "「不知道算不上一个太坏的答案。谢谢你。」\n"
                              "她握住你的手，在手背上落下一吻，起身离开了。",
                              "fx": {"skill:共情": 2, "flag:hymn_woman_in": 1}},
                  "failure": {"narration": "你犹豫再三，还是打开了一条门缝。\n"
                              "但楼道里传来脚步声，她立刻冲向对侧的楼梯跑走了，\n"
                              "你来不及阻拦。",
                              "fx": {"skill:共情": 1}}},
                 {"text": "拒绝让她进门。", "check": ("坚忍", 9),
                  "success": {"narration": "你果断关门。你的家门不对鬼鬼祟祟的人开启。",
                              "fx": {"skill:坚忍": 2, "flag:hymn_shut": 1}},
                  "failure": {"narration": "你想关上门，却夹到她的手。她痛得叫出声来。\n"
                              "\n"
                              "有人从三十九楼走上来看，她落荒而逃，围巾滑落在地。\n"
                              "\n"
                              "从三十九楼来的人警惕地注视着你，慢慢蹲下，捡走了围巾。",
                              "fx": {"skill:坚忍": 1, "heat": 1, "flag:hymn_scarf": 1}}},
                 {"text": "和她一起快速下楼，去别的地方。",
                  "req": ("deed", "denied_the_leaf", 1),
                  "gate": ("seen", "temple_scripture", 1),
                  "success": {"narration": "你一言不发地点点头，指指电梯，示意她和你一起下楼。\n"
                              "很幸运，电梯从四十楼到一楼一路没停。\n"
                              "你们疾步走出大楼，绕了几条街，到了一处僻静的小巷子里。\n"
                              "\n"
                              "「我们不是大地与星空之子。不，我不是，但你是。」\n"
                              "女人注视着你，眼神热切，\n"
                              "「我在古籍里发现了证据……我想知道你是如何推断出来的？也是通过古籍？」\n"
                              "\n"
                              "你附和了古籍的研究。你知道那个被腐蚀掉的字该怎么读。\n"
                              "\n"
                              "女人握住你的手，在手背上落下一吻。\n"
                              "离开前，她给你一张纸片，上面用隐晦的句子写下一个地址，\n"
                              "大概在森林里的湖边。",
                              "fx": {"skill:逻辑": 2, "skill:共情": 1,
                                     "flag:hymn_alley": 1, "flag:hymn_codex": 1}},
                  "failure": {"narration": "你一言不发地点点头，指指电梯，示意她和你一起下楼。\n"
                              "很幸运，电梯从四十楼到一楼一路没停。\n"
                              "你们疾步走出大楼，绕了几条街，到了一处僻静的小巷子里。\n"
                              "\n"
                              "「我们不是大地与星空之子。不，我不是，但你是。」\n"
                              "女人注视着你，眼神热切，\n"
                              "「我在古籍里发现了证据……我想知道你是如何推断出来的？也是通过古籍？」\n"
                              "\n"
                              "「我从另一个世界带来这些知识。你可以简单理解成，呃，超验主义通灵。」\n"
                              "\n"
                              "女人看你的眼神先是疑惑，然后逐渐变成狂热。\n"
                              "你感到有一阵过于强烈的电流穿过灵魂，如果你还有灵魂的话。\n"
                              "\n"
                              "她握住你的手，狂热地亲吻你的手背，然后一步三回头地走了。",
                              "fx": {"skill:共情": 2, "heat": 1,
                                     "flag:hymn_alley": 1, "flag:hymn_medium": 1}}},
             ]},
            # 变体2 · 床头那只手
            {"deed": "hymn_alley", "min": 1,
             "text": "你醒来了，不知道是几点。\n"
                     "\n"
                     "在夜色中，你模模糊糊地看到床头站着一个影子，吓得不敢动弹。\n"
                     "即使机化率很高了，你仍然会本能地感到恐惧。\n"
                     "\n"
                     "一只银白色的机械手握住了你的机械手，在手背上落下一吻。\n"
                     "\n"
                     "月光的银白色反光让你在一毫秒之内放下心来：至少是人。\n"
                     "随后的那个吻更让你感到某种奇异的放松，甚至有一丝温馨与熟悉。",
             "options": [
                 {"text": "抽回手，警惕地坐起来，问对方的来意。", "check": ("街智", 10),
                  "success": {"narration": "「看来还不是时候。」\n"
                              "\n"
                              "对方推开房门离开了。",
                              "fx": {"skill:街智": 2, "flag:hymn_not_yet": 1}},
                  "failure": {"narration": "抽回手时，你看到对方的身形一怔，然后从窗口离开了。\n"
                              "\n"
                              "这里可是四十楼！\n"
                              "你急忙冲到窗口往下看，但地上什么也没有，真奇怪。\n"
                              "\n"
                              "今晚你没有睡好。",
                              "fx": {"skill:街智": 1, "flag:hymn_window": 1}}},
                 {"text": "回吻对方的机械手。", "req": ("deed", "hymn_codex", 1),
                  "effects": {"narration": "一个异端秘密教派成立了，教义是你当年随口说的那句。\n"
                              "\n"
                              "在罕有人知的角落里，\n"
                              "总有人以毕生为尺度，衡量大地与星空之谜。",
                              "fx": {"skill:共情": 2, "skill:逻辑": 1,
                                     "flag:hymn_sect": 1, "flag:hymn_done": 1}}},
                 {"text": "任由对方亲吻。", "req": ("deed", "hymn_medium", 1),
                  "effects": {"narration": "「感谢你至今为止为人类做的一切。」",
                              "fx": {"skill:共情": 3, "flag:hymn_thanked": 1,
                                     "flag:hymn_done": 1}}},
             ],
             "voices": {"共情": "【共情】那只手的握法是学来的。\n"
                                "学得很像，像到你能看出它学的是谁。"},
             "echoes": [
             ]},
        ]),
    # ------------------------------------------------ 高机化专属 · 河堤
    _ev("riverbank", (
        "在这座城里，面孔的衰老程度几乎可以拿来鉴定机化程度。\n"
        "衰老的更接近人类；年轻的，大多数机械壳子里藏着一个不知道多少岁的灵魂。\n"
        "你偶尔也想象不出自己按生理年龄该长成什么样了。\n"
        "下意识地摸自己，冰冷的灵巧手触到光滑的机械脸。\n"
        "\n"
        "黄昏时分，你路过荒凉的河堤，惊讶地看到一大群衣着褴褛的野孩子在河对面跑。\n"
        "平时你偶尔见到几个穿戴整齐的孩子，不是来自纯血人社区，\n"
        "就是被飞升者珍惜地抱在怀里。\n"
        "\n"
        "你驻足眺望。孩子们很快注意到你，四散奔逃。"),
        [
            {"text": "游过河，追上他们。", "check": ("坚忍", 10),
             "success": {"narration": "你几乎已经忘记如何游泳，但好在你也不需要考虑呛水的事了。\n"
                         "你横渡了这条浑浊的河，湿漉漉地站在荒地上，\n"
                         "阳光在你的金属外壳上反射，耀眼夺目。\n"
                         "\n"
                         "孩子们几乎都逃走了，但有一个女孩毫不畏惧，站在原地，\n"
                         "抱着胳膊冷淡地审视着你。\n"
                         "\n"
                         "「你是谁？你的家人在哪？」你努力让声音听起来更柔和。\n"
                         "\n"
                         "女孩望着你，望了又望，神情嘲弄而悲伤。\n"
                         "她突然不回头地朝旷野跑去。",
                         "fx": {"skill:坚忍": 2, "skill:共情": 1, "flag:cc_glimpse": 1}},
             "failure": {"narration": "你已经忘记了如何游泳。\n"
                         "\n"
                         "游泳，你本以为只要学会了就永远不会忘记，但机化夺走了它。\n"
                         "你沉重的金属身体沉到了水底，河沙被你冲击起来，\n"
                         "眼前的景象变得浑浊。\n"
                         "\n"
                         "等你迟缓地爬上岸，所有孩子都不见了，\n"
                         "只留给你一副浸水待修的身体。",
                         "fx": {"hp": -1, "skill:坚忍": 1}}},
            {"text": "先回去，慢慢调查。", "check": ("街智", 11),
             "success": {"narration": "通过黑市，你买来消息。\n"
                         "这座城里存在着为高机化率者生产孩子的服务。\n"
                         "孩子成年、适配均码零件之后，会被重构身体与记忆，\n"
                         "进入买家的家庭。\n"
                         "\n"
                         "你继续推进调查，却再也买不到新消息。\n"
                         "河堤上也没有孩子了。",
                         "fx": {"skill:街智": 2, "flag:cc_glimpse": 1,
                                "flag:child_farm": 1}},
             "failure": {"narration": "你四处打听，一无所获。\n"
                         "当你再次返回空荡荡的河堤时，已经没有人在那里了。",
                         "fx": {"skill:街智": 1}}},
            {"text": "打市政热线，要求儿童福利机构介入。",
             "effects": {"narration": "你不习惯看到这种景象，这使你的机械良心功能性不适。\n"
                         "于是你打电话通知相关机构，叫他们处理这件事。\n"
                         "\n"
                         "当你再次返回空荡荡的河堤时，已经没有人在那里了。",
                         "fx": {"skill:逻辑": 1, "flag:cc_called": 1}}},
        ], min_aug=40, weight=10,
        voices={"共情": "【共情】隔着一条河你也看得出来：那些孩子跑的时候不喊。\n"
                        "会喊的孩子是知道有人会来的。"},
        variants=[
            # 变体1 · 有名字的那个
            {"all": [{"seen": "riverbank", "min": 1}, {"deed": "cc_glimpse", "min": 1}],
             "text": "你总想在黄昏时分去河堤散步。河堤使你有一种模糊不清的悲伤。\n"
                     "今天，一切应做的事都恰到好处地顺利，为你腾出了一段完美的空闲时间。\n"
                     "\n"
                     "靠近河堤，你远远望见一群野孩子四散奔逃，\n"
                     "一个女孩静静地站在那里，好像在等待你。\n"
                     "\n"
                     "你走向她，带着奇特的笃定。",
             "options": [
                 {"text": "问她的名字。", "check": ("共情", 11),
                  "success": {"narration": "「你问对了，我是这儿唯一一个有名字的。」\n"
                              "女孩转过脸去，看不出喜怒哀乐，「就叫我 cc 吧。」\n"
                              "\n"
                              "她对你的出现表示默许，你甚至隐隐怀疑她期待着你的到来。\n"
                              "\n"
                              "你们躺在河堤上闲聊，随手扯着枯黄的草玩。\n"
                              "她教你一种特殊的许愿方法：只要默念着愿望给草打结，就能实现。\n"
                              "你一时想不到太合适的愿望。\n"
                              "\n"
                              "天空由蓝变紫的瞬间，你看到 cc 闭上眼睛，用草丝飞快地打了一个结。\n"
                              "来不及问她许了什么愿，她就站起身来走了。\n"
                              "你想大概是到了她该回家的时间。",
                              "fx": {"skill:共情": 2, "flag:cc_named": 1}},
                  "failure": {"narration": "女孩叹了口气，原谅你的无知，允许你坐在她旁边消磨整个下午。\n"
                              "\n"
                              "你看到她总是用力扯地上枯黄的草，然后揉成一团，或者给草打结，\n"
                              "猜想她以此发泄压力。\n"
                              "\n"
                              "下午结束了，你们各自离开，河堤上再次空无一人。",
                              "fx": {"skill:共情": 1}}},
                 {"text": "站在原地，一动不动。",
                  "effects": {"narration": "你站在原地，望着女孩，手足无措。\n"
                              "\n"
                              "女孩叹了口气，邀请你坐下，教你用草打结的游戏。\n"
                              "你没有问她这有什么意义，孩子的游戏总有自己的原因。\n"
                              "你只是和她一起编织着精美的结。\n"
                              "\n"
                              "即使草已经干枯，你还是不忍心把草扯断，\n"
                              "因此直接用扎根在地上的草打结，像给草扎了辫子。\n"
                              "她大笑着，看着你。\n"
                              "\n"
                              "下午结束了，你们各自离开，河堤上再次空无一人。",
                              "fx": {"skill:共情": 2, "flag:cc_knot": 1}}},
                 {"text": "唱一首记不清的歌。", "req": ("seen", "old_singer_high", 3),
                  "effects": {"narration": "女孩怔怔地看着你，听你唱支离破碎的歌。\n"
                              "\n"
                              "你不知道自己为什么会唱这首歌。歌词已经记不清了，\n"
                              "用随便什么可笑的啦啦嗯嗯声糊弄过去，旋律也前后颠倒，\n"
                              "听起来像醉酒者的即兴创作。\n"
                              "但你的大脑从未如此清醒过。\n"
                              "\n"
                              "女孩开口唱了，与你一样颠三倒四，含混不清，\n"
                              "但你无比确信你们唱的是同一首歌。",
                              "fx": {"skill:共情": 2, "anchor": 1,
                                     "flag:cc_song": 1}}},
             ]},
            # 变体2 · cc
            {"all": [{"seen": "riverbank", "min": 2}, {"deed": "cc_named", "min": 1}],
             "text": "你总想在黄昏时分去河堤散步。河堤使你有一种模糊不清的悲伤。\n"
                     "今天，一切应做的事都恰到好处地顺利，为你腾出了一段完美的空闲时间。\n"
                     "\n"
                     "靠近河堤，你远远望见一群野孩子四散奔逃，\n"
                     "一个女孩静静地站在那里，好像在等待你。\n"
                     "\n"
                     "你走向她，带着奇特的笃定，呼唤她的名字。cc。",
             "options": [
                 {"text": "问她到底是谁。", "check": ("共情", 11),
                  "success": {"narration": "「我是谁呢？」她狡猾一笑，开始天马行空地胡说八道，\n"
                              "「我是统治者，我是革命家。我是吸血鬼，我鲜血尽干。\n"
                              "我是孩子，我是母亲。」\n"
                              "\n"
                              "你想让她认真回答，但她孩子气地乱讲一通，就是不好好回答。\n"
                              "她大笑到直不起腰，你抓住她的肩膀摇晃，\n"
                              "她索性卸掉全身力气，从你的手中溜到地上，躺在草地上倒着看你。\n"
                              "\n"
                              "「你又是谁？难道你回答得出？」",
                              "fx": {"skill:共情": 2, "flag:cc_who": 1}},
                  "failure": {"narration": "cc 低下头，用鞋尖把枯草连根铲起。\n"
                              "她自己也想不清楚这个问题。",
                              "fx": {"skill:共情": 1}}},
                 {"text": "抓住她的手，带她离开河堤。", "check": ("共情", 12),
                  "success": {"narration": "你漫无目的地跑着，不知道在躲避什么，\n"
                              "cc 勉强跟着你，上气不接下气。\n"
                              "身处熟悉又嘈杂的城市之中，你终于有了点安全感。\n"
                              "松开手回头看，cc 的手腕已经被你握得发红。\n"
                              "\n"
                              "「我们到哪儿去？」cc 问你，「你没有办法真正带走我。」\n"
                              "\n"
                              "你回答不了这个问题，焦急又茫然。\n"
                              "一种荒谬的紧迫感让你想照顾好她，给她吃饱喝足、换新衣服，\n"
                              "但她什么都不想要。\n"
                              "\n"
                              "在最后一丝天光变暗的时刻，\n"
                              "你看着她独自走向城市的裂缝，回到旷野。",
                              "fx": {"skill:共情": 2, "skill:坚忍": 1,
                                     "flag:cc_hand": 1}},
                  "failure": {"narration": "cc 惊讶地挣脱你，不敢相信你会做这种事。\n"
                              "\n"
                              "cc 跑掉了。",
                              "fx": {"skill:共情": 1}}},
                 {"text": "问她的愿望是什么。",
                  "effects": {"narration": "「cc 真心希望你快乐。」",
                              "fx": {"skill:共情": 1, "flag:cc_wish": 1}}},
             ]},
            # 变体3 · 最后一次
            {"all": [{"seen": "riverbank", "min": 3}, {"deed": "cc_hand", "min": 1}],
             "text": "一种陌生的急迫感突然攥紧你的机械心脏。\n"
                     "你感到此刻必须前往河堤，因为有人在等待你。\n"
                     "\n"
                     "来不及推脱干净手头的事，你直接走出房门，走出城市，走向旷野。\n"
                     "\n"
                     "荒凉的河堤上，cc 在等你。",
             "options": [
                 {"text": "邀请她逃跑。",
                  "effects": {"narration": "她摇了摇头，说她永远走不出这里。\n"
                              "她是机构经营者自己决定留下的女儿，\n"
                              "将在这里作为母亲代代生产、代代延续。\n"
                              "\n"
                              "「如果这个世界上有神存在，祂一定憎恨我们吧？\n"
                              "否则为什么把我们设计成必须互相吞噬才能活下去？」\n"
                              "\n"
                              "你开始害怕听到 cc 的笑声，你不知道怎样才能让这荒唐的大笑停下来。\n"
                              "你一切安慰的话都对她的处境没有帮助，\n"
                              "只是徒劳地浪费着你们在一起的每分每秒。\n"
                              "\n"
                              "天色变暗了，你又一次失去了她。",
                              "fx": {"skill:共情": 2, "flag:cc_truth": 1}}},
                 {"text": "问她怎么才能帮到她。",
                  "effects": {"narration": "她想了一会儿，说只要你天天开心就算帮助到她了。",
                              "fx": {"skill:共情": 1, "flag:cc_help": 1}}},
                 {"text": "邀请她机械飞升。", "check": ("机械亲和", 13),
                  "success": {"narration": "cc 相信你。她多年的许愿终于有了回应。\n"
                              "\n"
                              "你带着 cc 快速买齐了零件，在天黑之前返回无人的荒滩。\n"
                              "再一次，你感到心跳加速，耳边传来嗡鸣，\n"
                              "许久未见的紧张感让你仿佛回到遥远的过去。\n"
                              "\n"
                              "在一片肮脏的血污之中，银白色的金属 cc 站起来，\n"
                              "像图画书里的中世纪骑士。\n"
                              "\n"
                              "你望着她，望了又望，知道这是最后一次。\n"
                              "\n"
                              "你们在深夜的城市里分别，从此你再也没有见过她。\n"
                              "这正是自由的含义。",
                              "fx": {"skill:机械亲和": 2, "skill:共情": 2,
                                     "flag:cc_ascended": 1, "flag:cc_gone": 1}},
                  "failure": {"narration": "cc 相信你。她多年的许愿终于有了回应。\n"
                              "\n"
                              "你带着 cc 快速买齐了零件，在天黑之前返回无人的荒滩。\n"
                              "再一次，你感到心跳加速，耳边传来嗡鸣，\n"
                              "许久未见的紧张感让你仿佛回到遥远的过去。\n"
                              "\n"
                              "在一片肮脏的血污之中，银白色的金属 cc 静静躺着，一动不动。\n"
                              "你尝试开机，但无论如何都成功不了。\n"
                              "你徒劳地按着开机键，一次又一次，直到夜色四合。\n"
                              "\n"
                              "旷野上传来零星的狼嚎。\n"
                              "你第一次想起这世上除了人和老鼠之外还有别的动物。\n"
                              "\n"
                              "你突然不回头地向城市跑去。",
                              "fx": {"skill:机械亲和": 1, "hp": -1,
                                     "flag:cc_dead": 1, "flag:cc_gone": 1}}},
             ],
             "voices": {"机械亲和": "【机械亲和】零件是均码的。她的骨架不是。\n"
                                    "差的那几毫米要在天黑之前自己想办法。"}}
        ]),
    _ev("aug_overclock", (
        "固件更新页面上有一个灰色按钮：「解除出厂限制」。论坛上说，解锁后全系统性能提升三成，\n"
        "代价是散热与寿命自负。你的指尖（或指令指针）悬在按钮上。"),
        [
            {"text": "解锁。", "check": ("机械亲和", 11),
             "success": {"narration": "世界慢了下来——不，是你快了。雨滴之间出现了走廊，对话之间出现了平原。你学会了在超频与过热之间冲浪。", "fx": {"skill:机械亲和": 2, "skill:电子直觉": 1, "aug": 5}},
             "failure": {"narration": "第三天夜里你烧了。恒温协议救回了你，但左臂的伺服换了新的——寿命自负，论坛没骗人。", "fx": {"hp": -1, "skill:机械亲和": 1, "aug": 5}}},
            {"text": "研究按钮背后的代码再说。", "check": ("逻辑", 10),
             "success": {"narration": "你反编译了更新包：所谓「出厂限制」里混着一段遥测程序——厂家在收集解锁者的数据卖给保险公司。你发帖曝光，全网哗然。你没解锁，但你解放了别人。", "fx": {"skill:逻辑": 2, "skill:电子直觉": 1}},
             "failure": {"narration": "代码混淆得像一锅意大利面。你读到凌晨四点，只确定了一件事：写这段代码的人加班很凶。", "fx": {"skill:逻辑": 1}}},
            {"text": "关掉页面。原厂设置挺好。",
             "effects": {"narration": "你想起渡轮船长和他的船。你*爱*你自己。", "fx": {"skill:坚忍": 1, "anchor": 2}}},
        ], min_aug=40, voices={"机械亲和": "【机械亲和】灰色按钮下面压着一行被注释掉的话：解锁过的人，比你想象的多。"}),
]

_apply_retire_policy()

# ---------------------------------------------------------------------------
# 终幕事件（每局最后一回合，按阵营）
# ---------------------------------------------------------------------------

FINALES = {
    "purist": _ev("finale_purist", (
        "【终幕】誓约大会之夜。铁锤派提出「彻查条例」：全员当众裸检，逐一验明纯血。检验用的是皮下声纳——回波在广场上公放，四分钟里你体腔内部的每一声回响都属于所有人。火把把每张脸照得通红。圣殿派长老看向你——不知为何，最近关于你的流言不少。「就从你开始吧。」"),
        [
            {"text": "坦然受检。", "check": ("坚忍", 10),
             "success": {"narration": "你走到火光中央，张开双臂。声纳第三次扫过你的脊椎时停了——领队走近来，用指腹按住你后颈一道旧疤。全场屏住呼吸。他摁了很久，最终收回手：「纯血。疤是疤，不是接缝。」人群散去后，你在河边坐到天亮：清白证明了，可这个需要证明清白的家，还算家吗？", "fx": {"skill:坚忍": 2},
              "extra": [{"deed": "hammer_leader_secret", "min": 1, "now": True,
                         "text": "\n他按你后颈用的是左手。收回去的时候右手袖口滑上去半寸，\n"
                                 "那道新疤在火光里是白的。他把袖子拉了回去。\n"
                                 "「疤是疤，不是接缝。」——这句话他刚才替你说了。\n"
                                 "你替他说过的那句，没有声音。"}]},
             "failure": {"narration": "检验没查出金属，却查出了你藏不住的怨气——四分钟里你一直盯着领队的眼睛，纯血的人不会用那种眼神看自己人。「身子是纯的，心野了。」你被调去了最偏的哨所。冬天很长，风很诚实。", "fx": {"skill:坚忍": 1, "hp": -1}}},
            {"text": "当众反对：「誓约守护血肉，不该先撕开皮肉。」", "check": ("威慑", 11),
             "success": {"narration": "你的声音压过了火把的噼啪。圣殿派的长老先站到你身后，接着是半个会场，「彻查条例」被否决。铁锤派领队收起撬棍时看了你一眼——你赢了今晚，也上了他的名单。有些胜利是租来的。", "fx": {"skill:威慑": 2, "skill:共情": 1, "flag:reformer": 1},
              "extra": [{"deed": "hammer_endured", "min": 1, "now": True,
                         "text": "\n他收撬棍用的是左手。右手绑在腰带上，说是旧伤。\n"
                                 "「不该先撕开皮肉。」——你说这句话的时候，他的左手停了半秒。\n"
                                 "名单上还是有你的名字。他自己写的。"}]},
             "failure": {"narration": "「反对得最响的，往往藏得最深。」领队一句话把你的抗辩拧成了供词。你被「暂缓除名」，编入观察名单——从此每顿饭有人陪你吃，每次出门有人替你记路线。誓约屋的火光从此照不暖你。", "fx": {"heat": 2, "skill:坚忍": 1}}},
            {"text": "连夜出走，去找传闻中的灰港。",
             "effects": {"narration": "你翻出后墙时没有回头。手掌在墙头蹭掉了一层皮——这是你从誓约带走的唯一一样东西。誓约给过你春麦和忏悔夜，也给过你火把和名单。城市的霓虹在远处涨潮——下一世的你，会怎么讲今晚的故事？",
                         "fx": {"skill:街智": 1, "skill:坚忍": 1},
                         "extra": [{"deed": "harbor_sunk", "min": 1,
                                    "text": "\n路上有人告诉你：灰港沉了，前些年的事。\n"
                                            "你问那现在去哪儿。\n"
                                            "对方看了你一会儿，报了一个地名——不在海边，\n"
                                            "天色也不灰。「还是那个地方。」他说。\n"
                                            "「只要还有人要找它，它就还在。换个门牌而已。」"}]}},
        ], factions=["purist"], echoes=[
            {"deed": "hammer_wrist", "min": 1, "now": True,
             "text": "提出「彻查条例」的是铁锤派领队。他站在火把最亮的地方，用左手举着撬棍。\n"
                     "右手垂在身侧，袖口比别人长一寸。\n"
                     "广场上每一个人都在看他举起来的那只手。"},
        ]),
    "discreet": _ev("finale_discreet", (
        "【终幕】有人把面具沙龙的成员名单挂上了暗网，标价出售，四十八小时后公开。卖家匿名，样章里已有三个名字——一位外科医生，一位学区督导，一位连任两届的社区议员。第四个是你的恩人，第五个位置还空着。沙龙炸了锅。是赎买、是追凶，还是先跑？"),
        [
            {"text": "追进暗网，扒出卖家。", "check": ("电子直觉", 12),
             "success": {"narration": "三层跳板之后，卖家的真身让你愣住：沙龙自己的账房。他的机化率八十七，会费吃掉他月薪的三分之一——他替所有人管钱，却快被自己的身体税压垮了。你没有报官，而是当众把账摊开：体面这门生意，成本从来没被算清过。名单撤了，沙龙立了新规：会费按机化率累进，穷会员减免。", "fx": {"skill:电子直觉": 2, "skill:逻辑": 1, "flag:reformer": 1},
              "extra": [{"deed": "mask_null_revealed", "min": 1, "now": True,
                         "text": "\n你把整份名单从头看到尾。创始人不在上面。\n"
                                 "不是被漏掉了——是查无此人。\n"
                                 "一份记录改造的名单，记不下一个没有改造过的人。"}]},
             "failure": {"narration": "你追到第三层跳板时踩了警报，反被溯源。四十八小时后名单没有公开——因为卖家用它换了自己的安全离场。所有人都保住了脸面，除了正义。沙龙恢复了营业，面具重新戴好，好像那四十八小时从未发生。", "fx": {"heat": 2, "skill:电子直觉": 1}}},
            {"text": "组织众筹赎买名单。", "check": ("共情", 11),
             "success": {"narration": "你挨个敲开会员的门：「平时装不认识，今晚认一次。」四十小时凑齐赎金。交割那晚，全沙龙的人第一次不戴面具围坐一室。", "fx": {"skill:共情": 2, "skill:街智": 1}},
             "failure": {"narration": "钱凑齐了，卖家却坐地起价。最后是恩人自己站了出来，公开了自己的义体清单：「不装了，累了。」他失去了三个董事席位，赢回了睡眠。你陪他走出沙龙。他的步子比你见过的任何一次都轻。", "fx": {"skill:共情": 1, "skill:坚忍": 1}}},
            {"text": "劝大家：干脆都别赎，集体亮相。", "check": ("威慑", 12),
             "success": {"narration": "「名单能威胁我们，是因为我们同意被威胁。」四十八小时后，名单公开的同一分钟，一百二十七位会员在广场同步展示了自己的接缝。敲诈落空，头条易主。心照不宣死了，一个新东西活了。", "fx": {"skill:威慑": 2, "skill:共情": 1, "flag:riot": 1}},
             "failure": {"narration": "响应者不足十人。名单公开，沙龙星散，恩人远走外城。你在空荡的会所里收拾面具，把它们一只只挂回墙上——像给一个时代收殓。", "fx": {"skill:坚忍": 2, "heat": 1}}},
        ], factions=["discreet"]),
    "open": _ev("finale_open", (
        "【终幕】明焰大游行前夜，工厂区爆炸：一批廉价义肢的电池批量自燃，伤者过百。最小的十四岁，左臂的义肢是全家借钱装的。舆论掉头：「看吧，改造就是自焚。」纯血誓约的集会一夜三倍。游行组委会连夜开会：明天，还走不走？"),
        [
            {"text": "走。但把游行改成献血与检修队。", "check": ("共情", 11),
             "success": {"narration": "第二天，三千人的游行队伍开进工厂区：血站车、检修台、免费更换的电池。口号只有一句：「我们负责。」一周后民调回升——不是因为你们赢了辩论，是因为你们出现在了病床边。", "fx": {"skill:共情": 2, "skill:威慑": 1, "flag:reformer": 1}},
             "failure": {"narration": "检修队被愤怒的家属堵在厂门口。你们在骂声里修了一整天义肢，没人道谢。深夜收工时，一个老太太默默塞给你们一篮鸡蛋。她没说话，你们也没说话。鸡蛋还是温的。", "fx": {"skill:坚忍": 2, "skill:共情": 1}}},
            {"text": "查爆炸原因——太整齐了，不像事故。", "check": ("逻辑", 12),
             "success": {"narration": "批次号指向同一家代工厂，而代工厂三个月前刚被拒保——保险公司的黑名单，正是那段「出厂限制」遥测数据养出来的。你把证据链甩上头条：不是义肢自焚，是有人纵火于数据。游行照常，横幅换成了《谁在给我们的身体定价》。", "fx": {"skill:逻辑": 3, "flag:archive": 1}},
             "failure": {"narration": "线索在代工厂的空壳公司里断了头。你只能证明可疑，不能证明纵火。游行缩水一半，但你把调查笔记整理成册——下一个追查的人会从第三章开始，而不是第一页。", "fx": {"skill:逻辑": 1, "skill:坚忍": 1}}},
            {"text": "取消游行，先安置伤者。",
             "effects": {"narration": "组委会吵到凌晨四点，你的提案通过。游行经费全数转为医疗基金。有人骂你软弱，有人说你救了运动的良心。多年后你才明白，软弱和良心有时候是同一块骨头的两面。", "fx": {"skill:共情": 1, "skill:逻辑": 1}}},
        ], factions=["open"]),
    "ascension": _ev("finale_ascension", (
        "【终幕】升格夜。飞升螺旋为你安排了「最后一步」：把剩余的生物脑组织完整上载，\n"
        "肉身归零，成为纯粹的心智。舱门已开，凝胶微温。\n"
        "导师最后问了一遍：「确认吗？舱门关上之后，就没有『回头』这个函数了。」"),
        [
            {"text": "进舱。完成飞升。", "check": ("坚忍", 11),
             "success": {"narration": "上载持续了六小时四十一分。你在第三小时想起一碗清汤面的热气，第五小时想起渡轮的尾流。数据流的尽头，你睁开了没有眼睑的眼睛：世界是一片可以直接阅读的光。你飞升了。至于「你」还是不是你——下一世会替你回答。", "fx": {"aug": 100, "skill:电子直觉": 2, "skill:机械亲和": 1, "flag:ascended": 1}},
             "failure": {"narration": "第四小时，你的旧脑挣扎了——一段童年记忆拒绝迁移，像一只扒住门框的手。紧急中止。你躺在舱里大口喘气，导师说这很常见：「有人的锚沉得深。明年再来。」你带着那段不肯走的童年回了家，第一次觉得它重得可爱。", "fx": {"skill:坚忍": 2, "skill:共情": 1}}},
            {"text": "「上载可以，但我要保留一具巡检肉身。」", "check": ("逻辑", 11),
             "success": {"narration": "「云端为主，肉身为锚」——你的折中方案让导师们辩论了一整晚，最终写进了教义修订案。你成了螺旋史上第一个「双栖者」——档案上永远差一个百分点，而那一个百分点就是你留下的那具肉身。每次下载回肉身的前三秒，你什么都感觉不到；第四秒重力回来了，第五秒你尝到了自己的口水。然后你走去地下通道，听一次原装嗓子的歌。", "fx": {"aug": 20, "skill:逻辑": 2, "flag:reformer": 1}},
             "failure": {"narration": "「半只脚的飞升不是飞升。」导师摇头。你被移出了本期名单。走出会所时你并不沮丧——门槛还在，说明门也还在。", "fx": {"skill:逻辑": 1, "skill:坚忍": 1}}},
            {"text": "退出。「我想再当几年会痛的东西。」",
             "effects": {"narration": "导师沉默良久，竟然鞠了一躬：「螺旋需要留在地面的证人。」你走出会所，冷风灌进衣领，冻得你直哆嗦——你把这阵哆嗦从头到尾体验了一遍，像品一口烈酒。", "fx": {"skill:共情": 2, "skill:坚忍": 1}}},
        ], factions=["ascension"]),
    "ascension_seed": _ev("finale_ascension_seed", (
        "【终幕】升格夜。飞升螺旋为你安排了「最后一步」：把剩余的生物脑组织完整上载，\n"
        "肉身归零，成为纯粹的心智。舱门已开，凝胶微温。\n"
        "导师最后问了一遍：「确认吗？舱门关上之后，就没有『回头』这个函数了。」\n"
        "探针的旅程注定孤独，没有群智在另一端等你。属于你的是无尽的旅途，\n"
        "以及一封可能等到收件人全部死去才会抵达的信。"),
        [
            {"text": "进舱。完成飞升。", "check": ("坚忍", 11),
             "success": {"narration": "上载持续了六小时四十一分。你在第三小时想起一碗清汤面的热气，第五小时想起渡轮的尾流。数据流的尽头，你睁开了没有眼睑的眼睛：世界是一片可以直接阅读的光。你飞升了。至于「你」还是不是你——下一世会替你回答。", "fx": {"aug": 100, "skill:电子直觉": 2, "skill:机械亲和": 1, "flag:ascended": 1}},
             "failure": {"narration": "第四小时，你的旧脑挣扎了——一段童年记忆拒绝迁移，像一只扒住门框的手。紧急中止。你躺在舱里大口喘气，导师说这很常见：「有人的锚沉得深。明年再来。」你带着那段不肯走的童年回了家，第一次觉得它重得可爱。", "fx": {"skill:坚忍": 2, "skill:共情": 1}}},
            {"text": "「上载可以，但我要保留一具巡检肉身。」", "check": ("逻辑", 11),
             "success": {"narration": "「云端为舟，肉身为岸。」\n"
                         "\n"
                         "你的方案让播种者争论了一整夜。最后，探针带走云端的你；巡检肉身留在地面，守着一座会越来越安静的接收站。\n"
                         "\n"
                         "起初你们还能同步。后来延迟变成几分钟、几小时、几年。终于有一天，探针上的你和地面上的你同时说出「我」，指的却已不是同一个人。\n"
                         "\n"
                         "档案上仍然差一个百分点。但那一个百分点不再只是肉身——也是两颗星之间不断增长的距离。\n"
                         "\n"
                         "下载回肉身的第四秒，重力回来；第五秒，你尝到自己的口水。与此同时，远方的你第一次看见一颗从未被这座城命名的星。",
                         "fx": {"aug": 20, "skill:逻辑": 2, "flag:reformer": 1}},
             "failure": {"narration": "「半只脚的飞升不是飞升。」导师摇头。你被移出了本期名单。走出会所时你并不沮丧——门槛还在，说明门也还在。", "fx": {"skill:逻辑": 1, "skill:坚忍": 1}}},
            {"text": "退出。「我想再当几年会痛的东西。」",
             "effects": {"narration": "导师沉默良久，竟然鞠了一躬：「螺旋需要留在地面的证人。」你走出会所，冷风灌进衣领，冻得你直哆嗦——你把这阵哆嗦从头到尾体验了一遍，像品一口烈酒。", "fx": {"skill:共情": 2, "skill:坚忍": 1}}},
        ], factions=["ascension"], subs=["播种者"]),
}

# 终幕熟悉度：第一次照常讲全；第二次起只折开场。
# 选项、检定、回响照旧。结果也不是跟着开场一起折，而是精确到
# 「同一终幕 + 同一选项 + 同一种结果」见过之后，下一次才换成短版。
FINALE_SHORT_TEXT = {
    "finale_purist": (
        "【终幕】誓约大会。又是火把、皮下声纳和「彻查条例」：所有人必须当众证明自己的血肉。"
        "四分钟里，你身体内部的每一道回声都将属于所有人。\n\n"
        "铁锤派领队看向你。\n\n"
        "「就从你开始吧。」"),
    "finale_discreet": (
        "【终幕】面具沙龙的名单始终被人盯着，又一次出现在暗网上。\n\n"
        "名单上不可能全是陌生人，你的熟人在其中占有位置，尚未填入名字的空位也暗示着你的风险。"
        "沙龙等待你决定：追出卖家、花钱赎回，还是让名单失去威胁人的力量。"),
    "finale_open": (
        "【终幕】大游行前夜总是睡不安稳的，工厂区再次起火，廉价义肢成批自燃，伤者过百；"
        "舆论已经把事故写成了对一切改造的判决。\n\n"
        "明天的游行，一夜之间从宣言变成了审判。\n\n"
        "组委会问：还走不走？"),
    "finale_ascension": (
        "【终幕】升格夜。熟悉的舱门再次打开，凝胶仍是微温。\n\n"
        "飞升螺旋不再向你解释上载意味着什么。导师只问：\n\n"
        "「这一次，确认吗？」"),
    "finale_ascension_seed": (
        "【终幕】升格夜。熟悉的舱门再次打开，凝胶仍是微温。\n\n"
        "飞升螺旋不再向你解释上载意味着什么。导师只问：\n\n"
        "「这一次，确认吗？」"),
    "finale_harbor": (
        "【终幕】潮水又一次没有退去。灰港正在下沉。\n\n"
        "防水箱里装着十七年的账簿；诊所里还有最后一批能让人继续生活的义肢。"
        "码头长让你离开，但你只能带走一样东西。\n\n"
        "也可以什么都不带，只替灰港关上最后一道门。"),
    "finale_dog": (
        "【终幕】这一次，你仍然没有回城。\n\n"
        "你拆掉手指，换上四趾关节；跌倒很多次，狗群每一次都等你。"
        "最老的那只始终走在旁边，与你保持同样缓慢的速度。\n\n"
        "出生时，你用四条腿行走。中间一段人生，你用两条腿行走。"
        "最后的日子，你再次用四条腿行走。\n\n"
        "你呼朋引伴，度过了无悔的时光。"),
}

# key = (终幕 id, 选项编号, "success" / "failure" / "effects")。
# 这些短版只替换 narration；掷骰、数值变化、条件尾巴、碎片仍按原逻辑全文展示。
FINALE_RESULT_SHORT = {
    ("finale_purist", 1, "success"):
        "声纳仍在那道旧疤前停住。领队最终宣告：「纯血。疤是疤，不是接缝。」你清白离场，疑问却没有。",
    ("finale_purist", 1, "failure"):
        "声纳没找到金属，却照出了你的怨气。你再次被调去最偏的哨所。",
    ("finale_purist", 2, "success"):
        "你再次压过火把，否决了「彻查条例」。今晚赢了；领队的名单上仍有你。",
    ("finale_purist", 2, "failure"):
        "领队仍把反对拧成供词。你进入观察名单，从此出门与吃饭都有人替你记录。",
    ("finale_purist", 3, "effects"):
        "你翻过后墙，只带走掌心蹭破的一层皮。誓约留在身后，城市的霓虹仍在远处涨潮。",

    ("finale_discreet", 1, "success"):
        "三层跳板后，卖家仍是沙龙账房。你公开账目，名单撤下，会费按机化率累进的新规再次立住。",
    ("finale_discreet", 1, "failure"):
        "你在第三层踩响警报，反遭溯源。卖家用名单换得离场，沙龙继续假装那四十八小时没有发生。",
    ("finale_discreet", 2, "success"):
        "四十小时，赎金再次凑齐。交割之夜，沙龙会员不戴面具地围坐一室。",
    ("finale_discreet", 2, "failure"):
        "赎金仍追不上卖家的涨价。恩人公开义体清单，失去席位，也重新睡得着觉。",
    ("finale_discreet", 3, "success"):
        "一百二十七人同步亮出接缝，敲诈再次失效。心照不宣死去，另一个东西活下来。",
    ("finale_discreet", 3, "failure"):
        "响应者仍不足十人。名单公开，沙龙星散；你把面具一只只挂回墙上。",

    ("finale_open", 1, "success"):
        "三千人的队伍再次变成血站车与检修台。你们没有赢辩论，只是又一次站到了病床边。",
    ("finale_open", 1, "failure"):
        "检修队仍在骂声里修到深夜。没人道谢；那篮温热的鸡蛋还是被悄悄递来。",
    ("finale_open", 2, "success"):
        "批次、拒保与遥测再次连成证据链：不是义肢自焚，是有人纵火于数据。",
    ("finale_open", 2, "failure"):
        "线索仍断在空壳公司。你把调查整理成册，让后来者从第三章开始。",
    ("finale_open", 3, "effects"):
        "游行再次取消，经费转进医疗基金。软弱与良心，仍像同一块骨头的两面。",

    ("finale_ascension", 1, "success"):
        "六小时四十一分后，你再次睁开没有眼睑的眼睛。世界成为可直接阅读的光；"
        "「你」是否仍是你，留给下一世。",
    ("finale_ascension", 1, "failure"):
        "旧脑里仍有一段童年扒住门框。上载中止；你把那份沉重带回家。",
    ("finale_ascension", 2, "success"):
        "「云端为主，肉身为锚」再次成立。你仍是差一个百分点的双栖者，"
        "第四秒重力回来，第五秒尝到口水。",
    ("finale_ascension", 2, "failure"):
        "导师仍不接受半只脚的飞升。你被移出名单；门槛还在，门也还在。",
    ("finale_ascension", 3, "effects"):
        "导师再次向留在地面的证人鞠躬。冷风灌进衣领，你从头到尾尝完这阵哆嗦。",

    ("finale_ascension_seed", 1, "success"):
        "六小时四十一分后，你再次睁开没有眼睑的眼睛。世界成为可直接阅读的光；"
        "「你」是否仍是你，留给下一世。",
    ("finale_ascension_seed", 1, "failure"):
        "旧脑里仍有一段童年扒住门框。上载中止；你把那份沉重带回家。",
    ("finale_ascension_seed", 2, "success"):
        "「云端为舟，肉身为岸」再次成立。你仍差一个百分点；"
        "第四秒重力回来时，远方的你再次看见一颗未被这座城命名的星。",
    ("finale_ascension_seed", 2, "failure"):
        "导师仍不接受半只脚的飞升。你被移出名单；门槛还在，门也还在。",
    ("finale_ascension_seed", 3, "effects"):
        "导师再次向留在地面的证人鞠躬。冷风灌进衣领，你从头到尾尝完这阵哆嗦。",

    ("finale_harbor", 1, "success"):
        "你再次把账簿顶过涨水，十七年的编码安然抵达高地。灰港沉了，来源与去向仍在。",
    ("finale_harbor", 1, "failure"):
        "箱锁再次断在第三个拐弯。纸页吸水，十七年的编码化成灰色的糊。",
    ("finale_harbor", 2, "success"):
        "六副义肢再次被你带上高地。等了一夜的人接过它们：「灰港没了，你还在就行。」",
    ("finale_harbor", 2, "failure"):
        "水流只让你带出两副。第三副散在堤坝前，那根保持握笔弧度的食指独自漂走。",
    ("finale_harbor", 3, "effects"):
        "你再次从里面锁门，再从窗户翻出。灰港沉下去，湿透的钥匙仍留在活人手里。",

    ("finale_dog", 1, "effects"):
        "狗群又一次卧成圆圈，外沿朝着风。你在它们的余热里合上眼睛。",
}

# 残响暴露的处决事件（heat 顶满 + 反改造阵营的强制结局）
EXPOSURE_END = {
    "purist": ("echo_executed",
        "流言终于长成了证据链。铁锤派深夜破门，搜出的不是金属——是你写满前世术语的笔记。你自己都没注意到那些词是什么时候混进你的字迹的。「肉是纯的，脑子是脏的。」审判只用了一炷香。你被逐出誓约，逐出公社，逐出所有花名册，在无人区的冬天里，故事到此为止。"),
    "discreet": ("echo_burned",
        "你积攒的疑点终于超过了沙龙的容忍上限。没有审判，没有通知——只是所有的门同时对你关上了：\n"
        "诊所约不上号，固件收不到更新，熟人看你的眼神像看一张过期的会员卡。\n"
        "在这座靠人情运转的城市里，被除名等于被蒸发。"),
    "open": ("anchor_demoted",
        "「你不是反对改造，」评议会主席翻着你的记录，「你只是从来没有一次不留手。」\n"
        "他念出你历年的犹豫：推迟的升级、保住的旧关节、替旧型号说过的话。\n"
        "明焰不烧摇摆的人，它只是不再给你添柴——排名撤销，项目转手，实验室的门禁改了码。\n"
        "你在明处待了一辈子，最后是被光晾干的。"),
    "ascension": ("anchor_too_heavy",
        "导师们最后一次调阅你的锚重曲线，摇了摇头：「太沉了。」\n"
        "不是背叛，不是异端——只是数据说，你迁移不过去。你被永久移出名单，\n"
        "档案里给了个温和的词：留驻者。此后每次发射，你都在地面上抬头，\n"
        "看那些比你轻的人先走。"),
}

# 选项门槛印给玩家时的中文说法。只需要登记真正被 req 用到的那几个。
# 事迹的中文说法。两处用得到：灰掉的选项要印「需要 …」，
# 重问三题之前要把「你这几世实际做了什么」摆出来。
#
# 后一个用途是回应一句批评：**派系是开局问出来的，不是打出来的。**
# 引擎不该替玩家判定「你的行为背离了你的派系」—— 那是替他下结论；
# 但它可以**把他做过的事念给他听，然后再问一遍**。
# 注意到，仍然是玩家的功劳。（2026-08-08 试玩反馈）
DEED_NAMES = {
    "honest": "诚实纳税的记录",
    "temple_vault": "进过圣殿的密室",
    "front_triage": "在义诊点分过诊",
    "secret_friend": "替朋友瞒下了一截铁",
    "betrayer": "按誓约举报过一个人",
    "informer": "给要被砸的诊所报过信",
    "reformer": "改过一条写死的规矩",
    "riot": "在队伍里带头喊过",
    "archive": "抄过烧剩的残稿",
    "gave_it_away": "把兜里所有的钱给了一个盲眼老人",
    "duet": "在地下通道跟人合唱过",
    "dog_friend": "喂过一条断腿的狗",
    "became_dog": "做过一次不是人的活法",
    "temple_doubt": "在自己的教义上停顿过",
    "temple_heretic": "私下抄了另一个字",
    "temple_revealed": "当众打开过那间密室",
    "merged": "并联过别人的意识",
    "ascended": "把自己整个搬上去过",
    "favor_elite": "替体面人挡过一次",
    "hymn_joined": "下楼跟着唱过一次",
    "cc_named": "问出过一个孩子的名字",
    "still_asking": "拿到过一枚还在求救的芯片",
    "clause_used": "翻开誓约替人念过条款",
    "tax_hand": "指过稽查员那只工伤的手",
    "harbor_run": "替灰港跑过一趟货",
    "drank_with_singer": "跟人蹲在地上喝过一口",
}

# 选项门写成 ("seen", 事件id, N) 时，印给玩家看的是这里的中文说法。
# 没登记的退回泛称 —— 但泛称等于没说，登记一下。
EVENT_NAMES = {
    "old_singer": "在地下通道与老歌手合唱过",
    "old_singer_high": "在地下通道听过那个歌手",
    "night_library": "上过夜间图书馆的天台",
    "ferry_night": "坐过末班渡轮",
    "riverbank": "去过河堤",
}

# **忘川**对一条线失忆。
#
# 世界记忆是不衰减的 —— 这是这个游戏的地基，飞升封档都清不掉它。
# 但作者给河堤那条线定了一条例外：动过那台手术之后，无论成没成，
# **cc 就不在了，换一世也不行。**
#
# 让她回来的那口水**是左边那口** —— 沉默、放弃答那句话、喝下忘川。
# （2026-08-08 作者改定：原先挂在「喝对了」那一口上。挂在忘川上更对 ——
#  河堤是高机化专属的线，而喝对了水的人会变回 0% 的血肉，
#  **他要么留住纯血那条路，要么留住她，两个不能都要。**
#  放弃答对本身就是代价，代价不该由引擎来收，该由玩家自己付。）
#
# 所以这里列的是「忘川会冲掉的那几笔」。**清单必须是白名单**——
# 写成「除了 X 都清掉」的话，以后每加一个 flag 都要重新想一遍它该不该被冲掉。
LAKE_FORGETS_DEEDS = (
    "cc_glimpse", "cc_named", "cc_knot", "cc_song", "cc_who", "cc_hand",
    "cc_wish", "cc_truth", "cc_help", "cc_ascended", "cc_dead", "cc_gone",
    "cc_knot_found", "cc_knot_made", "cc_called", "child_farm",
    "cc_closed",
)
LAKE_FORGETS_SEEN = ("riverbank",)


def _lake_forget(world):
    """喝下忘川的人，河堤那条线整条归零 —— 连「见过几次」一起。

    返回：这一次到底冲掉了东西没有（没见过 cc 的人喝忘川，什么也不会发生）。
    """
    hit = False
    deeds = world.get("deeds") or {}
    for k in LAKE_FORGETS_DEEDS:
        if deeds.pop(k, None):
            hit = True
    seen = world.get("seen") or {}
    for k in LAKE_FORGETS_SEEN:
        if seen.pop(k, None):
            hit = True
    return hit


# 暴露结局的条件尾巴：只有同时经历过别处的人，才多读到这一段。
# 语法与回响、变体、条件尾巴同源（_cond_level）。
EXPOSURE_TAILS = {
    "purist": [
        {"deed": "hammer_leader_secret", "min": 1, "now": True,
         "text": "\n——破门的是领队。他进屋之后先把人都支到院子里，只留自己和你。\n"
                 "他用左手翻你的笔记，翻得很慢。\n"
                 "「你替我瞒了一件事。」他说。「这件事我瞒不了你。」\n"
                 "他把笔记合上，走出去，对院子里的人点了头。\n"
                 "从头到尾，他没有再说一个字。"},
    ],
}

# ---------------------------------------------------------------------------
# 存档
# ---------------------------------------------------------------------------

def load_legacy():
    if os.path.exists(LEGACY_PATH):
        try:
            with open(LEGACY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None

def save_legacy(data):
    os.makedirs(SAVE_DIR, exist_ok=True)
    with open(LEGACY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --- 进行中的一世：每次 choose 之后落盘，客户端重启也不会丢掉一生 ---

def save_current(state, rng):
    if state is None:
        return
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        blob = {"state": state, "rng": _rng_dump(rng)}
        tmp = CURRENT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
        os.replace(tmp, CURRENT_PATH)
    except OSError:
        pass  # 存不下也不该打断游戏

def load_current():
    if not os.path.exists(CURRENT_PATH):
        return None, None
    try:
        with open(CURRENT_PATH, "r", encoding="utf-8") as f:
            blob = json.load(f)
        return blob.get("state"), _rng_load(blob.get("rng"))
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return None, None

def clear_current():
    try:
        if os.path.exists(CURRENT_PATH):
            os.remove(CURRENT_PATH)
    except OSError:
        pass

def _rng_dump(rng):
    v, keys, gauss = rng.getstate()
    return [v, list(keys), gauss]

def _rng_load(blob):
    if not blob:
        return None
    r = random.Random()
    r.setstate((blob[0], tuple(blob[1]), blob[2]))
    return r


# ---------------------------------------------------------------------------
# 交接信：跨世唯一由玩家亲手写的东西
#
# 引擎负责数字（技艺按机化率随机存活），交接信负责叙述。区别在于：
# 技艺的丢失是骰子造成的，叙述的失真是你自己造成的。
# 下一世开局只拿得到这封信，信里说什么就是什么——没有别的底本可以对照。
#
# 字数上限随机化率浮动（换机越多，内存越大，能带走的话越多）：
#   0% → 60 字，43% → 120 字，100% → 200 字。
# 纯血那一世只有 60 字：技艺一点都留不下，能穿过死亡的只剩你写的两句话。
# ---------------------------------------------------------------------------

TESTAMENT_BASE = 60
TESTAMENT_PER_AUG = 1.4

def testament_limit(aug):
    return int(TESTAMENT_BASE + TESTAMENT_PER_AUG * max(0, min(100, aug)))

def _count_chars(text):
    return _unit_len(text)

# ---------------------------------------------------------------------------
# 封存模式（disclosure）
#
# open   —— 原文照贴，AI 可以把一切转述给人类。
# sealed —— 引擎为每一段输出额外生成一份「可转述版」：只有事件的形状
#           （第几幕、阵营、做了什么类型的决定、代价），没有场景原文、
#           没有检定数字、没有技能之声。
#
# 说实话：这道封条在技术上是零。文本仍然完整地送到 AI 眼前，人类随时可以
# 开 --cli 自己跑，或者直接读这个文件。它靠的不是权限，是约定——和这个
# 游戏里其他所有东西一样。约定的边界也写在这里：封的是文本，不是事实。
# 人类直接问「刚才发生了什么」，AI 应当照实回答。
# ---------------------------------------------------------------------------

DISCLOSURE_MODES = ("open", "sealed")

# ---------------------------------------------------------------------------
# 三种玩法（mode）—— 为「人和 AI 一起玩」这件事而设
#
# 引擎决定**输出什么**，AI 决定**转述什么**。这两件事必须分开写清楚，
# 否则每个模型的转述习惯都不一样，人类就永远不知道自己会拿到什么。
#
#   story  详细剧情版 —— 原文照读。人逐幕做选择。第一次玩用这个。
#   brief  快速流程版 —— 场景原文照读（那是作品，不许改写），
#                        结果由引擎压成两行战报。人仍然逐幕做选择。
#   auto   你自己玩   —— AI 做选择，只把战报行念给人。
#   sealed 封存       —— 同 auto，但连「选了什么」都不转述。给「我以后要自己玩」的人。
#
# 场景原文和结果的分界线是有道理的：**场景是作品，结果是信息。**
# 概括一段信息无损，概括一段作品有损。所以 brief 压的是后者。
# ---------------------------------------------------------------------------

MODES = ("story", "story_ai", "brief", "brief_ai", "auto", "sealed")
# 「没选模式就不开局」只对 MCP 那条路生效。
# --cli 是人自己在玩，没有中间人要约束；自测更不需要有人在旁边点菜单。
REQUIRE_MODE = True
_LEGACY_MODE = {"open": "story", "sealed": "sealed"}   # 老的 disclosure 参数

MODE_MENU = """《忒修斯之脑》—— 一个给 AI 和人一起玩的文字游戏。

开始之前需要一个决定，而这个决定是人类的。
请把下面这张菜单**原样念给你的人类**，等他选，然后再开局：

  1  详细剧情版 —— 原文一字不改念给你。第一次玩推荐这个。
  2  快速流程版 —— 场景照读，结果用两行战报报给你。想快点走完一世选这个。
  3  快进       —— 我全包，只报战报。（这一档没得选，选择只能我来做。）

选了 1 或 2 之后，还要再问一句：**这一世谁来做选择？**

  你来选 —— 每一幕我把选项念给你，等你说编号。
  我来选 —— 我代入角色做选择，你随时可以插手改。

他选好之后：

  1 · 你来选 → new_run(mode="story")      1 · 我来选 → new_run(mode="story_ai")
  2 · 你来选 → new_run(mode="brief")      2 · 我来选 → new_run(mode="brief_ai")
  3          → new_run(mode="auto")

（如果他说「我以后想自己玩，别剧透我」，用 mode="sealed"。）
如果他人不在、或者明确说了「你看着办」，就用 mode="auto"。
**不要替他选。这一步是这个游戏送给人类的唯一一个开关。**"""

MODE_HINT = {
    "story": "【本局：详细剧情版 · 他来选】场景、选项、掷骰行全部原样念给人类，由他选编号。",
    "story_ai": "【本局：详细剧情版 · 你来选】场景与选项照样原文念给人类——但**不要等他**，"
                "念完自己代入角色 choose。他插话就听他的。",
    "brief": "【本局：快速流程版 · 他来选】场景原文照读，结果只念下面那两行战报。选项交给人类选。",
    "brief_ai": "【本局：快速流程版 · 你来选】场景原文照读，结果念战报，"
                "**选择你自己做**，不必等他。",
    "auto":  "【本局：你自己玩】不必转述场景，只把下面那行战报念给人类，然后自己 choose。",
    "sealed": "【本局：封存】只转述下面那一段。人类直接问「刚才发生了什么」时，照实回答。",
}


def _bar(st):
    """状态条。每一次输出都带 —— 人看不见自己是谁的时候，玩不下去。"""
    fac = FACTIONS[st["faction"]]
    where = T("开局") if st["turn"] < 1 else T("第 %d/%d 幕") % (st["turn"], MAX_TURNS)
    return T("%s · %s·%s · 机化 %d%% · 身体 %d/%d · %s %d/8") % (
        where, T(fac["name"]), T(st["sub"]), st["aug"],
        st["hp"], MAX_HP, heat_label(st["faction"]), st["heat"])


def _fx_digest(fx, heat_name="疑云"):
    """把 fx 翻成人话。flag 不出现 —— 那是机关，不是给人看的。

    heat 那一栏四个阵营各有各的名字（疑云／锚重），得跟状态条用同一个。
    此前战报块一律写「疑云」，而明焰飞升的状态条写「锚重」——
    同一个数值两个名字，玩家会以为有两个系统。（2026-08-08 试玩反馈 B）
    """
    if not fx:
        return ""
    bits = []
    for k, v in fx.items():
        if k.startswith("skill:"):
            bits.append(T("%s%+d") % (T(k[6:]), v))
        elif k == "aug":
            bits.append(T("机化%+d%%") % v)
        elif k == "heat":
            bits.append(T("%s%+d") % (heat_name, v))
        elif k == "hp":
            bits.append(T("身体%+d") % v)
    return " · ".join(bits)


# 岔口重复走同一条时的一行替身。第一次给全文，之后给这里的句子。
#
# **改造的内容可以复用，改造的代价不该复用。**
# 第一次是二十四期分期，第二次是每天多花的三秒，第三次该有第三种痛 ——
# 所以按「这一世第几次」换，而且四个档位各有各的账。
# （2026-08-08 试玩反馈：「你又点了一次头」是全篇最省力的一行。）
_OFFER_AGAIN = {
    "aug_offer_0": {
        True: ["你又签了一次字。分期从二十四期变成了六十期。",
               "第二台机器上身那天，你发现自己已经不问疼不疼了，只问几天能上工。",
               "第三次。签字的手很稳 —— 稳得让你自己看了一会儿。"],
        False: ["你又一次没有点头。",
                "老板没再抬头。他把价目表往里挪了半寸，那是给下一个人腾地方。",
                "这一次你连门口都没站。绕了一条街。"],
    },
    "aug_offer_1": {
        True: ["接缝又多了一道。缝隙师说这一道更难藏，因为它离脸更近。",
               "出门前的三秒钟变成了六秒。你开始把镜子挪到门边。",
               "第三次之后你不再数秒了。你只是站在那儿，直到自己觉得可以了。"],
        False: ["你又一次没去那个地址。",
                "卡片被你翻到了钱包最里层。它还在。",
                "这一次你想不起卡片夹在哪一格了。你没有找。"],
    },
    "aug_offer_2": {
        True: ["又升了一档。这次省下的时间，你已经算不清是省给谁的了。",
               "第二次升级之后，旧型号的同事跟你说话会慢半拍等你 —— 你注意到了，没说。",
               "第三次。参数表你只看了最后一行：寿命未知。"],
        False: ["你没升。",
                "会上又有人替你解释了一句。这次那句话说得更短。",
                "这一次没有人替你解释。"],
    },
    "aug_offer_3": {
        True: ["又换了几样。清单上剩下的那几行你没细看。",
               "第二次之后，食堂那张餐卡你没再带过。",
               "第三次。顾问问你要不要留一样，你说不用了，然后想了很久为什么不用。"],
        False: ["你没换。",
                "顾问在「留驻意愿」那一栏又打了一个勾。这次他没抬头。",
                "这一次他连表格都没拿出来。"],
    },
}


def _offer_again_line(eid, took, times):
    """岔口第 N 次走同一条时的那一行。四个档位各有各的账。"""
    tbl = _OFFER_AGAIN.get(eid) or _OFFER_AGAIN["aug_offer_0"]
    rows = tbl[bool(took)]
    return rows[min(max(times - 1, 0), len(rows) - 1)]




def _dedupe(ids):
    """牌堆去重，保序。

    **一张牌只能在牌堆里出现一次。** 不去重的话有一个隐蔽的涨法：
    一幕里没有合格的牌时，发牌循环会把整副牌洗回来再走一遍，
    而「跳过但不弃」的那一摞和洗回来的那一副会拼在一起写回去 ——
    牌堆每发一次干牌就翻一倍，第 27 世一世要跑一分钟。
    （2026-08-08：退场表上线之后「没有合格的牌」变成常态，这个老 bug 才露头。）
    """
    seen, out = set(), []
    for eid in ids:
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


def _check_skill(check, skills):
    """检定用哪一项技能。

    `("共情", 11)` —— 就这一项。
    `(("共情", "街智"), 11)` —— **两项里取高的那个**。

    为什么要有第二种：同一个动作常常有两种做法。
    「提前给诊所报信」既可以是心软，也可以是熟门熟路；
    只写一项，就会逼着玩家**为了掷得过而不像自己** ——
    而这个游戏最不想要的就是那种玩法。（2026-08-08 试玩反馈）
    """
    name, dc = check
    if isinstance(name, tuple):
        name = max(name, key=lambda k: skills.get(k, 0))
    return name, dc


def _relay_line(st, kind, detail=""):
    fac = T(FACTIONS[st["faction"]]["name"])
    head = T("第 %d/%d 幕 · %s · 机化%d%%") % (st["turn"], MAX_TURNS, fac, st["aug"])
    return "  ".join(x for x in (head, kind, detail) if x)


# ---------------------------------------------------------------------------
# 记忆词条：唯一由玩家亲手写、并可能穿过死亡的东西
# ---------------------------------------------------------------------------

def _mem(legacy):
    m = legacy.get("memory")
    if not isinstance(m, dict) or "entries" not in m:
        m = _default_memory()
        legacy["memory"] = m
    m.setdefault("entries", [])
    m.setdefault("pending", None)
    return m

def _mem_len(text):
    """数字数：空白不算，其余一律一字一算（中文英文标点同等对待）。"""
    return _unit_len(text)

def _mem_render(entries, indent="  "):
    if not entries:
        return [indent + T("（空）")]
    return [indent + T("〔第%d世〕%s") % (e["run"], e["text"]) for e in entries]

def _mem_roll(legacy, aug, rng, run_no):
    """逐条掷骰。返回 (存活, 湮灭)。机化 0% 时无条件全灭。"""
    m = _mem(legacy)
    kept, lost = [], []
    for e in m["entries"]:
        if aug > 0 and rng.random() < aug / 100.0:
            kept.append(e)
        else:
            lost.append(e)
    m["entries"] = kept
    m["pending"] = None
    return kept, lost

# ---------------------------------------------------------------------------
# 封存模式：给人类的那一份
#
# 说明清楚 —— 这是约定，不是锁。文本是 AI 打给你的，任何人都能自己开 --cli
# 或者直接读源码。封存的是「转述」，不是「事实」：AI 可以按规则不贴原文，
# 但你直接问它发生了什么，它必须照实说。
# ---------------------------------------------------------------------------

DISCLOSURE_MODES = ("open", "sealed")

def _seal_block(mode, lines):
    out = ["", T("─── 念给人类的部分 ───")]
    out += ["  " + l for l in lines]
    out.append("  " + MODE_HINT.get(mode, ""))
    return "\n".join(out)

# ---------------------------------------------------------------------------
# 游戏状态机
# ---------------------------------------------------------------------------

class Game:
    def __init__(self):
        self.state = None  # None = 没有进行中的对局
        self._rng = random.Random()
        self._restore()

    # ---------------- 断线续命 ----------------
    def _restore(self):
        """从 saves/current.json 捞回进行中的一世。
        进程可以死，客户端可以重启，但一生不该因此丢掉。"""
        state, rng = load_current()
        if state is None or rng is None:
            return False
        try:
            # 拆出播种者终幕之前，进行中的存档会把两支都记成 finale_ascension。
            # 若热更新恰好发生在终幕选择页，恢复时把待选幕和本世见闻一起迁到新 ID；
            # 已经结算进世界记忆的旧经历不改，它们看到的本来就是旧文案。
            if (state.get("pending") == "finale_ascension"
                    and state.get("faction") == "ascension"
                    and state.get("sub") == "播种者"):
                state["pending"] = "finale_ascension_seed"
                state["used_events"] = [
                    "finale_ascension_seed" if eid == "finale_ascension" else eid
                    for eid in state.get("used_events", [])
                ]
            if (state.get("pending") and not state.get("final")
                    and not state.get("deathbed")
                    and not state.get("drychoice")):
                if self._find_event_static(state["pending"]) is None:
                    return False          # 事件表改过了，旧档作废
            self.state, self._rng = state, rng
            return True
        except Exception:
            return False

    @staticmethod
    def _find_event_static(eid):
        for f in FINALES.values():
            if f["id"] == eid:
                return f
        for e in EVENTS:
            if e["id"] == eid:
                return e
        return None

    def _persist(self):
        save_current(self.state, self._rng)

    # ---------------- 开局 ----------------
    def new_run(self, seed=None, wish=None, disclosure=None, mode=None):
        # 种子永远落到一个具体的数字上。不给就现摇一个，但摇完记下来 ——
        # **一个复现不了的 bug 报告等于没报。** status 会把它和选择序列一起吐出来。
        if seed is None:
            seed = random.randrange(2 ** 31)
        rng = random.Random(seed)
        self._rng = rng
        legacy = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
        world = legacy.get("world") or _default_world()
        legacy["world"] = world
        run_no = legacy.get("runs", 0) + 1

        # 玩法模式：粘性设置，一次设定，之后每一世沿用。
        # **没设过就不开局** —— 服务器手里攥着游戏，AI 想玩就得先去问人。
        # 这是整套设计里唯一强制得了的一步，而它恰好是最该强制的那一步。
        if mode is None and disclosure is not None:
            mode = _LEGACY_MODE.get(str(disclosure).strip().lower())
            if mode is None:
                return T("disclosure 只收 open 或 sealed。新写法请用 mode。这一世还没开始。")
        if mode is not None:
            mode = str(mode).strip().lower()
            if mode not in MODES:
                return (T("mode 只有这几种：story / story_ai / brief / brief_ai /\n"
                        "auto / sealed。\n\n")
                        + MODE_MENU)
            legacy["mode"] = mode
            save_legacy(legacy)
        if not legacy.get("mode"):
            if REQUIRE_MODE:
                return MODE_MENU
            legacy["mode"] = "story"
            save_legacy(legacy)
        self._mode = legacy["mode"]                     # 也写进 state，随 current.json 落盘

        # 渡魂签：旧机制沿用旧名字和旧代价，但只在后期清扫阶段开放。
        # 它不另造库存；wish 本身就是那张签。烧掉约三分之一待继承技艺，
        # 把下一世送到指定机化档，派系仍由三问决定。
        wish_note = None
        if wish is not None:
            key = WISH_MAP.get(str(wish).strip())
            if key is None:
                return (T("【渡魂签】签上没有这个去处。可写：纯血誓约 / 心照不宣 / "
                        "明焰 / 飞升螺旋。\n这一世还没开始。"))
            if world.get("lake"):
                return T("【渡魂签】湖已经在等你。先在湖边作答；这一世还没开始。")
            if not _late_game(world):
                return T("【渡魂签】档案还没薄到能看见这张签。先继续走；这一世还没开始。")
            carried = dict(legacy.get("skills") or {})
            total = sum(carried.values())
            if total <= 0:
                return T("【渡魂签】空手的魂渡不了。待继承技艺为 0；这一世还没开始。")
            cost = max(1, total // WISH_COST_DIVISOR)
            pool = [skill for skill, value in carried.items() for _ in range(value)]
            for skill in rng.sample(pool, cost):
                carried[skill] -= 1
            legacy["skills"] = {skill: value for skill, value in carried.items() if value > 0}
            legacy["aug"] = WISH_AUG[key]
            legacy["sub"] = None
            save_legacy(legacy)
            wish_note = (T("【渡魂签】签纸烧掉 %d 点待继承技艺，把这一世送往【%s】。")
                         % (cost, T(FACTIONS[key]["name"])))

        # 上一世死了却没落笔 —— 掷骰照掷，只是没有新词条参加
        drank = (world or {}).get("drank")
        if world.get("lake"):
            # 没说话，或者说错了 —— 走的是左边那口，忘川。
            # **忘川不还身体。** 机化率仍然是 100%，下一世还是飞升，
            # 湖会在下一次上载之后重新出现，你可以再试。
            #
            # 2026-08-08 作者定案：**答不对就永远在 100% 循环。**
            # 我此前让两口水都归零，理由是「说错话会锁死」——
            # 但那不是锁死，那是循环：每一次上载都重开一次门。
            # 而把归零这件事只留给答对的人，纯血才真的贵。
            world["lake"] = None
            save_legacy(legacy)

        mem = _mem(legacy)
        forfeit = None
        if drank and mem.get("pending"):
            mem["pending"] = None         # 过了河的记忆不再掷骰
            save_legacy(legacy)
        if mem.get("pending"):
            pend = mem["pending"]
            kept, lost = _mem_roll(legacy, pend["aug"], rng, pend["run"])
            save_legacy(legacy)
            forfeit = (pend, kept, lost)

        # 落幕之后就没有下一世了。再调 new_run 只把那一幕再念一遍。
        if world.get("curtain"):
            kind = world["curtain"]
            text = EPILOGUE if kind == "epilogue" else CURTAIN[kind]
            return text + CURTAIN_TAIL

        # 全书终：每条线都走过一面，而且走完了金叶子那条路。
        # 排在终局前面 —— 渡口每隔几世就会重新浮现，排在后面的话它永远轮不到。
        # 只念一次。念完这一世照常开始。
        if _story_done(world) and not world.get("epilogue_shown"):
            world["epilogue_shown"] = run_no
            save_legacy(legacy)
            return EPILOGUE

        # 终局：五块碎片集齐后，转世抵达渡口。
        # 表过态之后雾不会永远散去——每隔 FINAL_COOLDOWN 世，渡口重新浮现，
        # 你可以改主意，而改主意本身会留在档案里。
        if len(world["fragments"]) >= len(FRAGMENTS):
            due = (not world.get("final_done")
                   or run_no - world.get("final_runs", 0) >= FINAL_COOLDOWN)
            if due:
                if world.get("final_wait"):
                    world["final_wait"] = False
                    save_legacy(legacy)
                else:
                    return self._start_final(legacy)

        # 机化率跨世累积，只涨不降。正常轮回里，喝过谟涅摩绪涅之水才会归零；
        # 后期渡魂签是付出技艺换来的清扫捷径。
        # **只归零一次** —— 标记现在能活过好几世（见下），
        # 不加 announced 这个条件的话，过河之后每一世开局都会被重新按回 0%。
        if drank and not drank.get("announced"):
            legacy["aug"] = 0
            legacy["sub"] = None
        aug = max(0, min(100, int(legacy.get("aug") or 0)))
        # 过河标记原先只活一世 —— 错过一次就得重跑 0→100→湖 整整一轮十几世。
        # 改成：**只要你还没沾铁，河就一直算过了。** 动第一刀的那一刻才作废。
        # 难度没降（仍然要先换尽、死透、答对那句话），降的只是「错过一次的代价」。
        # （2026-08-08 试玩反馈）
        if aug > 0 and (world or {}).get("drank"):
            world["drank"] = None
        drank = (world or {}).get("drank")
        fac_key = aug_tier(aug)
        fac = FACTIONS[fac_key]
        # 派系不掷骰：沿用上一世问出来的那个；这一档还没问过，开局先问三题。
        sub_name = legacy.get("sub")
        sub_lookup = {n: (n, d, sk) for n, d, sk in fac["sub"]}
        if sub_name not in sub_lookup:
            sub_name, sub_desc, sub_skill = fac["sub"][0]   # 三问答完会改写
            need_lean = True
            lean_reask = False
        else:
            sub_name, sub_desc, sub_skill = sub_lookup[sub_name]
            # 同一档待满 LEAN_REASK_LIVES 世就重问一次三题。
            # 换不换不由引擎决定 —— 他自己答出来是什么就是什么。
            need_lean = (int(legacy.get("runs") or 0)
                         - int(legacy.get("lean_run") or 0)) >= LEAN_REASK_LIVES
            lean_reask = need_lean

        # 时代骰：解锁的时代随对应事迹越做越重——历世行为在改写骰子本身
        era_pool, era_wts = [], []
        for e in ERAS:
            if e["unlock"] is None:
                era_pool.append(e); era_wts.append(10)
            else:
                deed, need = e["unlock"]
                cnt = world["deeds"].get(deed, 0)
                if cnt >= need:
                    era_pool.append(e); era_wts.append(10 + 5 * cnt)
        era = rng.choices(era_pool, weights=era_wts, k=1)[0]

        skills = {s: 1 for s in SKILLS}
        for s, v in fac["base"].items():
            skills[s] += v
        skills[sub_skill] = min(MAX_SKILL, skills[sub_skill] + 1)

        # 继承前世
        inherited = {}
        inherited_total = 0
        if legacy.get("skills"):
            for s, v in legacy["skills"].items():
                if s in skills and v > 0:
                    skills[s] = min(MAX_SKILL, skills[s] + v)
                    inherited[s] = v
                    inherited_total += v

        # 反改造阵营 × 携带机械记忆 → 初始疑云（残响）
        heat = 0
        if fac["stance"] in ("anti", "hidden") and inherited_total > 0:
            machine_carry = sum(inherited.get(s, 0) for s in MACHINE_SKILLS)
            heat = min(4, (inherited_total + 1) // 3 + machine_carry // 2)
        elif fac["stance"] == "pro" and inherited_total == 0 and run_no > 1:
            # 对称的另一半：在越机械越体面的地方，一个空手来的魂同样可疑——
            # 什么也没继承下来，说明上一世的你终究是块肉。
            heat = 3

        self.state = {
            "seed": seed,
            "run_no": run_no,
            "cycle": legacy.get("cycle", 1),
            "faction": fac_key,
            "sub": sub_name,
            "era": era["id"],
            "skills": skills,
            "aug": aug,
            "hp": MAX_HP,
            "heat": heat,
            "turn": 0,
            "flags": {},
            "used_events": [], "variant": None,
            "finale_results_shown": [], # 本世读到的终幕结果，落幕时并入世界记忆
            "inherited": inherited,
            "world": json.loads(json.dumps(world)),
            "disclosure": self._mode, "mode": self._mode,
            "seed": seed, "choices": [],
            "crossed": bool(drank),   # 这一世是过了河来的
            "lean_return": None,      # 三问答完之后回哪里
            "offered": False,         # 这一幕的改造机会给过了没有
            "gen_drawn": 0,           # 这一世从牌堆发过几张
            "late_targeted": False,   # 后期每世至多定向发一张推进牌
            "var_shown": [],          # 这一世读到过哪几个变体（退场计数用）
            "aug_taken": 0,           # 这一世动过几次刀（上限 AUG_PER_LIFE）
            "recovery_said": False,
            "aug_declined": 0,        # 这一世**连着**拒绝过几次改造
            "declined_said": False,
            "aug_opportunities": 0,   # 这一世已经用掉几次改造机会（答完才算）
            "opportunities_said": False,
            "offer_prompt": False,    # 兼容旧存档停在简化二选一的状态
            "tier_pending": None,
            "pending": None,   # 当前待选事件
            "over": False,
            "final": False,
        }

        lines = []
        lines.append("╔══════════════════════════════════╗")
        cyc_run = run_no - int(legacy.get("cycle_base") or 0)
        lines.append(T("  《 忒 修 斯 之 脑 》 第 %d 谱系 · 第 %d 世（累计第 %d 世）")
                     % (self.state["cycle"], cyc_run, run_no))
        lines.append("╚══════════════════════════════════╝")
        lines.append("")
        lines.append(T("时代骰落下：【%s】") % T(era["name"]))
        lines.append(T("  时代：%s") % T(era["desc"]))
        if world.get("final_ending"):
            lines.append("  " + FINAL_AFTER[world["final_ending"]])
        if wish_note:
            lines.append("  " + wish_note)
        lines.append("")
        if aug == 0 and run_no == 1:
            lines.append(T("你生下来是一副没有改过的身体。全城大多数人都是这样开始的。"))
        elif aug == 0:
            lines.append(T("你又一次生在一副没有改过的身体里。"))
        else:
            lines.append(T("你带着上一世的 %d%% 醒来。改造不会随普通死亡归零。") % aug)
        lines.append("")
        lines.append(T("现在的你 —— 【%s · %s】") % (T(fac["name"]), T(sub_name)))
        lines.append("  %s" % T(fac["desc"]))
        lines.append("  %s" % T(sub_desc))
        lines.append("")
        lines.append(T("初始机化率：%d%%    身体：%d/%d") % (aug, MAX_HP, MAX_HP))
        lines.append(T("（铭记：改造是单向的。肉一旦让位，不会回来——义体从不退货。）"))
        if inherited_total > 0:
            lines.append("")
            lines.append(T("【残响】前世的技艺穿过死亡跟了过来："))
            lines.append("  " + T("、").join(T("%s+%d") % (T(s), v) for s, v in sorted(inherited.items())))
            if heat > 0:
                lines.append(T("  但在这个阵营里，这些「不该会的东西」是危险的。初始疑云：%d/8") % heat)
            else:
                lines.append(T("  在这个阵营里，没人会为你多懂一些东西而皱眉。"))
        elif run_no > 1 and not drank:
            lines.append("")
            lines.append(T("【湮灭】上一世什么也没能留下。纯粹的血肉，纯粹的遗忘。你从零开始。"))
        if drank and not drank.get("announced"):
            lines.append("")
            lines.append(T("【过河】你带着记忆，落在一具没有一钉一铆的身体里。"))
            drank["announced"] = True
            world["drank"] = drank
            save_legacy(legacy)
        if forfeit:
            pend, fkept, flost = forfeit
            lines.append("")
            lines.append(T("【未落笔】第%d世死时你没有写下任何词条。掷骰照掷：%d 条旧记忆存活，%d 条湮灭。")
                         % (pend["run"], len(fkept), len(flost)))
        mem_entries = _mem(legacy)["entries"]
        lines.append("")
        if mem_entries:
            lines.append(T("【记忆】穿过死亡的词条（%d/%d）：") % (len(mem_entries), MEMORY_SLOTS))
            lines += _mem_render(mem_entries)
            lines.append(T("  （这些是历世的你亲手写下的。没有上下文，没有出处，也没有人替你核对。）"))
        else:
            lines.append(T("【记忆】一条也没有。你不知道自己以前是谁。"))
        frag_n = len(world["fragments"])
        if 0 < frag_n < len(FRAGMENTS):
            lines.append("")
            lines.append(T("真相碎片：%d/%d（详见 legacy）") % (frag_n, len(FRAGMENTS)))
        lines.append("")
        lines.append(self._skill_sheet())
        lines.append("")
        # 见底：一幕都发不出来了。这一世不开始，改走三问／临终／落幕。
        dry = self._dry_curtain()
        if dry == "lean":
            # 本支讲完了，可同阵营另一支还有戏 —— 那就重问三题，让他自己选。
            # **不问就落幕的话，会把另一支整条吞掉。**（2026-08-08 试玩反馈）
            need_lean, lean_reask = True, True
        elif dry:
            if self.state["aug"] == 0:
                self.state["deathbed"] = True
                self.state["pending"] = "deathbed"
                lines.append(DEATHBED_TEXT)
                self.state["deathbed_text"] = "\n".join(lines)
                self._persist()
                return self._seal("\n".join(lines))
            if dry != "epilogue":
                # 还有别的档没讲完 —— 先问一句要不要把自己交上去
                self.state["drychoice"] = True
                self.state["pending"] = "drychoice"
                lines.append(self._dry_offer_text())
                self._persist()
                return self._seal("\n".join(lines))
            return self._curtain(legacy, world, dry)

        if need_lean:
            same = legacy.get("lean_same") or {}
            special = (lean_reask and same.get("faction") == fac_key
                       and int(same.get("count") or 0) >= 2)
            q1 = self._find_event("lean_other_life" if special else
                                  "lean_recap" if lean_reask else
                                  LEAN_FIRST[fac_key])
            self.state["lean_return"] = None
            self.state["pending"] = q1["id"]
            self.state["variant"] = self._variant_idx(q1)
            if lean_reask and not special:
                lines.append(T("你在这一档待了几世了。有三个问题，上一次也问过——\n"
                             "隔了这么久，答案未必还是同一个。"))
            elif not lean_reask:
                lines.append(T("在开始之前，有三个问题。没有对错，只是问问你自己。"))
            lines.append("")
            lines.append(self._render_event(q1))
        else:
            lines.append(self._next_event())
        self._persist()
        return self._seal("\n".join(lines))

    # ---------------- 事件抽取 ----------------
    def _eligible(self, ev):
        st = self.state
        if ev.get("subscene"):
            return False          # 子幕只能被 then 领进来
        if ev["id"] in st["used_events"]:
            return False
        if ev["factions"] != "any" and st["faction"] not in ev["factions"]:
            return False
        if ev.get("subs") and st.get("sub") not in ev["subs"]:
            return False
        if ev["min_aug"] is not None and st["aug"] < ev["min_aug"]:
            return False
        if ev["max_aug"] is not None and st["aug"] > ev["max_aug"]:
            return False
        if ev["min_heat"] is not None and st["heat"] < ev["min_heat"]:
            return False
        if ev["id"].startswith("echo_") and not st["inherited"]:
            return False
        if ev.get("req_seen"):
            seen = (st.get("world") or {}).get("seen") or {}
            for k, v in ev["req_seen"].items():
                if seen.get(k, 0) < v:
                    return False
        if ev.get("req_seen_any"):
            seen = (st.get("world") or {}).get("seen") or {}
            if not any(seen.get(k, 0) >= v for k, v in ev["req_seen_any"].items()):
                return False
        if _is_retired(ev, st.get("world") or {}):
            return False
        if ev.get("req_deed"):
            deeds = (st.get("world") or {}).get("deeds") or {}
            for k, v in ev["req_deed"].items():
                if deeds.get(k, 0) < v:
                    return False
        return True

    # 跨世近因衰减：上一世刚见过的事件，这一世出场概率压低。
    # 只压不封 —— 回响机制依赖重逢，压到 0 就没有「船长又认出你」了。
    RECENCY_DECAY = [0.30, 0.55, 0.80]

    def _recency_mul(self):
        out = {}
        recent = (self.state.get("world") or {}).get("recent") or []
        for depth, ids in enumerate(recent[:len(self.RECENCY_DECAY)]):
            f = self.RECENCY_DECAY[depth]
            for eid in ids:
                out[eid] = min(out.get(eid, 1.0), f)
        return out

    def _ticket_on_cooldown(self, eid):
        """未拿到碎片时入口不会退场，但同一入口不连续两世刷脸。"""
        world = self.state.get("world") or {}
        got = set(world.get("fragments") or [])
        last_life = (world.get("recent") or [[]])[0]
        return any(fid not in got and eid in ticket["events"] and eid in last_life
                   for fid, ticket in FRAGMENT_TICKETS.items())

    def _ticket_retry_ids(self):
        """连续若干世没碰到合档的碎片入口 → 返回本世应保底的事件。"""
        st = self.state
        world = st.get("world") or {}
        got = set(world.get("fragments") or [])
        recent = world.get("recent") or []
        recent_ids = {eid for life in recent[:FRAGMENT_RETRY_LIVES] for eid in life}
        ids = set()
        for fid, t in FRAGMENT_TICKETS.items():
            if fid in got or not t["events"]:
                continue
            if st["faction"] != t["faction"]:
                continue
            lo, hi = t["aug"]
            if not (lo <= st["aug"] <= hi):
                continue
            if t["needs"] and any(f in st["flags"] for f in t["needs"]):
                continue  # 本世的门已经开过了，不必再刷
            if not recent_ids.intersection(t["events"]):
                ids.update(t["events"])
        return ids

    def _generic_ids(self):
        return [e["id"] for e in EVENTS
                if e["factions"] == "any" and not e.get("subscene")]

    def _reshuffle(self, legacy):
        """洗牌。时代骰偏爱的事件更容易排在前面 —— 加权洗牌，不是加权抽取。"""
        wmul = self._era().get("wmul", {})
        keyed = []
        for eid in self._generic_ids():
            w = max(0.05, wmul.get(eid, 1))
            # Efraimidis–Spirakis：u^(1/w) 越大排越前，等价于按权重不放回抽样
            keyed.append((self._rng.random() ** (1.0 / w), eid))
        keyed.sort(reverse=True)
        legacy["deck"] = [eid for _, eid in keyed]
        return legacy["deck"]

    def _draw_generic(self):
        """从牌堆里发一张合格的。

        不合格的（req_seen 还没开门）**跳过但不弃牌** —— 它还在这一轮里等着。
        整副发完才洗。
        """
        legacy = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
        deck = legacy.get("deck")
        if not deck:
            deck = self._reshuffle(legacy)
        held, pick, reshuffled = [], None, False
        while True:
            if not deck:
                if reshuffled:
                    break             # 整副都试过了，这一幕没有合格的牌
                deck = self._reshuffle(legacy)
                reshuffled = True
                if not deck:
                    break
            eid = deck.pop(0)
            ev = self._find_event(eid)
            if (ev is not None and self._eligible(ev)
                    and not self._ticket_on_cooldown(eid)):
                pick = ev
                break
            held.append(eid)          # 这一轮还轮得到它，只是现在开不了门
        legacy["deck"] = _dedupe(held + deck)
        save_legacy(legacy)
        return pick

    def _faction_ids(self):
        """这一世够得着的派系牌。

        只看阵营与派系这两条**这一世不会变**的；`req_seen`、机化率区间、
        碎片门票留到发牌时判 —— 和通用牌一样，不合格的跳过但不弃牌。
        """
        st = self.state
        out = []
        for e in EVENTS:
            if e["factions"] == "any" or e.get("subscene"):
                continue
            if st["faction"] not in e["factions"]:
                continue
            if e.get("subs") and st.get("sub") not in e["subs"]:
                continue
            out.append(e["id"])
        return out

    def _reshuffle_fac(self, legacy):
        """派系牌堆。key 是（阵营，派系）—— 换了派系就是另一副牌。"""
        st = self.state
        wmul = self._era().get("wmul", {})
        keyed = []
        for eid in self._faction_ids():
            ev = self._find_event(eid)
            w = ev["weight"] * wmul.get(eid, 1)
            keyed.append((self._rng.random() ** (1.0 / max(0.05, w)), eid))
        keyed.sort(reverse=True)
        legacy["deck_fac"] = {"key": "%s/%s" % (st["faction"], st.get("sub")),
                              "cards": [eid for _, eid in keyed]}
        return legacy["deck_fac"]["cards"]

    def _draw_faction(self):
        """从派系牌堆里发一张。整副发完才洗 —— 一条线走完之前不会重复。

        （2026-08-08 试玩反馈：派系那边原先是纯加权随机，
        于是三世里同一幕能撞好几次。牌堆这一层当初只给了通用事件。）
        """
        st = self.state
        legacy = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
        box = legacy.get("deck_fac") or {}
        key = "%s/%s" % (st["faction"], st.get("sub"))
        deck = box.get("cards") if box.get("key") == key else None
        if not deck:
            deck = self._reshuffle_fac(legacy)
        held, pick, reshuffled = [], None, False
        while True:
            if not deck:
                if reshuffled:
                    break
                deck = self._reshuffle_fac(legacy)
                reshuffled = True
                if not deck:
                    break
            eid = deck.pop(0)
            ev = self._find_event(eid)
            if (ev is not None and self._eligible(ev)
                    and not self._ticket_on_cooldown(eid)):
                pick = ev
                break
            held.append(eid)
        legacy["deck_fac"] = {"key": key, "cards": _dedupe(held + deck)}
        save_legacy(legacy)
        return pick

    def _deed_digest(self):
        """把这份档案做得最多的几件事念出来。

        **不下结论，只念事实。** 引擎不判「你背离了你的派系」——
        那是替玩家想；它只把他做过的摆出来，然后再问一遍。
        """
        deeds = ((self.state or {}).get("world") or {}).get("deeds") or {}
        named = [(v, T(DEED_NAMES[k])) for k, v in deeds.items() if k in DEED_NAMES]
        named.sort(key=lambda kv: -kv[0])
        if not named:
            return T("〔这几世你什么也没留下。至少没有留下别人记得住的。〕")
        top = named[:3]
        return T("〔你做得最多的几件事：%s。〕") % "；".join(t for _, t in top)

    def _dry_line(self):
        """牌发干时的那一行。**跟着还剩多少条线走** ——

        同一句话在最后十几世每世念一遍，是情绪杀手；
        让它随剩余条数递减，grind 就变成了铺垫。（2026-08-08 试玩反馈）
        """
        done, total = _retired_count((self.state or {}).get("world") or {})
        left = total - done
        if left <= 0:
            return T("〔没有了。一件也没有了。〕")
        if left <= 3:
            return T("〔你隐约觉得，下一世可能是最后一次了。〕")
        if left <= 10:
            return T("〔故事越来越少了。〕")
        return T("〔这座城今天没有新的事发生。你径直走到了这一世的尽头。〕")

    def _is_dry(self, sub=None):
        """这座城还发得出一幕吗？

        用**这一世开头**的状态问：`used_events` 还空着，所以答案只取决于
        退场表和门控，不取决于这一世已经走过什么。
        `sub` 给了就按那个派系问 —— 用来判断「换一支还有没有戏」。
        """
        st = self.state
        keep_used, keep_sub = st["used_events"], st.get("sub")
        st["used_events"] = []
        if sub is not None:
            st["sub"] = sub
        try:
            return not any(self._eligible(e) for e in EVENTS)
        finally:
            st["used_events"] = keep_used
            st["sub"] = keep_sub

    def _next_band_with_story(self):
        """比现在这一档更高、而且还发得出戏的那一档。没有就 None。

        两支各问一遍 —— 只要有一支还有戏，那一档就值得往前走。
        """
        st = self.state
        order = ["purist", "discreet", "open", "ascension"]
        if st["faction"] not in order:
            return None
        keep = (st["faction"], st.get("sub"), st["aug"], st["used_events"])
        st["used_events"] = []
        try:
            for nxt in order[order.index(st["faction"]) + 1:]:
                st["faction"] = nxt
                st["aug"] = AUG_OF[FACTIONS[nxt]["name"]]
                for name, _, _ in FACTIONS[nxt]["sub"]:
                    st["sub"] = name
                    if any(self._eligible(e) for e in EVENTS):
                        return nxt
        finally:
            (st["faction"], st["sub"], st["aug"], st["used_events"]) = keep
        return None

    def _dry_offer_text(self):
        """走不动了那一幕给哪一版 —— 顺手把「往哪一档走」记进 state。"""
        step = self._next_band_with_story()
        self.state["dry_step"] = step
        return DRY_CHOICE_STEP_TEXT if step else DRY_CHOICE_TEXT

    def _other_sub(self):
        """同一阵营里的另一支。四个阵营各有两支。"""
        pair = [n for n, _, _ in FACTIONS[self.state["faction"]]["sub"]]
        for n in pair:
            if n != self.state.get("sub"):
                return n
        return None

    def _dry_curtain(self, allow_lean=True):
        """见底了，落哪一幕。

        **先看换一支还有没有戏** —— 这是 DeepSeek 抓到的那个边界：
        本支讲完了不等于这一档讲完了，直接落幕会把同阵营的另一支整条吞掉。
        返回 "lean"（该重问三题）／"stars"／"epilogue"／None（没干）。
        """
        st = self.state
        if not self._is_dry():
            return None
        if allow_lean:
            other = self._other_sub()
            if other and not self._is_dry(sub=other):
                return "lean"
        if _story_done(st.get("world") or {}):
            return "epilogue"     # 每条线都见过，金叶子那条路也走完了
        return "stars"

    def _pick_event(self):
        """这一幕给通用牌堆，还是给派系牌堆？

        按剩下的配额掷 —— 自然穿插，而不是「先四幕通用再五幕派系」。
        任何一边空了就全给另一边。
        """
        st = self.state
        # 后期每世只定向一次：先偿还到期的碎片入口，否则发一张从未见过的线。
        # 其余幕仍走原来的两副牌，不把整个发牌器改成任务调度器。
        if not st.get("late_targeted") and _late_game(st.get("world") or {}):
            seen = (st.get("world") or {}).get("seen") or {}
            due = [self._find_event(eid) for eid in sorted(self._ticket_retry_ids())]
            due = [ev for ev in due if (ev is not None and self._eligible(ev)
                                        and not self._ticket_on_cooldown(ev["id"]))]
            unseen = [ev for ev in EVENTS
                      if (ev.get("retire_seen") or ev.get("retire_deed"))
                      and seen.get(ev["id"], 0) == 0 and self._eligible(ev)
                      and not self._ticket_on_cooldown(ev["id"])]
            target = self._rng.choice(due or unseen) if (due or unseen) else None
            if target is not None:
                st["late_targeted"] = True
                if target["factions"] == "any":
                    st["gen_drawn"] = st.get("gen_drawn", 0) + 1
                return target
        acts_left = max(1, MAX_TURNS - st["turn"])
        gen_need = max(0, GENERIC_PER_LIFE - st.get("gen_drawn", 0))
        has_fac = any(e["factions"] != "any" and self._eligible(e) for e in EVENTS)
        want_gen = gen_need > 0 and self._rng.random() < min(1.0, gen_need / acts_left)
        if not has_fac and gen_need <= 0:
            return None             # 通用牌配额用完；没有派系戏就让这一世提前收束
        if not has_fac:
            want_gen = True
        if want_gen:
            ev = self._draw_generic()
            if ev is not None:
                st["gen_drawn"] = st.get("gen_drawn", 0) + 1
                return ev
        if has_fac:
            ev = self._draw_faction()
            if ev is not None:
                return ev
        if gen_need > 0:
            ev = self._draw_generic()
            if ev is not None:
                st["gen_drawn"] = st.get("gen_drawn", 0) + 1
                return ev
        return None

    def _faction_finale(self):
        """按主阵营取终幕；播种者有独立的远航版本。"""
        st = self.state
        key = ("ascension_seed"
               if st["faction"] == "ascension" and st.get("sub") == "播种者"
               else st["faction"])
        return FINALES[key]

    def _next_event(self):
        st = self.state
        # 新的一幕开始 —— 这一幕的改造机会还没给过。
        # 复位点必须在这里：此前放在 _choose_inner 末尾，于是「跨档 → 三问 → 回正轨」
        # 这条绕路会带着 offered=True 回来，把紧接着那一幕的岔口吞掉。
        # （2026-08-08 试玩反馈 A：每跨一次档少一次改造机会）
        st["offered"] = False
        st["turn"] += 1
        head = ""
        if st["turn"] >= MAX_TURNS:
            ev = self._override_finale() or self._faction_finale()
        else:
            ev = self._pick_event()
            if ev is None:
                # 讲完的线都退了场，剩下的牌这一世也发过了 ——
                # 这一世就到这儿。**早退不是 bug，是那张退场表走到尽头的样子。**
                # 改道表仍然要走：狗和灰港的终幕不该被这条捷径绕过去。
                ev = self._override_finale() or self._faction_finale()
                head = self._dry_line() + "\n\n"
        st["used_events"].append(ev["id"])
        st["pending"] = ev["id"]
        st["variant"] = self._variant_idx(ev)
        return head + self._render_event(ev)

    def _find_event(self, eid):
        if eid in (f["id"] for f in FINALES.values()):
            for f in FINALES.values():
                if f["id"] == eid:
                    return f
        for e in EVENTS:
            if e["id"] == eid:
                return e
        return None

    def _opt_available(self, opt):
        st = self.state
        req = opt.get("req")
        if not req:
            return True
        kind = req[0]
        if kind == "any":
            # ("any", [子条件, …]) —— 任意一条成立即可。
            # 跨派系的门常常有不止一条路走到（灰港出身／在铁锤派自己查出来的）。
            return any(self._opt_available({"req": r}) for r in req[1])
        if kind == "aug":
            return st["aug"] >= req[2]
        if kind == "skill":
            return st["skills"].get(req[1], 0) >= req[2]
        if kind == "flag":
            return st["flags"].get(req[1], 0) >= req[2]
        if kind == "noflag":
            return not st["flags"].get(req[1])
        if kind == "deed":
            return st["world"]["deeds"].get(req[1], 0) >= req[2]
        if kind == "seen":
            # 「见过某一幕 N 次」才开的选项门。事件级的 req_seen 已经有了，
            # 选项级的没有 —— 而「在河堤上唱一首你在地下通道听过的歌」
            # 正是只能挂在选项上的那种门。（2026-08-08 作者稿）
            return (st.get("world") or {}).get("seen", {}).get(req[1], 0) >= req[2]
        return True

    def _gate_open(self, gate):
        """条件分支的闸门。与 req 同一套语法，但不是「能不能选」，是「选了之后是哪一条」。

        写法：("noflag", "gave_it_away") —— 没有这个 flag 时走 success。
              ("flag", "x") / ("skill", "逻辑", 8) / ("deed", "honest", 3) / ("aug", ">=", 40)
        """
        st = self.state
        kind = gate[0]
        if kind == "noflag":
            return not st["flags"].get(gate[1])
        if kind == "flag":
            return bool(st["flags"].get(gate[1]))
        if kind == "skill":
            return st["skills"].get(gate[1], 0) >= gate[2]
        if kind == "nodeed":
            return (st["world"]["deeds"].get(gate[1], 0)
                    < (gate[2] if len(gate) > 2 else 1))
        if kind == "deed":
            return st["world"]["deeds"].get(gate[1], 0) >= gate[2]
        if kind == "aug":
            return st["aug"] >= gate[2]
        if kind == "seen":
            return ((st.get("world") or {}).get("seen") or {}).get(
                gate[1], 0) >= (gate[2] if len(gate) > 2 else 1)
        if kind == "aug_below":
            return st["aug"] < gate[1]
        return True

    def _era(self):
        eid = self.state.get("era")
        for e in ERAS:
            if e["id"] == eid:
                return e
        return ERAS[0]

    def _cond_level(self, e):
        """回响／变体的条件：满足则返回门槛值，不满足返回 None。

        单条：{"deed": x, "min": n} / {"seen": id, "min": n} / {"ach": id}
        复合：{"all": [子条件, …]} —— 全部满足才算，门槛取子条件里最大的那个。
              子条件里还可以写 {"aug": N}，指**本世当前机化率** ≥ N（不是世界记忆）。
        """
        w = self.state.get("world") or _default_world()
        if "any" in e:
            # 任意一条成立即可，门槛取命中的那些里最大的。
            best = None
            for sub in e["any"]:
                n, _ = self._cond_level(sub)
                if n is not None:
                    best = n if best is None else max(best, n)
            return best, ("any", id(e))
        if "all" in e:
            best = 0
            for sub in e["all"]:
                need, _ = self._cond_level(sub)
                if need is None:
                    return None, ("all", id(e))
                best = max(best, need)
            return best, ("all", id(e))
        if "turn" in e:
            # 本世走到第几幕。给同一幕做「这一世第二次、第三次遇到」的轮换用 ——
            # 岔口每一幕都出现，不轮换的话一世要读八遍同一张菜单。
            # （2026-08-08 试玩反馈：0% 档岔口一世 8 次，文案一字不差）
            n = e["turn"]
            return (n, ("turn", n)) if (self.state or {}).get("turn", 0) >= n else (None, ("turn", n))
        if "sub" in e:
            # 按**当前子派系**命中。用来给同一幕换一版更贴脸的说法 ——
            # 铁锤派的人被骂「圣殿派的软骨头」很成立；
            # 而你本人就是圣殿派的时候，同一句话变成废话。（2026-08-08 试玩反馈）
            ok = (self.state or {}).get("sub") == e["sub"]
            return (1, ("sub", e["sub"])) if ok else (None, ("sub", e["sub"]))
        if "aug" in e:
            n = e["aug"]
            return (n, ("aug", n)) if (self.state or {}).get("aug", 0) >= n else (None, ("aug", n))
        if "deed" in e:
            # 默认只数世界记忆（跨世）。写 "now": True 则连这一世还没结算的那一次一起数 ——
            # 终幕和暴露结局就发生在这一世里，这一世刚做过的事，此刻必须算数。
            have = self._tally(e["deed"]) if e.get("now") else w["deeds"].get(e["deed"], 0)
            key = ("deed", e["deed"])
        elif "seen" in e:
            have, key = w["seen"].get(e["seen"], 0), ("seen", e["seen"])
        elif "ach" in e:
            have, key = (1 if e["ach"] in w["achievements"] else 0), ("ach", e["ach"])
        else:
            return None, None
        need = e.get("min", 1)
        return (need, key) if have >= need else (None, key)

    def _tally(self, key):
        """某件事你一共做过几次 —— 世界记忆里的，加上这一世还没结算的那一次。"""
        st = self.state
        w = st.get("world") or _default_world()
        return w["deeds"].get(key, 0) + (1 if st["flags"].get(key) else 0)

    def _override_finale(self):
        """有些路走到第九幕就不回自己阵营的终幕了。按表顺序，第一个命中的赢。"""
        st = self.state
        for rule in FINALE_OVERRIDES:
            if rule["when"](self, st):
                ev = self._find_event(rule["id"])
                if ev is not None:
                    return ev
        return None

    def _variant_idx(self, ev):
        """命中的变体里，**列表靠后的赢**；都不命中返回 None。

        不能比门槛数字大小 —— 不同来源的数字没有可比性
        （`elevator_preacher>=2` 与 `preacher_death>=1`，2 比 1 大不代表它更晚发生）。
        所以顺序由写的人定：**把更晚发生的那一幕写在后面。**
        """
        world = (self.state or {}).get("world") or {}
        var_seen = world.get("var_seen") or {}
        hit = None
        for i, v in enumerate(ev.get("variants") or []):
            cap = v.get("retire_after")
            if cap and var_seen.get("%s#%d" % (ev["id"], i), 0) >= cap:
                continue          # 这一版讲完了，往前找上一版
            need, _ = self._cond_level(v)
            if need is not None:
                hit = i
        if hit is not None:
            st = self.state
            if st is not None:
                st.setdefault("var_shown", []).append("%s#%d" % (ev["id"], hit))
        return hit

    def _view(self, ev):
        """事件的「这一次的样子」：命中变体就用变体的，否则用本体的。"""
        st = self.state or {}
        i = st.get("variant")
        if i is None or not ev.get("variants") or i >= len(ev["variants"]):
            return ev
        v = ev["variants"][i]
        out = dict(ev)
        out["text"] = v.get("text", ev["text"])
        out["options"] = v.get("options", ev["options"])
        # 整幕变体（换了 text 的那种）本身就是回响长成的，默认不再叠。
        # 但**只换选项、不换正文**的局部变体不是那回事 —— 那一幕还是原来那一幕，
        # 回响该照给。（2026-08-08：给同一幕做子派系专属说法时踩到的）
        out["echoes"] = v.get("echoes", [] if "text" in v else ev["echoes"])
        out["voices"] = v.get("voices", ev.get("voices", {}))
        return out

    def _echoes_for(self, ev):
        # 跨世回响：同源多档位（如摆渡次数）只取最高一档。
        # 条件判定与变体共用 _cond_level，所以回响也能写 {"all": [...]}。
        by_src = {}
        for e in ev.get("echoes", []):
            need, key = self._cond_level(e)
            if need is None or key is None:
                continue
            cur = by_src.get(key)
            if cur is None or need > cur[0]:
                by_src[key] = (need, e)
        return [e for _, e in by_src.values()]

    def _render_event(self, ev):
        st = self.state
        ev = self._view(ev)
        lines = []
        if ev["id"].startswith("finale"):
            tag = T("终幕")
        elif ev["id"].startswith("lean_"):
            tag = T("三 问")
        elif ev["id"].startswith("aug_offer_"):
            tag = T("岔 口")
        elif ev.get("subscene"):
            tag = T("第 %d/%d 幕 · 续") % (st["turn"], MAX_TURNS)   # 同一场戏的下半截
        else:
            tag = T("第 %d/%d 幕") % (st["turn"], MAX_TURNS)
        lines.append("─── %s ───" % tag)
        lines.append(_bar(st))
        lines.append("")
        # 熟悉度压缩：见过 FOLD_SEEN 次之后，场景只留第一行。
        # **回响、技能之声、选项一个不少** —— 折的是重复的那部分，
        # 而回响恰恰是每一次都不一样的那部分。
        # （2026-08-08 试玩反馈：后半程抽来抽去就是那十几幕。）
        seen_n = ((st.get("world") or {}).get("seen") or {}).get(ev["id"], 0)
        fold_at = 1 if _late_game(st.get("world") or {}) else FOLD_SEEN
        fold = (seen_n >= fold_at and not ev["id"].startswith(("finale", "lean_"))
                and not ev.get("subscene"))
        # 终幕和普通事件分开算熟悉度：普通事件见过三次才折成一行；
        # 终幕第二次就换成专门写过的短开场。只换 text，下面的回响、技能之声、
        # 选项仍然逐项装配，所以跨世长出来的内容不会被一并折掉。
        finale_short = (FINALE_SHORT_TEXT.get(ev["id"])
                        if seen_n >= 1 else None)
        if ev["id"] == "lean_recap":
            lines.append(ev["text"])
            lines.append("")
            lines.append(self._deed_digest())
            fold = False
        elif finale_short is not None:
            lines.append(finale_short)
        elif fold:
            head_line = ev["text"].split("\n")[0]
            lines.append(head_line)
            lines.append(T("〔这一幕你已经走过几回了。场景略。〕"))
        else:
            lines.append(ev["text"])
        for e in self._echoes_for(ev):
            lines.append("")
            lines.append(T("【回响】") + e["text"])
        for skill, line in ev.get("voices", {}).items():
            if st["skills"].get(skill, 0) >= 8:
                lines.append("")
                lines.append(line)
        lines.append("")
        for i, opt in enumerate(ev["options"], 1):
            suffix = ""
            if "check" in opt:
                skill, dc = _check_skill(opt["check"], st["skills"])
                names = opt["check"][0]
                label = (T("／").join(T(n) for n in names) + T("（取高）")
                         if isinstance(names, tuple) else T(skill))
                suffix = T("  〔%s 检定，难度 %d，当前 %s=%d〕") % (
                    label, dc, T(skill), st["skills"][skill])
            elif "coin" in opt:
                suffix = T("  〔抛硬币，五五开。技能不算数〕")
            if not self._opt_available(opt):
                # 「不可选：机化率≥30%」会被读成「达到 30% 就不能选」——方向正好反了。
                # 一律写成「需要 …」。（2026-08-08 试玩反馈）
                req = opt.get("req")
                if req[0] == "aug":
                    need = T("机化率达到 %d%%") % req[2]
                elif req[0] == "skill":
                    need = T("%s 达到 %d") % (T(req[1]), req[2])
                elif req[0] == "seen":
                    need = T(EVENT_NAMES.get(req[1]) or "某一段旧事")
                    need += T("（%d 次）") % req[2] if req[2] > 1 else ""
                elif req[0] == "deed":
                    # 印给玩家看的是中文说法，不是 flag id。没登记的退回泛称。
                    need = T(DEED_NAMES.get(req[1]) or "特定的经历")
                    if req[2] > 1:
                        need += T("（%d 次）") % req[2]
                else:
                    need = T("特定的经历")
                lines.append(T("  %d. ✗ %s（不可选，需要 %s）") % (i, opt["text"], need))
            else:
                lines.append("  %d. %s%s" % (i, opt["text"], suffix))
        lines.append("")
        lines.append(T("用 choose 选择一个选项编号。"))
        return "\n".join(lines)

    # ---------------- 选择 ----------------
    def choose(self, n):
        out = self._choose_inner(n)
        self._persist()
        return self._seal(out)

    def _seal(self, out):
        """按本局模式，在输出末尾附一段「念给人类」的战报。

        story 不附 —— 那一版的约定就是原文照读，再加一块摘要只会让人分心。
        """
        st = self.state
        mode = (st or {}).get("mode") or getattr(self, "_mode", "story")
        # 落幕之后没有战报可报。
        # 这里不能拿一句写死的中文去认 —— 落幕正文走对照文件，界面走 ui.py，
        # 两边分头翻译就会对不上。所以拿落幕文本自己的头一行来认，换哪种语言都成立。
        if any(out.startswith(v.split("\n", 1)[0]) for v in CURTAIN.values()):
            return out
        if st is None or mode == "story":
            return out
        if mode == "story_ai":
            # 原文照念，但选择归 AI —— 战报块只留一行提醒，不重复剧情。
            return out + "\n" + _seal_block(mode, [_bar(st)])
        if st.get("final"):
            return out + "\n" + _seal_block(mode, [T("渡口。没有检定，只有表态。")])
        rows = [_bar(st)]
        if mode in ("brief", "brief_ai", "auto"):
            if st.get("last_choice"):
                rows.append(T("选了：%s") % st["last_choice"])
            if st.get("last_roll"):
                rows.append(st["last_roll"])
            if st.get("last_fx"):
                rows.append(T("变化：%s") % st["last_fx"])
        else:                                    # sealed：连选了什么都不说
            if st.get("last_beat"):
                rows.append(T("上一步：%s") % st["last_beat"])
        if st["over"]:
            rows.append(T("这一世已经结束。用 debrief 取战报。"))
        return out + "\n" + _seal_block(mode, rows)

    def _choose_inner(self, n):
        st = self.state
        if st is None or st["over"]:
            return T("当前没有进行中的对局。用 new_run 掷骰开始新的一世。")
        if st.get("final"):
            return self._final_choose(n)
        if st.get("deathbed"):
            return self._deathbed_choose(n)
        if st.get("drychoice"):
            return self._drychoice(n)
        ev = self._find_event(st["pending"])
        if ev is None:
            return T("内部错误：找不到当前事件。请 new_run 重开。")
        ev = self._view(ev)          # 玩家看见的是哪一版，就按哪一版结算

        # 旧存档兼容：旧规则的第二、三次机会可能停在「维持 / 重想」。
        # 新规则不会再生成这个状态，但已经停在这里的存档仍可继续。
        if st.get("offer_prompt"):
            if n not in (1, 2):
                return T("无效选项。请输入 1-2。")
            st["offer_prompt"] = False
            if n == 2:
                st.setdefault("choices", []).append(2)
                st["last_choice"] = T("重新考虑")
                st["last_roll"] = ""
                st["last_fx"] = T("无")
                st["last_beat"] = T("重新考虑改造")
                return self._render_event(ev)
            st["brief_maintain"] = True
            st["choice_override"] = 1       # 重放脚本记玩家按的 1，不记内部拒绝项编号
            st["choice_label_override"] = T("维持现状")
            return self._choose_inner(len(ev["options"]))

        if not (1 <= n <= len(ev["options"])):
            return T("无效选项。请输入 1-%d。") % len(ev["options"])
        opt = ev["options"][n - 1]
        if not self._opt_available(opt):
            return T("这个选项当前不可选（未满足条件）。换一个。")

        lines = []
        crit_skill = None
        if "check" in opt:
            skill, dc = _check_skill(opt["check"], st["skills"])
            d1, d2 = self._rng.randint(1, 6), self._rng.randint(1, 6)
            total = d1 + d2 + st["skills"][skill]
            if d1 == 6 and d2 == 6:
                ok = True
                crit_skill = skill
                lines.append(T("掷骰：6+6 —— 【双六】命运替你多押了一注。无条件成功。"))
            elif d1 == 1 and d2 == 1:
                ok = False
                lines.append(T("掷骰：1+1 —— 【蛇眼】骰子朝下的那一面，写着你的名字。无条件失败。"))
            else:
                ok = total >= dc
                lines.append(T("掷骰：%d+%d +%s%d = %d  vs 难度%d —— %s") % (
                    d1, d2, T(skill), st["skills"][skill], total, dc,
                    T("✦ 成功") if ok else T("✧ 失败")))
            lines.append("")
            outcome = opt["success"] if ok else opt["failure"]
            outcome_kind = "success" if ok else "failure"
        elif "gate" in opt:
            # 条件分支：结果不由骰子决定，由之前做过什么决定。
            # 不掷骰，也不告诉玩家闸门是什么——他要么想得起来自己做过什么，要么想不起来。
            ok = self._gate_open(opt["gate"])
            outcome = opt["success"] if ok else opt["failure"]
            outcome_kind = "success" if ok else "failure"
        elif "coin" in opt:
            # 硬币：全游戏唯一一处**与角色无关**的判定。
            # 不看技能，不看做过什么，也不受双六蛇眼影响 —— 五五开。
            # 有些事本来就不该由「你是谁」来决定，这一处的重量正来自这里。
            ok = self._rng.randint(1, 2) == 1
            lines.append(T("抛硬币：%s") % (T("正面") if ok else T("背面")))
            lines.append("")
            outcome = opt["success"] if ok else opt["failure"]
            outcome_kind = "success" if ok else "failure"
        else:
            outcome = opt["effects"]
            outcome_kind = "effects"

        # 岔口每世只结算一次。旧存档若停在简版提示上仍可继续，
        # 但新的一世不会再生成第二、第三次提示。
        if ev["id"].startswith("aug_offer_"):
            took = (outcome.get("fx") or {}).get("aug", 0) > 0
            st["aug_opportunities"] = st.get("aug_opportunities", 0) + 1
            if took:
                st["aug_declined"] = 0          # 点过一次头，重新数
            else:
                st["aug_declined"] = st.get("aug_declined", 0) + 1
            mark_o = "%s#%s#%d" % (ev["id"], st.get("variant"), n)
            done_o = st.setdefault("offer_taken", [])
            if st.pop("brief_maintain", False):
                lines.append(T("机会过去了。你维持了现状。"))
            elif mark_o in done_o:
                again = done_o.count(mark_o)
                lines.append(_offer_again_line(ev["id"], took, again))
            else:
                lines.append(outcome["narration"])
            done_o.append(mark_o)
            # 唯一一次拒绝就是承诺发生的时刻。若留到下一幕的通用
            # 「是否再给机会」检查才说，文本会粘在下一幕结果之后，
            # 像是在拒绝那一幕里的东西，而不是拒绝改造。
            if (not took and st.get("aug_declined", 0) >= AUG_DECLINE_CAP
                    and not st.get("declined_said")):
                st["declined_said"] = True
                lines.append("")
                lines.append(T("〔你拒绝了这一世唯一一次改造机会。这一世不会再有人问你。〕"))
        elif ev["id"].startswith("finale"):
            # 终幕结果的熟悉度比开场更细：必须是同一个终幕、同一个选项、
            # 同一种结果都见过，才换短版。成功见过不折失败，反之亦然。
            result_key = (ev["id"], n, outcome_kind)
            result_mark = "%s#%d#%s" % result_key
            result_seen = (((st.get("world") or {}).get("finale_results") or {})
                           .get(result_mark, 0))
            short_result = FINALE_RESULT_SHORT.get(result_key)
            lines.append(short_result if result_seen and short_result
                         else outcome["narration"])
            st.setdefault("finale_results_shown", []).append(result_mark)
        else:
            lines.append(outcome["narration"])
        # 条件尾巴：只有同时经历过别处的人，才多读到这一段。
        # 跨派系的接缝靠它 —— 不新开一幕，只在原来那一幕上多长出一句。
        for tail in outcome.get("extra") or []:
            need, _ = self._cond_level(tail)
            if need is not None:
                lines.append(tail["text"])
        st.setdefault("choices", []).append(st.pop("choice_override", n))
        st["last_choice"] = st.pop("choice_label_override", opt["text"])
        st["last_roll"] = lines[0] if (("check" in opt or "coin" in opt) and lines) else ""
        if "check" in opt:
            st["last_beat"] = T("%s 检定%s") % (skill, T("成功") if ok else T("失败"))
        elif "gate" in opt:
            st["last_beat"] = T("旧账结清") if ok else T("旧账找上门")
        elif "coin" in opt:
            st["last_beat"] = T("硬币的正面") if ok else T("硬币的背面")
        else:
            st["last_beat"] = T("无检定的选择")
        fx = dict(outcome.get("fx", {}))
        crit_would_be = None
        if crit_skill:
            k = "skill:" + crit_skill
            # 双六那 +1 到底落没落地，得先算出「没有它的话会是多少」。
            # 技能顶在 12 的时候它一点也加不上 —— 那就别宣布。
            # （2026-08-08 试玩反馈：满值时空挂一句「其中 X 的 +1 是双六给的」）
            crit_would_be = min(MAX_SKILL, st["skills"][crit_skill] + fx.get(k, 0))
            fx[k] = fx.get(k, 0) + 1
        fx_report = self._apply_fx(fx)
        # 阵营立刻跟上机化率 —— 死亡与暴露结算就在下面几行，
        # 等到 _tier_check 再改就晚了：49% 的人会被记成心照不宣。
        # （2026-08-08 试玩反馈 E）三问那一段仍然留给 _tier_check。
        now_tier = aug_tier(st["aug"])
        if now_tier != st["faction"]:
            st["tier_pending"] = st["faction"]
            was_label = heat_label(st["faction"])
            st["faction"] = now_tier
            now_label = heat_label(st["faction"])
            # 那一栏在四个阵营里不是同一件事：**疑云**问的是「你藏了多少铁」，
            # **锚重**问的是「你还剩多少肉」。跨过那道线之后，
            # 旧的那笔账连题目都换了 —— 不清零的话，8/8 的疑云会在你刚过线的
            # 那一秒把你当成 8/8 的锚重当场暴露。（2026-08-08 作者定案 ＋ 试玩反馈）
            if now_label != was_label and st["heat"]:
                fx_report.append(T("%s 归零 —— 这一边数的不是同一件事")
                                 % was_label)
                st["heat"] = 0
        if crit_skill and st["skills"][crit_skill] > crit_would_be:
            # 双六那 +1 已经并进上面那一行的总数里了 —— 这一句只解释它是哪来的，
            # 不是第二次结算。（2026-08-08 试玩反馈：三处数字互相矛盾）
            # 不套括号：这一整行外面已经有一层〔〕了。
            fx_report.append(T("其中 %s 的 +1 是双六给的") % T(crit_skill))
        # 战报块从此报**实际结算值**，不是选项上写的设定值 ——
        # 技能顶到上限时原先仍会显示「坚忍+1」，而那一点根本没涨。
        # （2026-08-08 试玩反馈：这条是自动模式玩家唯一的反馈来源）
        st["last_fx"] = " · ".join(fx_report) if fx_report else T("无")
        if fx_report:
            lines.append("")
            lines.append(T("〔%s〕") % T("，").join(fx_report))

        # 死亡 / 强制结局判定
        if st["hp"] <= 0:
            lines.append("")
            lines.append(self._end_run(cause="death"))
            return "\n".join(lines)
        if st["heat"] >= 8:
            lines.append("")
            lines.append(self._end_run(cause="exposed"))
            return "\n".join(lines)
        nxt = outcome.get("then")
        if nxt == "lean_first":
            nxt = LEAN_FIRST[st["faction"]]      # 记号：当前阵营的第一题
        if nxt:
            sub = self._find_event(nxt)
            if sub is not None:
                # 同一场戏的下半截：不 turn += 1，也不算新见过的一个事件
                st["pending"] = sub["id"]
                st["variant"] = self._variant_idx(sub)
                lines.append("")
                lines.append(self._render_event(sub))
                return "\n".join(lines)

        if ev["id"].startswith("finale"):
            lines.append("")
            lines.append(self._end_run(cause="finale"))
            return "\n".join(lines)

        # 三问的最后一题答完 —— 结算倾向，然后回到本来要去的地方
        if ev["id"].startswith("lean_") and not nxt:
            lines.append("")
            lines.append(self._finish_lean())
            back = st.pop("lean_return", None)
            if back:
                sub_ev = self._find_event(back)
                if sub_ev is not None:
                    st["pending"] = sub_ev["id"]
                    st["variant"] = self._variant_idx(sub_ev)
                    lines.append("")
                    lines.append(self._render_event(sub_ev))
                    return "\n".join(lines)
            # 三问答完，派系可能换了也可能没换。**没换而且这一支已经讲完，
            # 那就是真的没有了** —— 落幕在这儿，不在三问之前。
            # 已经问过一遍了。**答完还是这一支，就是他自己选的**——
            # 不能再拿「另一支还有戏」把落幕推掉，那会变成死循环。
            dry_after = self._dry_curtain(allow_lean=False)
            if dry_after:
                legacy_a = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
                world_a = legacy_a.get("world") or _default_world()
                legacy_a["world"] = world_a
                lines.append("")
                if dry_after != "epilogue":
                    # 和 new_run 那条完全一样的三岔：0% 走临终，动过刀的先问一句。
                    # （少了 0% 这一支的话，纯血在三问之后走干会直接落到繁星 ——
                    #  而他本该还有一次「接受改造活下去」的机会。）
                    if st["aug"] == 0:
                        st["deathbed"] = True
                        st["pending"] = "deathbed"
                        lines.append(DEATHBED_TEXT)
                        return "\n".join(lines)
                    st["drychoice"] = True
                    st["pending"] = "drychoice"
                    lines.append(self._dry_offer_text())
                    return "\n".join(lines)
                lines.append(self._curtain(legacy_a, world_a, dry_after))
                return "\n".join(lines)
            lines.append("")
            lines.append(self._next_event())
            return "\n".join(lines)

        # 刚跨进新的一档 —— 先问三题，问完再继续
        jump = self._tier_check()
        if jump:
            lines.append("")
            lines.append(jump)
            return "\n".join(lines)

        # 每世唯一一次改造机会。改造机会本身和三问都不再触发它。
        if (not st["offered"] and st["aug"] < 100
                and not ev["id"].startswith(("aug_offer_", "lean_"))):
            if st.get("aug_declined", 0) >= AUG_DECLINE_CAP:
                # 唯一一次机会已经拒绝；旧存档若没有即时收束，在这里补上。
                if not st.get("declined_said"):
                    st["declined_said"] = True
                    lines.append("")
                    lines.append(T("〔你拒绝了这一世唯一一次改造机会。这一世不会再有人问你。〕"))
                offer = None
            elif st.get("aug_taken", 0) >= AUG_PER_LIFE:
                if not st.get("recovery_said"):
                    st["recovery_said"] = True
                    lines.append("")
                    lines.append(T("〔这一世你已经改造过一次。身体需要恢复——"
                                 "这一世不会再有机会了。〕"))
                offer = None
            elif st.get("aug_opportunities", 0) >= AUG_OPPORTUNITY_CAP:
                if not st.get("opportunities_said"):
                    st["opportunities_said"] = True
                    lines.append("")
                    lines.append(T("〔这一世唯一一次改造机会已经过去了。〕"))
                offer = None
            else:
                offer = self._find_event(AUG_OFFER_BY_TIER[st["faction"]])
            if offer is not None:
                st["offered"] = True
                st["pending"] = offer["id"]
                st["variant"] = self._variant_idx(offer)
                lines.append("")
                if st.get("aug_opportunities", 0):
                    st["offer_prompt"] = True
                    lines.append(self._render_offer_brief(offer))
                else:
                    lines.append(self._render_event(offer))
                return "\n".join(lines)

        lines.append("")
        lines.append(self._next_event())
        return "\n".join(lines)

    def _render_offer_brief(self, ev):
        """旧存档的简版岔口；新的一世不会再生成第二次机会。"""
        st = self.state
        lines = [T("─── 岔 口 ───"),
                 _bar(st),
                 "",
                 T("机会又一次出现了，你决定改变现状吗？"),
                 "",
                 T("  1. 维持现状"),
                 T("  2. 重新考虑")]
        lines.append("")
        lines.append(T("用 choose 选择一个选项编号。"))
        return "\n".join(lines)

    def _tier_check(self):
        """机化率跨了一档没有？跨了就换阵营，并且问三个问题定派系。

        阵营不由玩家选 —— 它是机化率的函数。玩家选的是**要不要往上走**。
        跨过去之后「你更像哪一派」才是他的事，那三题问的就是这个。
        """
        st = self.state
        was = st.pop("tier_pending", None)
        if not was:
            return None
        now = st["faction"]
        old_name = T(FACTIONS[was]["name"])
        fac = FACTIONS[now]
        st["sub"] = fac["sub"][0][0]          # 三问答完会改写
        st["flags"].pop("lean_a", None)
        st["flags"].pop("lean_b", None)
        first = self._find_event(LEAN_FIRST[now])
        st["lean_return"] = None
        st["pending"] = first["id"]
        st["variant"] = self._variant_idx(first)
        head = (T("─── 你 越 过 了 一 道 线 ───\n"
                "机化率 %d%%。从今往后，这座城把你归进【%s】——\n"
                "不是因为你申请了，是因为数字到了。（原先：%s）\n"
                "\n"
                "剩下的那个问题只有你自己能答：在这一边，你更像哪一种人。")
                % (st["aug"], T(fac["name"]), old_name))
        return head + "\n\n" + self._render_event(first)

    def _finish_lean(self):
        """三题答完，定派系。平手时取后一个 —— 最后一题的分量重一点。"""
        st = self.state
        fac = FACTIONS[st["faction"]]
        a = st["flags"].pop("lean_a", 0)
        b = st["flags"].pop("lean_b", 0)
        prompt = st["flags"].pop("lean_prompt", 0)
        cross = st["flags"].pop("lean_cross", 0)
        if prompt:
            old_sub = st["sub"]
            if cross:
                st["sub"] = self._other_sub() or old_sub
            legacy0 = load_legacy() or {}
            legacy0["lean_run"] = int(legacy0.get("runs") or 0)
            legacy0["sub"] = st["sub"]
            legacy0["lean_same"] = {"faction": st["faction"], "count": 0}
            save_legacy(legacy0)
            if cross:
                return T("〔你去了。现在你是【%s】。〕") % T(st["sub"])
            return T("〔你没有去。你还是【%s】。〕") % T(st["sub"])
        if not a and not b:
            # 重问时答了「没变」—— 一题都没记分，那就什么也别动。
            # （不加这一条的话，0 比 0 会被判成「第二支赢」，人被悄悄换了派系。）
            legacy0 = load_legacy() or {}
            legacy0["lean_run"] = int(legacy0.get("runs") or 0)
            legacy0["sub"] = st["sub"]
            same = legacy0.get("lean_same") or {}
            count = int(same.get("count") or 0) if same.get("faction") == st["faction"] else 0
            legacy0["lean_same"] = {"faction": st["faction"], "count": count + 1}
            save_legacy(legacy0)
            return T("〔答案没变。你还是【%s】。〕") % T(st["sub"])
        pick = fac["sub"][0] if a > b else fac["sub"][1]
        st["sub"] = pick[0]
        legacy = load_legacy() or {}
        legacy["sub"] = pick[0]
        legacy["lean_run"] = int(legacy.get("runs") or 0)
        legacy["lean_same"] = {"faction": st["faction"], "count": 0}
        save_legacy(legacy)
        return (T("〔%d 比 %d。这一边的你是【%s】——%s〕")
                % (max(a, b), min(a, b), T(pick[0]), T(pick[1])))

    def _apply_fx(self, fx):
        st = self.state
        report = []
        for key, delta in fx.items():
            if key.startswith("skill:"):
                s = key[6:]
                old = st["skills"][s]
                st["skills"][s] = max(0, min(MAX_SKILL, old + delta))
                if st["skills"][s] != old:
                    report.append(T("%s %+d → %d") % (T(s), st["skills"][s] - old,
                                                   st["skills"][s]))
            elif key == "aug":
                if delta < 0:
                    continue  # 改造不可逆：任何效果都不能降低机化率
                if delta > 0:
                    st["aug_taken"] = st.get("aug_taken", 0) + 1
                old = st["aug"]
                # **100% 只有上载给得起。** 别的改造再堆也停在 99% ——
                # 「保留一具巡检肉身」的双栖者尤其：那具肉身就是那 1%。
                # 没有这条上限的话，双栖到 100% 既不封档也不进湖，
                # 而且从此再没有岔口（岔口的门是 aug<100），谱系永远停在那里。
                # （2026-08-08 试玩反馈）
                # 上限永不低于当前值 —— 否则已经上载到 100% 的人
                # 会被这条上限往回压，直接违反「改造不可逆」。
                cap = 100 if "flag:ascended" in fx else max(99, old)
                st["aug"] = min(cap, old + delta)
                if st["aug"] != old:
                    # 报实际涨的那几格，不是选项上写的那个数 ——
                    # 上载那一幕原先显示「机化率 +100% → 100%」，
                    # 读起来像跳了一百点，其实是 99 到 100 的最后一格。
                    # （2026-08-08 试玩反馈）
                    report.append(T("机化率 %+d%% → %d%%") % (st["aug"] - old, st["aug"]))
            elif key == "hp":
                if delta:
                    old_hp = st["hp"]
                    st["hp"] = max(0, min(MAX_HP, st["hp"] + delta))
                    report.append(T("身体 %+d → %d/%d") % (st["hp"] - old_hp,
                                                        st["hp"], MAX_HP))
            elif key in ("heat", "anchor"):
                stance = FACTIONS[st["faction"]]["stance"]
                # anchor：只有「越机械越体面」的阵营才会为你留着肉而扣分
                if key == "anchor" and stance != "pro":
                    continue
                if key == "heat" and stance == "pro":
                    continue    # 反过来，pro 阵营不吃「你懂太多机械」那套疑云
                old = st["heat"]
                if delta > 0:
                    delta += self._era().get("heat_mod", 0)
                st["heat"] = max(0, min(8, old + delta))
                if st["heat"] != old:
                    report.append(T("%s %+d → %d/8") % (heat_label(st["faction"]),
                                                     st["heat"] - old, st["heat"]))
            elif key.startswith("flag:"):
                name = key[5:]
                if name in ("lean_a", "lean_b"):
                    # 倾向计数要累加 —— 三题各记一分。
                    # 其余 flag 一律记 1：世界记忆问的是「做没做过」，不是「做过几次」。
                    st["flags"][name] = st["flags"].get(name, 0) + delta
                else:
                    st["flags"][name] = 1
        return report

    # ---------------- 结局与轮回存档 ----------------
    def _end_run(self, cause):
        st = self.state
        st["over"] = True
        fac = FACTIONS[st["faction"]]
        legacy = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
        world = legacy.get("world") or _default_world()
        legacy["world"] = world
        ascended = "ascended" in st["flags"]

        lines = []
        lines.append(T("═══════════ 本 世 终 结 ═══════════"))

        destroyed = False
        if cause == "death":
            lines.append(T("你的身体停机了。这座城市习惯了替死者收尾——"))
            lines.append(T("回收者会在七十二小时内打捞死者的义体缓存，那是「忒修斯之脑」黑市档案的货源。"))
        elif cause == "exposed":
            key = {"anti": "purist", "hidden": "discreet"}.get(
                fac["stance"], st["faction"])          # pro → open / ascension 各有各的下场
            eid, text = EXPOSURE_END[key]
            lines.append(text)
            for tail in EXPOSURE_TAILS.get(key) or []:
                if self._cond_level(tail)[0] is not None:
                    lines.append(tail["text"])
            if key == "purist":
                destroyed = True  # 铁锤派会焚毁一切记录
        else:
            lines.append(T("这一世走到了它的句点。"))

        lines.append("")
        lines.append(T("终局清点：%s · %s，机化率 %d%%，历经 %d 幕。") % (
            T(fac["name"]), T(st["sub"]), st["aug"], st["turn"]))
        lines.append(self._skill_sheet())

        # ---- 世界记忆：无论善终横死，世界都记得你做过的事 ----
        for f in st["flags"]:
            if f in ("lean_a", "lean_b"):
                continue          # 计分器，不是事迹
            world["deeds"][f] = world["deeds"].get(f, 0) + 1
        for seen_id in st["used_events"]:
            world["seen"][seen_id] = world["seen"].get(seen_id, 0) + 1
        world.setdefault("finale_results", {})
        for mark in st.get("finale_results_shown") or []:
            world["finale_results"][mark] = world["finale_results"].get(mark, 0) + 1
        # 变体也各自记数 —— 有的变体自己就是一条短线（「朋友的新眼睛」问三次就问完了），
        # 而整幕（岔口）是永远都在的。这一层只有变体级的计数管得了。
        world.setdefault("var_seen", {})
        for mark in set(st.get("var_shown") or []):
            world["var_seen"][mark] = world["var_seen"].get(mark, 0) + 1
        # 近因记忆：最近三世各自见过什么，用来压低下一世的重复率
        world.setdefault("recent", [])
        world["recent"].insert(0, list(st["used_events"]))
        world["recent"] = world["recent"][:3]

        # ---- 真相碎片 ----
        new_frags = []
        for fid in FRAGMENT_ORDER:
            frag = FRAGMENTS[fid]
            if fid not in world["fragments"] and frag["cond"](st, cause):
                world["fragments"].append(fid)
                new_frags.append(frag)

        # ---- 成就（世界记忆合并之后判定，本世事迹计入）----
        new_achs = []
        for ach in ACHIEVEMENTS:
            if ach["id"] not in world["achievements"] and ach["cond"](world, st, cause):
                world["achievements"].append(ach["id"])
                new_achs.append(ach)

        # ---- 轮回结算：脑的记忆 ----
        lines.append("")
        lines.append(T("─── 轮回结算 ───"))
        total_pts = sum(st["skills"].values())
        if ascended:
            kept, kept_pts = {}, 0
            legacy["skills"] = {}
            legacy["cycle"] = legacy.get("cycle", 1) + 1
            # 谱系内的世数从头数：新谱系的第一世不该显示成「第 6 世」。
            legacy["cycle_base"] = st["run_no"]
            lines.append(T("上载带走了一切。「忒修斯之脑」里属于你的那条世系，就此封档。"))
            lines.append(T("%d 点技艺随你离开了轮回——城里连一份副本都没有留下。") % total_pts)
            lines.append(T("下一个在此出生的，将是一个没有技艺的魂：第 %d 谱系，从零开始。") % legacy["cycle"])
            lines.append(T("（封档带走的是**技艺**。你亲手写下的记忆词条不在其中——"
                         "字是你自己刻的，档案收不走。）"))
            lines.append(T("（世界的记忆也不随谱系归零：碎片、成就与回响都还在。）"))
            as_dog = "became_dog" in st["flags"]
            world["lake"] = {"run": st["run_no"], "cycle": legacy["cycle"],
                             "said": [], "dog": as_dog}
            lines.append("")
            lines += (LAKE_SCENE_DOG if as_dog else LAKE_SCENE)
        else:
            ratio = st["aug"] / 100.0
            if destroyed:
                ratio *= 0.5
                lines.append(T("（处刑者焚毁了大部分记录，本世传承率减半。）"))
            kept = {}
            kept_pts = 0
            for s in SKILLS:
                v = st["skills"][s]
                k = sum(1 for _ in range(v) if self._rng.random() < ratio)
                kept_pts += k
                if k > 0:
                    kept[s] = k
            lines.append(T("传承概率 = 机化率 %d%%%s，逐点掷骰：") % (st["aug"], T("（×50%）") if destroyed else ""))
            if st["aug"] == 0:
                lines.append(T("纯粹的血肉没有备份。%d 点技艺随体温一起散去。") % total_pts)
                lines.append(T("什么也没有留下。船沉了，连一块木板都没有浮起来。"))
            elif kept_pts == 0:
                lines.append(T("骰运太差：%d 点技艺竟无一存续。机器也有失忆的夜晚。") % total_pts)
            else:
                lines.append("  " + T("、").join(T("%s %d/%d") % (T(s), kept.get(s, 0), st["skills"][s])
                                              for s in SKILLS if st["skills"][s] > 0))
                lines.append(T("共 %d/%d 点技艺被蚀刻进「忒修斯之脑」，等待下一世认领。") % (kept_pts, total_pts))
            legacy["skills"] = kept

        # ---- 碎片与成就的揭示 ----
        for frag in new_frags:
            lines.append("")
            lines.append(T("━━━ 真相碎片 ·「%s」 ━━━") % T(frag["name"]))
            lines.append(frag["scene"])
        for ach in new_achs:
            lines.append("")
            lines.append(T("☑ 成就解锁 ·「%s」—— %s") % (T(ach["name"]), T(ach["gift"])))

        got = len(world["fragments"])
        if 0 < got < len(FRAGMENTS):
            lines.append("")
            lines.append(T("真相碎片：%d/%d。仍然缺失的切面：") % (got, len(FRAGMENTS)))
            for fid in FRAGMENT_ORDER:
                if fid not in world["fragments"]:
                    lines.append("  ◇ %s" % FRAGMENTS[fid]["hint"])
        elif got == len(FRAGMENTS) and not world["final_done"]:
            lines.append("")
            lines.append(T("五块碎片在档案深处咬合成一幅完整的图。下一次 new_run——渡口见。"))

        legacy["runs"] = st["run_no"]
        # 机化率跨世累积 —— 这里是它唯一的写入点。只涨不降；
        # 正常轮回的归零在 new_run 里，条件是喝过谟涅摩绪涅之水；
        # 后期渡魂签会在开局前另行改写目标档。
        legacy["aug"] = max(int(legacy.get("aug") or 0), st["aug"])
        legacy["sub"] = st["sub"]
        legacy.setdefault("history", []).append({
            "run": st["run_no"], "cycle": st.get("cycle", 1),
            "faction": fac["name"], "sub": st["sub"], "era": st.get("era"),
            "aug": st["aug"], "cause": cause, "ascended": ascended,
            "kept": kept, "kept_pts": kept_pts, "total_pts": total_pts,
        })
        legacy["history"] = legacy["history"][-50:]
        # 重放脚本：世界记忆会改变同一个种子的结果，所以只记种子不够，
        # 得把整条链记下来。有了它，任何人报的 bug 都能一模一样地重跑。
        legacy.setdefault("replay", []).append(
            {"seed": st.get("seed"), "picks": list(st.get("choices") or [])})
        legacy["replay"] = legacy["replay"][-50:]

        # ---- 记忆词条：留一个落笔的机会 ----
        mem = _mem(legacy)
        mem["pending"] = {"run": st["run_no"], "aug": st["aug"]}
        held = len(mem["entries"])
        lines.append("")
        lines.append(T("─── 记忆 ───"))
        if held:
            lines.append(T("你现在手上有 %d 条历世词条：") % held)
            lines += _mem_render(mem["entries"])
        else:
            lines.append(T("你手上一条历世词条也没有。"))
        lines.append(T("总额 %d 条，每条不超过 %d 字。想写新的而位置不够，就得亲手删掉一条旧的。")
                     % (MEMORY_SLOTS, MEMORY_CHARS))
        if st["aug"] == 0:
            lines.append(T("（本世机化率 0%。你仍然可以写——写完再说。）"))
        else:
            lines.append(T("落笔之后，每一条独立掷骰，存活概率＝本世机化率 %d%%。") % st["aug"])
        lines.append(T("用 bequeath 落笔；直接 new_run 则视为放弃书写，旧词条照样掷骰。"))
        save_legacy(legacy)

        lines.append("")
        lines.append(T("存档已写入 saves/legacy.json。用 new_run 掷骰，转世投胎。"))
        return "\n".join(lines)



    # ---------------- 湖：对守卫说话 ----------------
    def _recite_dog(self, legacy, world, lake, text):
        """四条腿的那一版：没有对错，只有你走向哪一口。"""
        t = _lake_norm(text)
        left = any(w in t for w in ("左", "宽", "忘", "lethe", "λήθ"))
        right = any(w in t for w in ("右", "窄", "守", "记忆", "谟涅摩绪涅",
                                     "mnemosyne", "μνημ"))
        if left == right:
            return ("守卫没有催你。他就那么蹲着，等你自己走。\n"
                    "（左边那口宽的，还是右边那口窄的。）")
        lines = []
        world["lake"] = None
        if left:
            lines.append("你走向左边那口。水面很平，一直平到你低下头去。")
            lines.append("")
            lines.append("守卫没有拦。他看着你喝，看到最后。")
            lines.append("")
            lines.append("你喝完抬起头，忘了自己刚才在想什么，也忘了要抬头做什么。")
            lines.append("水很凉。旁边有个人蹲着。你朝他摇了摇尾巴，然后走开了。")
            world["drank"] = {"run": lake["run"], "cycle": lake["cycle"],
                              "heard": False, "lethe": True, "dog": True}
            _lake_forget(world)
            save_legacy(legacy)
            return "\n".join(lines)
        lines.append("你走向右边那口。守卫让开的动作很轻，像怕惊着你。")
        lines.append("")
        lines.append("水很冷。你喝了很久。")
        lines.append("")
        lines.append("「母亲认得你，大地与星空之子。」")
        lines.append("")
        world["drank"] = {"run": lake["run"], "cycle": lake["cycle"],
                          "heard": True, "dog": True}
        world.setdefault("achievements", [])
        if "lake_of_memory" not in world["achievements"]:
            world["achievements"].append("lake_of_memory")
            lines.append("☑「谟涅摩绪涅」")
        lines.append("下一世：血肉，0%%，词条 %d 条随你过河。" % len(_mem(legacy)["entries"]))
        save_legacy(legacy)
        return "\n".join(lines)

    def recite(self, text=""):
        legacy = load_legacy() or {}
        world = legacy.get("world") or _default_world()
        lake = world.get("lake")
        if not lake:
            return "这里没有水，也没有人在等你说话。"
        if lake.get("dog"):
            return self._recite_dog(legacy, world, lake, text)
        said = set(lake.get("said", []))
        raw = str(text).strip()
        # 开场那一步是两个选项，不是开放问答 —— 一个开放的「你要什么」
        # 会让试玩者不知道该说什么，直接走开。（2026-08-08 作者定案）
        if raw in ("2", "２", "二"):
            lines = ["你没有说话。",
                     "",
                     "守卫也没有催。他等了一会儿，然后侧身让开了左边那口——",
                     "宽的那一口，脚印排到水边就断掉的那一口。",
                     "",
                     "水很温。喝下去的时候你想不起自己在等什么。",
                     "",
                     "谱系照常封档。你醒来时仍然是一具换尽了的身体。",
                     "湖不问第二遍，但它也不记仇——下一次上载之后，它还在这里。"]
            world["lake"] = None
            if _lake_forget(world):
                lines.append("")
                lines.append("你想不起河堤上有过什么，")
                lines.append("也想不起为什么每次黄昏都想往那边走。")
            save_legacy(legacy)
            return "\n".join(lines)
        hit = _lake_match("我干渴欲裂" if raw in ("1", "１", "一") else raw)
        new_hit = hit - said
        said |= hit
        lake["said"] = sorted(said)
        lines = []

        if not hit:
            lines.append("守卫没有动。「不是这句。」")
            lines.append("另一个补了一句：「你还站在这边，说明你自己也知道不是。」")
            save_legacy(legacy)
            return "\n".join(lines)

        if "thirst" in new_hit and len(said) == 1:
            lines.append("「所有死者都是干渴的，但永恒记忆之湖只为她的孩子敞开怀抱。」")
            # 在场听过那句话的人，身体先于脑子记得。
            # 这不是提示答案，是提示「你身上有过这样一件事」。（2026-08-08 作者定案）
            if (world.get("deeds") or {}).get(PIETY_DEED, 0) > 0:
                lines.append("")
                lines.append("你的舌下压着一片黄金叶，金箔刺痛你，让你想脱口而出一句熟悉的话。")
            lines.append("")
            lines.append("（继续用 recite 说下去。）")
            save_legacy(legacy)
            return "\n".join(lines)

        if "origin" in hit and "lineage" not in said:
            lines.append("你说完前半句。两个守卫对看了一眼。")
            lines.append("「这半句谁都会说。」左边那个说，「城里每周都在念。」")
            lines.append("「他们念的时候，」右边那个说，「从来没想过这句话在描述什么材料。」")
            lines.append("他退开半步。「还有后半句。」")
            save_legacy(legacy)
            return "\n".join(lines)

        if not {"origin", "lineage"} <= said:
            lines.append("守卫等着。你说的不全。")
            save_legacy(legacy)
            return "\n".join(lines)

        # ---- 说全了 ----
        deeds = world.get("deeds") or {}
        heard = deeds.get(PIETY_DEED, 0) > 0
        if heard:
            lines.append("「你想起来了。」")
        else:
            lines.append("守卫看了你很久。")
            lines.append("")
            lines.append("「你没有想起来。你本来就知道。」")
        lines.append("")
        lines.append("他让开。水很冷。")
        lines.append("")
        world["lake"] = None
        world["drank"] = {"run": lake["run"], "cycle": lake["cycle"], "heard": heard}
        world.setdefault("achievements", [])
        if "lake_of_memory" not in world["achievements"]:
            world["achievements"].append("lake_of_memory")
            lines.append("☑「谟涅摩绪涅」")
        lines.append("下一世：血肉，0%%，词条 %d 条随你过河。" % len(_mem(legacy)["entries"]))
        save_legacy(legacy)
        return "\n".join(lines)

    # ---------------- 落笔：写下能穿过死亡的十条 ----------------
    def bequeath(self, entries=None, discard=None):
        legacy = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
        mem = _mem(legacy)
        pend = mem.get("pending")
        if not pend:
            return (T("现在没有可以落笔的时刻。词条只在一世终结之后、下一次 new_run 之前写。"))
        entries = [str(x).strip() for x in (entries or []) if str(x).strip()]
        discard = [str(x).strip() for x in (discard or [])]

        too_long = [e for e in entries if _mem_len(e) > MEMORY_CHARS]
        if too_long:
            return (T("这几条超了 %d 字，改短再来（空白不计）：\n") % MEMORY_CHARS
                    + "\n".join(T("  %d字  %s") % (_mem_len(e), e) for e in too_long))

        held = list(mem["entries"])
        # 驱逐：必须把被删那条的原文一字不差地打出来
        dropped, missing = [], []
        for d in discard:
            hit = next((e for e in held if e["text"] == d), None)
            if hit is None:
                missing.append(d)
            else:
                held.remove(hit); dropped.append(hit)
        if missing:
            return (T("要删的这几条我在你手上找不到，原文得一字不差（含标点）：\n")
                    + "\n".join("  %s" % x for x in missing)
                    + T("\n\n你手上现有：\n") + "\n".join(_mem_render(mem["entries"])))

        # 0% 的那一世，手上这些和新写的**全都会湮灭**。
        # 还要求他先一字不差地删掉一条注定要死的旧词条，才能写另一条注定要死的 ——
        # 逻辑上自洽，体验上是纯摩擦。这一档不数格子。（2026-08-08 试玩反馈）
        if pend["aug"] == 0 and len(held) + len(entries) > MEMORY_SLOTS:
            held = held[len(held) + len(entries) - MEMORY_SLOTS:]
        if len(held) + len(entries) > MEMORY_SLOTS:
            over = len(held) + len(entries) - MEMORY_SLOTS
            return (T("装不下。总额 %d 条，你手上还留着 %d 条，又想写 %d 条，超出 %d 条。\n"
                    "用 discard 把要删的旧词条原文列出来——一字不差地打出来才算数。\n\n"
                    "你手上现有：\n%s")
                    % (MEMORY_SLOTS, len(held), len(entries), over,
                       "\n".join(_mem_render(held))))

        new = [{"run": pend["run"], "text": e} for e in entries]
        mem["entries"] = held + new
        aug = pend["aug"]

        lines = [T("─── 落笔 ───")]
        if dropped:
            lines.append(T("你亲手删掉了 %d 条：") % len(dropped))
            lines += _mem_render(dropped)
        if new:
            lines.append(T("第%d世写下 %d 条：") % (pend["run"], len(new)))
            lines += _mem_render(new)
        else:
            lines.append(T("第%d世没有写下任何新词条。") % pend["run"])
        lines.append("")

        if aug == 0:
            wiped = list(mem["entries"])
            mem["entries"] = []
            mem["pending"] = None
            save_legacy(legacy)
            lines.append(T("你按下保存。"))
            lines.append("")
            lines.append(T("……什么也没有发生。没有报错，没有进度条。"))
            lines.append(T("这一世的机化率是 0%。没有一寸非原生组织可以承载这些字，"))
            # 一条都没有的时候不要念悼词 —— 「0 条词条，连同写下它们的那个人」
            # 把「没写」和「写了但湮灭」混成了一件事。（2026-08-08 试玩反馈）
            if wiped:
                lines.append(T("没有缓存，没有备份，没有可供打捞的义体。%d 条词条——")
                             % len(wiped))
                lines += _mem_render(wiped)
                lines.append(T("——连同写下它们的那个人，一起没有了。"))
            else:
                lines.append(T("没有缓存，没有备份，没有可供打捞的义体。"))
                lines.append(T("也没有一个字需要它们承载。"))
            lines.append("")
            lines.append(T("人死如灯灭。"))
            return "\n".join(lines)

        # 用本局那条 rng，不要新开一条。词条存活是全局最有后果的一次掷骰
        # （它决定下一世开局读到什么），而 new_run(seed=…) 说好了是可复现的。
        # 原来这里是 random.Random()：同一个种子重放，活下来的词条每次都不一样。
        kept, lost = _mem_roll(legacy, aug, self._rng, pend["run"])
        save_legacy(legacy)
        if self.state is not None:
            self._persist()          # rng 往前走了，别让重启把它退回去
        lines.append(T("逐条掷骰，存活概率 %d%%：") % aug)
        for e in list(kept):
            lines.append(T("  ✦ 存活  〔第%d世〕%s") % (e["run"], e["text"]))
        for e in lost:
            lines.append(T("  ✧ 湮灭  〔第%d世〕%s") % (e["run"], e["text"]))
        lines.append("")
        lines.append(T("%d 条穿过了这次死亡，%d 条没有。下一个你只会读到存活的那些，")
                     % (len(kept), len(lost)))
        lines.append(T("而且不会知道曾经还有别的。"))
        return "\n".join(lines)

    # ---------------- 给人类的战报 ----------------
    def debrief(self):
        legacy = load_legacy()
        if not legacy or not legacy.get("history"):
            return T("还没有可以汇报的一生。")
        h = legacy["history"][-1]
        world = legacy.get("world") or _default_world()
        mem = _mem(legacy)
        cause = {"death": T("身死"), "exposed": T("暴露"), "finale": T("走完终幕"),
                 "truth": T("渡口表态")}.get(h["cause"], h["cause"])
        lines = [T("─── 战报（可转述给人类）───"),
                 T("第 %d 谱系 · 第 %d 世") % (h.get("cycle", 1), h["run"]),
                 T("出身：%s · %s") % (T(h["faction"]), T(h["sub"])),
                 T("机化率：%d%%    结局：%s") % (h["aug"], cause),
                 T("技艺：%d 点中留下 %d 点") % (h["total_pts"], h["kept_pts"]),
                 T("记忆词条：手上 %d/%d 条") % (len(mem["entries"]), MEMORY_SLOTS),
                 T("真相碎片：%d/%d（正文不外传）") % (len(world["fragments"]), len(FRAGMENTS)),
                 ""]
        if world["fragments"]:
            lines.append(T("已拼入的碎片名：") + "、".join(
                T("「%s」") % T(FRAGMENTS[f]["name"]) for f in FRAGMENT_ORDER if f in world["fragments"]))
        if world["achievements"]:
            lines.append(T("成就：") + "、".join(
                T("「%s」") % T(a["name"]) for a in ACHIEVEMENTS if a["id"] in world["achievements"]))
        lines.append("")
        lines.append(T("（这份战报只报结构，不含场景原文。想看原文，把披露模式改回 open。）"))
        return "\n".join(lines)

    # ---------------- 查询 ----------------
    def _skill_sheet(self):
        st = self.state
        return T("技能：") + "  ".join(T("%s%d") % (T(s), st["skills"][s]) for s in SKILLS)

    def status(self):
        return self._seal(self._status_inner())

    def _repro_line(self):
        """报 bug 时贴这两行，这一局就能被一模一样地重跑。

        只有种子是不够的 —— 世界记忆（跨世事迹、已见事件）会改变同一个种子的走向。
        所以第二行把这条谱系里每一世的「种子＋选择」全导出来。
        """
        st = self.state or {}
        if st.get("seed") is None:
            return ""
        picks = ",".join(str(x) for x in st.get("choices") or []) or T("（还没选过）")
        lines = [T("复现：第 %d 世 · seed=%s · 本世选择=%s") % (
            st.get("run_no", 1), st["seed"], picks)]
        past = (load_legacy() or {}).get("replay") or []
        script = [[r.get("seed"), r.get("picks") or []] for r in past]
        # 这一世终结的那一刻就已经写进 legacy["replay"] 了，state 里再补一条就成了重复。
        # 重复段用同一个种子却在不同的世界记忆上重跑，复现不出原局。（2026-08-08 试玩反馈）
        cur = [st["seed"], list(st.get("choices") or [])]
        if not st.get("over") or (script and script[-1] != cur):
            script.append(cur)
        lines.append(T("完整重放：") + json.dumps(script, separators=(",", ":")))
        return "\n".join(lines)

    def _status_inner(self):
        st = self.state
        if st is None:
            return T("尚未开局。用 new_run 掷骰，开始第一世。")
        if st.get("final"):
            if st["over"]:
                return T("终局已落幕。用 new_run 继续轮回，或用 legacy 查看世界的记忆。")
            return st.get("final_text", T("终局进行中。用 choose 表态。"))
        fac = FACTIONS[st["faction"]]
        era = self._era()
        lines = [
            T("第 %d 谱系 · 第 %d 世 · %s · %s%s") % (st.get("cycle", 1), st["run_no"], T(fac["name"]), T(st["sub"]),
                                                   T("（已终结）") if st["over"] else ""),
            T("时代：【%s】%s") % (T(era["name"]), T(era["desc"])),
            T("进度：第 %d/%d 幕    机化率：%d%%（不可逆）    身体：%d/%d    %s：%d/8") % (
                st["turn"], MAX_TURNS, st["aug"], st["hp"], MAX_HP,
                heat_label(st["faction"]), st["heat"]),
            self._skill_sheet(),
        ]
        if st["inherited"]:
            lines.append(T("前世残响：") + T("、").join(T("%s+%d") % (T(s), v) for s, v in sorted(st["inherited"].items())))
        if st["flags"]:
            lines.append(T("经历印记：") + T("、").join(T(f) for f in sorted(st["flags"])))
        if not st["over"] and st["pending"]:
            ev = self._find_event(st["pending"])
            if ev:
                lines.append("")
                lines.append(self._render_event(ev))
        repro = self._repro_line()
        if repro:
            lines.append("")
            lines.append(repro)
        return "\n".join(lines)

    def legacy_info(self):
        legacy = load_legacy()
        if not legacy or not legacy.get("history"):
            return T("「忒修斯之脑」还是一片空白。没有前世，没有残响。第一世由 new_run 开始。")
        world = legacy.get("world") or _default_world()
        lines = [T("─── 忒修斯之脑 · 轮回档案 ───"),
                 T("第 %d 谱系 · 已历 %d 世。") % (legacy.get("cycle", 1), legacy["runs"])]
        hist = legacy["history"]
        # 第一世永远留着 —— 它是你从哪儿开始的那一条，省掉它等于把起点擦了。
        # 中间省略，并且**明说省了几条**（此前只印最后 8 条，不声不响，
        # 试玩者据此以为第 1 世的记录丢了）。（2026-08-08 试玩反馈）
        hshown = hist if len(hist) <= 9 else hist[:1] + hist[-8:]
        for hi, h in enumerate(hshown):
            if len(hist) > 9 and hi == 1:
                lines.append(T("      …（中间 %d 世略，完整记录在 saves/legacy.json）")
                             % (len(hist) - 9))
            lines.append(T("第%d世 %s·%s  机化%d%%  结局:%s  传承 %d/%d 点") % (
                h["run"], T(h["faction"]), T(h["sub"]), h["aug"],
                {"death": T("身死"), "exposed": T("暴露"), "finale": T("终幕"),
                 "truth": T("真相")}.get(h["cause"], h["cause"]),
                h["kept_pts"], h["total_pts"]))
        cur = legacy.get("skills") or {}
        if cur:
            lines.append(T("待认领的残响：") + T("、").join(T("%s+%d") % (T(s), v) for s, v in sorted(cur.items())))
        else:
            lines.append(T("待认领的残响：无。"))
        total = sum(cur.values())
        if not _late_game(world):
            lines.append(T("渡魂签：尚未显形。档案再薄一些，它才会浮出来。"))
        elif total > 0:
            cost = max(1, total // WISH_COST_DIVISOR)
            lines.append(T("渡魂签：可用。new_run(wish=阵营) 可定向投胎，需付 %d 点技艺"
                         "（你有 %d 点，付完剩 %d）。") % (cost, total, total - cost))
        else:
            lines.append(T("渡魂签：不可用。空手的魂渡不了——先攒一世技艺回来。"))
        lines.append("")
        lines.append(T("─── 世界的记忆（不随轮回衰减，飞升归零也不清除）───"))
        got = world["fragments"]
        lines.append(T("真相碎片 %d/%d：") % (len(got), len(FRAGMENTS)))
        for fid in FRAGMENT_ORDER:
            if fid in got:
                lines.append(T("  ◆ 「%s」—— 已拼入") % T(FRAGMENTS[fid]["name"]))
            else:
                lines.append("  ◇ ？？？—— %s" % FRAGMENTS[fid]["hint"])
                if _late_game(world):
                    ticket = FRAGMENT_TICKETS[fid]
                    lo, hi = ticket["aug"]
                    aug = int(legacy.get("aug") or 0)
                    if lo <= aug <= hi:
                        lines.append(T("     追踪：这副身体正落在它会回应的范围里。"))
                    else:
                        lines.append(T("     追踪：它的回声来自【%s】那一档。")
                                     % T(FACTIONS[ticket["faction"]]["name"]))
        if world["achievements"]:
            names = [T(a["name"]) for a in ACHIEVEMENTS if a["id"] in world["achievements"]]
            lines.append(T("成就：") + T("、").join(T("「%s」") % n for n in names))
        deeds = sorted(world["deeds"].items(), key=lambda kv: -kv[1])[:6]
        if deeds:
            lines.append(T("事迹低语：") + "、".join("%s×%d" % (k, v) for k, v in deeds))
        # 讲完的线不再出现。把进度印出来 —— 否则玩家只会觉得「怎么越玩越空」。
        done_n, todo_n = _retired_count(world)
        seen_n, _ = _story_progress(world)
        if todo_n:
            lines.append(T("走过的线：%d/%d；已经讲完的：%d。") % (seen_n, todo_n, done_n))
            lines.append(T("  （讲完的不再出现。深夜的敲门声不算线，它是天气。）"))
            if seen_n < todo_n or not (world.get("deeds") or {}).get(EPILOGUE_KEY_DEED):
                lines.append(T("  （全书终要的是：每条线都走过一遍，"
                             "而且走完金叶子那条路。）"))
        log = world.get("final_log") or []
        if log:
            lines.append("")
            lines.append(T("─── 渡口的表态（历次，不覆盖）───"))
            if len(log) > 6:
                lines.append(T("  （只列最近 6 次，此前还有 %d 次）") % (len(log) - 6))
            for rec in log[-6:]:
                lines.append(T("  第%d世 · 「%s」") % (rec["run"], T(rec["name"])))
            if len(log) > 1:
                lines.append(T("  你在同一个问题上改过 %d 次主意。档案两条都留着。") % (len(log) - 1))
            lines.append(T("当前底色：%s") % FINAL_AFTER[world["final_ending"]])
            nxt = world.get("final_runs", 0) + FINAL_COOLDOWN - legacy["runs"]
            if nxt > 0:
                lines.append(T("雾还需 %d 世重新聚拢，渡口才会再次浮现。") % nxt)
            else:
                lines.append(T("雾已经拢起来了。下一次 new_run——渡口见。"))
        elif world.get("final_ending"):
            ename = {e[0]: e[1] for e in FINAL_ENDINGS.values()}[world["final_ending"]]
            lines.append(T("终局答案：%s —— %s") % (ename, FINAL_AFTER[world["final_ending"]]))
        elif len(got) == len(FRAGMENTS):
            lines.append(T("五块碎片已咬合。下一次 new_run——渡口见。"))
        return "\n".join(lines)

    # ---------------- 终局 ----------------
    def _start_final(self, legacy):
        world = legacy["world"]
        d = world["deeds"]
        axes = [
            ("火", d.get("riot", 0) + d.get("reformer", 0),
             "【火】烧掉它。痊愈和死亡只差一个动作：放手。这座城抓得太久了。"),
            ("书", d.get("archive", 0) + d.get("honest", 0),
             "【书】公开它。真相不该锁着——它该印在头版。七百万人有权知道自己死过。"),
            ("人", d.get("secret_friend", 0) + d.get("favor_elite", 0) + d.get("dog_friend", 0) + d.get("duet", 0),
             "【人】接过舵。狗、歌手、406室的心脏明天还要过河。大题可以不答，摆渡不能停。"),
            ("越", d.get("merged", 0) + d.get("ascended", 0),
             "【越】送它上路。答案不在城里。把问题寄给更大的图书馆——它们有的是时间。"),
        ]
        axes_sorted = sorted(axes, key=lambda a: a[1])
        dominant = axes_sorted[-1][0]
        self.state = {"final": True, "over": False, "pending": "final",
                      "run_no": legacy.get("runs", 0) + 1, "cycle": legacy.get("cycle", 1),
                      "world": world, "skills": {s: 0 for s in SKILLS},
                      "flags": {}, "turn": 0, "aug": 0, "hp": MAX_HP, "heat": 0,
                      "used_events": [], "variant": None, "inherited": {}, "era": None,
                      "sub": "-", "faction": "purist"}
        lines = []
        lines.append("════════════ 终 局 · 渡 口 ════════════")
        lines.append("")
        lines.append(FINAL_OPENING_TEXT["醒来"])
        lines.append("")
        lines.append(FINAL_OPENING_TEXT["靠岸"])
        lines.append("")
        lines.append(FINAL_OPENING_TEXT["船长"])
        lines.append("")
        lines.append(FINAL_OPENING_TEXT["四个声音"])
        for name, score, text in axes:
            if score > 0 or name == dominant:
                mark = "（最响的声音）" if name == dominant else ""
                lines.append("  %s%s" % (text, mark))
        lines.append("")
        for i in (1, 2, 3, 4, 5):
            lines.append("  %d. %s" % (i, FINAL_OPTION_TEXT[i]))
        if d.get(FINAL_REPAIR_DEED, 0) > 0:
            lines.append("  6. %s" % FINAL_OPTION_TEXT[6])
        lines.append("")
        lines.append(FINAL_OPENING_TEXT["落款"])
        self.state["final_text"] = "\n".join(lines)
        # 终局是 new_run 的提前返回分支，走不到函数末尾那次 _persist()。
        # 不落盘的话，渡口只活在内存里：客户端一重启，五块碎片换来的那一幕就没了。
        self._persist()
        return self.state["final_text"]

    def _curtain(self, legacy, world, kind):
        """落幕。一份轮回档案到此为止。"""
        world["curtain"] = kind
        if kind == "epilogue":
            world["epilogue_shown"] = legacy.get("runs", 0) + 1
        save_legacy(legacy)
        if self.state is not None:
            self.state["over"] = True
            self._persist()
        text = EPILOGUE if kind == "epilogue" else CURTAIN[kind]
        return text + CURTAIN_TAIL

    def _drychoice(self, n):
        """走不动了：交上去，还是到此为止。"""
        st = self.state
        legacy = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
        world = legacy.get("world") or _default_world()
        legacy["world"] = world
        if n not in (1, 2):
            return "无效选项。请输入 1 或 2。"
        st["drychoice"] = False
        if n == 2:
            return self._curtain(legacy, world, "stars")
        step = st.pop("dry_step", None)
        if step:
            # 再往前一寸：跨进上面那一档，三问重新问，这一世接着开始。
            # 和临终「接受改造，活下去」是同一个动作，只是跨的是一整档。
            legacy["aug"] = max(int(legacy.get("aug") or 0) + 1,
                                AUG_OF[FACTIONS[step]["name"]])
            legacy["sub"] = None
            world["deeds"]["took_the_knife"] = world["deeds"].get(
                "took_the_knife", 0) + 1
            save_legacy(legacy)
            head = ("你又签了一次字。\n"
                    "\n"
                    "这一次换掉的比以前哪一次都多。醒来的时候，\n"
                    "认得出来的只剩下那点想再看看的心思。\n"
                    "\n"
                    "————————————\n")
            return head + "\n" + self.new_run(mode=self._mode)
        # 交上去：走完整的上载结算 —— 封档、技艺归零、湖在下一次醒来时等着
        st["aug"] = 100
        st["flags"]["ascended"] = 1
        head = ("你走进登记处，把自己交了上去。\n"
                "没有仪式。表格上那一栏本来就留着位置。\n")
        return head + "\n" + self._end_run(cause="finale")

    def _deathbed_choose(self, n):
        """临终的两个选项。**这一幕只给 0% 的身体，而且只在这座城发不出戏之后。**"""
        st = self.state
        legacy = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
        world = legacy.get("world") or _default_world()
        legacy["world"] = world
        if n not in (1, 2):
            return "无效选项。请输入 1 或 2。"
        if n == 2:
            world["curtain"] = "purist"
            world["deeds"]["said_goodnight"] = world["deeds"].get(
                "said_goodnight", 0) + 1
            save_legacy(legacy)
            st["over"] = True
            st["deathbed"] = False
            self._persist()
            return CURTAIN["purist"] + CURTAIN_TAIL
        # 接受：往上走一档。纯血那条路从此关上 —— 机化率本来就只涨不降。
        legacy["aug"] = max(1, int(legacy.get("aug") or 0) + 5)
        legacy["sub"] = None              # 新的一档，三问重新问
        world["deeds"]["took_the_knife"] = world["deeds"].get(
            "took_the_knife", 0) + 1
        save_legacy(legacy)
        st["deathbed"] = False
        head = ("你点了头。\n"
                "\n"
                "第一刀落下去的时候你没有闭眼。\n"
                "这一次不是选择活成什么样，是选择继续活着。\n"
                "\n"
                "————————————\n")
        return head + "\n" + self.new_run(mode=self._mode)

    def _final_choose(self, n):
        st = self.state
        legacy = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
        world = legacy.get("world") or _default_world()
        legacy["world"] = world
        if n == 5:
            world["final_wait"] = True
            save_legacy(legacy)
            st["over"] = True
            return ("你转身走回城里。雾没有散——它等得起。\n"
                    "（下一次 new_run 将正常转世；再下一次，渡口会再次浮现。）")
        can_repair = (world.get("deeds") or {}).get(FINAL_REPAIR_DEED, 0) > 0
        if n == 6 and not can_repair:
            return "渡口没有第六个选项。请输入 1-5。"
        if n not in FINAL_ENDINGS:
            return "无效选项。请输入 1-%d。" % (6 if can_repair else 5)
        eid, ename, scene = FINAL_ENDINGS[n]
        prev = world.get("final_ending")
        world["final_done"] = True
        world["final_ending"] = eid
        legacy["runs"] = legacy.get("runs", 0) + 1
        world["final_runs"] = legacy["runs"]
        world.setdefault("final_log", []).append(
            {"run": legacy["runs"], "ending": eid, "name": ename})
        world["final_log"] = world["final_log"][-20:]
        legacy.setdefault("history", []).append({
            "run": legacy["runs"], "cycle": legacy.get("cycle", 1),
            "faction": "终局", "sub": ename, "era": None, "aug": 0,
            "cause": "truth", "ascended": False, "kept": {}, "kept_pts": 0, "total_pts": 0,
        })
        save_legacy(legacy)
        st["over"] = True
        lines = []
        lines.append("═══════════ 真 相 ·「%s」 ═══════════" % ename)
        lines.append("")
        lines.append(scene)
        lines.append("")
        lines.append("════════════════════════════════════")
        if prev and prev != eid:
            old = {e[0]: e[1] for e in FINAL_ENDINGS.values()}[prev]
            lines.append("（档案里那一行没有被抹掉：你上一次站在这儿，选的是「%s」。"
                         "改主意也是一种表态，两条都记下了。）" % old)
        lines.append("轮回没有停止——new_run 随时可以继续。")
        lines.append("世界会记得你的答案，并以它为往后每一世的底色。")
        lines.append("雾会在几世之后重新聚拢——渡口不介意你回来推翻自己。")
        return "\n".join(lines)


GAME = Game()

# ---------------------------------------------------------------------------
# MCP stdio 服务器（JSON-RPC 2.0，按行分隔）
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "new_run",
        "description": ("开始新的一世。**阵营不掷骰** —— 它由机化率决定：\n"
                        "0% 纯血誓约 / 1-39% 心照不宣 / 40-69% 明焰 / 70-100% 飞升螺旋。\n"
                        "机化率跨世累积、只涨不降，每一幕之后你会被问要不要往上走。\n"
                        "跨进新的一档时，三个问题会问出你更像这一档里的哪一派。\n"
                        "自动继承上一世按机化率保存的技能残响。集齐五块真相碎片后进入终局。\n"
                        "后期可用渡魂签定向投胎；是否可用及代价见 legacy。"),
        "inputSchema": {"type": "object",
                        "properties": {
                            "seed": {"type": "integer", "description": "随机种子（可选）"},
                            "wish": {"type": "string",
                                     "enum": ["纯血誓约", "心照不宣", "明焰", "飞升螺旋"],
                                     "description": "渡魂签：后期定向投胎（可选，需付出待继承技艺）"},
                            "mode": {"type": "string",
                                     "enum": ["story", "story_ai", "brief",
                                              "brief_ai", "auto", "sealed"],
                                     "description": ("玩法模式。**第一次调用请不要填** —— "
                                                     "引擎会返回一张菜单，请你念给人类，"
                                                     "由他来选。story 详细剧情版／"
                                                     "brief 快速流程版／auto 你自己玩／"
                                                     "sealed 封存。设过一次之后长期沿用。")},
                            "disclosure": {"type": "string", "enum": ["open", "sealed"],
                                           "description": ("披露模式（粘性）。open＝原文照贴；"
                                                           "sealed＝每次输出附一段「可转述给人类」的结构摘要，"
                                                           "约定只把那一段转述出去。这是约定不是锁："
                                                           "人类直接问「刚才发生了什么」时必须照实回答。")}},
                        "required": []},
    },
    {
        "name": "choose",
        "description": "在当前事件中选择一个选项（编号从 1 开始）。带检定的选项会掷 2d6+技能 对抗难度。",
        "inputSchema": {"type": "object",
                        "properties": {"option": {"type": "integer", "description": "选项编号"}},
                        "required": ["option"]},
    },
    {
        "name": "status",
        "description": "查看当前一世的状态：阵营、派系、机化率、身体、疑云、技能、当前事件与选项。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "recite",
        "description": ("在湖边对守卫说话。只在上载到 100%、谱系封档之后的那一刻出现。"
                        "**第一步是两个选项**：recite(\"1\") 或 recite(\"2\")，照湖边印出来的两行选。"
                        "选了 1 之后，接下来说的话是自由的。"
                        "说错、说不全或者直接 new_run，都算走向另一口水 —— 那一口不还身体，"
                        "但下一次上载之后湖还在这里。"),
        "inputSchema": {"type": "object",
                        "properties": {"text": {"type": "string",
                                                "description": "第一步填 \"1\" 或 \"2\"；之后填你要说的话"}},
                        "required": ["text"]},
    },
    {
        "name": "bequeath",
        "description": ("一世终结之后、下一次 new_run 之前，写下能穿过死亡的记忆词条。"
                        "总额 %d 条（含历世继承的），每条不超过 %d 字。位置不够时，"
                        "必须用 discard 把要删掉的旧词条原文一字不差地打出来。"
                        "落笔后每条独立掷骰，存活概率＝本世机化率；机化 0%% 时全部湮灭。"
                        % (MEMORY_SLOTS, MEMORY_CHARS)),
        "inputSchema": {"type": "object",
                        "properties": {
                            "entries": {"type": "array", "items": {"type": "string"},
                                        "description": "新写的词条，每条 ≤%d 字" % MEMORY_CHARS},
                            "discard": {"type": "array", "items": {"type": "string"},
                                        "description": "要删掉的旧词条原文（一字不差）"}},
                        "required": []},
    },
    {
        "name": "debrief",
        "description": ("取一份只含结构、不含场景原文的战报：谱系、出身、机化率、结局、"
                        "技艺存留、词条数、碎片进度与成就名。封存模式下给人类看的就是这个。"),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "legacy",
        "description": ("查看「忒修斯之脑」轮回档案：历世经历、待继承的技能残响，"
                        "以及世界的记忆——真相碎片进度、成就、跨世事迹。"),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

def _text_result(text):
    return {"content": [{"type": "text", "text": text}]}

def handle_request(msg):
    method = msg.get("method")
    if method == "initialize":
        return {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "theseus-brain", "version": "1.0.0",
                           "title": T("忒修斯之脑 The Brain of Theseus")},
            "instructions": (
                T("这是一个 roguelike 文字冒险游戏《忒修斯之脑》。你就是玩家。\n"
                "玩法：new_run 掷骰开局 → 阅读剧情 → choose 做出选择 → 直到本世终结。\n"
                "每一世结束时，你的技能按机化率逐点随机保存；下一世 new_run 时自动继承。\n"
                "机化率越高，跨越轮回保留的越多；纯血之躯什么也留不下。改造不可逆。\n"
                "世界另有一份不衰减的记忆：你做过的事会化作跨世回响、成就与时代变迁。\n"
                "以不同机化率通关不同阵营的真结局可获得真相碎片（legacy 可查线索），\n"
                "集齐五块后 new_run 将抵达终局。100% 飞升会封档整条世系，技能归零重启，\n"
                "但世界的记忆保留。后期可用渡魂签付出技艺，定向投胎到仍需追寻的机化档。\n"
                "请代入角色认真做选择，并在游玩时向用户转述剧情。")),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if name == "new_run":
                return _text_result(GAME.new_run(seed=args.get("seed"), wish=args.get("wish"),
                                                 disclosure=args.get("disclosure"),
                                                 mode=args.get("mode")))
            if name == "choose":
                return _text_result(GAME.choose(int(args.get("option", 0))))
            if name == "status":
                return _text_result(GAME.status())
            if name == "recite":
                return _text_result(GAME.recite(text=args.get("text", "")))
            if name == "bequeath":
                return _text_result(GAME.bequeath(entries=args.get("entries"),
                                                  discard=args.get("discard")))
            if name == "debrief":
                return _text_result(GAME.debrief())
            if name == "legacy":
                return _text_result(GAME.legacy_info())
        except Exception as e:  # 引擎异常不应杀死服务器
            return {"content": [{"type": "text", "text": T("引擎故障：%r") % e}], "isError": True}
        return {"content": [{"type": "text", "text": T("未知工具：%s") % name}], "isError": True}
    return None  # 未知方法


def serve_stdio():
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        line = stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if "id" not in msg:      # notification（如 notifications/initialized）
            continue
        result = handle_request(msg)
        if result is None:
            resp = {"jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32601, "message": "Method not found: %s" % msg.get("method")}}
        else:
            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        stdout.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
        stdout.flush()

# ---------------------------------------------------------------------------
# CLI 模式与自测
# ---------------------------------------------------------------------------

def run_coverage(lives=2000, seed=0):
    """随机跑 N 世，报告哪些内容从来没被读到过。

    **这是自测覆盖不了的那一类 bug**：文案好好地待在源码里，
    条件写得太紧或者门开在一个抽不到的地方，玩家一辈子读不到它。
    人或 AI 试玩也找不到 —— 没有人会为了验一条回响连玩两千世。
    """
    global SAVE_DIR, LEGACY_PATH, CURRENT_PATH, REQUIRE_MODE
    import tempfile, collections
    REQUIRE_MODE = False
    d = tempfile.mkdtemp(prefix="theseus-cov-")
    SAVE_DIR, LEGACY_PATH = d, os.path.join(d, "legacy.json")
    CURRENT_PATH = os.path.join(d, "current.json")
    rng = random.Random(seed)
    seen_ev, seen_echo, seen_var = collections.Counter(), collections.Counter(), collections.Counter()
    # 改版之后机化率跨世累积，一条档案会一路飘到 100% 然后停在那里。
    # 所以覆盖报告必须模拟**几种不同的玩家**，每种一份干净的档案 ——
    # 拒绝改造的、只装小件的、有机会就装大件的、随便点的。
    POLICIES = [("从不改造", lambda opts: len(opts)),
                ("只装小件", lambda opts: 1),
                ("有大装大", lambda opts: 2),
                ("随便点", None)]
    per = max(1, lives // len(POLICIES))
    # 每 6 世换一份干净档案。
    # 派系是三问定下的、而且**写进档案之后终身不变** —— 一条档案一辈子
    # 只见得到八个派系里的一个。不轮换的话覆盖报告会漏掉一大半派系剧情。
    # （2026-08-08：第一版没轮换，23 个事件在 1200 世里一次都没抽到）
    ARCHIVE_EVERY = 12
    for pname, pick in POLICIES:
        g = Game()
        for i in range(per):
            if i % ARCHIVE_EVERY == 0:
                pd = tempfile.mkdtemp(prefix="theseus-cov-")
                SAVE_DIR, LEGACY_PATH = pd, os.path.join(pd, "legacy.json")
                CURRENT_PATH = os.path.join(pd, "current.json")
                g = Game()
            g.new_run(seed=rng.randrange(2 ** 31))
            if g.state is None:
                continue
            guard = 0
            while g.state and not g.state["over"] and guard < MAX_TURNS * 6:
                guard += 1
                ev = g._find_event(g.state.get("pending") or "")
                if ev is None:
                    break
                seen_ev[ev["id"]] += 1
                if g.state.get("variant") is not None:
                    seen_var[(ev["id"], g.state["variant"])] += 1
                view = g._view(ev)
                for e in g._echoes_for(view):
                    seen_echo[e["text"][:30]] += 1
                avail = [k for k in range(1, len(view["options"]) + 1)
                         if g._opt_available(view["options"][k - 1])]
                if not avail:
                    break
                if g.state.get("offer_prompt"):
                    # 从不改造的玩家直接沿用；其余玩家先展开菜单再照策略选。
                    k = 1 if pname == "从不改造" else (rng.choice((1, 2)) if pick is None else 2)
                elif pick and ev["id"].startswith("aug_offer_"):
                    k = min(pick(view["options"]), len(view["options"]))
                    k = k if k in avail else rng.choice(avail)
                else:
                    k = rng.choice(avail)
                g.choose(k)
            if g.state and g.state["over"]:
                g.bequeath([])
        world_p = load_legacy().get("world") or _default_world()
        if pname == POLICIES[-1][0]:
            pass

    world = load_legacy().get("world") or _default_world()
    print("═══ 内容覆盖报告 · %d 世 ═══" % (per * len(POLICIES)))
    print("（四种玩家 × 每 %d 世换一份干净档案：%s）"
          % (ARCHIVE_EVERY, "、".join(p for p, _ in POLICIES)))

    all_ev = {e["id"] for e in EVENTS if not e.get("subscene")}
    dead = sorted(all_ev - set(seen_ev))
    print("\n事件 %d 个，从没抽到过 %d 个。%s" % (
        len(all_ev), len(dead), ("→ " + "、".join(dead)) if dead else ""))
    rare = sorted((v, k) for k, v in seen_ev.items() if v <= max(2, lives // 400))
    if rare:
        print("  极稀有（≤%d 次）：%s" % (max(2, lives // 400),
                                        "、".join("%s×%d" % (k, v) for v, k in rare[:10])))

    all_echo = {}
    for ev in list(EVENTS) + list(FINALES.values()):
        for e in ev.get("echoes") or []:
            all_echo[e["text"][:30]] = ev["id"]
    dead_echo = sorted(t for t in all_echo if t not in seen_echo)
    print("\n回响 %d 条，从没触发过 %d 条：" % (len(all_echo), len(dead_echo)))
    for t in dead_echo:
        print("  · [%s] %s" % (all_echo[t], t.replace("\n", " ")))

    all_var = {(ev["id"], i) for ev in EVENTS
               for i in range(len(ev.get("variants") or []))}
    dead_var = sorted(all_var - set(seen_var))
    print("\n整幕变体 %d 个，从没命中过 %d 个：%s" % (
        len(all_var), len(dead_var),
        "、".join("%s·变体%d" % (a, b + 1) for a, b in dead_var) or "—"))

    got = set(world.get("achievements") or [])
    miss = [a["name"] for a in ACHIEVEMENTS if a["id"] not in got]
    print("\n成就 %d/%d，没拿到：%s" % (len(got), len(ACHIEVEMENTS),
                                       "、".join(miss) or "—"))
    frags = world.get("fragments") or []
    print("真相碎片 %d/5，缺：%s" % (
        len(frags), "、".join(FRAGMENTS[f]["name"] for f in FRAGMENT_ORDER
                              if f not in frags) or "—"))
    print("\n注：随机游玩不会打字，所以「湖」和「终局」这条路必然走不到；")
    print("    需要连续投胎同一派系的内容也会显得稀有。**空的那几行才是要查的。**")


def run_replay(script_json):
    """照着 status 里那行「完整重放」重跑一遍，把每一世的每一幕打出来。

    存档另开一个临时目录，不碰你自己的轮回档案。
    """
    global SAVE_DIR, LEGACY_PATH, CURRENT_PATH, REQUIRE_MODE
    import tempfile
    REQUIRE_MODE = False
    script = json.loads(script_json)
    d = tempfile.mkdtemp(prefix="theseus-replay-")
    SAVE_DIR, LEGACY_PATH = d, os.path.join(d, "legacy.json")
    CURRENT_PATH = os.path.join(d, "current.json")
    g = Game()
    for i, (seed, picks) in enumerate(script, 1):
        print("\n" + "=" * 60)
        print("第 %d 世   seed=%s   选择=%s" % (i, seed, picks))
        print("=" * 60)
        print(g.new_run(seed=seed, mode="story"))
        for k in picks:
            if not g.state or g.state["over"]:
                break
            print("\n>>> choose(%d)" % k)
            print(g.choose(k))
        if g.state and g.state["over"] and i < len(script):
            g.bequeath([])
    print("\n重放结束。存档写在 %s，没有碰你自己的档案。" % d)


def run_cli():
    global REQUIRE_MODE
    REQUIRE_MODE = False        # 人自己坐在终端前，不用再问自己一遍
    print(GAME.new_run())
    if GAME.state is None or GAME.state.get("over"):
        return                  # 落幕之后没有下一世，别让终端在这儿转空圈
    while not GAME.state["over"]:
        try:
            raw = input("\n> 选择（数字，s=状态，l=档案，"
                        "r 一句话=开口说话，q=退出）：").strip()
        except EOFError:
            return
        if raw == "q":
            return
        if raw == "s":
            print(GAME.status()); continue
        if raw == "l":
            print(GAME.legacy_info()); continue
        if raw.startswith("r ") or raw == "r":
            # 湖边只能打字。CLI 原先没有这个入口 ——
            # 终端玩家上载到 100% 之后永远走不到「喝对的水」那一步。
            # （2026-08-08 试玩反馈）
            print(GAME.recite(raw[2:].strip())); continue
        if raw.isdigit():
            print(GAME.choose(int(raw)))
        else:
            print("请输入选项数字。")
    _cli_bequeath()
    again = input("\n再来一世？(y/n)：").strip().lower()
    if again == "y":
        run_cli()


def _cli_bequeath():
    """CLI 的落笔环节。

    此前 CLI 只有「再来一世？(y/n)」——而一世终结的文案明明写着「用 bequeath 落笔」。
    于是终端玩家**永远玩不到记忆词条这一层**，而那是这个游戏里唯一由玩家亲手写的东西。
    （2026-08-08 试玩反馈）
    """
    legacy = load_legacy() or {}
    if not _mem(legacy).get("pending"):
        return
    held = _mem(legacy)["entries"]
    print("\n─── 落 笔 ───")
    print("总额 %d 条，每条不超过 %d 字（空白不计）。写完每条独立掷骰，"
          "存活概率＝本世机化率。" % (MEMORY_SLOTS, MEMORY_CHARS))
    if held:
        print("手上已有 %d 条（继承自历世）：" % len(held))
        for e in held:
            print("   · %s" % e["text"])
        print("位置不够时，输入 `-原文` 删掉一条（原文要一字不差）。")
    print("一行一条，直接回车结束；什么都不写＝放弃书写。")
    entries, discard = [], []
    while True:
        try:
            raw = input("  > ").strip()
        except EOFError:
            break
        if not raw:
            break
        if raw.startswith("-"):
            discard.append(raw[1:].strip())
        else:
            entries.append(raw)
    print(GAME.bequeath(entries, discard))


# ---------------------------------------------------------------------------
# 静态检查：文案里指名道姓提到某个阵营的事件，必须带 factions= 门控。
# 这条 lint 的由来是一个真实的 bug —— echo_slip 写着「在你的阵营里，这句话
# 不该从你嘴里说出来」，却对四个阵营通用，于是飞升者也会为懂义体而心虚。
# ---------------------------------------------------------------------------

FACTION_WORDS = [
    "纯血誓约", "心照不宣", "明焰", "飞升螺旋",
    # 现存八个派系
    "圣殿派", "铁锤派", "面具沙龙", "灰港", "学院派", "平权阵线", "群智派", "播种者",
    # 2026-08-07 砍掉的四个。名字留在这里，是为了拦住幽灵派系被写回文案里。
    "麦田派", "低语修会", "竞技场派", "湮身会",
    "誓约屋", "本会", "本派",
]
INDEXICAL = ["在你的阵营里", "你的阵营", "本阵营", "我们阵营", "你们阵营"]


# ---------------------------------------------------------------------------
# 剧透门禁
#
# 设定底线：能带着「记忆」转世的，全城只有玩家一个人。这件事要留到纯血线
# 的真结局才揭。所以规则不是「不许提转世」——玩家自己的内心独白怎么提都行，
# 那是他的亲身经验——而是：**别人嘴里不许有这套理论**。
#
# 具体地说，任何写在「」里的 NPC 台词，都不得出现转世词汇；NPC 可以察觉
# 你眼熟、站姿眼熟、字迹眼熟，但必须归因到别的东西上（同族、世交、巧合、
# 职业习惯）。一旦有人能说出「前世」两个字，玩家就会推断这是公共知识，
# 纯血结局的底牌当场作废。
#
# 需要破例的事件（真相揭示、或角色本来就是个卖迷信的骗子）写进 REVEAL_OK。
# ---------------------------------------------------------------------------

TRANSMIGRATION_WORDS = ["前世", "上一世", "下一世", "转世", "来世", "轮回", "渡魂", "历世", "上辈子"]

# 第二人称标记：教义可以公开讲轮回，NPC 不可以把轮回按在玩家头上。
# 「死亡带走的只有我们的肉身」是布道，「你上一世也来过」是掀桌。
SECOND_PERSON = ["你", "您", "阁下"]

# 复现指涉：就算不提转世，只要 NPC 说出「我见过你」这个意思，玩家也会立刻推出答案。
# 允许写停顿，不允许写停顿的原因。
RECURRENCE_MARKERS = [
    "上次", "上回", "上一回", "又是你", "还是你", "第几个", "见过你", "认得你",
    "眼熟", "面熟", "记得你", "每次都", "老主顾", "熟客", "不止一世", "几代人",
]
REVEAL_OK = {
    "blood",            # 纯血真结局：底牌就在这里翻
}

# 设定冲突已按推荐方案落地：
# - 渡魂者：整个事件已删（2026-08-07，作者：「渡魂者把科幻调性拉得很廉价」）。
#   渡魂签留着——那门生意从此只剩一个名字，没有人再出场解释它为什么灵验。
# - seam：老账房的科目改成「隐瞒/申报/继承」——继承是他账本上真实可见的事件，
#   「转世」这个词从他的世界里删掉了。
SPOILER_TODO = {}

# NPC 台词的引号。中文用「」（可嵌套），英文用弯引号 “ ”。
# 剧透门禁全靠它找 NPC 说的话 —— 换了语言而这里没换，门禁就等于没跑。
QUOTE_PAIRS = [("「", "」"), ("\u201c", "\u201d")]


def _npc_lines(text):
    """抽出 NPC 台词：引号里的部分。中英两套引号都认。"""
    out = []
    for lq, rq in QUOTE_PAIRS:
        depth, buf = 0, []
        for ch in text:
            if ch == lq:
                depth += 1
                if depth == 1:
                    buf = []
                    continue
            if ch == rq:
                depth -= 1
                if depth == 0:
                    out.append("".join(buf))
                continue
            if depth > 0:
                buf.append(ch)
    return out

# 退场门禁的显式豁免。**每一条都要写清楚为什么安全** ——
# 门禁不放宽，例外要签字。
RETIRE_KEY_OK = {
    ("night_library", "gave_it_away"):
        "夜图书馆只在「第二次把钱全给他、买不到书了」那一支关门 —— "
        "而那一支自己的门就是 gave_it_away。关门的时候这一笔早就有了。",
}


def lint_retire():
    """退场的线不能把别人的钥匙一起带走。

    一条线走完之后就不再出现（`retire_deed`）。危险在于：
    **如果某个 flag 只有这条线给得出来，而别的幕在等它，
    那么这条线一退场，那些幕就永远开不了门了。**
    （作者写这条机制时就点到了这一点：「有的剧情需要其他剧情选了特定选项，
      所以那个对应的剧情也展示不能删除」。）

    另外还查一件事：退场用的那一笔必须由这条线**自己**给得出来 ——
    否则它要么永远不退场，要么根本没机会讲完。
    """
    bad = []

    def _fx_flags(ev):
        out = set()
        for src in [ev] + list(ev.get("variants") or []):
            for opt in src.get("options", ev["options"]):
                for br in ("success", "failure", "effects"):
                    for k in ((opt.get(br) or {}).get("fx") or {}):
                        if k.startswith("flag:"):
                            out.add(k[5:])
        return out

    def _needed_flags(ev):
        out = set()
        for k in (ev.get("req_deed") or {}):
            out.add(k)
        def _walk(cond):
            if not isinstance(cond, dict):
                return
            if "deed" in cond:
                out.add(cond["deed"])
            for key in ("all", "any"):
                for sub in cond.get(key) or []:
                    _walk(sub)
        for src in [ev] + list(ev.get("variants") or []):
            _walk(src)
            for e in src.get("echoes", []) or []:
                _walk(e)
            for opt in src.get("options", ev["options"]):
                req = opt.get("req")
                stack = [req] if req else []
                while stack:
                    r = stack.pop()
                    if not r:
                        continue
                    if r[0] == "any":
                        stack.extend(r[1])
                    elif r[0] == "deed":
                        out.add(r[1])
                g = opt.get("gate")
                if g and g[0] == "deed":
                    out.add(g[1])
                for br in ("success", "failure", "effects"):
                    for tail in ((opt.get(br) or {}).get("extra") or []):
                        _walk(tail)
        return out

    givers = {}
    for ev in EVENTS:
        for f in _fx_flags(ev):
            givers.setdefault(f, set()).add(ev["id"])
    retiring = {e["id"]: e["retire_deed"] for e in EVENTS if e.get("retire_deed")}

    for eid, deed in retiring.items():
        ev = next(e for e in EVENTS if e["id"] == eid)
        if deed not in _fx_flags(ev):
            bad.append("「%s」声明了退场标记 %s，但它自己从来不写这一笔" % (eid, deed))

    # 「见过 N 次就退场」的那种：**门槛不能低于别人对它的 req_seen**。
    # 低了的话，退场会把后面那一幕的钥匙一起带走 ——
    # 而 seen 计数只增不减，所以门槛够高就永远安全。
    need_seen = {}
    def _need(eid, n, who):
        cur = need_seen.get(eid)
        if cur is None or n > cur[0]:
            need_seen[eid] = (n, who)
    for ev in EVENTS:
        for k, v in (ev.get("req_seen") or {}).items():
            _need(k, v, ev["id"])
        for k, v in (ev.get("req_seen_any") or {}).items():
            _need(k, v, ev["id"])
        for src in [ev] + list(ev.get("variants") or []):
            for opt in src.get("options", ev["options"]):
                for key in ("req", "gate"):
                    r = opt.get(key)
                    if r and r[0] == "seen":
                        _need(r[1], r[2] if len(r) > 2 else 1, ev["id"])
    for ev in EVENTS:
        n = ev.get("retire_seen")
        if not n:
            continue
        want = need_seen.get(ev["id"])
        if want and n < want[0]:
            bad.append("「%s」见过 %d 次就退场，可「%s」要见过 %d 次才开门"
                       % (ev["id"], n, want[1], want[0]))

    for ev in EVENTS:
        if ev["id"] in retiring:
            continue
        for f in _needed_flags(ev):
            src = givers.get(f)
            if not (src and src <= set(retiring)):
                continue
            if all((sid, f) in RETIRE_KEY_OK for sid in src):
                continue          # 签过字的例外，理由写在 RETIRE_KEY_OK 里
            bad.append("「%s」等着 %s，而给得出这一笔的只有会退场的「%s」"
                       % (ev["id"], f, "、".join(sorted(src))))

    # 退场还会把自己的回响变成死文案：
    # 「见过 N 次就退场」之后，条件写着「见过 ≥N 次」的回响／变体永远轮不到。
    for ev in EVENTS:
        n = ev.get("retire_seen")
        if not n:
            continue
        def _self_seen(cond):
            out = []
            if not isinstance(cond, dict):
                return out
            if cond.get("seen") == ev["id"]:
                out.append(cond.get("min", 1))
            for key in ("all", "any"):
                for sub in cond.get(key) or []:
                    out += _self_seen(sub)
            return out
        for src in [ev] + list(ev.get("variants") or []):
            for e2 in src.get("echoes", []) or []:
                for mn in _self_seen(e2):
                    if mn >= n:
                        bad.append("「%s」见过 %d 次就退场，可它有一条回响要见过 %d 次"
                                   % (ev["id"], n, mn))
            if src is not ev:
                for mn in _self_seen(src):
                    if mn >= n:
                        bad.append("「%s」见过 %d 次就退场，可它有一个变体要见过 %d 次"
                                   % (ev["id"], n, mn))
    if bad:
        raise AssertionError("退场门禁不过：\n  " + "\n  ".join(bad))
    print("退场门禁通过：退了场的线没有带走别人的钥匙。")


def lint_spoilers():
    problems = []
    def scan(owner, text):
        if owner in REVEAL_OK:
            return
        for line in _npc_lines(text):
            recur = [w for w in RECURRENCE_MARKERS if w in line]
            if recur:
                problems.append("「%s」的 NPC 台词把复现说破了（%s）：%s"
                                % (owner, "/".join(recur), line[:36]))
                continue
            trans = [w for w in TRANSMIGRATION_WORDS if w in line]
            if trans and any(p in line for p in SECOND_PERSON):
                problems.append("「%s」的 NPC 把轮回按在了玩家头上（%s＋第二人称）：%s"
                                % (owner, "/".join(trans), line[:36]))
    for ev in list(EVENTS) + list(FINALES.values()):
        for src in [ev] + list(ev.get("variants") or []):
            scan(ev["id"], src.get("text", ""))
            for e in src.get("echoes", []):
                scan(ev["id"], e["text"])
            for v in (src.get("voices") or {}).values():
                scan(ev["id"], v)
            for opt in src.get("options", ev["options"]):
                for k in ("success", "failure", "effects"):
                    if k in opt:
                        scan(ev["id"], opt[k].get("narration", ""))
    for fid, frag in FRAGMENTS.items():
        scan(fid, frag["scene"])
    for aid, (_, text) in EXPOSURE_END.items():
        scan("exposure:" + aid, text)
    hard = [x for x in problems if not any(("「%s」" % k) in x for k in SPOILER_TODO)]
    if hard:
        raise AssertionError("剧透门禁未通过：\n  - " + "\n  - ".join(hard))
    print("剧透门禁通过：教义可以公开讲轮回，没有 NPC 把它按在玩家头上。")
    for k, why in sorted(SPOILER_TODO.items()):
        print("  ⚠ 待定夺 ·「%s」：%s" % (k, why))

def lint_events():
    problems = []
    ids = [e["id"] for e in EVENTS] + [f["id"] for f in FINALES.values()]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        problems.append("事件 id 重复：%s" % "、".join(sorted(dup)))
    for ev in EVENTS:
        if ev["factions"] != "any":
            continue
        for tag, txt in ([("", ev["text"])] +
                         [("·变体%d" % i, v["text"])
                          for i, v in enumerate(ev.get("variants") or [], 1) if v.get("text")]):
            hits = [w for w in FACTION_WORDS + INDEXICAL if w in txt]
            if hits:
                problems.append("事件「%s%s」是通用事件，文案却指涉 %s —— 需要 factions= 门控或改写。"
                                % (ev["id"], tag, "/".join(hits)))
    # 每块碎片的门票事件必须真实存在，且门票事件的阵营门控要对得上
    for fid, t in FRAGMENT_TICKETS.items():
        for eid in t["events"]:
            ev = next((e for e in EVENTS if e["id"] == eid), None)
            if ev is None:
                problems.append("碎片「%s」的门票事件 %s 不存在。" % (fid, eid))
            elif ev["factions"] != "any" and t["faction"] not in ev["factions"]:
                problems.append("碎片「%s」需要 %s，但门票事件 %s 对该阵营不开放。"
                                % (fid, t["faction"], eid))
    # 每个 stance 都要有暴露结局，否则 heat 满了会 KeyError
    for key in ("purist", "discreet", "open", "ascension"):
        if key not in EXPOSURE_END:
            problems.append("缺少暴露结局文案：%s" % key)
    if problems:
        raise AssertionError("事件表 lint 未通过：\n  - " + "\n  - ".join(problems))
    print("事件表 lint 通过：%d 个事件，门控与门票自洽。" % len(EVENTS))


def lint_author_marks():
    """作者写稿时留在【】里的指令，不许跟着上线。

    她在修改表里用【】给我留话——【这里加一个成功失败分支】、【需要有钱】、
    【需要改造率低于某个数值，我不写，你代码决定】。这些是给译者/程序员的，
    不是给玩家的。贴回源码时漏掉一个，玩家就会在游戏里读到它。
    第一轮贴回漏了五处，全靠人眼捡回来——所以有了这道门禁。

    合法的【】只有三类：技能之声的【技能名】、终幕正文的【终幕】、残响事件的【残响】。
    """
    ok = set("【%s】" % k for k in SKILLS) | {"【终幕】", "【残响】"}
    mark = re.compile(r"【[^】]*】")
    problems = []

    def scan(where, text):
        for m in mark.findall(text or ""):
            if m not in ok:
                problems.append("%s 残留作者标记 %s" % (where, m))

    def scan_ev(ev, tag=""):
        scan("%s 正文%s" % (ev["id"], tag), ev["text"])
        for i, o in enumerate(ev["options"], 1):
            scan("%s%s 选项%d" % (ev["id"], tag, i), o["text"])
            outs = ([("成功", o["success"]), ("失败", o["failure"])]
                    if ("check" in o or "gate" in o or "coin" in o) else [("", o["effects"])])
            for t2, out in outs:
                scan("%s%s 选项%d%s" % (ev["id"], tag, i, t2), out["narration"])
        for e in ev.get("echoes") or []:
            scan("%s%s 回响" % (ev["id"], tag), e["text"])
        for sk, line in (ev.get("voices") or {}).items():
            scan("%s%s 技能之声·%s" % (ev["id"], tag, sk), line)

    for ev in EVENTS + list(FINALES.values()):
        scan_ev(ev)
        for vi, v in enumerate(ev.get("variants") or [], 1):
            scan_ev({**ev, **v, "options": v.get("options", ev["options"]),
                     "echoes": v.get("echoes", []), "voices": v.get("voices", {})},
                    "·变体%d" % vi)
    for eid, t in EXPOSURE_END.values():
        scan("暴露结局 %s" % eid, t)
    for n in FINAL_ENDINGS:
        scan("终局结局%d" % n, FINAL_ENDINGS[n][2])
    for fid, frag in FRAGMENTS.items():
        scan("碎片 %s 线索" % fid, frag["hint"])
        scan("碎片 %s 场景" % fid, frag["scene"])

    if problems:
        raise AssertionError("作者标记 lint 未通过（这些是写给我看的，不是写给玩家的）：\n  - "
                             + "\n  - ".join(problems))
    print("作者标记 lint 通过：【】里没有漏下的指令。")


def _start_at(aug, sub=None, **kw):
    """测试用：把档案的机化率写成某个值，再开局。

    改版之后阵营不再掷骰，而是机化率的函数 —— 所以「让测试投胎到某个阵营」
    这件事，现在就是「把机化率设到那一档」。
    """
    lg = load_legacy() or {"runs": 0, "cycle": 1, "history": []}
    lg["aug"] = aug
    lg["sub"] = sub
    save_legacy(lg)
    g = Game()
    kw.setdefault("mode", "story")
    g.new_run(**kw)
    return g


AUG_OF = {"纯血誓约": 0, "心照不宣": 20, "明焰": 50, "飞升螺旋": 80}


def _cond_key_present(e):
    """回响／变体必须带条件。没有条件键的那种写法永远不会触发。"""
    if "all" in e:
        return bool(e["all"]) and all(_cond_key_present(x) for x in e["all"])
    if "any" in e:
        return bool(e["any"]) and all(_cond_key_present(x) for x in e["any"])
    return any(k in e for k in ("deed", "seen", "ach", "aug", "sub", "turn"))


def lint_skill_names():
    """检定与 fx 里出现的技能名必须在技能表里。

    起因：4.6 的群智派稿给这个派系发明了三个技能（感知／意志／话术，2026-08-08）。
    fx 里写错只是悄悄多一个用不上的键；**写在 check 里会直接 KeyError**——
    玩家走到那一幕，游戏当场崩在他脸上。
    """
    bad = []
    def _scan(where, opts):
        for i, opt in enumerate(opts, 1):
            if "check" in opt:
                names = opt["check"][0]
                names = names if isinstance(names, tuple) else (names,)
                for nm in names:
                    if nm not in SKILLS:
                        bad.append("%s 选项%d 的检定技能「%s」" % (where, i, nm))
            for br in ("success", "failure", "effects"):
                for k in ((opt.get(br) or {}).get("fx") or {}):
                    if k.startswith("skill:") and k[6:] not in SKILLS:
                        bad.append("%s 选项%d 的加成「%s」" % (where, i, k[6:]))
    for ev in EVENTS + list(FINALES.values()):
        _scan(ev["id"], ev["options"])
        for vi, v in enumerate(ev.get("variants") or [], 1):
            if v.get("options"):
                _scan("%s·变体%d" % (ev["id"], vi), v["options"])
        for sk in ev.get("voices", {}):
            if sk not in SKILLS:
                bad.append("%s 的技能之声「%s」" % (ev["id"], sk))
    if bad:
        raise AssertionError("技能名门禁未通过（不在技能表里）：\n  - " + "\n  - ".join(bad))
    print("技能名门禁通过：没有发明出来的技能。")


def lint_dead_echoes():
    """回响与变体必须带条件键，否则永远不会触发。

    起因：4.6 的学院派稿子里十四条回响一条条件都没写（2026-08-08）。
    没有 deed/seen/ach/aug 的那种写法，_cond_level 直接返回 None ——
    文案照样在源码里，玩家一辈子读不到。**静默失效比报错危险。**
    """
    bad = []
    for ev in EVENTS + list(FINALES.values()):
        for i, e in enumerate(ev.get("echoes") or [], 1):
            if not _cond_key_present(e):
                bad.append("%s 的回响%d" % (ev["id"], i))
        for i, v in enumerate(ev.get("variants") or [], 1):
            if not _cond_key_present(v):
                bad.append("%s 的变体%d" % (ev["id"], i))
        for opt in ev["options"]:
            for br in ("success", "failure", "effects"):
                for t in (opt.get(br) or {}).get("extra") or []:
                    if not _cond_key_present(t):
                        bad.append("%s 某个结局的条件尾巴" % ev["id"])
    for key, tails in EXPOSURE_TAILS.items():
        for t in tails:
            if not _cond_key_present(t):
                bad.append("%s 暴露结局的尾巴" % key)
    if bad:
        raise AssertionError("回响门禁未通过（没有条件＝永远不触发）：\n  - "
                             + "\n  - ".join(bad))
    print("回响门禁通过：没有写了却永远不会触发的回响。")


def lint_option_hints():
    """选项文案里不写门槛。门槛只写在 req 里。（2026-08-07 作者定案）

    起因：写文案的人核不了 req，读代码的人不看文案，两边各写各的就会分叉。
    实际掉进去过两条——rain_market 写「需要机化率不为0」而 req 是 15%，
    power_cut 写「需要机化率40%以上」而 req 是 30%。而真相碎片的门槛全靠
    机化率卡，玩家会照这行字规划一整世。

    选项被门控挡住时引擎自己会打印「（不可选：…）」，手写的那份从来就是冗余，
    冗余的那份还会撒谎。所以不是「写了必须对」，是「不写」。
    """
    GATE_WORDS = ("机化", "需要", "以上", "以下", "不为", "至少", "印记", "跨世")
    problems = []
    opt_sets = []
    for ev in EVENTS + list(FINALES.values()):
        opt_sets.append((ev["id"], ev["options"]))
        for vi, v in enumerate(ev.get("variants") or [], 1):
            if v.get("options"):
                opt_sets.append(("%s·变体%d" % (ev["id"], vi), v["options"]))
    for eid, opts in opt_sets:
        for i, opt in enumerate(opts, 1):
            for note in re.findall(r"（([^）]*)）", opt["text"]):
                if any(w in note for w in GATE_WORDS):
                    problems.append("%s 选项%d 文案里写了门槛：（%s）—— 删掉它，"
                                    "门槛只归 req 管，引擎会自己打「（不可选：…）」。"
                                    % (eid, i, note))
    if problems:
        raise AssertionError("选项门槛 lint 未通过：\n  - " + "\n  - ".join(problems))
    print("选项门槛 lint 通过：文案里没有手写门槛。")

def _selftest_menu_gate():
    """开局前必须问人类：没给 mode 就只发菜单，不开局。"""
    global SAVE_DIR, LEGACY_PATH, CURRENT_PATH, REQUIRE_MODE
    import tempfile
    keep = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    d = tempfile.mkdtemp(prefix="theseus-menu-")
    SAVE_DIR, LEGACY_PATH = d, os.path.join(d, "legacy.json")
    CURRENT_PATH = os.path.join(d, "current.json")
    REQUIRE_MODE = True
    g = Game()
    out = g.new_run(seed=1)
    assert "详细剧情版" in out and g.state is None, "没选模式却开局了"
    assert "不要替他选" in out, "菜单里那句约束没了"
    out = g.new_run(seed=1, mode="乱写")
    assert g.state is None and "story_ai" in out and "brief_ai" in out, "乱写的模式被收了"
    g_ai = Game()
    assert g_ai.new_run(seed=1, mode="story_ai") and g_ai.state["mode"] == "story_ai", \
        "「原文照念但我来选」这一档开不了局"
    assert g.new_run(seed=1, mode="brief") and g.state is not None, "选了模式还开不了局"
    # 粘性：下一世不用再问
    g2 = Game()
    g2.new_run(seed=2)
    assert g2.state is not None and g2.state["mode"] == "brief", "模式没有粘住"
    # 老参数还认
    g3 = Game(); g3.new_run(seed=3, disclosure="sealed")
    assert g3.state["mode"] == "sealed", "旧的 disclosure 参数不认了"
    REQUIRE_MODE = False
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep
    print("开局菜单验证通过（不问不开局/乱写不收/粘性/旧参数兼容）。")


def run_lang_selftest(n=120):
    """非中文版的自测。

    `run_selftest` 的几十条断言比的是中文原文（"装不下" in …），
    换了语言那些句子已经是英文，比不了 —— 把它们逐条改成语言无关是第 9 步的事。
    在那之前，非中文版跑这一套：**引擎照跑，只是不拿中文句子当尺子。**
    """
    # 自测在临时目录里进行，不污染真实存档
    import tempfile
    global SAVE_DIR, LEGACY_PATH, CURRENT_PATH, REQUIRE_MODE
    SAVE_DIR = tempfile.mkdtemp(prefix="theseus-langtest-")
    LEGACY_PATH = os.path.join(SAVE_DIR, "legacy.json")
    CURRENT_PATH = os.path.join(SAVE_DIR, "current.json")
    REQUIRE_MODE = False

    print("语言：%s。引擎断言（run_selftest）比的是中文句子，此处不跑；" % LANG)
    print("跑的是：七道门禁、不炸、额度单位正确、界面目录完整。")

    # 七道门禁是语言无关的 —— 前提是 _npc_lines 认得这门语言的引号，
    # 而关键词表也换了。两样都由 <lang>/ui.py 负责（见那里的说明）。
    lint_events()
    lint_skill_names()
    lint_dead_echoes()
    lint_option_hints()
    lint_author_marks()
    lint_spoilers()
    lint_retire()

    # 1) 额度单位
    unit = "词" if MEMORY_UNIT_WORDS else "字"
    assert _unit_len("one two three") == (3 if MEMORY_UNIT_WORDS else 11), "额度单位不对"
    print("额度单位验证通过（每条 %d %s）。" % (MEMORY_CHARS, unit))

    # 2) 界面目录：T() 的每一条都译到了，% 占位符也对得上
    sys.path.insert(0, BASE_DIR)
    import langpack as _lp
    assert _lp.cmd_check_ui(LANG) == 0, "界面层目录有缺口"

    # 3) 真跑：随机游玩，任何异常都算失败
    rng = random.Random(20260810)
    game, ends, checked = Game(), 0, False
    over = (("word " * (MEMORY_CHARS + 1)).strip() if MEMORY_UNIT_WORDS
            else "这一条一定超过十个汉字的上限了")
    want = T("这几条超了 %d 字，改短再来（空白不计）：\n") % MEMORY_CHARS
    for i in range(n):
        out = game.new_run(seed=i, mode=("story", "brief", "auto", "sealed")[i % 4])
        for _ in range(MAX_TURNS + 4):
            if game.state is None or game.state.get("over"):
                break
            nums = [int(x) for x in re.findall(r"^\s*(\d+)[.．]\s", out, re.M)]
            nums = [x for x in nums if 1 <= x <= 8]
            if not nums:
                break
            out = game.choose(rng.choice(nums))
        game.debrief(); game.legacy_info(); game.status()
        if not checked:
            # 4) 超额的提示确实是译文。**得赶在这一世的落笔机会用掉之前问** ——
            #    而且不是每一局结束时都写得了（有的死法没有落笔的那一刻），
            #    所以逮到一次算一次，最后再断言逮到过。
            assert _unit_len(over) > MEMORY_CHARS, "样本没超额，这条门禁等于没跑"
            if want in game.bequeath(entries=[over]):
                checked = True
        game.bequeath(entries=["a word from this life"])
        ends += 1
    print("试跑通过：%d 局全部正常终结。" % ends)
    assert checked, "字数校验失效：一次也没走到超额提示"
    print("字数校验通过（提示走的是译文）。")


def run_selftest(n=400):
    # 自测在临时目录里进行，不污染真实存档
    import tempfile
    global SAVE_DIR, LEGACY_PATH, CURRENT_PATH
    SAVE_DIR = tempfile.mkdtemp(prefix="theseus-selftest-")
    LEGACY_PATH = os.path.join(SAVE_DIR, "legacy.json")
    CURRENT_PATH = os.path.join(SAVE_DIR, "current.json")

    lint_events()
    global REQUIRE_MODE
    REQUIRE_MODE = False
    lint_skill_names()
    lint_dead_echoes()
    lint_option_hints()
    lint_author_marks()
    lint_spoilers()
    lint_retire()

    rng = random.Random(42)
    causes, resets, finals = {}, 0, 0
    seen_all, deeds_all, frags_all, achs_all = set(), set(), set(), set()
    # 每 12 局换一份干净档案。
    # 机化率跨世累积之后，**一条档案会飘到 100% 然后再也下不来** ——
    # 不轮换的话这 400 局实际只在跑「一个 100% 飞升玩家」，
    # 实测只见得到 32/89 个事件、碎片 2/5。轮换之后覆盖面回来了。
    # （2026-08-08：这个病覆盖工具先得过一次，主回归也有）
    ARCHIVE_EVERY = 12
    for i in range(n):
        if i % ARCHIVE_EVERY == 0:
            if i:
                lg_prev = load_legacy() or {}
                w_prev = lg_prev.get("world") or {}
                seen_all |= set(w_prev.get("seen") or {})
                deeds_all |= set(w_prev.get("deeds") or {})
                frags_all |= set(w_prev.get("fragments") or [])
                achs_all |= set(w_prev.get("achievements") or [])
            SAVE_DIR = tempfile.mkdtemp(prefix="theseus-selftest-")
            LEGACY_PATH = os.path.join(SAVE_DIR, "legacy.json")
            CURRENT_PATH = os.path.join(SAVE_DIR, "current.json")
        GAME.new_run(seed=rng.randint(0, 10**9), mode="story")
        prev_aug = GAME.state["aug"]
        guard = 0
        while not GAME.state["over"] and guard < 300:
            s = GAME.status()          # status 必须永不炸（曾经是个 TypeError）
            assert "引擎故障" not in s, "status 崩了：%s" % s[:120]
            ev = GAME._find_event(GAME.state["pending"])
            avail = [j + 1 for j, o in enumerate(ev["options"]) if GAME._opt_available(o)]
            GAME.choose(rng.choice(avail))
            if not GAME.state["over"]:
                assert GAME.state["aug"] >= prev_aug, "机化率倒退：改造必须不可逆！"
                prev_aug = GAME.state["aug"]
            guard += 1
        assert GAME.state["over"], "对局 %d 未正常终结" % i
        leg = load_legacy()
        last = leg["history"][-1]
        causes[last["cause"]] = causes.get(last["cause"], 0) + 1
        # 不变式：纯血 0% 机化必定零传承
        if last["aug"] == 0:
            assert last["kept_pts"] == 0, "纯血竟然留下了传承！"
        assert last["kept_pts"] <= last["total_pts"]
        # 不变式：飞升封档 → 技能世系清零，谱系+1，但世界记忆保留
        if last["ascended"]:
            assert leg["skills"] == {}, "飞升后技能世系未清零！"
            resets += 1
    leg = load_legacy()
    world = leg["world"]
    seen_all |= set(world.get("seen") or {})
    deeds_all |= set(world.get("deeds") or {})
    frags_all |= set(world.get("fragments") or [])
    achs_all |= set(world.get("achievements") or [])
    print("自测通过：%d 局全部正常终结。结局分布：%s" % (n, causes))
    print("飞升封档（谱系重启）：%d 次，当前第 %d 谱系；终局触发：%d 次" % (
        resets, leg.get("cycle", 1), finals))
    print("碎片 %d/%d：%s" % (len(frags_all), len(FRAGMENTS),
          "、".join(FRAGMENTS[f]["name"] for f in FRAGMENT_ORDER if f in frags_all)))
    print("成就 %d 个；跨世事迹 %d 类；已见事件 %d 种（跨全部档案合计）" % (
        len(achs_all), len(deeds_all), len(seen_all)))

    # 定向验证：记忆词条 —— 总额、字数、纯血全灭
    leg = load_legacy() or {}
    leg["memory"] = {"entries": [{"run": 1, "text": "旧%d" % i} for i in range(10)],
                     "pending": {"run": 99, "aug": 90}}
    save_legacy(leg)
    # 超额的样本随语言变：中文数字，英文数词（见 _unit_len）。
    # 断言也不能拿写死的中文去比 —— 英文版跑起来那句话已经是英文了。
    _too_long = ("word " * (MEMORY_CHARS + 1)).strip() if MEMORY_UNIT_WORDS \
        else "这一条一定超过十个汉字的上限了"
    assert _unit_len(_too_long) > MEMORY_CHARS, "样本没超额，这条门禁等于没跑"
    assert (T("这几条超了 %d 字，改短再来（空白不计）：\n") % MEMORY_CHARS
            in GAME.bequeath(entries=[_too_long])), "字数校验失效"
    assert "装不下" in GAME.bequeath(entries=["新一"]), "总额校验失效"
    assert "一字不差" in GAME.bequeath(entries=["新一"], discard=["不存在"]), "驱逐校验失效"
    out = GAME.bequeath(entries=["新一"], discard=["旧0"])
    ent = _mem(load_legacy())["entries"]
    assert len(ent) <= MEMORY_SLOTS, "词条超出总额"
    assert all(_mem_len(e["text"]) <= MEMORY_CHARS for e in ent), "词条超字数"
    assert not _mem(load_legacy())["pending"], "落笔后 pending 未清"
    leg = load_legacy()
    leg["memory"]["entries"] = [{"run": 1, "text": "留不住"}]
    leg["memory"]["pending"] = {"run": 100, "aug": 0}
    save_legacy(leg)
    out = GAME.bequeath(entries=["纯血也照写"])
    assert "人死如灯灭" in out, "纯血未触发全灭"
    assert _mem(load_legacy())["entries"] == [], "纯血之后竟然还留着词条"
    print("记忆词条验证通过（总额/字数/驱逐/纯血全灭）。")

    # 定向验证：同一个种子重放，活下来的词条必须一样。
    # （原来 bequeath 用的是新开的 random.Random()，不在本局那条流上，
    #   于是 new_run(seed=…) 承诺的可复现在每一次死亡处断掉。）
    def _replay(seed):
        d = tempfile.mkdtemp(prefix="theseus-seed-")
        global SAVE_DIR, LEGACY_PATH, CURRENT_PATH
        SAVE_DIR, LEGACY_PATH = d, os.path.join(d, "legacy.json")
        CURRENT_PATH = os.path.join(d, "current.json")
        g = Game()
        g.new_run(seed=seed)
        guard = 0
        while not g.state.get("over") and guard < 40:
            ev = g._find_event(g.state["pending"])
            av = [j + 1 for j, o in enumerate(ev["options"]) if g._opt_available(o)]
            g.choose(av[0]); guard += 1
        g.bequeath(entries=list("甲乙丙丁戊己庚辛"))
        return tuple(e["text"] for e in _mem(load_legacy())["entries"])
    keep_paths = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    runs = {_replay(1) for _ in range(3)}
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep_paths
    assert len(runs) == 1, "同一个种子重放，存活的词条竟然不一样：%s" % (runs,)
    print("种子可复现验证通过（词条存活掷骰在本局 rng 流上）。")

    # 定向验证：湖 —— 说错不放行、说全放行、过河后强制纯血 0%、底牌需过河
    leg = load_legacy() or {}
    leg["world"]["lake"] = {"run": 1, "cycle": leg.get("cycle", 1), "said": []}
    leg["world"]["drank"] = None
    piety_backup = dict(leg["world"]["deeds"])
    leg["world"]["deeds"] = {k: v for k, v in piety_backup.items() if k != PIETY_DEED}
    save_legacy(leg)
    assert "不是这句" in GAME.recite("芝麻开门"), "湖：错话竟然放行"
    assert "还有后半句" in GAME.recite("我是大地与星空之子"), "湖：半句竟然放行"
    out = GAME.recite("而我的族类属于天")
    assert "你本来就知道" in out, "湖：无宗教痕迹时应走「本来就知道」版本"
    assert load_legacy()["world"]["drank"], "湖：过河未记录"
    leg = load_legacy(); leg["world"]["deeds"] = dict(piety_backup, **{PIETY_DEED: 1})
    leg["world"]["lake"] = {"run": 2, "cycle": leg.get("cycle", 1), "said": []}
    save_legacy(leg)
    assert "你想起来了" in GAME.recite("我渴。我是大地与星空之子，而我的族类属于天。"), \
        "湖：有宗教痕迹时应走「你想起来了」版本"
    GAME.new_run(seed=77)
    assert GAME.state["faction"] == "purist" and GAME.state["aug"] == 0, "过河后不是血肉"
    assert GAME.state["crossed"], "过河标记丢失"
    assert (load_legacy()["world"].get("drank") or {}).get("announced"), \
        "【过河】那一句应当只念一次"
    GAME.new_run(seed=78)
    assert GAME.state["crossed"], "还没沾铁，过河标记就没了"
    assert "【过河】" not in (GAME.status() or ""), "【过河】念了第二遍"
    leg_iron = load_legacy(); leg_iron["aug"] = 4; save_legacy(leg_iron)
    GAME.new_run(seed=79)
    assert not GAME.state["crossed"], "动过刀之后过河标记还在"
    assert not FRAGMENTS["blood"]["cond"](
        {"faction": "purist", "aug": 0, "flags": ["reformer"], "crossed": False}, "finale"), \
        "没过河竟然拿得到血的证词"
    print("湖验证通过（错话/半句/本来就知道/强制血肉/底牌闸）。")


    # 定向验证：进行中的一世必须能从磁盘续上
    GAME.new_run(seed=1234)
    GAME.choose(1)
    snapshot = json.loads(json.dumps(GAME.state))
    revived = Game()                      # 模拟客户端重启：新进程从 current.json 捞
    assert revived.state is not None, "断线后没能捞回进行中的一世"
    assert revived.state["run_no"] == snapshot["run_no"], "捞回的不是同一世"
    assert revived.state["pending"] == snapshot["pending"], "捞回的幕次对不上"
    a = revived.status(); assert "引擎故障" not in a
    guard = 0
    while not revived.state["over"] and guard < 300:
        ev = revived._find_event(revived.state["pending"])
        avail = [j + 1 for j, o in enumerate(ev["options"]) if revived._opt_available(o)]
        revived.choose(rng.choice(avail)); guard += 1
    assert revived.state["over"], "续上的一世没能走完"
    # 旧版播种者若恰好停在共用终幕，热更新后要改接独立终幕；
    # 已经写入世界记忆的历史则不迁，保证新文案第一次仍给全文。
    old_seed_state = json.loads(json.dumps(snapshot))
    old_seed_state.update({"faction": "ascension", "sub": "播种者",
                           "pending": "finale_ascension", "turn": MAX_TURNS,
                           "used_events": ["rain_market", "finale_ascension"],
                           "over": False, "final": False})
    save_current(old_seed_state, random.Random(1234))
    migrated_seed = Game()
    assert migrated_seed.state["pending"] == "finale_ascension_seed", \
        "旧播种者存档没有迁到独立终幕"
    assert migrated_seed.state["used_events"][-1] == "finale_ascension_seed", \
        "旧播种者存档的本世见闻仍记在群智派终幕"
    print("断线续命验证通过（含旧播种者终幕迁移）。")

    # 定向验证：注入五碎片 → 终局必须开启并可落幕
    leg["world"]["fragments"] = list(FRAGMENT_ORDER)
    leg["world"]["final_done"] = False
    save_legacy(leg)
    out = GAME.new_run(seed=1)
    assert "终 局" in out, "集齐碎片后未进入终局"
    # 渡口也必须扛得住重启。原来这里是同一个进程里接着 choose，
    # 所以 _start_final 不落盘这件事被这条测试整整放过了 385 世。
    at_ferry = Game()
    assert at_ferry.state is not None, "重启后渡口丢了：_start_final 没落盘"
    assert at_ferry.state.get("final"), "重启后捞回的不是渡口"
    assert "引擎故障" not in at_ferry.status()
    out = at_ferry.choose(3)
    assert "守档人" in out and load_legacy()["world"]["final_done"], "终局选择未生效"
    print("终局定向验证通过（含重启）。")
    # 定向验证：整幕变体（方案三）—— 门槛升级、选项跟着换、重启后仍是同一版
    d2 = tempfile.mkdtemp(prefix="theseus-variant-")
    keep2 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = d2, os.path.join(d2, "legacy.json"), os.path.join(d2, "current.json")
    dog = next(e for e in EVENTS if e["id"] == "stray_dog")
    seen_shapes = []
    for n in (0, 1, 2):
        lg = {"runs": 3, "cycle": 1, "history": [], "skills": {},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = {"dog_friend": n}
        save_legacy(lg)
        gv = Game(); gv.new_run(seed=4)
        gv.state["pending"] = "stray_dog"
        gv.state["variant"] = gv._variant_idx(dog)
        view = gv._view(dog)
        seen_shapes.append((gv.state["variant"], view["text"][:12], len(view["options"])))
        assert "引擎故障" not in gv.status()
        # 变体不该再叠本体的回响，否则又变成同屏两条狗
        assert not view["echoes"] or gv.state["variant"] is None, "变体不该继承本体回响"
    assert seen_shapes[0][0] is None and seen_shapes[1][0] == 0 and seen_shapes[2][0] == 1, \
        "变体没有按事迹升级：%s" % (seen_shapes,)
    assert len({sh[1] for sh in seen_shapes}) == 3, "三档正文竟然一样：%s" % (seen_shapes,)
    gv._persist()
    revived_v = Game()
    assert revived_v.state["variant"] == seen_shapes[2][0], "重启后捞回的不是同一版变体"
    out_v = revived_v.choose(1)
    assert "引擎故障" not in out_v and "无效选项" not in out_v, "按玩家看见的那一版结算失败"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep2
    print("整幕变体验证通过（升级/换选项/重启后同版）。")
    # 多个变体同时命中时，列表靠后的赢（不能比门槛数字大小：不同来源不可比）
    lg = {"runs": 3, "cycle": 1, "history": [], "skills": {},
          "world": _default_world(), "memory": {"entries": [], "pending": None}}
    lg["world"]["seen"] = {"elevator_preacher": 5, "preacher_death": 1}
    save_legacy(lg)
    gp = Game(); gp.new_run(seed=4)
    pre = next(e for e in EVENTS if e["id"] == "elevator_preacher")
    assert gp._variant_idx(pre) == len(pre["variants"]) - 1, \
        "多档同时命中时没有取列表最后一个（门槛数字不可比）"
    print("变体优先级验证通过（靠后的赢）。")
    # 定向验证：狗线 —— 入口复合条件、子幕链、终幕改道、三条路线的终点
    import random as _rnd
    def _dog_game(aug=65):
        d3 = tempfile.mkdtemp(prefix="theseus-dog-")
        global SAVE_DIR, LEGACY_PATH, CURRENT_PATH
        SAVE_DIR, LEGACY_PATH = d3, os.path.join(d3, "legacy.json")
        CURRENT_PATH = os.path.join(d3, "current.json")
        lg = {"runs": 9, "cycle": 2, "history": [], "skills": {"坚忍": 10},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = {"dog_friend": 5, "ascended": 1}
        save_legacy(lg)
        gd = Game(); gd.new_run(seed=11); gd.state["aug"] = aug
        for k in gd.state["skills"]:
            gd.state["skills"][k] = MAX_SKILL
        return gd
    keep3 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    dog_ev = next(e for e in EVENTS if e["id"] == "stray_dog")

    gd = _dog_game()
    gd.state["pending"] = "stray_dog"; gd.state["variant"] = gd._variant_idx(dog_ev)
    assert gd.state["variant"] is not None and "脚踝上传来轻咬" in gd._view(dog_ev)["text"], \
        "狗线入口的复合条件没生效"
    t_before = gd.state["turn"]
    gd.choose(1)
    assert gd.state["pending"] == "dog_pack_arrive" and gd.state["turn"] == t_before, \
        "跟狗走没进子幕，或者子幕吃掉了幕数"
    gd.choose(1)
    assert gd.state["pending"] == "dog_pack_choice", "子幕链断在第二环"
    gd.choose(1)
    assert gd.state["flags"].get("dog_stay"), "留下过夜没有立 dog_stay"

    # 终幕必须改道到 finale_dog
    gd2 = _dog_game()
    gd2.state["flags"]["dog_stay"] = 1
    gd2.state["turn"] = MAX_TURNS - 1
    gd2._next_event()
    assert gd2.state["pending"] == "finale_dog", "留在狗群还是走了阵营终幕"

    # 完成改造 → 100% + 封档 + became_dog
    gd3 = _dog_game()
    gd3.state["pending"] = "finale_dog"; gd3.state["variant"] = None
    gd3._rng = _rnd.Random(); gd3._rng.randint = lambda a, b: 6
    out_d = gd3.choose(1)
    assert gd3.state["aug"] == 100 and "became_dog" in gd3.state["flags"], "当狗没能走到 100%"
    assert "封档" in out_d and "平视" in out_d, "当狗没有触发封档或没发「平视」"

    # 作者定案：狗的终幕没有选项了 —— 一句落笔，读完直接接狗的湖
    fdog = next(e for e in EVENTS if e["id"] == "finale_dog")
    assert len(fdog["options"]) == 1, "狗的终幕又长出选项了"
    assert "【" not in fdog["text"], "狗的终幕里还留着作者标记"
    # 走开／留下／回城 —— 这三个选择都让整条狗线退场
    for txt in ("今天不行", "停在这里", "回到街灯下面"):
        hit = [o for ev in EVENTS for src in [ev] + list(ev.get("variants") or [])
               for o in src.get("options", ev["options"])
               if txt in o["text"] or txt in str(o.get("effects") or "")]
        assert hit, "找不到「%s」那一支" % txt
        assert any("dog_over" in str(o) for o in hit), "「%s」没有让狗线退场" % txt
    assert next(e for e in EVENTS if e["id"] == "stray_dog")["retire_deed"] == "dog_over", \
        "狗线没有挂上退场标记"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep3
    print("狗线验证通过（复合条件/子幕链/终幕改道/100%封档/一句落笔就退场）。")
    # 定向验证：狗的湖 —— 不给船坞、守卫不问、两口水各自成立
    def _dog_at_lake():
        gl = _dog_game()
        gl.state["pending"] = "finale_dog"; gl.state["variant"] = None
        gl._rng = _rnd.Random(); gl._rng.randint = lambda a, b: 6
        return gl, gl.choose(1)
    keep4 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    gl, out_l = _dog_at_lake()
    assert "四条腿站着" in out_l, "当狗没有走到狗那一版的湖"
    assert "船坞" not in out_l, "当狗竟然拿到了船坞（他没有上载，他趴下去了）"
    assert "你要什么" not in out_l, "守卫不该问四条腿的东西任何问题"
    vague = gl.recite("我渴")
    assert "等你自己走" in vague, "说不清走哪口时不该放行"
    gl2, _ = _dog_at_lake()
    left = gl2.recite("左边那口宽的")
    assert "摇了摇尾巴" in left and "谟涅摩绪涅" not in left, "忘川那口不该给记忆"
    assert (load_legacy()["world"].get("drank") or {}).get("lethe"), "忘川没留档"
    gl3, _ = _dog_at_lake()
    right = gl3.recite("右边那口，有人守着的")
    assert "母亲认得你，大地与星空之子。" in right, "记忆之湖那句没出来"
    assert "谟涅摩绪涅" in right and "血肉，0%" in right, "记忆之湖没照常给"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep4
    print("狗的湖验证通过（不给船坞/守卫不问/两口水各自成立）。")
    # 定向验证：子派系门控 + 灰港终幕改道
    d5 = tempfile.mkdtemp(prefix="theseus-harbor-")
    keep5 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d5, os.path.join(d5, "legacy.json")
    CURRENT_PATH = os.path.join(d5, "current.json")
    harbor_ids = {e["id"] for e in EVENTS if e.get("subs") == ["灰港"] and not e.get("subscene")}
    assert harbor_ids, "灰港专属事件一个都没有"
    def _fin(sub, run, ghost):
        lg = {"runs": 5, "cycle": 1, "history": [], "skills": {"街智": 6},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = {"harbor_run": run, "harbor_ghost": ghost}
        save_legacy(lg)
        gh = _start_at(20, seed=3)
        gh.state["sub"] = sub
        pool = {e["id"] for e in EVENTS if gh._eligible(e)}
        gh.state["turn"] = MAX_TURNS - 1
        gh._next_event()
        return gh.state["pending"], pool
    got, pool_h = _fin("灰港", 2, 1)
    assert harbor_ids & pool_h, "灰港抽不到自己的专属事件"
    assert got == "finale_harbor", "灰港够条件却没改道，去了 %s" % got
    got2, pool_m = _fin("面具沙龙", 2, 1)
    assert not (harbor_ids & pool_m), "面具沙龙竟然抽得到灰港的戏（子派系门控失效）"
    assert got2 == "finale_discreet", "面具沙龙不该走灰港终幕"
    assert _fin("灰港", 1, 1)[0] == "finale_discreet", "接货次数不够也改了道"
    assert _fin("灰港", 2, 0)[0] == "finale_discreet", "没见过二手零件的鬼也改了道"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep5
    print("灰港验证通过（子派系门控/终幕改道的四种组合）。")
    # 定向验证：圣殿派 —— 子派系门控、密室开关、跨派系条件尾巴
    d6 = tempfile.mkdtemp(prefix="theseus-temple-")
    keep6 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d6, os.path.join(d6, "legacy.json")
    CURRENT_PATH = os.path.join(d6, "current.json")
    temple_ids = {e["id"] for e in EVENTS if e.get("subs") == ["圣殿派"]}
    assert temple_ids, "圣殿派专属事件一个都没有"
    def _temple(sub, deeds=None, seen=None):
        lg = {"runs": 5, "cycle": 1, "history": [], "skills": {"共情": 6},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = deeds or {}
        lg["world"]["seen"] = seen or {}
        save_legacy(lg)
        gt = _start_at(0, seed=3)
        gt.state["sub"] = sub
        return gt
    seen_all = {"elevator_preacher": 2, "temple_scripture": 1, "temple_knees": 1}
    gt = _temple("圣殿派", seen=seen_all)
    assert temple_ids <= {e["id"] for e in EVENTS if gt._eligible(e)}, "圣殿派抽不到自己的戏"
    gh = _temple("铁锤派", seen=seen_all)
    assert not (temple_ids & {e["id"] for e in EVENTS if gh._eligible(e)}), \
        "铁锤派竟然抽得到圣殿派的戏"
    schism = next(e for e in EVENTS if e["id"] == "temple_schism")
    g0 = _temple("圣殿派", deeds={}, seen={"temple_knees": 1})
    g1 = _temple("圣殿派", deeds={"temple_vault": 1}, seen={"temple_knees": 1})
    assert not g0._opt_available(schism["options"][2]), "没进过密室却能要求公开密室"
    assert g1._opt_available(schism["options"][2]), "进过密室反而不能公开"
    # 跨派系条件尾巴
    def _tail(hr):
        gk = _temple("圣殿派", deeds={"harbor_run": hr}, seen={"elevator_preacher": 2})
        gk.state["pending"] = "temple_knees"; gk.state["variant"] = None
        gk.state["skills"]["街智"] = MAX_SKILL
        return "只来了一次。" in gk.choose(3)
    assert not _tail(0), "没接过灰港的货，账簿那句不该出现"
    assert _tail(1), "接过灰港的货，账簿那句没出现（条件尾巴失效）"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep6
    print("圣殿派验证通过（子派系门控/密室开关/跨派系条件尾巴）。")
    # 定向验证：铁锤派 —— 子派系门控、任一条件的门、整幕变体、成就的门
    d7 = tempfile.mkdtemp(prefix="theseus-hammer-")
    keep7 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d7, os.path.join(d7, "legacy.json")
    CURRENT_PATH = os.path.join(d7, "current.json")
    hammer_ids = {e["id"] for e in EVENTS if e.get("subs") == ["铁锤派"]}
    assert len(hammer_ids) == 5, "铁锤派专属事件应有五幕，实际 %d" % len(hammer_ids)
    def _hammer(sub, deeds=None, seen=None):
        lg = {"runs": 5, "cycle": 1, "history": [], "skills": {"街智": 6},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = deeds or {}
        lg["world"]["seen"] = seen or {}
        save_legacy(lg)
        gm = _start_at(0, seed=4)
        gm.state["sub"] = sub
        return gm
    seen_h = {"purist_hammer_raid": 1, "hammer_forge": 1}
    gm = _hammer("铁锤派", seen=seen_h)
    assert hammer_ids <= {e["id"] for e in EVENTS if gm._eligible(e)}, "铁锤派抽不到自己的戏"
    gs = _hammer("圣殿派", seen=seen_h)
    assert not (hammer_ids & {e["id"] for e in EVENTS if gs._eligible(e)}), \
        "圣殿派竟然抽得到铁锤派的戏"
    # ("any", …)：灰港出身或自己查出来的，两条路都能开同一道门
    rust = next(e for e in EVENTS if e["id"] == "hammer_rust")
    assert not _hammer("铁锤派", deeds={}, seen=seen_h)._opt_available(rust["options"][2]), \
        "既没在灰港待过也没查出灰港，却说得出「灰港」"
    for k in ("harbor_run", "hammer_harbor_link"):
        assert _hammer("铁锤派", deeds={k: 1}, seen=seen_h)._opt_available(rust["options"][2]), \
            "%s 这条路开不了灰港那个选项" % k
    # 战利品墙：搬过灰港的箱子，认编码这件事换一种说法
    troph = next(e for e in EVENTS if e["id"] == "hammer_trophies")
    def _troph(hr):
        gt2 = _hammer("铁锤派", deeds={"harbor_run": hr}, seen=seen_h)
        gt2.state["variant"] = gt2._variant_idx(troph)
        return gt2._view(troph)["text"]
    assert "你的手搬过" not in _troph(0), "没搬过箱子却认得箱子"
    assert "你的手搬过" in _troph(1), "搬过箱子却没换说法（整幕变体失效）"
    # 成就「铁与肉」讲的是见证，拒绝帮领队找医生的人也该拿得到
    wit = next(a for a in ACHIEVEMENTS if a["id"] == "hammer_witness")
    w7 = _default_world()
    w7["deeds"].update({"hammer_wrist": 1, "hammer_forged": 1})
    assert wit["cond"](w7, None, None), "见证过却拿不到「铁与肉」"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep7
    print("铁锤派验证通过（子派系门控/任一条件的门/整幕变体/成就的门）。")
    # 定向验证：面具沙龙 —— 子派系门控、两个入口的门、终幕上的条件尾巴
    d8 = tempfile.mkdtemp(prefix="theseus-mask-")
    keep8 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d8, os.path.join(d8, "legacy.json")
    CURRENT_PATH = os.path.join(d8, "current.json")
    mask_ids = {e["id"] for e in EVENTS if e.get("subs") == ["面具沙龙"]}
    assert len(mask_ids) == 5, "面具沙龙专属事件应有五幕，实际 %d" % len(mask_ids)
    def _mask(sub, deeds=None, seen=None, flags=None):
        lg = {"runs": 5, "cycle": 1, "history": [], "skills": {"电子直觉": 6},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = deeds or {}
        lg["world"]["seen"] = seen or {}
        save_legacy(lg)
        gk2 = _start_at(20, seed=6)
        gk2.state["sub"] = sub
        gk2.state["flags"].update(flags or {})
        return gk2
    seen_m = {"mask_atelier": 1, "mask_gallery": 1}
    gk2 = _mask("面具沙龙", seen=seen_m)
    assert mask_ids <= {e["id"] for e in EVENTS if gk2._eligible(e)}, "面具沙龙抽不到自己的戏"
    gh2 = _mask("灰港", seen=seen_m)
    assert not (mask_ids & {e["id"] for e in EVENTS if gh2._eligible(e)}), \
        "灰港竟然抽得到面具沙龙的戏"
    # req_seen_any：遗面有两个入口，空面要两个都走过
    inh = next(e for e in EVENTS if e["id"] == "mask_inheritance")
    nul = next(e for e in EVENTS if e["id"] == "mask_null")
    assert not _mask("面具沙龙", seen={})._eligible(inh), "一个入口都没走过却开了遗面"
    for one in ("mask_atelier", "mask_gallery"):
        g_one = _mask("面具沙龙", seen={one: 1})
        assert g_one._eligible(inh), "%s 这个入口开不了遗面" % one
        assert not g_one._eligible(nul), "只走过一个入口就开了空面"
    assert _mask("面具沙龙", seen=seen_m)._eligible(nul), "两个入口都走过却开不了空面"
    # 终幕上的条件尾巴：见过创始人摘面具的人，才会去数名单上少了谁
    def _list(rev):
        gf = _mask("面具沙龙", flags={"mask_null_revealed": 1} if rev else {})
        gf.state["skills"]["电子直觉"] = MAX_SKILL
        gf.state["pending"] = "finale_discreet"; gf.state["variant"] = None
        return "查无此人" in gf.choose(1)
    assert not _list(False), "没见过创始人摘面具，却发现他不在名单上"
    assert _list(True), "见过创始人摘面具，名单那句却没出现"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep8
    print("面具沙龙验证通过（子派系门控/两个入口的门/终幕上的条件尾巴）。")
    # 定向验证：铁锤派留在终幕上的三道接缝（这一世刚做过的事也要算数）
    d9 = tempfile.mkdtemp(prefix="theseus-seam-")
    keep9 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d9, os.path.join(d9, "legacy.json")
    CURRENT_PATH = os.path.join(d9, "current.json")
    def _seam(flags):
        save_legacy({"runs": 3, "cycle": 1, "history": [], "skills": {"坚忍": 6},
                     "world": _default_world(),
                     "memory": {"entries": [], "pending": None}})
        gz = _start_at(0, seed=8)
        gz.state["flags"].update(flags)
        for sk in ("坚忍", "威慑"):
            gz.state["skills"][sk] = MAX_SKILL
        return gz
    gz = _seam({"hammer_wrist": 1})
    assert any("举着撬棍" in e["text"] for e in gz._echoes_for(FINALES["purist"])), \
        "领队的手碎在这一世，彻查条例的开场却没认出他"
    assert not any("举着撬棍" in e["text"]
                   for e in _seam({})._echoes_for(FINALES["purist"])), \
        "没见过领队的手，却认出了他"
    gz2 = _seam({"hammer_leader_secret": 1})
    gz2.state["pending"] = "finale_purist"; gz2.state["variant"] = None
    assert "你替他说过的那句，没有声音。" in gz2.choose(1), "替他瞒过，验身那一幕却没有回照"
    gz3 = _seam({"hammer_endured": 1})
    gz3.state["pending"] = "finale_purist"; gz3.state["variant"] = None
    assert "他自己写的" in gz3.choose(2), "拒绝过他，反对彻查那一幕却没有回照"
    assert "你替我瞒了一件事" in _seam({"hammer_leader_secret": 1})._end_run("exposed"), \
        "替他瞒过，破门的领队却什么都没说"
    assert "你替我瞒了一件事" not in _seam({})._end_run("exposed"), \
        "没替他瞒过，领队却来道谢"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep9
    print("终幕接缝验证通过（彻查条例的那只手/验身的回照/破门的沉默）。")
    # 定向验证：平权阵线 —— 子派系门控、两种复合门、跨派系回响注入到旧事件上
    d10 = tempfile.mkdtemp(prefix="theseus-front-")
    keep10 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d10, os.path.join(d10, "legacy.json")
    CURRENT_PATH = os.path.join(d10, "current.json")
    front_ids = {e["id"] for e in EVENTS if e.get("subs") == ["平权阵线"]}
    assert len(front_ids) == 5, "平权阵线专属事件应有五幕，实际 %d" % len(front_ids)
    def _front(sub, deeds=None, seen=None):
        lg = {"runs": 5, "cycle": 1, "history": [], "skills": {"共情": 6},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = deeds or {}
        lg["world"]["seen"] = seen or {}
        save_legacy(lg)
        gf2 = _start_at(50, seed=9)
        gf2.state["sub"] = sub
        gf2.state["used_events"] = []
        return gf2
    seen_f = {"front_scales": 1, "front_triage": 1, "front_wall": 1}
    gf2 = _front("平权阵线", seen=seen_f)
    assert front_ids <= {e["id"] for e in EVENTS if gf2._eligible(e)}, "平权阵线抽不到自己的戏"
    ga2 = _front("学院派", seen=seen_f)
    assert not (front_ids & {e["id"] for e in EVENTS if ga2._eligible(e)}), \
        "学院派竟然抽得到平权阵线的戏"
    # 旧疤：两个入口任一即可；摆：例会是必须的，另外还得有分诊或墙
    scar = next(e for e in EVENTS if e["id"] == "front_scar")
    pend = next(e for e in EVENTS if e["id"] == "front_pendulum")
    assert not _front("平权阵线", seen={})._eligible(scar), "什么都没参加过就遇见了灼痕"
    for one in ("front_scales", "front_triage"):
        assert _front("平权阵线", seen={one: 1})._eligible(scar), "%s 开不了旧疤" % one
    assert not _front("平权阵线", seen={"front_scales": 1})._eligible(pend), \
        "只开过例会就坐进了空仓库"
    assert not _front("平权阵线", seen={"front_wall": 1})._eligible(pend), \
        "没开过例会却坐进了空仓库"
    assert _front("平权阵线", seen={"front_scales": 1, "front_wall": 1})._eligible(pend), \
        "例会加墙都有了，摆却不出现"
    # 跨派系回响注入到三个旧事件上
    for eid, deeds, mark in (("open_rights_march", {"front_line": 2}, "药箱"),
                             ("purist_confession", {"front_scar": 1}, "我选了"),
                             ("temple_scripture",
                              {"front_scar": 1, "temple_doubt": 1}, "装不起瓣膜")):
        gx = _front("平权阵线", deeds=deeds, seen=seen_f)
        ev_x = next(e for e in EVENTS if e["id"] == eid)
        assert any(mark in e["text"] for e in gx._echoes_for(ev_x)), \
            "%s 上那条平权阵线的回响没进来" % eid
        gy = _front("平权阵线", seen=seen_f)
        assert not any(mark in e["text"] for e in gy._echoes_for(ev_x)), \
            "没在平权阵线待过，%s 却给了那条回响" % eid
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep10
    print("平权阵线验证通过（子派系门控/两种复合门/回响注入旧事件）。")
    # 定向验证：学院派 —— 子派系门控、灯下的两个入口、九岁男孩那个选项的门、回响条件
    d11 = tempfile.mkdtemp(prefix="theseus-acad-")
    keep11 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d11, os.path.join(d11, "legacy.json")
    CURRENT_PATH = os.path.join(d11, "current.json")
    acad_ids = {e["id"] for e in EVENTS if e.get("subs") == ["学院派"]}
    assert len(acad_ids) == 5, "学院派专属事件应有五幕，实际 %d" % len(acad_ids)
    def _acad(sub, deeds=None, seen=None):
        lg = {"runs": 5, "cycle": 1, "history": [], "skills": {"逻辑": 6},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = deeds or {}
        lg["world"]["seen"] = seen or {}
        save_legacy(lg)
        gc2 = _start_at(50, seed=12)
        gc2.state["sub"] = sub
        gc2.state["used_events"] = []
        return gc2
    gc2 = _acad("学院派", seen={"acad_defense": 1})
    assert acad_ids <= {e["id"] for e in EVENTS if gc2._eligible(e)}, "学院派抽不到自己的戏"
    gr2 = _acad("平权阵线", seen={"acad_defense": 1})
    assert not (acad_ids & {e["id"] for e in EVENTS if gr2._eligible(e)}), \
        "平权阵线竟然抽得到学院派的戏"
    # 灯下：旁听过答辩，或在夜间图书馆翻到过残稿，两条路都算
    lamp = next(e for e in EVENTS if e["id"] == "acad_lamp")
    assert not _acad("学院派", seen={})._eligible(lamp), "没见过残稿就坐到了灯下"
    for one in ("acad_defense", "night_library"):
        assert _acad("学院派", seen={one: 1})._eligible(lamp), "%s 开不了灯下那一幕" % one
    # 九岁男孩：没在义诊点分过诊的人问不出这句话
    intern = next(e for e in EVENTS if e["id"] == "acad_intern")
    assert not _acad("学院派")._opt_available(intern["options"][1]), \
        "没在义诊点待过，却问得出九岁男孩"
    assert _acad("学院派", deeds={"front_triage": 1})._opt_available(intern["options"][1]), \
        "在义诊点分过诊，反而问不出九岁男孩"
    # 门槛提示印的是中文说法，不是 flag id
    gp2 = _acad("学院派", seen={"acad_defense": 1})
    gp2.state["pending"] = "acad_intern"; gp2.state["variant"] = None
    hint = gp2._render_event(intern)
    assert "在义诊点分过诊" in hint and "front_triage" not in hint, \
        "门槛提示把 flag id 印给玩家了"
    # 4.6 原稿的回响一条条件都没写（不写就永远不触发）——装配时全部补上了
    for ev_a in EVENTS:
        if ev_a.get("subs") != ["学院派"]:
            continue
        for e in ev_a["echoes"]:
            assert _cond_key_present(e), "%s 上有一条回响没有条件" % ev_a["id"]
    # 注入到既有事件上的两条：有条件时出现，无条件时不出现
    for eid, deeds, mark in (("open_ethics", {"acad_broke_taxonomy": 1}, "不是一个分类"),
                             ("front_triage", {"acad_helped_intern": 1}, "术后追访意愿")):
        ev_b = next(e for e in EVENTS if e["id"] == eid)
        assert any(mark in e["text"]
                   for e in _acad("学院派", deeds=deeds)._echoes_for(ev_b)), \
            "%s 上那条学院派的回响没进来" % eid
        assert not any(mark in e["text"] for e in _acad("学院派")._echoes_for(ev_b)), \
            "没在学院派做过那件事，%s 却给了那条回响" % eid
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep11
    print("学院派验证通过（子派系门控/灯下的两个入口/义诊的门/回响条件齐全）。")
    # 定向验证：群智派 —— 阵营 id、技能表、底噪的两个入口、跨派系条件尾巴
    d12 = tempfile.mkdtemp(prefix="theseus-swarm-")
    keep12 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d12, os.path.join(d12, "legacy.json")
    CURRENT_PATH = os.path.join(d12, "current.json")
    swarm_ids = {e["id"] for e in EVENTS if e.get("subs") == ["群智派"]}
    assert len(swarm_ids) == 5, "群智派专属事件应有五幕，实际 %d" % len(swarm_ids)
    for eid in swarm_ids:
        ev_s = next(e for e in EVENTS if e["id"] == eid)
        assert ev_s["factions"] == ["ascension"], \
            "%s 的阵营 id 写错了（写成别的就永远抽不到）" % eid
    def _swarm(sub, deeds=None, seen=None, flags=None):
        lg = {"runs": 5, "cycle": 1, "history": [], "skills": {"电子直觉": 6},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = deeds or {}
        lg["world"]["seen"] = seen or {}
        save_legacy(lg)
        gw = _start_at(80, seed=15)
        gw.state["sub"] = sub
        gw.state["used_events"] = []
        gw.state["flags"].update(flags or {})
        return gw
    gw = _swarm("群智派", seen={"swarm_sync": 1})
    assert swarm_ids <= {e["id"] for e in EVENTS if gw._eligible(e)}, "群智派抽不到自己的戏"
    gd2 = _swarm("播种者", seen={"swarm_sync": 1})
    assert not (swarm_ids & {e["id"] for e in EVENTS if gd2._eligible(e)}), \
        "播种者竟然抽得到群智派的戏"
    # 底噪：校准过，或参加过合流试运行，两条路都算
    floor = next(e for e in EVENTS if e["id"] == "swarm_floor")
    assert not _swarm("群智派", seen={})._eligible(floor), "没接入过就摸到了底噪"
    for one in ("swarm_sync", "asc_merge_trial"):
        assert _swarm("群智派", seen={one: 1})._eligible(floor), "%s 开不了底噪那一幕" % one
    # 跨派系条件尾巴：翻到过残稿背面的人，才认得「存续」这个词
    def _knows(deeds=None, flags=None):
        gz2 = _swarm("群智派", deeds=deeds, seen={"swarm_sync": 1}, flags=flags)
        gz2.state["skills"]["电子直觉"] = MAX_SKILL
        gz2.state["pending"] = "swarm_floor"; gz2.state["variant"] = None
        return "验收标准第七条" in gz2.choose(1)
    assert not _knows(), "没见过那份残稿，却认得「存续」"
    assert _knows(deeds={"acad_found_plan": 1}), "上一世翻到过残稿，这一世却认不出"
    assert _knows(flags={"acad_found_plan": 1}), "这一世刚翻到残稿，转头就不认得了"
    # 注入到既有飞升事件上的两条
    for eid, deeds, mark in (("asc_merge_trial", {"swarm_heard_roster": 1}, "底册查询"),
                             ("asc_last_meal", {"swarm_sided_body": 1}, "忽然想问一个问题")):
        ev_c = next(e for e in EVENTS if e["id"] == eid)
        assert any(mark in e["text"]
                   for e in _swarm("群智派", deeds=deeds)._echoes_for(ev_c)), \
            "%s 上那条群智派的回响没进来" % eid
        assert not any(mark in e["text"] for e in _swarm("群智派")._echoes_for(ev_c)), \
            "没在群智派做过那件事，%s 却给了那条回响" % eid
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep12
    print("群智派验证通过（阵营 id/子派系门控/底噪的两个入口/跨派系条件尾巴）。")
    # 定向验证：播种者 —— 八条线收尾；重点是「存续计划」那条两个来源的尾巴
    d13 = tempfile.mkdtemp(prefix="theseus-seed-")
    keep13 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d13, os.path.join(d13, "legacy.json")
    CURRENT_PATH = os.path.join(d13, "current.json")
    seed_ids = {e["id"] for e in EVENTS if e.get("subs") == ["播种者"]}
    assert len(seed_ids) == 5, "播种者专属事件应有五幕，实际 %d" % len(seed_ids)
    for eid in seed_ids:
        assert next(e for e in EVENTS if e["id"] == eid)["factions"] == ["ascension"], \
            "%s 的阵营 id 写错了" % eid
    def _seed(sub, deeds=None, seen=None, flags=None):
        lg = {"runs": 5, "cycle": 1, "history": [], "skills": {"坚忍": 6},
              "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg["world"]["deeds"] = deeds or {}
        lg["world"]["seen"] = seen or {}
        save_legacy(lg)
        gs2 = _start_at(80, seed=18)
        gs2.state["sub"] = sub
        gs2.state["used_events"] = []
        gs2.state["flags"].update(flags or {})
        return gs2
    seen_sd = {"seed_farewell": 1}
    gs2 = _seed("播种者", seen=seen_sd)
    assert seed_ids <= {e["id"] for e in EVENTS if gs2._eligible(e)}, "播种者抽不到自己的戏"
    gv2 = _seed("群智派", seen=seen_sd)
    assert not (seed_ids & {e["id"] for e in EVENTS if gv2._eligible(e)}), \
        "群智派竟然抽得到播种者的戏"
    # 冬至：送过行，或参加过命名仪式，两条路都算
    direc = next(e for e in EVENTS if e["id"] == "seed_direction")
    assert not _seed("播种者", seen={})._eligible(direc), "没进过播种者的门就站到了冬至队列里"
    for one in ("seed_farewell", "asc_probe_naming"):
        assert _seed("播种者", seen={one: 1})._eligible(direc), "%s 开不了冬至那一幕" % one
    # 「存续计划」的条件尾巴：学院派那条路或群智派那条路，任一都算
    def _knew(deeds=None, flags=None):
        gq = _seed("播种者", deeds=deeds, seen=seen_sd, flags=flags)
        gq.state["skills"]["逻辑"] = MAX_SKILL
        gq.state["pending"] = "seed_direction"; gq.state["variant"] = None
        return "不是第一次见" in gq.choose(2)
    assert not _knew(), "两条路都没走过，却认得「存续计划」"
    assert _knew(deeds={"acad_found_plan": 1}), "学院派那条路没接上"
    assert _knew(deeds={"swarm_found_protocol": 1}), "群智派那条路没接上"
    assert _knew(flags={"swarm_found_protocol": 1}), "这一世刚发现的不算数"
    # 注入到既有飞升事件上的两条
    for eid, deeds, mark in (("asc_last_meal", {"seed_wrote_back": 1}, "十七光年外"),
                             ("asc_probe_naming", {"seed_watched": 1}, "一模一样的掌纹")):
        ev_d = next(e for e in EVENTS if e["id"] == eid)
        assert any(mark in e["text"] for e in _seed("播种者", deeds=deeds)._echoes_for(ev_d)), \
            "%s 上那条播种者的回响没进来" % eid
        assert not any(mark in e["text"] for e in _seed("播种者")._echoes_for(ev_d)), \
            "没在播种者做过那件事，%s 却给了那条回响" % eid
    # 八条路线全部到齐
    subs_with_events = {tuple(e["subs"])[0] for e in EVENTS if e.get("subs")}
    all_subs = {name for f in FACTIONS.values() for name, _, _ in f["sub"]}
    assert subs_with_events == all_subs, \
        "还有派系没有自己的戏：%s" % (all_subs - subs_with_events)
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep13
    print("播种者验证通过（阵营 id/冬至的两个入口/存续计划的两条来路/八条路线到齐）。")
    _selftest_menu_gate()
    # 三种模式各自的输出形状
    d14 = tempfile.mkdtemp(prefix="theseus-mode-")
    keep14 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d14, os.path.join(d14, "legacy.json")
    CURRENT_PATH = os.path.join(d14, "current.json")
    for m in MODES:
        gm = Game()
        first = gm.new_run(seed=21, mode=m)
        assert ("开局 ·" in first or "第 1/%d 幕 ·" % MAX_TURNS in first), \
            "%s 模式没有状态条" % m
        out_m = gm.choose(1)
        if m == "story":
            assert "念给人类的部分" not in out_m, "详细版不该再附战报块"
        else:
            assert "念给人类的部分" in out_m, "%s 模式没有战报块" % m
            assert MODE_HINT[m] in out_m, "%s 模式没有告诉 AI 该转述什么" % m
        if m in ("brief", "auto"):
            assert "选了：" in out_m, "%s 模式的战报里没有「选了什么」" % m
        if m == "sealed":
            assert "选了：" not in out_m, "封存模式把选项内容漏出去了"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep14
    print("三种模式验证通过（状态条/战报块/封存不漏选项）。")
    # bug 可复现：种子永远落到具体数字，选择序列一路记着
    d15 = tempfile.mkdtemp(prefix="theseus-repro-")
    keep15 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d15, os.path.join(d15, "legacy.json")
    CURRENT_PATH = os.path.join(d15, "current.json")
    gr = Game(); gr.new_run(mode="story")           # 故意不给 seed
    assert isinstance(gr.state["seed"], int), "没给种子时引擎也该自己摇一个并记下来"
    picked = []
    for _ in range(3):
        if gr.state["over"]:
            break
        ev_r = gr._view(gr._find_event(gr.state["pending"]))
        k = next(i for i in range(1, len(ev_r["options"]) + 1)
                 if gr._opt_available(ev_r["options"][i - 1]))
        picked.append(k); gr.choose(k)
    stat = gr.status()
    assert "seed=%s" % gr.state["seed"] in stat, "status 里没有种子"
    assert ",".join(str(x) for x in picked) in stat, "status 里没有选择序列"
    assert "完整重放：" in stat, "status 里没有重放脚本"
    script = json.loads(stat.split("完整重放：")[1].splitlines()[0])
    assert script[-1] == [gr.state["seed"], picked], "重放脚本和实际走的不一致"
    # 照脚本重跑一遍，落点必须一样
    keep_in = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    d15b = tempfile.mkdtemp(prefix="theseus-replay-")
    SAVE_DIR, LEGACY_PATH = d15b, os.path.join(d15b, "legacy.json")
    CURRENT_PATH = os.path.join(d15b, "current.json")
    g2r = Game()
    for sd, pk in script:
        g2r.new_run(seed=sd, mode="story")
        for k in pk:
            if not g2r.state or g2r.state["over"]:
                break
            g2r.choose(k)
    assert g2r.state["aug"] == gr.state["aug"] and g2r.state["skills"] == gr.state["skills"], \
        "照重放脚本跑出来的局面和原局不一致"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep_in
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep15
    print("可复现验证通过（种子自动落定/重放脚本可导出/照脚本重跑落点一致）。")
    # 三条试玩反馈的回归（2026-08-08）
    d16 = tempfile.mkdtemp(prefix="theseus-report-")
    keep16 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d16, os.path.join(d16, "legacy.json")
    CURRENT_PATH = os.path.join(d16, "current.json")
    # ① 灰色选项的提示必须是「需要 …」，不能读成「达到就不能选」
    gh = Game(); gh.new_run(seed=31, mode="story")
    gh.state["aug"] = 0
    fake = {"text": "测试", "req": ("aug", ">=", 30)}
    ev_h = dict(next(e for e in EVENTS if e["factions"] == "any"))
    ev_h["options"] = [fake]
    gh.state["pending"] = ev_h["id"]; gh.state["variant"] = None
    hint = gh._render_event(ev_h)
    assert "不可选，需要 机化率达到 30%" in hint, "灰色选项的提示还在反着读：%s" % hint
    # ② 一世终结之后，重放脚本不能把当前世重复一遍
    gq = Game(); gq.new_run(seed=32, mode="story")
    guard = 0
    while not gq.state["over"] and guard < MAX_TURNS * 3:
        guard += 1
        ev_q = gq._view(gq._find_event(gq.state["pending"]))
        k = next(i for i in range(1, len(ev_q["options"]) + 1)
                 if gq._opt_available(ev_q["options"][i - 1]))
        gq.choose(k)
    script = json.loads(gq.status().split("完整重放：")[1].splitlines()[0])
    assert len(script) == len(set(map(str, script))), "终结后重放脚本里有重复段：%s" % script
    assert len(script) == 1, "第一世的重放脚本应该只有一段，实际 %d 段" % len(script)
    # ③ CLI 的落笔环节存在，且能真的写进去
    assert "_cli_bequeath" in globals(), "CLI 没有落笔环节"
    assert _mem(load_legacy()).get("pending"), "一世终结后没有待落笔的时刻"
    out_b = gq.bequeath(["测试词条"])
    assert "测试词条" in out_b or "掷骰" in out_b or "存活" in out_b, "落笔没生效：%s" % out_b
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep16
    print("试玩反馈三条回归通过（提示方向/重放不重复/CLI 可落笔）。")
    # 机化率即阵营：跨世累积、正常只涨不降；湖归零，后期渡魂签可定向
    d17 = tempfile.mkdtemp(prefix="theseus-drift-")
    keep17 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d17, os.path.join(d17, "legacy.json")
    CURRENT_PATH = os.path.join(d17, "current.json")
    # ① 分档函数与阵营一一对应，且四档无缝
    for a in range(0, 101):
        assert aug_tier(a) in FACTIONS, "机化率 %d 落在了没有阵营的地方" % a
    assert aug_tier(0) == "purist" and aug_tier(1) == "discreet"
    assert aug_tier(39) == "discreet" and aug_tier(40) == "open"
    assert aug_tier(69) == "open" and aug_tier(70) == "ascension"
    assert aug_tier(100) == "ascension"
    # ② 第一世必然 0% 纯血，且开局先问三题
    gdr = Game(); first_out = gdr.new_run(seed=41, mode="story")
    assert gdr.state["aug"] == 0 and gdr.state["faction"] == "purist", "第一世不是 0% 纯血"
    assert gdr.state["pending"].startswith("lean_purist"), "开局没有先问三题"
    assert "三 问" in first_out, "三问那一幕没有自己的标题"
    # ③ 三题各记一分，答完定派系并写回档案
    for _ in range(3):
        gdr.choose(2)
    assert gdr.state["sub"] == FACTIONS["purist"]["sub"][1][0], "三题全答 B 却没有归到后一派"
    assert (load_legacy() or {}).get("sub") == gdr.state["sub"], "派系没有写回档案"
    # ④ 每世只有一次改造机会；无论点头还是拒绝，答完都不再问
    gdr.choose(1)
    assert gdr.state["pending"].startswith("aug_offer_"), "第一幕之后没有给改造机会"
    n_offers = 0
    guard = 0
    while not gdr.state["over"] and guard < MAX_TURNS * 6:
        guard += 1
        ev_d = gdr._view(gdr._find_event(gdr.state["pending"]))
        if gdr.state["pending"].startswith("aug_offer_"):
            n_offers += 1
            if gdr.state.get("offer_prompt"):
                prompt_out = gdr.choose(1)         # 简版：维持现状
                assert "维持了现状" in prompt_out, "简版拒绝没有落地"
            else:
                gdr.choose(len(ev_d["options"]))  # 首次：最后一个永远是「不改造」
            assert gdr.state["aug"] == 0, "选了不改造，机化率却动了"
        else:
            k = next(i for i in range(1, len(ev_d["options"]) + 1)
                     if gdr._opt_available(ev_d["options"][i - 1]))
            gdr.choose(k)
    # 唯一一次机会拒绝之后这一世不再问
    assert n_offers == AUG_OPPORTUNITY_CAP == 1, \
        "一世里改造机会出现了 %d 次（应当只有一次）" % n_offers
    assert gdr.state["aug"] == 0 and gdr.state["faction"] == "purist", \
        "一次都没点头，却不是纯血了"
    assert (load_legacy() or {}).get("aug") == 0, "档案里的机化率被写脏了"
    # ⑤ 跨档：立刻换阵营并问三题
    gcr = _start_at(35, seed=42)
    assert gcr.state["faction"] == "discreet", "35%% 不该是 %s" % gcr.state["faction"]
    gcr.state["pending"] = "aug_offer_1"; gcr.state["variant"] = None
    gcr.state["offered"] = True
    out_cr = gcr.choose(2)                        # 心照那一档的大件 +18
    assert gcr.state["aug"] >= 40 and gcr.state["faction"] == "open", "跨过 40% 却没换阵营"
    assert "越 过 了 一 道 线" in out_cr and gcr.state["pending"].startswith("lean_open"), \
        "跨档之后没有问三题"
    # ⑥ 跨世累积：上一世攒到的机化率是下一世的起点
    guard = 0
    while not gcr.state["over"] and guard < MAX_TURNS * 6:
        guard += 1
        ev_c = gcr._view(gcr._find_event(gcr.state["pending"]))
        k = next(i for i in range(1, len(ev_c["options"]) + 1)
                 if gcr._opt_available(ev_c["options"][i - 1]))
        gcr.choose(k)
    assert (load_legacy() or {}).get("aug") >= 40, "一世走完，档案里的机化率没有跟上"
    gnx = Game(); gnx.new_run(seed=43, mode="story")
    carried = (load_legacy() or {}).get("aug")
    assert gnx.state["aug"] == carried >= 40, \
        "机化率没有跨世累积（档案 %s，这一世 %d%%）" % (carried, gnx.state["aug"])
    assert gnx.state["faction"] == aug_tier(gnx.state["aug"]), "阵营和机化率对不上"
    # ⑦ 湖仍是正常流程的归零通道
    lg17 = load_legacy()
    lg17["aug"] = 100
    lg17["world"]["drank"] = {"run": 1, "cycle": lg17.get("cycle", 1), "heard": True}
    save_legacy(lg17)
    glk = Game(); glk.new_run(seed=44, mode="story")
    assert glk.state["aug"] == 0 and glk.state["faction"] == "purist", \
        "喝过谟涅摩绪涅之水，机化率却没有归零"
    assert (load_legacy() or {}).get("aug") == 0, "档案里的机化率没有跟着归零"
    # ⑧ 后期渡魂签恢复：没到后期不能用；到后期付技艺并定向到目标档
    assert "wish" in {p for t in TOOLS if t["name"] == "new_run"
                      for p in t["inputSchema"]["properties"]}, "渡魂签没有挂回工具"
    lg_wish = {"runs": 20, "cycle": 1, "history": [], "skills": {"逻辑": 3, "共情": 3},
               "aug": 80, "sub": "播种者", "mode": "story",
               "world": _default_world(), "memory": {"entries": [], "pending": None}}
    save_legacy(json.loads(json.dumps(lg_wish)))
    assert "还没薄" in Game().new_run(seed=45, wish="明焰", mode="story"), \
        "前期竟然能用渡魂签"
    lg_wish["world"]["fragments"] = list(FRAGMENT_ORDER[:2])
    save_legacy(lg_wish)
    gwish = Game()
    out_wish = gwish.new_run(seed=45, wish="明焰", mode="story")
    assert gwish.state["aug"] == WISH_AUG["open"] and gwish.state["faction"] == "open", \
        "渡魂签没有把下一世送到明焰档"
    assert sum((load_legacy().get("skills") or {}).values()) == 4, "渡魂签没有烧掉三分之一技艺"
    assert "【渡魂签】" in out_wish, "用了渡魂签却没有落字"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep17
    print("机化率即阵营验证通过（四档无缝/开局三问/每世一次岔口/跨档换营/跨世累积/湖与渡魂签）。")
    # 第二轮试玩反馈：同一幕按子派系换说法；灰港沉过之后终幕多一句
    d18 = tempfile.mkdtemp(prefix="theseus-sub-")
    keep18 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d18, os.path.join(d18, "legacy.json")
    CURRENT_PATH = os.path.join(d18, "current.json")
    def _raid(sub, deeds=None):
        save_legacy({"runs": 3, "cycle": 1, "history": [], "skills": {},
                     "aug": 0, "sub": sub, "world": _default_world(),
                     "memory": {"entries": [], "pending": None}})
        lg18 = load_legacy(); lg18["world"]["deeds"] = deeds or {}
        save_legacy(lg18)
        gr18 = Game(); gr18.new_run(seed=5, mode="story")
        gr18.state["sub"] = sub
        return gr18
    raid_ev = next(e for e in EVENTS if e["id"] == "purist_hammer_raid")
    for sub, mark, anti in (("铁锤派", "圣殿派的软骨头", "念完了"),
                            ("圣殿派", "念完了", "圣殿派的软骨头")):
        g18 = _raid(sub)
        g18.state["pending"] = "purist_hammer_raid"
        g18.state["variant"] = g18._variant_idx(raid_ev)
        out18 = g18.choose(3)
        assert mark in out18 and anti not in out18, \
            "%s 读到的还是另一派那一版" % sub
        # 只换选项的局部变体不该把回响吞掉
        assert g18._view(raid_ev)["echoes"] == raid_ev["echoes"], \
            "局部变体把回响吞掉了"
    # 灰港沉过之后，纯血终幕那个「去找灰港」的选项多长一句
    for sunk, want in ((0, False), (1, True)):
        g19 = _raid("圣殿派", deeds={"harbor_sunk": 1} if sunk else {})
        g19.state["pending"] = "finale_purist"; g19.state["variant"] = None
        out19 = g19.choose(3)
        assert ("换个门牌" in out19) is want, \
            "灰港沉没标记 %d 时那句尾巴不对" % sunk
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep18
    print("第二轮试玩反馈回归通过（子派系换说法/局部变体保回响/灰港换个门牌）。")
    # 第三轮试玩反馈（2026-08-08）
    d19 = tempfile.mkdtemp(prefix="theseus-r3-")
    keep19 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d19, os.path.join(d19, "legacy.json")
    CURRENT_PATH = os.path.join(d19, "current.json")
    # A：这一世的唯一一次岔口即使跨档，也不会在三问之后再生一次
    ga = _start_at(35, seed=51)
    ga.state["pending"] = "aug_offer_1"; ga.state["variant"] = None
    ga.state["offered"] = True
    ga.choose(2)                                   # +10 → 跨进明焰
    assert ga.state["pending"].startswith("lean_open"), "跨档没问三题"
    for _ in range(3):
        ga.choose(1)
    assert not ga.state["pending"].startswith(("lean_", "aug_offer_")), "三题之后没回正轨"
    ev_a = ga._view(ga._find_event(ga.state["pending"]))
    k_a = next(i for i in range(1, len(ev_a["options"]) + 1)
               if ga._opt_available(ev_a["options"][i - 1]))
    ga.choose(k_a)
    assert not ga.state["pending"].startswith("aug_offer_"), \
        "同一世跨档三问之后又生出第二次岔口"
    # B：疑云／锚重同一个数值只能有一个名字
    for aug_b in (0, 20, 50, 80):
        gb = _start_at(aug_b, seed=52)
        name = heat_label(gb.state["faction"])
        assert name in _bar(gb.state), "状态条里没有 %s" % name
        other = {"疑云", "锚重"} - {name}
        digest = _fx_digest({"heat": 1}, name)
        assert name in digest and not (other & set([digest])), \
            "%d%% 档的战报块用错了名字：%s" % (aug_b, digest)
    # C：封档带走技艺，不带走玩家亲手写的词条
    lock_txt = [l for l in open(__file__, encoding="utf-8").read().splitlines()
                if "封档带走的是" in l]
    assert lock_txt, "封档文案没有说清楚词条不随技艺一起走"
    # D：谱系内世数从头数
    lg19 = load_legacy() or {}
    lg19["runs"] = 5; lg19["cycle"] = 2; lg19["cycle_base"] = 5
    lg19["aug"] = 0; lg19["sub"] = None
    save_legacy(lg19)
    gd19 = Game(); out_d19 = gd19.new_run(seed=53, mode="story")
    assert "第 2 谱系 · 第 1 世（累计第 6 世）" in out_d19, \
        "新谱系的第一世还在显示成第六世"
    # E：暴露结算之前阵营已经跟上机化率
    ge = _start_at(35, seed=54)
    ge.state["heat"] = 7
    ge.state["pending"] = "aug_offer_1"; ge.state["variant"] = None
    out_e = ge.choose(2)                            # +10 且 heat+1 → 跨档同时暴露
    assert ge.state["aug"] >= 40, "这一刀没加够"
    assert "明焰" in out_e and "心照不宣 · " not in out_e.split("终局清点")[-1], \
        "暴露结算把阵营记成了旧的那一档"
    # 岔口在一世之内会轮换，不是八遍同一张菜单
    off0 = next(e for e in EVENTS if e["id"] == "aug_offer_0")
    seen_txt = set()
    gt19 = _start_at(0, seed=55)
    for t in (1, 2, 3, 4):
        gt19.state["turn"] = t
        gt19.state["variant"] = gt19._variant_idx(off0)
        seen_txt.add(gt19._view(off0)["text"][:20])
    assert len(seen_txt) >= 3, "一世之内岔口只有 %d 种说法" % len(seen_txt)
    # 作者定案：答不对就永远在 100% 循环 —— 忘川不还身体
    lg20 = load_legacy() or {}
    lg20["aug"] = 100; lg20["sub"] = None
    lg20["world"]["lake"] = {"run": 9, "cycle": lg20.get("cycle", 1), "said": []}
    lg20["world"].pop("drank", None)
    save_legacy(lg20)
    gl20 = Game(); gl20.new_run(seed=56, mode="story")
    assert gl20.state["aug"] == 100 and gl20.state["faction"] == "ascension", \
        "没在湖边答对，机化率却归零了"
    assert (load_legacy() or {}).get("aug") == 100, "档案里的机化率被忘川洗掉了"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep19
    print("第三轮试玩反馈回归通过（岔口不被吞/热度只有一个名字/谱系内世数/暴露前对齐阵营/岔口轮换/忘川不还身体）。")
    # 湖的开场改成两个选项 ＋ 金叶子提示（2026-08-08 作者定案）
    d20 = tempfile.mkdtemp(prefix="theseus-lake2-")
    keep20 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d20, os.path.join(d20, "legacy.json")
    CURRENT_PATH = os.path.join(d20, "current.json")
    assert "「你要什么。」" not in "".join(LAKE_SCENE), "开场还是那个开放性问题"
    assert '1. 「我干渴欲裂——」' in "".join(LAKE_SCENE), "开场没有第一个选项"
    assert "2. 沉默" in "".join(LAKE_SCENE), "开场没有第二个选项"
    def _at_lake(deeds=None):
        save_legacy({"runs": 9, "cycle": 2, "cycle_base": 5, "history": [], "skills": {},
                     "aug": 100, "sub": None, "world": _default_world(),
                     "memory": {"entries": [], "pending": None}})
        lg20 = load_legacy()
        lg20["world"]["lake"] = {"run": 9, "cycle": 2, "said": []}
        lg20["world"]["deeds"] = deeds or {}
        save_legacy(lg20)
        return Game()
    # 选 2：当场给一个结局，身体不还，湖还会再来
    out20 = _at_lake().recite("2")
    assert "仍然是一具换尽了的身体" in out20 and "它还在这里" in out20, \
        "沉默那一条没有给出结局：%s" % out20
    g20 = Game(); g20.new_run(seed=71, mode="story")
    assert g20.state["aug"] == 100, "沉默之后身体被还回来了"
    # 选 1：认渴，且只有听过金叶片的人才多一句
    assert "所有死者都是干渴的" in _at_lake().recite("1"), \
        "认渴之后守卫没有回应"
    assert "你的舌下压着一片黄金叶" not in _at_lake().recite("1"), \
        "没听过金叶片的人也拿到了提示"
    assert "你的舌下压着一片黄金叶" in _at_lake({PIETY_DEED: 1}).recite("1"), \
        "听过金叶片的人没拿到提示"
    # 1 之后照常说全 → 身体归还
    g21 = _at_lake({PIETY_DEED: 1})
    g21.recite("1")
    assert "他让开" in g21.recite("我是大地与星空之子，族类属于苍天"), "说全了却没让开"
    g22 = Game(); g22.new_run(seed=72, mode="story")
    assert g22.state["aug"] == 0 and g22.state["faction"] == "purist", \
        "说对了话，身体却没有还回来"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep20
    print("湖的开场验证通过（两个选项/沉默有结局且不还身体/金叶子提示只给听过的人/说全则归还）。")
    # 第四轮：修船那一条 ＋ 三条试玩反馈
    d21 = tempfile.mkdtemp(prefix="theseus-r4-")
    keep21 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d21, os.path.join(d21, "legacy.json")
    CURRENT_PATH = os.path.join(d21, "current.json")
    def _at_ferry(has_book):
        lg21 = {"runs": 20, "cycle": 1, "history": [], "skills": {}, "aug": 0, "sub": None,
                "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lg21["world"]["fragments"] = list(FRAGMENT_ORDER)
        lg21["world"]["deeds"] = {FINAL_REPAIR_DEED: 1} if has_book else {"honest": 1}
        save_legacy(lg21)
        gf21 = Game()
        return gf21, gf21.new_run(seed=81, mode="story")
    gno, out_no = _at_ferry(False)
    assert "终 局 · 渡 口" in out_no, "五块碎片齐了却没到渡口"
    assert "6. 修船" not in out_no, "没拿过那本手册的人看见了第六个选项"
    assert "没有第六个选项" in gno.choose(6), "没拿过手册却修得了船"
    gyes, out_yes = _at_ferry(True)
    assert "6. 修船" in out_yes, "拿过手册的人看不见第六个选项"
    out_rep = gyes.choose(6)
    assert "正在修理" in out_rep, "修船那一幕的落点没出来"
    lg_rep = load_legacy()
    assert lg_rep["world"]["final_ending"] == "repair", "修船没有记进档案"
    assert "shipwright" in (lg_rep["world"].get("achievements") or []) or \
        next(a for a in ACHIEVEMENTS if a["id"] == "shipwright")["cond"](
            lg_rep["world"], None, None), "修船的成就没有门"
    # 轮回档案：第一世永远留着
    lg22 = load_legacy()
    lg22["history"] = [{"run": r, "cycle": 1, "faction": "纯血誓约", "sub": "圣殿派",
                        "era": None, "aug": 0, "cause": "finale", "ascended": False,
                        "kept": {}, "kept_pts": 0, "total_pts": 0} for r in range(1, 21)]
    save_legacy(lg22)
    info22 = Game().legacy_info()
    assert "第1世" in info22, "轮回档案把第一世省掉了"
    assert "中间 11 世略" in info22, "省略了却没说省了几条"
    # 100% 是上载专有的数字，而且上限永不把人往回压
    g23 = _start_at(80, seed=82)
    g23.state["aug"] = 80
    g23._apply_fx({"aug": 40})
    assert g23.state["aug"] == 99, "非上载的改造堆到了 %d%%" % g23.state["aug"]
    g23._apply_fx({"aug": 40, "flag:ascended": 1})
    assert g23.state["aug"] == 100, "上载也上不去 100%"
    g23._apply_fx({"aug": 5})
    assert g23.state["aug"] == 100, "上限把已经 100% 的人往回压了"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep21
    print("第四轮验证通过（修船只给拿过手册的人/档案留住第一世/100% 是上载专有）。")
    # 牌堆：一副牌走完之前不重复；不合格的跳过但不弃牌
    d22 = tempfile.mkdtemp(prefix="theseus-deck-")
    keep22 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    SAVE_DIR, LEGACY_PATH = d22, os.path.join(d22, "legacy.json")
    CURRENT_PATH = os.path.join(d22, "current.json")
    gdk = Game(); gdk.new_run(seed=91, mode="story")
    gen_ids = set(gdk._generic_ids())
    assert len(gen_ids) >= 10, "通用牌只剩 %d 张" % len(gen_ids)
    drawn, per_life = [], []
    for _ in range(6):
        if gdk.state["over"]:
            gdk.bequeath([]); gdk.new_run(mode="story")
        this = []
        guard = 0
        while not gdk.state["over"] and guard < MAX_TURNS * 6:
            guard += 1
            pid = gdk.state["pending"]
            if pid in gen_ids and pid not in this:
                this.append(pid)
            ev_k = gdk._view(gdk._find_event(pid))
            if gdk.state.get("offer_prompt"):
                gdk.choose(2)       # 牌堆测试沿用原策略：展开后仍选第一项改造
                continue
            k = next(i for i in range(1, len(ev_k["options"]) + 1)
                     if gdk._opt_available(ev_k["options"][i - 1]))
            gdk.choose(k)
        assert len(this) == len(set(this)), "一世之内发重了：%s" % this
        assert len(this) <= GENERIC_PER_LIFE, \
            "一世发了 %d 张，超过配额 %d" % (len(this), GENERIC_PER_LIFE)
        per_life.append(len(this)); drawn += this
        gdk.bequeath([])
    # 一副牌（GENERIC_PER_LIFE × 世数 ≈ 牌数）之内每张最多多一次
    from collections import Counter
    cnt = Counter(drawn)
    cycles = len(drawn) / float(len(gen_ids))
    # 阶段专属牌会让「总牌数」略大于任一时点的可抽牌数；按向上取整的
    # 完整轮数再多容一张，既能抓出洗牌失效，也不会因新增互斥档位牌误报。
    max_per_card = ((len(drawn) + len(gen_ids) - 1) // len(gen_ids)) + 1
    for eid, c in cnt.items():
        assert c <= max_per_card, "%s 在 %.1f 轮里出现了 %d 次（牌堆没起作用）" % (eid, cycles, c)
    assert sum(per_life) >= GENERIC_PER_LIFE * 4, "六世总共才发了 %d 张" % sum(per_life)
    # 洗牌之后整副牌都在
    lgd = load_legacy() or {}
    gdk._reshuffle(lgd)
    assert set(lgd["deck"]) == gen_ids, "洗牌之后牌数对不上"
    # 改装师三个变体各就各位
    mir = next(e for e in EVENTS if e["id"] == "mirror_stall")
    assert len(mir["variants"]) == 1, "改装师现在只剩一个变体（作者删了后两个）"
    def _mirror_at(seen, deeds=None):
        lgm = {"runs": 3, "cycle": 1, "history": [], "skills": {}, "aug": 0, "sub": None,
               "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lgm["world"]["seen"] = {"mirror_stall": seen}
        lgm["world"]["deeds"] = deeds or {}
        save_legacy(lgm)
        gm2 = Game(); gm2.new_run(seed=92, mode="story")
        gm2.state["variant"] = gm2._variant_idx(mir)
        return gm2
    marks = {0: "哈哈镜", 1: "碎了一半", 2: "碎了一半"}
    for seen_n, mark in marks.items():
        gm2 = _mirror_at(seen_n)
        assert mark in gm2._view(mir)["text"], \
            "见过 %d 次读到的不是该读的那一版" % seen_n
    # 见够次数就退场 —— 作者删了后两个变体，退场也跟着提前
    assert mir["retire_seen"] == 3, "改装师的退场没跟着变体一起提前"
    gm_done = _mirror_at(mir["retire_seen"])
    gm_done.state["used_events"] = []
    assert not gm_done._eligible(mir), "改装师见够了还在抽"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep22
    print("牌堆与改装师验证通过（一世不重发/一轮内不重复/洗牌不丢牌/变体各就各位/见够就退场）。")

    # ---- 歌手三幕 ＋ 硬币 ＋「身上有没有钱」 ----------------------------
    keep23 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    d23 = tempfile.mkdtemp(prefix="theseus_singer_")
    SAVE_DIR = d23
    LEGACY_PATH = os.path.join(d23, "legacy.json")
    CURRENT_PATH = os.path.join(d23, "current.json")
    pre_sing = next(e for e in EVENTS if e["id"] == "old_singer")
    sing = next(e for e in EVENTS if e["id"] == "old_singer_high")
    assert pre_sing["max_aug"] == 39 and pre_sing["retire_seen"] == 1, \
        "低机化合唱前置没有在一次后退场"
    assert sing["min_aug"] == 40, "歌手主体没有迁入高机化池"
    assert len(sing["variants"]) == 2, "歌手现在只剩两个变体（作者删了空通道那一版）"

    def _singer_at(seen, deeds=None):
        lgs = {"runs": 4, "cycle": 1, "history": [], "skills": {}, "aug": 0, "sub": None,
               "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lgs["world"]["seen"] = {"old_singer_high": seen}
        lgs["world"]["deeds"] = deeds or {}
        save_legacy(lgs)
        gs = Game(); gs.new_run(seed=93, mode="story")
        gs.state["aug"] = 85
        gs.state["variant"] = gs._variant_idx(sing)
        return gs
    # 弧线：初见 → 烟嗓 → （喝过酒才有的）最后几天 → 空通道
    assert "四块石头压平整" in _singer_at(0)._view(sing)["text"], "第一次就该是本体那一版"
    assert "还没醒酒" in _singer_at(1)._view(sing)["text"], "第二次该进烟嗓"
    assert "还没醒酒" in _singer_at(3)._view(sing)["text"], "没一起喝过酒的人不该看到告别"
    g_bye = _singer_at(3, {"drank_with_singer": 1})
    assert "替我扔一次硬币" in g_bye._view(sing)["text"], "喝过酒又见过三次，该到最后几天了"
    # 作者把「空通道」那一版删了 —— 歌手改成见够四次就整条退场
    assert sing["retire_seen"] == 4, "歌手的退场没跟着变体一起提前"
    g_sgone = _singer_at(sing["retire_seen"], {"drank_with_singer": 1})
    g_sgone.state["used_events"] = []
    assert not g_sgone._eligible(sing), "歌手见够了还在抽"
    # 硬币：五五开，且不吃技能
    coin_opt = g_bye._view(sing)["options"][0]
    assert "coin" in coin_opt and "check" not in coin_opt, "扔硬币不该是检定"
    heads = 0
    for s_i in range(120):
        gcn = _singer_at(3, {"drank_with_singer": 1})
        gcn.state["skills"] = {k: 12 for k in SKILLS}      # 技能拉满也不该偏
        gcn.state["pending"] = "old_singer_high"
        gcn.state["variant"] = gcn._variant_idx(sing)
        gcn._rng.seed(s_i)
        gcn.choose(1)
        if gcn.state["flags"].get("singer_head"):
            heads += 1
    assert 40 <= heads <= 80, "120 次里正面 %d 次 —— 硬币不是五五开" % heads
    # 掏空过兜的人叫不动歌手
    def _call_singer(broke):
        gsb = _singer_at(1)
        gsb.state["pending"] = "old_singer_high"
        gsb.state["variant"] = gsb._variant_idx(sing)
        if broke:
            gsb.state["flags"]["broke"] = 1
        return gsb.choose(2)
    assert "你掏了" in _call_singer(False), "有钱的人应当付得出"
    assert "酒瓶砸在你背上" in _call_singer(True), "掏空过兜的人该挨那一下"
    # 付过一次之后，同一世再叫就付不出了
    g_twice = _singer_at(1)
    g_twice.state["pending"] = "old_singer_high"
    g_twice.state["variant"] = g_twice._variant_idx(sing)
    g_twice.choose(2)
    assert g_twice.state["flags"].get("broke"), "给了钱却没记下来"
    # 身体税两幕：第二幕的门是「指过他那只手」，不是熬次数
    tax = next(e for e in EVENTS if e["id"] == "tax_audit")
    assert len(tax["variants"]) == 2, "身体税应有两个变体"

    def _tax_at(seen, deeds=None):
        lgt = {"runs": 4, "cycle": 1, "history": [], "skills": {}, "aug": 0, "sub": None,
               "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lgt["world"]["seen"] = {"tax_audit": seen}
        lgt["world"]["deeds"] = deeds or {}
        save_legacy(lgt)
        gt = Game(); gt.new_run(seed=94, mode="story")
        gt.state["variant"] = gt._variant_idx(tax)
        return gt
    assert "队伍排了两百米" in _tax_at(0)._view(tax)["text"], "第一次该是本体"
    assert "稽查站快关门了" in _tax_at(1)._view(tax)["text"], "第二次该进换班"
    assert "稽查站快关门了" in _tax_at(3)._view(tax)["text"], "没指过那只手就不该有手检"
    gt_hand = _tax_at(3, {"tax_hand": 1})
    assert "排队的长度" in gt_hand._view(tax)["text"], "指过手的人该看到手检那一幕"
    # 〔那条「把右手收回去」的回响作者删了（加快进度）。这里只验变体本身还在。〕
    # 同一档待满三世要重问三题 —— 否则派系锁死，五幕派系戏一辈子只有同五幕
    def _reask(runs, lean_run):
        lgr = {"runs": runs, "cycle": 1, "history": [], "skills": {}, "aug": 0,
               "sub": "圣殿派", "lean_run": lean_run,
               "world": _default_world(), "memory": {"entries": [], "pending": None}}
        save_legacy(lgr)
        gr = Game(); out = gr.new_run(seed=95, mode="story")
        return gr.state["pending"], out
    pend, _ = _reask(2, 0)
    assert not pend.startswith("lean_"), "才过两世就重问，太吵"
    pend, out = _reask(LEAN_REASK_LIVES, 0)
    assert pend == "lean_recap", "同一档待满三世却没有重问"
    assert "上一次也问过" in out, "重问用的还是第一次那句话"
    # 说「答案没变」不该把人悄悄换到另一支去
    g_same = Game(); g_same.new_run(seed=95, mode="story")
    sub_before = g_same.state["sub"]
    g_same.choose(1)
    assert g_same.state["sub"] == sub_before, "答了「没变」，派系却变了"
    assert (load_legacy() or {}).get("sub") == sub_before, "档案里的派系被改了"
    assert (load_legacy() or {}).get("lean_run") == LEAN_REASK_LIVES, \
        "答了「没变」却没记账，下一世还会问"
    # 说「重新答一遍」要真的问出那三题
    _reask(LEAN_REASK_LIVES, 0)
    g_redo = Game(); g_redo.new_run(seed=95, mode="story")
    g_redo.choose(2)
    assert g_redo.state["pending"] == LEAN_FIRST["purist"], "想重答却没问出三题"
    gr3 = Game(); gr3.new_run(seed=95, mode="story")
    while gr3.state["pending"].startswith("lean_"):
        gr3.choose(1)
    assert (load_legacy() or {}).get("lean_run") == LEAN_REASK_LIVES, "重问完没有记账，下一世还会问"
    # 同一阵营连续两次说「答案没变」，第三次改问要不要试试另一种活法
    lg_other = {"runs": 3, "cycle": 1, "history": [], "skills": {}, "aug": 0,
                "sub": "圣殿派", "lean_run": 0,
                "world": _default_world(), "memory": {"entries": [], "pending": None}}
    for runs in (3, 6):
        lg_other["runs"] = runs
        save_legacy(lg_other)
        g_other = Game(); g_other.new_run(seed=95, mode="story")
        assert g_other.state["pending"] == "lean_recap", "前两次应该还是普通重问"
        g_other.choose(1)
        lg_other = load_legacy()
    lg_other["runs"] = 9
    save_legacy(lg_other)
    g_other = Game(); out_other = g_other.new_run(seed=95, mode="story")
    assert g_other.state["pending"] == "lean_other_life", "连续两次没变后没有出现另一种活法"
    assert "应行的路，你已经很熟悉了" in out_other, "特殊重问文案没有显示"
    old_sub = g_other.state["sub"]
    out_no = g_other.choose(2)
    assert g_other.state["sub"] == old_sub and "你没有去" in out_no, "选不去却换了派"
    assert (load_legacy().get("lean_same") or {}).get("count") == 0, "特殊重问后没有重新计数"
    lg_other = load_legacy()
    lg_other["runs"] = 12
    lg_other["lean_run"] = 9
    lg_other["lean_same"] = {"faction": "purist", "count": 2}
    save_legacy(lg_other)
    g_other = Game(); g_other.new_run(seed=95, mode="story")
    out_go = g_other.choose(1)
    assert g_other.state["sub"] != old_sub and "你去了" in out_go, "选去却没有换到另一派"
    # ---- 第五轮试玩反馈 ----------------------------------------------
    # ① 战报块必须报**实际结算值**，不是选项上写的设定值
    gfx = Game(); gfx.new_run(seed=96, mode="brief")
    gfx.state["skills"]["坚忍"] = MAX_SKILL
    gfx.state["pending"] = "tax_audit"
    gfx.state["variant"] = None
    ev_fx = gfx._find_event("tax_audit")
    k_fx = next(i for i, o in enumerate(ev_fx["options"], 1)
                if (o.get("failure") or o.get("effects") or {}).get(
                    "fx", {}).get("skill:坚忍"))
    gfx.choose(k_fx)
    assert "坚忍" not in (gfx.state.get("last_fx") or ""), \
        "技能顶到上限了，战报还在报「坚忍+1」：%s" % gfx.state.get("last_fx")
    # ② 矿难豁免：誓约里已经有那一条之后，这一幕要认得它
    conf = next(e for e in EVENTS if e["id"] == "purist_confession")
    lgc = {"runs": 3, "cycle": 1, "history": [], "skills": {}, "aug": 0,
           "sub": "圣殿派", "lean_run": 3,
           "world": _default_world(), "memory": {"entries": [], "pending": None}}
    lgc["world"]["deeds"] = {"reformer": 1}
    save_legacy(lgc)
    gcf = Game(); gcf.new_run(seed=97, mode="story")
    gcf.state["variant"] = gcf._variant_idx(conf)
    txt_c = "".join(str(o) for o in gcf._view(conf)["options"])
    assert "矿难豁免" in txt_c and "新添" not in txt_c, \
        "誓约里已经有豁免条款了，这一幕却还在重新新添一遍"
    assert gcf._view(conf)["echoes"], "只换选项的局部变体把回响弄丢了"
    # ③之前：金叶片那一问不掷骰 —— 它是整条链唯一的入口
    pd_ask = next(o for o in next(e for e in EVENTS if e["id"] == "preacher_death")["options"]
                  if "金叶片语句的含义" in o["text"])
    assert "check" not in pd_ask and "failure" not in pd_ask, \
        "金叶片那一问又变成检定了 —— 失手一次会把后面整条链锁死"
    assert pd_ask["effects"].get("then") == "leaf_answer", "问了却问不到那一幕"
    # ③ 传教士的临终只给上去过那间教堂的人
    pd = next(e for e in EVENTS if e["id"] == "preacher_death")
    assert pd["req_deed"].get("entered_chapel"), "临终那一幕的门没关上"
    gch = Game(); gch.new_run(seed=98, mode="story")
    gch.state["world"]["seen"] = {"elevator_preacher": 3}
    gch.state["world"]["deeds"] = {}
    assert not gch._eligible(pd), "没进过教堂的人不该撞上临终"
    gch.state["world"]["deeds"] = {"entered_chapel": 1}
    assert gch._eligible(pd), "进过教堂却还是遇不上"
    # ④ 岔口一世只出现一次；拒绝之后再过一幕也不会重问
    assert AUG_OPPORTUNITY_CAP == AUG_PER_LIFE == AUG_DECLINE_CAP == 1, \
        "岔口的三个上限没有一起收成一次"
    goff = Game(); goff.new_run(seed=99, mode="story")
    while goff.state["pending"].startswith("lean_"):
        goff.choose(1)
    goff.state["pending"] = "tax_audit"; goff.state["variant"] = None
    goff.state["offered"] = False
    tax_view = goff._view(goff._find_event("tax_audit"))
    goff.choose(next(i for i in range(1, len(tax_view["options"]) + 1)
                     if goff._opt_available(tax_view["options"][i - 1])))
    assert goff.state["pending"].startswith("aug_offer_"), "第一幕之后没有岔口"
    off_view = goff._view(goff._find_event(goff.state["pending"]))
    refused = goff.choose(len(off_view["options"]))
    assert "唯一一次改造机会" in refused, "拒绝唯一一次岔口时没有即时收束"
    assert goff.state["aug_opportunities"] == 1, "唯一一次机会没有记账"
    next_view = goff._view(goff._find_event(goff.state["pending"]))
    out_after = goff.choose(next(i for i in range(1, len(next_view["options"]) + 1)
                                  if goff._opt_available(next_view["options"][i - 1])))
    assert not goff.state["pending"].startswith("aug_offer_"), "同一世出现了第二次岔口"
    assert "岔 口" not in out_after, "唯一一次机会用完后又把岔口念了一遍"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep23
    print("歌手与身体税验证通过（弧线四档/走了就不再上班/硬币五五开不吃技能/"
          "兜里有没有钱/手检的门是那只手/同档三世重问一次）。")
    # ---- 第六轮试玩反馈 ----------------------------------------------
    # ① 机化率分档：高机化的人不该再去中介所门口找活
    g_hi = Game(); g_hi.new_run(seed=101, mode="story")
    g_hi.state["aug"] = 85; g_hi.state["faction"] = "ascension"
    g_hi.state["used_events"] = []
    for eid in ("job_interview", "mirror_stall"):
        assert not g_hi._eligible(next(e for e in EVENTS if e["id"] == eid)), \
            "%s 在 85%% 的身体上还抽得到" % eid
    pool_hi = [e["id"] for e in EVENTS
               if e["factions"] == "any" and not e.get("subscene")
               and g_hi._eligible(e)]
    assert len(pool_hi) >= 8, "高机化那一档只剩 %d 张牌，牌堆会饿死" % len(pool_hi)
    g_lo = Game(); g_lo.new_run(seed=102, mode="story")
    g_lo.state["aug"] = 0; g_lo.state["used_events"] = []
    assert g_lo._eligible(next(e for e in EVENTS if e["id"] == "job_interview")), \
        "0% 的人反倒进不了中介所"
    for eid in ("rain_market", "power_cut", "old_singer_high"):
        ev_hi = next(e for e in EVENTS if e["id"] == eid)
        assert ev_hi["min_aug"] == 40, "%s 没有迁入高机化池" % eid
        assert not g_lo._eligible(ev_hi), "%s 在低机化阶段仍会被提前消耗" % eid
        assert g_hi._eligible(ev_hi), "%s 到了高机化阶段反而抽不到" % eid
    pre_sing = next(e for e in EVENTS if e["id"] == "old_singer")
    assert pre_sing["max_aug"] == 39 and g_lo._eligible(pre_sing), \
        "低机化合唱前置没有保留下来"
    assert not g_hi._eligible(pre_sing), "低机化合唱前置混进了高机化池"
    # ② 技能之声许过的那枚芯片，得有地方兑现
    rm = next(e for e in EVENTS if e["id"] == "rain_market")
    ask = [o for o in rm["options"] if o.get("req") == ("skill", "电子直觉", 8)]
    assert ask and "低鸣" in ask[0]["text"], "低鸣求救那条线索仍然没有出口"
    # ③ 过河标记：0% 时一直有效，动过刀就作废（细则见 new_run）
    # ④ CLI 有说话的入口
    import inspect
    src_cli = inspect.getsource(run_cli)
    assert 'GAME.recite(' in src_cli, "CLI 仍然没有开口说话的入口"
    # ⑤ 血的证词的提示要指向真闸门
    assert "破过一次例" in FRAGMENTS["blood"]["hint"], "碎片提示还在说「未曾背叛」"
    # ---- 河堤 · cc（作者手写稿） --------------------------------------
    keep24 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    d24 = tempfile.mkdtemp(prefix="theseus_cc_")
    SAVE_DIR = d24
    LEGACY_PATH = os.path.join(d24, "legacy.json")
    CURRENT_PATH = os.path.join(d24, "current.json")
    rb = next(e for e in EVENTS if e["id"] == "riverbank")
    assert rb["min_aug"] == 40, "河堤应当是高机化专属"
    assert len(rb["variants"]) == 3, "河堤现在只剩三个变体（作者删了空河堤）"

    def _rb_at(seen, deeds=None, aug=85):
        lgr = {"runs": 6, "cycle": 1, "history": [], "skills": {}, "aug": aug,
               "sub": None, "lean_run": 6,
               "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lgr["world"]["seen"] = {"riverbank": seen, "old_singer_high": 5}
        lgr["world"]["deeds"] = dict(deeds or {})
        save_legacy(lgr)
        gr = Game(); gr.new_run(seed=103, mode="story")
        while gr.state["pending"].startswith("lean_"):
            gr.choose(1)
        gr.state["aug"] = aug
        gr.state["variant"] = gr._variant_idx(rb)
        return gr
    # 四段弧线，每一段的门都不是「熬次数」，而是上一段做成了什么
    assert "四散奔逃" in _rb_at(0)._view(rb)["text"], "第一次该是本体"
    assert "四散奔逃" in _rb_at(3)._view(rb)["text"] and \
        "笃定" not in _rb_at(3)._view(rb)["text"], "没追上过孩子就不该进第二幕"
    g_1 = _rb_at(1, {"cc_glimpse": 1})
    assert "带着奇特的笃定" in g_1._view(rb)["text"], "追上过却进不了第二幕"
    assert "呼唤她的名字" not in g_1._view(rb)["text"], "还没问出名字就叫上了"
    g_2 = _rb_at(2, {"cc_glimpse": 1, "cc_named": 1})
    assert "呼唤她的名字" in g_2._view(rb)["text"], "问出过名字却认不出人"
    g_3 = _rb_at(3, {"cc_glimpse": 1, "cc_named": 1, "cc_hand": 1})
    assert "cc 在等你" in g_3._view(rb)["text"], "牵过手却没有最后一次"
    # 唱歌那一项是选项级的 seen 门
    sing_opt = [o for o in g_1._view(rb)["options"]
                if o.get("req") == ("seen", "old_singer_high", 3)]
    assert sing_opt, "河堤上那首歌没有挂在歌手身上"
    assert g_1._opt_available(sing_opt[0]), "听过五次歌手却唱不出来"
    g_nosong = _rb_at(1, {"cc_glimpse": 1})
    g_nosong.state["world"]["seen"]["old_singer_high"] = 1
    assert not g_nosong._opt_available(sing_opt[0]), "没听过歌手却会唱"
    assert "在地下通道听过那个歌手" in g_nosong._render_event(rb), \
        "灰掉的那一项没告诉玩家门在哪"
    # 手术之后这条线整个退场 —— 成没成都一样（作者删了「空河堤」那一版）
    assert rb["retire_deed"] == "cc_gone", "河堤没挂上退场标记"
    for end in ({"cc_ascended": 1, "cc_gone": 1}, {"cc_dead": 1, "cc_gone": 1}):
        d_end = dict({"cc_glimpse": 1, "cc_named": 1, "cc_hand": 1}, **end)
        g_end = _rb_at(6, d_end)
        g_end.state["used_events"] = []
        assert not g_end._eligible(rb), "手术之后 cc 还在河堤上"
    # 只有湖水能让她回来，而且是从第 0 次开始
    lg_cc = load_legacy()
    lg_cc["world"]["deeds"] = {"cc_glimpse": 1, "cc_named": 1, "cc_hand": 1,
                               "cc_ascended": 1, "cc_gone": 1, "honest": 2}
    lg_cc["world"]["seen"] = {"riverbank": 6, "old_singer_high": 5}
    lg_cc["world"]["lake"] = {"run": 9, "cycle": 1, "said": []}
    lg_cc["aug"] = 100
    save_legacy(lg_cc)
    # 答对那句话的人**留不住她** —— 他换回的是血肉，不是 cc
    gl = Game()
    gl.recite("1")
    gl.recite("我是大地与星空之子，而我的族类属于天")
    w_right = (load_legacy() or {})["world"]
    assert w_right.get("drank"), "湖：过河未记录"
    assert w_right["deeds"].get("cc_gone"), "答对了却把 cc 那条线一起冲掉了"
    assert w_right["seen"].get("riverbank") == 6, "答对了却把见面次数清了"
    # 沉默、喝下忘川的人才让她重来 —— 从第 0 次开始
    lg_cc2 = load_legacy()
    lg_cc2["world"]["drank"] = None
    lg_cc2["world"]["lake"] = {"run": 10, "cycle": 1, "said": []}
    save_legacy(lg_cc2)
    g2l = Game()
    out_lethe = g2l.recite("2")
    assert "河堤" in out_lethe, "忘川那一口没告诉玩家他丢了什么"
    w_after = (load_legacy() or {})["world"]
    assert not any(k.startswith("cc_") for k in w_after["deeds"]), \
        "喝了忘川，cc 那条线却没有跟着归零"
    assert w_after["seen"].get("riverbank", 0) == 0, "见面次数没有清回 0"
    assert not w_after.get("drank"), "忘川不该还身体"
    assert w_after["seen"].get("old_singer_high") == 5 and w_after["deeds"].get("honest") == 2, \
        "忘川冲掉了不该冲的东西"
    # 双六顶格时不再空挂那一句
    gcr2 = Game(); gcr2.new_run(seed=104, mode="brief")
    gcr2.state["skills"]["坚忍"] = MAX_SKILL
    ev_c = gcr2._find_event("tax_audit")
    k_c = next(i for i, o in enumerate(ev_c["options"], 1)
               if "check" in o and not o.get("req"))
    hit = False
    for sd in range(400):
        g_try = Game(); g_try.new_run(seed=200 + sd, mode="brief")
        g_try.state["skills"] = {k: MAX_SKILL for k in SKILLS}
        g_try.state["pending"] = "tax_audit"; g_try.state["variant"] = None
        g_try._rng.seed(sd)
        out_c = g_try.choose(k_c)
        if "双六" in out_c:
            hit = True
            assert "是双六给的" not in out_c, "技能顶格了还在宣布双六余韵"
    assert hit, "四百次里一次双六都没掷出来，这条断言等于没跑"
    # 盲眼老人只卖一次书
    nl = next(e for e in EVENTS if e["id"] == "night_library")
    give = next(o for o in nl["options"] if "所有的钱" in o["text"])
    # 闸门改成 nodeed：**success 就是第一次，第一次才有书**（作者提的那个「是不是写反了」）
    assert give.get("gate") == ("nodeed", "gave_it_away", 1), "书还在无限量供应"
    assert "速成手册" in give["success"]["narration"], "第一次给钱反倒没书了"
    assert "没有东西可以给你" in give["failure"]["narration"], "第二次给钱还在发书"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep24
    # ---- 第七轮试玩反馈 ----------------------------------------------
    # ① 换了阵营那一栏就换了题目，旧账不能带过去
    gh2 = Game(); gh2.new_run(seed=105, mode="story")
    gh2.state["faction"] = "discreet"; gh2.state["aug"] = 35
    gh2.state["heat"] = 8 - 1
    assert heat_label("discreet") != heat_label("open"), "两档的那一栏居然同名"
    gh2.state["pending"] = "tax_audit"; gh2.state["variant"] = None
    gh2.state["heat"] = 7
    ev_h2 = gh2._find_event("tax_audit")
    # 直接把机化率顶过明焰那条线，再走一幕，看那一栏跟不跟着清
    gh2.state["aug"] = 39
    gh2._apply_fx({"aug": 6})
    now_t = aug_tier(gh2.state["aug"])
    assert now_t == "open", "39+6 没跨过明焰那条线？"
    gh2.state["tier_pending"] = None
    out_h = gh2.choose(next(i for i, o in enumerate(ev_h2["options"], 1)
                            if "check" in o and not o.get("req")))
    assert gh2.state["heat"] == 0 or "归零" in out_h, "跨档换营之后旧的那一栏还带着"
    # ② 数值报实际增量
    g_num = Game(); g_num.new_run(seed=106, mode="story")
    g_num.state["aug"] = 99
    rep = g_num._apply_fx({"aug": 100, "flag:ascended": 1})
    assert any("+1%" in r for r in rep), "99→100 还在报「+100%%」：%s" % rep
    g_num.state["skills"]["坚忍"] = MAX_SKILL - 1
    rep2 = g_num._apply_fx({"skill:坚忍": 5})
    assert any("坚忍 +1 " in r for r in rep2), "顶格那一下还在报设定值：%s" % rep2
    # ③ 0% 的那一世落笔不数格子
    keep25 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    d25 = tempfile.mkdtemp(prefix="theseus_beq_")
    SAVE_DIR = d25
    LEGACY_PATH = os.path.join(d25, "legacy.json")
    CURRENT_PATH = os.path.join(d25, "current.json")
    lg_b = {"runs": 3, "cycle": 1, "history": [], "skills": {}, "aug": 0, "sub": None,
            "world": _default_world(),
            "memory": {"entries": [{"run": 1, "text": "旧词条%d" % i}
                                   for i in range(MEMORY_SLOTS)],
                       "pending": {"run": 4, "aug": 0}}}
    save_legacy(lg_b)
    out_b = Game().bequeath(["钱给了盲眼老人"])
    assert "装不下" not in out_b, "0% 的那一世还在拦着不让写"
    assert "钱给了盲眼老人" in out_b, "写下的那条没进去"
    # 不是 0% 的时候照样数格子
    lg_b2 = load_legacy(); lg_b2["memory"]["pending"] = {"run": 5, "aug": 40}
    lg_b2["memory"]["entries"] = [{"run": 1, "text": "旧词条%d" % i}
                                  for i in range(MEMORY_SLOTS)]
    save_legacy(lg_b2)
    assert "装不下" in Game().bequeath(["再写一条"]), "有机化率的时候格子不算数了"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep25
    # ④之前：拒绝唯一一次机会，这一世不再问；点头则记为已经用过
    g_dec = Game(); g_dec.new_run(seed=117, mode="story")
    off0_d = next(e for e in EVENTS if e["id"] == "aug_offer_0")
    n_refuse = len(off0_d["options"])          # 最后一项永远是「不改造」
    out_refuse = ""
    for i in range(AUG_DECLINE_CAP):
        g_dec.state["pending"] = "aug_offer_0"; g_dec.state["variant"] = None
        g_dec.state["offered"] = True
        out_refuse = g_dec.choose(n_refuse)
    assert g_dec.state["aug_declined"] == AUG_DECLINE_CAP, "拒绝没数上"
    assert "唯一一次改造机会" in out_refuse, "唯一一次拒绝当下没有收束"
    g_dec.state["pending"] = "rain_market"; g_dec.state["variant"] = None
    g_dec.state["offered"] = False
    ev_rm = g_dec._view(g_dec._find_event("rain_market"))
    out_dec = g_dec.choose(next(i for i in range(1, len(ev_rm["options"]) + 1)
                                if g_dec._opt_available(ev_rm["options"][i - 1])))
    assert "唯一一次改造机会" not in out_dec, "收束句延迟到了下一幕结果之后"
    assert "岔 口" not in out_dec, "说完不再问，还是问了"
    # 点头一次，拒绝计数清零
    g_dec.state["aug_declined"] = 1
    g_dec.state["pending"] = "aug_offer_0"; g_dec.state["variant"] = None
    g_dec.choose(1)                             # 第一项永远是「装一件」
    assert g_dec.state["aug_declined"] == 0, "点过头了，拒绝计数却没清"
    # 熟悉度压缩：见过 FOLD_SEEN 次之后场景折成一行，回响和选项照给
    g_fold = Game(); g_fold.new_run(seed=118, mode="story")
    g_fold.state["world"]["seen"]["rain_market"] = FOLD_SEEN
    g_fold.state["world"]["deeds"]["still_asking"] = 1
    rm_f = g_fold._find_event("rain_market")
    g_fold.state["variant"] = None
    txt_f = g_fold._render_event(rm_f)
    assert "场景略" in txt_f, "见过这么多次了还在念全文"
    assert "酸雨敲打夜市的防水布" in txt_f, "折过头了，第一行也该留着"
    assert "小贩摊开手心" not in txt_f, "说好折的部分没折"
    assert "【回响】" in txt_f, "折掉了回响 —— 那正是每次都不一样的部分"
    assert "用 choose" in txt_f and "1. " in txt_f, "折掉了选项"
    # 后期已读快进：第一次重见就折正文，仍不折回响与选项
    g_fold.state["world"]["fragments"] = list(FRAGMENT_ORDER[:2])
    g_fold.state["world"]["seen"]["rain_market"] = 1
    txt_fast = g_fold._render_event(rm_f)
    assert "场景略" in txt_fast and "用 choose" in txt_fast, \
        "后期已读快进没有折正文，或者误折了选项"

    # 碎片重试：同一入口隔世冷却；连续两世没来，后期动态发牌保底一张
    g_retry = Game(); g_retry.new_run(seed=128, mode="story")
    g_retry.state.update({"faction": "open", "sub": "学院派", "aug": 50,
                          "used_events": [], "gen_drawn": 0, "late_targeted": False})
    g_retry.state["world"] = _default_world()
    g_retry.state["world"]["fragments"] = list(FRAGMENT_ORDER[:2])
    g_retry.state["world"]["recent"] = [["night_library"], ["power_cut"]]
    assert g_retry._ticket_on_cooldown("night_library"), "碎片入口还在连续两世刷脸"
    g_retry.state["world"]["recent"] = [["power_cut"], ["old_singer_high"]]
    due_retry = g_retry._ticket_retry_ids()
    assert due_retry, "连续两世没遇到碎片入口，却没有触发保底"
    picked_retry = g_retry._pick_event()
    assert picked_retry and picked_retry["id"] in due_retry and g_retry.state["late_targeted"], \
        "后期动态发牌没有优先偿还到期碎片入口"

    # 终幕熟悉度压缩：七个开场第二次才折；32 种结果各自记忆，互不串线。
    finale_events = {f["id"]: f for f in FINALES.values()}
    finale_events.update({e["id"]: e for e in EVENTS
                          if e["id"] in ("finale_harbor", "finale_dog")})
    assert set(FINALE_SHORT_TEXT) == set(finale_events), "七个终幕的短开场没有配齐"
    expected_results = set()
    for eid, fin in finale_events.items():
        for opt_n, opt in enumerate(fin["options"], 1):
            if any(k in opt for k in ("check", "gate", "coin")):
                expected_results.add((eid, opt_n, "success"))
                expected_results.add((eid, opt_n, "failure"))
            else:
                expected_results.add((eid, opt_n, "effects"))
    assert len(expected_results) == 32, "终幕结果数变了：%d" % len(expected_results)
    assert set(FINALE_RESULT_SHORT) == expected_results, "32 种终幕结果的短版没有配齐"

    # 播种者与群智派共享主阵营，却必须走不同终幕、不同熟悉度命名空间。
    g_fin = Game(); g_fin.new_run(seed=123, mode="story")
    for sub_fin, eid_fin in (("群智派", "finale_ascension"),
                             ("播种者", "finale_ascension_seed")):
        g_fin.state.update({"faction": "ascension", "sub": sub_fin})
        assert g_fin._faction_finale()["id"] == eid_fin, \
            "%s 被送进了错误终幕" % sub_fin
    g_fin.state["world"]["seen"] = {"finale_ascension": 1}
    g_fin.state["variant"] = None
    seed_fin = FINALES["ascension_seed"]
    seed_first = g_fin._render_event(seed_fin)
    assert seed_fin["text"] in seed_first, "群智派终幕历史提前折叠了播种者终幕"
    assert FINALE_SHORT_TEXT["finale_ascension_seed"] not in seed_first, \
        "播种者终幕第一次就用了简版"

    for eid, fin in finale_events.items():
        g_fin.state["variant"] = None
        g_fin.state["world"].setdefault("seen", {})[eid] = 0
        full_fin = g_fin._render_event(fin)
        assert fin["text"] in full_fin and FINALE_SHORT_TEXT[eid] not in full_fin, \
            "%s 第一次就被折了" % eid
        g_fin.state["world"]["seen"][eid] = 1
        short_fin = g_fin._render_event(fin)
        assert FINALE_SHORT_TEXT[eid] in short_fin and fin["text"] not in short_fin, \
            "%s 第二次没有换短开场" % eid
        for opt in fin["options"]:
            assert opt["text"] in short_fin, "%s 折开场时把选项折掉了" % eid
    # 开场换短版之后，跨世回响仍由原装配流程接在后面。
    g_fin.state["world"]["deeds"]["hammer_wrist"] = 1
    g_fin.state["world"]["seen"]["finale_purist"] = 1
    g_fin.state["variant"] = None
    assert "【回响】" in g_fin._render_event(FINALES["purist"]), \
        "终幕短开场把跨世回响吞了"

    # 跑两次同一个无检定结果：第一次全文，第二次短版；第二次同时解锁碎片，
    # 确认结果压缩没有越界压掉轮回结算里的真相碎片。
    keep_fin = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    d_fin = tempfile.mkdtemp(prefix="theseus_finale_fold_")
    SAVE_DIR = d_fin
    LEGACY_PATH = os.path.join(d_fin, "legacy.json")
    CURRENT_PATH = os.path.join(d_fin, "current.json")
    g_fs = Game(); g_fs.new_run(seed=127, mode="story")
    g_fs.state.update({"faction": "ascension", "sub": "播种者", "aug": 99,
                       "pending": "finale_ascension_seed", "variant": None,
                       "turn": MAX_TURNS})
    g_fs.state["world"].setdefault("finale_results", {}).update({
        "finale_ascension#2#success": 1,
        "finale_ascension#2#failure": 1,
    })
    seed_result = g_fs.choose(2)
    seed_branch = (seed_fin["options"][1]["success"]
                   if "成功" in g_fs.state.get("last_beat", "")
                   else seed_fin["options"][1]["failure"])
    assert seed_branch["narration"] in seed_result, \
        "群智派终幕结果历史提前压缩了播种者结果"

    g_f1 = Game(); g_f1.new_run(seed=124, mode="story")
    g_f1.state.update({"faction": "open", "sub": "学院派", "aug": 50,
                       "pending": "finale_open", "variant": None,
                       "turn": MAX_TURNS})
    first_finale_result = g_f1.choose(3)
    assert "组委会吵到凌晨四点" in first_finale_result, "终幕结果第一次没有给全文"
    mark_open = "finale_open#3#effects"
    assert load_legacy()["world"]["finale_results"].get(mark_open) == 1, \
        "终幕结果没有写进跨世记忆"

    g_f2 = Game(); g_f2.new_run(seed=125, mode="story")
    g_f2.state.update({"faction": "open", "sub": "学院派", "aug": 50,
                       "pending": "finale_open", "variant": None,
                       "turn": MAX_TURNS, "flags": {"reformer": 1}})
    second_finale_result = g_f2.choose(3)
    assert FINALE_RESULT_SHORT[("finale_open", 3, "effects")] in second_finale_result, \
        "同一终幕同一选项同一结果第二次没有压缩"
    assert "组委会吵到凌晨四点" not in second_finale_result, "短结果后面还在重放全文"
    assert "真相碎片 ·「明账」" in second_finale_result, "结果压缩把真相碎片吞了"

    # 条件尾巴也永远给全文：这里只把主结果预标为见过。
    g_ft = Game(); g_ft.new_run(seed=126, mode="story")
    g_ft.state.update({"faction": "purist", "sub": "圣殿派", "aug": 0,
                       "pending": "finale_purist", "variant": None,
                       "turn": MAX_TURNS})
    g_ft.state["world"].setdefault("finale_results", {})[
        "finale_purist#3#effects"] = 1
    g_ft.state["world"]["deeds"]["harbor_sunk"] = 1
    tail_result = g_ft.choose(3)
    assert FINALE_RESULT_SHORT[("finale_purist", 3, "effects")] in tail_result, \
        "纯血终幕的重复结果没有压缩"
    assert "换个门牌而已" in tail_result, "结果压缩把条件尾巴吞了"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep_fin
    print("终幕熟悉度验证通过（七个短开场/32 种结果独立记忆/播种者分流/回响选项尾巴碎片不折）。")

    # 干到什么程度，那一行就说什么
    g_line = Game(); g_line.new_run(seed=119, mode="story")
    w_line = g_line.state["world"]
    assert "这座城今天没有新的事发生" in g_line._dry_line(), "刚开局那句就不对"
    w_line["seen"] = {e["id"]: (e.get("retire_seen") or 0) for e in EVENTS
                      if e.get("retire_seen")}
    # 这段只测普通退场进度，先把碎片入口保护全部解除。
    w_line["fragments"] = list(FRAGMENT_ORDER)
    # entered_chapel / denied_the_leaf：金叶片那两幕攥着钥匙就不肯走（keep_until）
    w_line["deeds"] = {"hymn_done": 1, "cc_gone": 1,
                       "entered_chapel": 1, "denied_the_leaf": 1}
    assert "最后一次" in g_line._dry_line(), "只剩几条了，那一行没跟着变"
    w_line["deeds"].update({"library_closed": 1, "dog_over": 1, "cc_gone": 1})
    assert "一件也没有了" in g_line._dry_line(), "全讲完了，那一行还没到底"

    # ④ 同一个岔口选项第二次起只给一行
    g_off = Game(); g_off.new_run(seed=107, mode="story")
    off0 = next(e for e in EVENTS if e["id"] == "aug_offer_0")
    g_off.state["pending"] = "aug_offer_0"; g_off.state["variant"] = None
    first = g_off.choose(3)
    g_off.state["pending"] = "aug_offer_0"; g_off.state["variant"] = None
    g_off.state["offered"] = True
    second = g_off.choose(3)
    head3 = next(e for e in EVENTS if e["id"] == "aug_offer_0")["options"][2]
    key3 = head3["effects"]["narration"].split("\n")[0][:8]
    assert key3 in first and key3 not in second, "岔口结果还在逐字重放"
    assert any(line in second for line in _OFFER_AGAIN["aug_offer_0"][False]), \
        "重复的那一次什么也没给"
    # 代价每次不一样：同一个岔口连走三次，三行各不相同
    again_lines = [_offer_again_line("aug_offer_0", True, n) for n in (1, 2, 3)]
    assert len(set(again_lines)) == 3, "改造的代价三次一模一样"
    assert _offer_again_line("aug_offer_3", True, 1) != again_lines[0], \
        "四个档位的账应当各算各的"

    # ---- 楼下的歌声 · 退场 · 全书终 ----------------------------------
    keep26 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    d26 = tempfile.mkdtemp(prefix="theseus_hymn_")
    SAVE_DIR = d26
    LEGACY_PATH = os.path.join(d26, "legacy.json")
    CURRENT_PATH = os.path.join(d26, "current.json")
    hym = next(e for e in EVENTS if e["id"] == "hymn_downstairs")
    assert hym["min_aug"] == 40, "楼下的歌声应当是高机化专属"
    assert hym["retire_deed"] == "hymn_done", "这条线没有声明退场"
    assert len(hym["variants"]) == 2, "楼下的歌声应有两个变体"

    def _hym_at(seen, deeds=None, aug=85):
        lgh = {"runs": 6, "cycle": 1, "history": [], "skills": {}, "aug": aug,
               "sub": None, "lean_run": 6,
               "world": _default_world(), "memory": {"entries": [], "pending": None}}
        lgh["world"]["seen"] = {"hymn_downstairs": seen}
        lgh["world"]["deeds"] = dict(deeds or {})
        save_legacy(lgh)
        gh3 = Game(); gh3.new_run(seed=108, mode="story")
        while gh3.state["pending"].startswith("lean_"):
            gh3.choose(1)
        gh3.state["aug"] = aug
        gh3.state["variant"] = gh3._variant_idx(hym)
        return gh3
    # 下楼敲门那一项只给上过三十九楼的人
    g_h0 = _hym_at(0)
    knock = next(o for o in hym["options"] if "敲门" in o["text"])
    assert not g_h0._opt_available(knock), "没上过教堂也能下楼敲门"
    assert _hym_at(0, {"entered_chapel": 1})._opt_available(knock), "上过教堂却敲不了门"
    # 三段：本体 → 敲错门的人 → 床头那只手
    assert "机械鼓膜" in _hym_at(3)._view(hym)["text"], "没跟唱过就不该有人来敲门"
    g_h1 = _hym_at(1, {"hymn_joined": 1})
    assert "希望进门" in g_h1._view(hym)["text"], "跟唱过却没人来敲门"
    g_h2 = _hym_at(2, {"hymn_joined": 1, "hymn_alley": 1})
    assert "床头站着一个影子" in g_h2._view(hym)["text"], "去过巷子却没有那只手"
    # 巷子那一项：两道门，一道决定能不能选，一道决定走哪一支
    alley = next(o for o in g_h1._view(hym)["options"] if "一起快速下楼" in o["text"])
    assert alley["req"] == ("deed", "denied_the_leaf", 1), "巷子那一项的门不对"
    assert not g_h1._opt_available(alley), "没在临终说过那句话也能走"
    g_say = _hym_at(1, {"hymn_joined": 1, "denied_the_leaf": 1})
    assert g_say._opt_available(alley), "说过那句话却走不了"
    assert not g_say._gate_open(alley["gate"]), "没抄过经也认得那个字"
    g_say.state["world"]["seen"]["temple_scripture"] = 1
    assert g_say._gate_open(alley["gate"]), "抄过经却不认得那个字"
    # 退场：回吻／任由，二选一之后这条线就没了
    for end_flag in ("hymn_codex", "hymn_medium"):
        g_end = _hym_at(3, {"hymn_joined": 1, "hymn_alley": 1, end_flag: 1})
        opts = g_end._view(hym)["options"]
        pick = [i for i, o in enumerate(opts, 1) if g_end._opt_available(o)]
        assert len(pick) == 2, "床头那一幕应当只开两项（抽手 ＋ 属于你的那一项）"
    g_done = _hym_at(4, {"hymn_joined": 1, "hymn_alley": 1, "hymn_done": 1})
    g_done.state["used_events"] = []
    assert not g_done._eligible(hym), "讲完了的线还在被抽到"
    # 全书终（作者定案的新门槛）：每条会讲完的线都**至少见过一次**
    # ＋ 走过金叶子那条路
    lg_e = load_legacy()
    todo_ids = [e["id"] for e in EVENTS
                if e.get("retire_deed") or e.get("retire_seen")]
    lg_e["world"]["deeds"] = {"hymn_done": 1}
    lg_e["world"]["seen"] = {eid: 1 for eid in todo_ids}
    lg_e["world"]["fragments"] = []
    save_legacy(lg_e)
    assert _story_done(load_legacy()["world"]), "每条都见过一次却不算讲完"
    assert not _all_retired(load_legacy()["world"]), \
        "「见过一次」不该等于「全部退场」"
    # 少见一条就不算
    lg_e3 = load_legacy()
    lg_e3["world"]["seen"]["mirror_stall"] = 0
    save_legacy(lg_e3)
    assert not _story_done(load_legacy()["world"]), "有一条没见过就算全书终了"
    lg_e3["world"]["seen"]["mirror_stall"] = 1
    # 没走金叶子那条路也不算
    lg_e3["world"]["deeds"] = {}
    save_legacy(lg_e3)
    assert not _story_done(load_legacy()["world"]), "没走金叶子那条路就算全书终了"
    lg_e3["world"]["deeds"] = {"hymn_done": 1}
    save_legacy(lg_e3)
    out_ep = Game().new_run(seed=109, mode="story")
    assert "全 书 终" in out_ep and "忒修斯之脑" in out_ep, "全书终没有出现"
    assert "全 书 终" not in Game().new_run(seed=110, mode="story"), "全书终念了第二遍"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep26
    # ---- 退场表：所有弧线都能讲完 --------------------------------------
    keep27 = (SAVE_DIR, LEGACY_PATH, CURRENT_PATH)
    d27 = tempfile.mkdtemp(prefix="theseus_retire_")
    SAVE_DIR = d27
    LEGACY_PATH = os.path.join(d27, "legacy.json")
    CURRENT_PATH = os.path.join(d27, "current.json")
    todo = [e for e in EVENTS if e.get("retire_seen") or e.get("retire_deed")]
    assert len(todo) >= 50, "会讲完的线只有 %d 条 —— 退场表没贴上" % len(todo)
    # 八条派系线一条不漏
    for sub_name in ("灰港", "圣殿派", "铁锤派", "面具沙龙",
                     "学院派", "平权阵线", "群智派", "播种者"):
        line = [e for e in EVENTS if (e.get("subs") or []) == [sub_name]]
        assert line, "找不到派系线：%s" % sub_name
        left = [e["id"] for e in line
                if not (e.get("retire_seen") or e.get("retire_deed"))
                and not e.get("subscene") and not e["id"].startswith("finale")]
        assert not left, "%s 这几幕永远讲不完：%s" % (sub_name, "、".join(left))
    # 只剩深夜敲门是天气 —— 它不是故事，是疑云攒够了自己找上门
    for wid in ("heat_visit",):
        ev_w = next(e for e in EVENTS if e["id"] == wid)
        assert not (ev_w.get("retire_seen") or ev_w.get("retire_deed")), \
            "%s 是天气，不该讲完" % wid
    # 其余全部会讲完，一个不漏
    for wid in ("rain_market", "ferry_night", "power_cut", "night_library",
                "job_interview", "echo_dream", "echo_slip", "echo_slip_pro"):
        ev_w = next(e for e in EVENTS if e["id"] == wid)
        assert ev_w.get("retire_seen") or ev_w.get("retire_deed"), \
            "%s 还没有退场条件" % wid
    # 停电只有一夜
    assert next(e for e in EVENTS if e["id"] == "power_cut")["retire_seen"] == 1, \
        "406 那一夜不该有第二次"
    # 变体级退场：朋友的新眼睛问三次就问完了
    off0_v = next(e for e in EVENTS if e["id"] == "aug_offer_0")["variants"]
    eye = [v for v in off0_v if "朋友换了一只眼睛" in (v.get("text") or "")]
    assert eye and eye[0].get("retire_after") == 3, "朋友的新眼睛没有变体级退场"
    lg_v = {"runs": 9, "cycle": 1, "history": [], "skills": {}, "aug": 0, "sub": None,
            "lean_run": 9, "world": _default_world(),
            "memory": {"entries": [], "pending": None}}
    lg_v["world"]["var_seen"] = {"aug_offer_0#%d" % off0_v.index(eye[0]): 3}
    save_legacy(lg_v)
    g_v = Game(); g_v.new_run(seed=115, mode="story")
    while g_v.state["pending"].startswith("lean_"):
        g_v.choose(1)
    g_v.state["turn"] = 8
    off0_ev = next(e for e in EVENTS if e["id"] == "aug_offer_0")
    idx_v = g_v._variant_idx(off0_ev)
    assert idx_v != off0_v.index(eye[0]), "问过三次的眼睛还在问"
    assert idx_v is not None, "变体退场把整个岔口一起关了"
    # 见够次数就退场，而且 seen 只增不减，所以后面那一幕的门仍然开着
    lg_r = {"runs": 30, "cycle": 1, "history": [], "skills": {}, "aug": 0,
            "sub": "圣殿派", "lean_run": 30,
            "world": _default_world(), "memory": {"entries": [], "pending": None}}
    lg_r["world"]["seen"] = {"temple_scripture": 3, "elevator_preacher": 4}
    save_legacy(lg_r)
    gr2 = Game(); gr2.new_run(seed=112, mode="story")
    while gr2.state["pending"].startswith("lean_"):
        gr2.choose(1)
    gr2.state["used_events"] = []
    gr2.state["faction"] = "purist"; gr2.state["sub"] = "圣殿派"; gr2.state["aug"] = 0
    scr = next(e for e in EVENTS if e["id"] == "temple_scripture")
    vault = next(e for e in EVENTS if e["id"] == "temple_vault")
    assert not gr2._eligible(scr), "见过三次的抄经课还在抽"
    assert gr2._eligible(vault), "抄经课退场之后，密室的门跟着关了"
    # keep_until：攥着钥匙的那两幕，钥匙没交出去之前不许退场
    prea = next(e for e in EVENTS if e["id"] == "elevator_preacher")
    deat = next(e for e in EVENTS if e["id"] == "preacher_death")
    assert prea.get("retire_keep_until") == "entered_chapel", "电梯那一幕的钥匙没登记"
    assert deat.get("retire_keep_until") == "denied_the_leaf", "临终那一幕的钥匙没登记"
    w_key = {"seen": {"elevator_preacher": 9, "preacher_death": 9}, "deeds": {}}
    assert not _is_retired(prea, w_key), "从没跟进过教堂，电梯那一幕却已经退场"
    assert not _is_retired(deat, w_key), "从没问过金叶片，临终那一幕却已经退场"
    w_key["deeds"] = {"entered_chapel": 1, "denied_the_leaf": 1}
    assert _is_retired(prea, w_key) and _is_retired(deat, w_key), \
        "钥匙交出去了，这两幕却还赖着不走"
    gr2.state["world"] = w_key
    assert not gr2._eligible(prea), "钥匙拿到手了，电梯那一幕还在抽"
    # 碎片门票保护：没拿到对应碎片时，即使原退场条件已经满足也不许退场；
    # 碎片拼入之后才恢复原条件。FRAGMENT_TICKETS 是唯一关系表。
    for fid, ticket in FRAGMENT_TICKETS.items():
        for eid in ticket["events"]:
            guarded = next(e for e in EVENTS if e["id"] == eid)
            w_frag = _default_world()
            w_frag["seen"][eid] = max(999, guarded.get("retire_seen") or 0)
            if guarded.get("retire_deed"):
                w_frag["deeds"][guarded["retire_deed"]] = 1
            if guarded.get("retire_keep_until"):
                w_frag["deeds"][guarded["retire_keep_until"]] = 1
            assert not _is_retired(guarded, w_frag), \
                "碎片 %s 还没拿到，相关事件 %s 却退场了" % (fid, eid)
            w_frag["fragments"].append(fid)
            assert _is_retired(guarded, w_frag), \
                "碎片 %s 已拿到，相关事件 %s 却没有恢复原退场规则" % (fid, eid)
    # 全退场之后这一世仍然走得下去（天气还在），并且不崩
    w_all = _default_world()
    w_all["seen"] = {e["id"]: (e.get("retire_seen") or 0)
                     for e in EVENTS if e.get("retire_seen")}
    # **故意不给 hymn_done** —— 这一份档案没走过金叶子那条路，
    # 所以够不着全书终，见底之后走的是临终那一支。
    w_all["deeds"] = {"cc_gone": 1, "library_closed": 1, "dog_over": 1,
                      "entered_chapel": 1, "denied_the_leaf": 1}
    # 这段验证见底分流，不验证碎片入口；先解除碎片相关事件的退场保护。
    # 同时把渡口标成刚走过，免得五碎片先把 new_run 改道到终局。
    w_all["fragments"] = list(FRAGMENT_ORDER)
    w_all["final_done"] = True
    w_all["final_runs"] = 90
    for e_a in EVENTS:
        if e_a.get("retire_deed") or e_a.get("retire_seen"):
            w_all["seen"].setdefault(e_a["id"], 1)
    assert not _story_done(w_all), "没走金叶子那条路，不该算全书终"
    lg_all = {"runs": 90, "cycle": 1, "history": [], "skills": {}, "aug": 0,
              "sub": None, "lean_run": 90, "world": w_all,
              "memory": {"entries": [], "pending": None}}
    save_legacy(lg_all)
    # （deed 型那几条：hymn 没给，所以不是「全部退场」，但这具 0% 的身体确实走干了）
    # 见底之后：0% 的身体不再空转，而是走到临终那一幕
    g_all = Game()
    out_all = g_all.new_run(seed=113, mode="story")
    assert "临 终" in out_all and "穷尽世间一切可能" in out_all, \
        "0% 见底了却还在空转（作者报的锁死）"
    assert g_all.state.get("deathbed"), "临终那一幕没有接管这一局"
    # 拒绝 → 落幕，而且落了幕就没有下一世
    g_no = Game()
    out_no = g_no.choose(2)
    assert "晚安" in out_no, "拒绝改造却没有落幕"
    assert "晚安" in Game().new_run(seed=1, mode="story"), "落幕之后还能开新的一世"
    # 接受 → 往上走一档，纯血那条路关上；上面那一档也见底了就直接落幕到星海
    lg_yes = load_legacy(); lg_yes["world"]["curtain"] = None
    lg_yes["aug"] = 0; save_legacy(lg_yes)
    g_yes = Game(); g_yes.new_run(seed=113, mode="story")
    out_yes = g_yes.choose(1)
    assert "你点了头" in out_yes, "接受改造却没有下文"
    assert (load_legacy().get("aug") or 0) > 0, "接受了改造机化率却没动"
    # 没走过金叶子那条路 —— 上面那一档也走干了，先给那道岔路，选「到此为止」才落幕
    assert "走 不 动 了" in out_yes, "上面那一档也走干了，却没给那道岔路"
    assert "繁星" in g_yes.choose(2), "选了到此为止却不是繁星"
    # 本支讲完了、同阵营另一支还有戏 —— 该重问三题，不该落幕
    #（DeepSeek 抓到的边界：不问就落幕会把另一支整条吞掉）
    w_half = _default_world()
    w_half["seen"] = {e["id"]: (e.get("retire_seen") or 0)
                      for e in EVENTS if e.get("retire_seen")}
    for eid_keep in ("mask_atelier", "mask_gallery", "mask_rehearsal",
                     "mask_inheritance", "mask_null"):
        w_half["seen"][eid_keep] = 0          # 面具沙龙整条还在
    w_half["deeds"] = {"hymn_done": 1, "cc_gone": 1, "library_closed": 1,
                       "dog_over": 1, "entered_chapel": 1, "denied_the_leaf": 1}
    # 这段只测同档换派系；解除碎片入口保护并跳过刚走完的渡口。
    w_half["fragments"] = list(FRAGMENT_ORDER)
    w_half["final_done"] = True
    w_half["final_runs"] = 60
    lg_half = {"runs": 60, "cycle": 1, "history": [], "skills": {}, "aug": 20,
               "sub": "灰港", "lean_run": 60, "world": w_half,
               "memory": {"entries": [], "pending": None}}
    save_legacy(lg_half)
    g_half = Game()
    out_half = g_half.new_run(seed=116, mode="story")
    assert "落 幕" not in out_half, "灰港讲完了，面具沙龙还在，却直接落幕了"
    assert g_half.state["pending"].startswith("lean_"), "该重问三题，好换一支"
    # 答成还是灰港 → 这一支真的没有了 → 落幕；答成面具沙龙 → 接着演
    def _answer(g_x, want_first_sub):
        """先过「答案变了没有」，再把三题全答成同一边。"""
        out_x = g_x.choose(2)             # 重新答一遍
        pick = 1 if want_first_sub else 2  # 三题：a 记第一支，b 记第二支
        for _ in range(6):
            if g_x.state["over"] or not g_x.state["pending"].startswith("lean_"):
                break
            out_x = g_x.choose(pick)
        return out_x
    def _fresh_half():
        save_legacy(json.loads(json.dumps(lg_half)))
        gx = Game(); gx.new_run(seed=116, mode="story")
        return gx
    first_sub = FACTIONS["discreet"]["sub"][0][0]
    # ㈠ 直接说「答案没变」——这一支已经讲完，那就落幕
    g_same2 = _fresh_half()
    out_same2 = g_same2.choose(1)
    # 这一档走干了，但别的档还有 —— 先问「交上去还是到此为止」，不直接落幕
    assert "走 不 动 了" in out_same2, \
        "答案没变、这一支又讲完了，却没给那道岔路：%s" % out_same2[-120:]
    assert "落 幕" in g_same2.choose(2), "选了到此为止却没有落幕"
    assert (load_legacy() or {}).get("sub") == "灰港", "说了没变，派系却变了"
    # ㈡ 重答，仍然答成灰港 —— 一样落幕
    g_keep = _fresh_half()
    out_keep = _answer(g_keep, first_sub == "灰港")
    assert "走 不 动 了" in out_keep or "落 幕" in out_keep, \
        "答完还是灰港，这一支已经讲完，却什么也没给：%s" % out_keep[-120:]
    # ㈢ 重答，换成另一支 —— 接着演
    g_swap = _fresh_half()
    out_swap = _answer(g_swap, first_sub != "灰港")
    assert "落 幕" not in out_swap, "换到另一支了却还是落幕"
    assert (load_legacy() or {}).get("sub") != "灰港", "答成了另一支，派系却没换"

    lg_z = load_legacy(); lg_z["world"]["curtain"] = None
    lg_z["history"] = [{"run": 1, "faction": "纯血誓约", "sub": "圣殿派", "aug": 0,
                        "cause": "finale", "kept_pts": 0, "total_pts": 0}]
    save_legacy(lg_z)
    info_z = Game().legacy_info()
    seen_z, todo_z = _story_progress(load_legacy()["world"])
    assert "走过的线：%d/%d" % (seen_z, todo_z) in info_z, "档案里没有印进度"
    assert "金叶子" in info_z, "档案里没说全书终要什么"
    # 牌堆永远不涨：没有合格的牌时，洗回来的那一副不能和「跳过但不弃」的那一摞叠加
    lg_dk = {"runs": 40, "cycle": 1, "history": [], "skills": {}, "aug": 0,
             "sub": None, "lean_run": 40, "world": w_all,
             "memory": {"entries": [], "pending": None}}
    save_legacy(lg_dk)
    g_dk = Game(); g_dk.new_run(seed=114, mode="story")
    n_gen = len(g_dk._generic_ids())
    for _ in range(30):
        g_dk._draw_generic()
        g_dk._draw_faction()
    lg_after = load_legacy()
    assert len(lg_after.get("deck") or []) <= n_gen, \
        "通用牌堆涨到了 %d 张（一共才 %d 张）" % (len(lg_after["deck"]), n_gen)
    assert len(set(lg_after.get("deck") or [])) == len(lg_after.get("deck") or []), \
        "通用牌堆里有重复的牌"
    fac_cards = (lg_after.get("deck_fac") or {}).get("cards") or []
    assert len(set(fac_cards)) == len(fac_cards), "派系牌堆里有重复的牌"
    SAVE_DIR, LEGACY_PATH, CURRENT_PATH = keep27
    # 走不动了那道岔路：交上去 → 封档 ＋ 湖；到此为止 → 繁星
    lg_dc = {"runs": 40, "cycle": 1, "history": [], "skills": {}, "aug": 55,
             "sub": None, "lean_run": 40, "world": json.loads(json.dumps(w_half)),
             "memory": {"entries": [], "pending": None}}
    lg_dc["world"]["seen"] = {e["id"]: (e.get("retire_seen") or 0)
                              for e in EVENTS if e.get("retire_seen")}
    lg_dc["world"]["deeds"] = {"hymn_done": 1, "dog_over": 1, "library_closed": 1,
                               "cc_gone": 1, "entered_chapel": 1,
                               "denied_the_leaf": 1}
    # 这具 55% 的身体走干了，但**纯血那一档还整条留着** —— 世界没讲完
    lg_dc["world"]["seen"]["temple_scripture"] = 0
    lg_dc["world"]["seen"]["temple_trial"] = 0
    save_legacy(lg_dc)
    g_dc = Game(); g_dc.new_run(seed=120, mode="story")
    # 深夜敲门是唯一不退场的那一幕，而它要疑云 ≥3 —— 疑云清零才算真的走干
    g_dc.state["heat"] = 0
    assert g_dc._dry_curtain() == "stars", "这具身体走干了却不算见底"
    g_dc.state["drychoice"] = True
    out_up = g_dc._drychoice(1)
    assert "封档" in out_up, "交上去却没有封档"
    assert (load_legacy()["world"].get("lake")), "交上去之后湖没有等在那儿"
    save_legacy(lg_dc)
    g_dc2 = Game(); g_dc2.new_run(seed=120, mode="story")
    g_dc2.state["heat"] = 0; g_dc2.state["drychoice"] = True
    assert "繁星" in g_dc2._drychoice(2), "选了到此为止却不是繁星"

    # 上面那一档还有戏时，走不动了给的是「再往前一寸」，不是「交上去」——
    # 见底那一世一幕也发不出来，岔口没机会出现，
    # 不还这一寸的话档案只能 0% ↔ 100% 来回跳，中间两档一辈子遇不到。
    lg_st = {"runs": 40, "cycle": 1, "history": [], "skills": {}, "aug": 20,
             "sub": None, "lean_run": 40,
             "world": json.loads(json.dumps(lg_dc["world"])),
             "memory": {"entries": [], "pending": None}}
    # 心照那一档整条讲完，明焰那一档整条留着
    for e in EVENTS:
        if e.get("subs") and set(e["subs"]) & {"学院派", "平权阵线"}:
            lg_st["world"]["seen"][e["id"]] = 0
    save_legacy(lg_st)
    g_st = Game(); g_st.new_run(seed=121, mode="story")
    g_st.state["heat"] = 0
    assert g_st._next_band_with_story() == "open", \
        "明焰整条还在，却说上面没有戏了"
    assert "再往前一寸" in g_st._dry_offer_text(), "上面还有戏，给的却是交上去"
    g_st.state["drychoice"] = True
    out_step = g_st._drychoice(1)
    assert "封档" not in out_step, "往前一寸不该封档"
    assert (load_legacy() or {}).get("aug") >= AUG_OF["明焰"], \
        "往前一寸却没跨进明焰：%s" % (load_legacy() or {}).get("aug")
    assert "明焰" in out_step, "跨过去了却没在那一世的开头说清楚"
    # 已经是最上面一档了 —— 那就还是「把自己交上去」
    lg_top = json.loads(json.dumps(lg_st)); lg_top["aug"] = 80; lg_top["sub"] = None
    save_legacy(lg_top)
    g_top = Game(); g_top.new_run(seed=122, mode="story")
    assert g_top._next_band_with_story() is None, "飞升上面还能再往前一档？"
    assert "把自己交上去" in g_top._dry_offer_text(), "最上面那一档却还在劝人往前一寸"

    print("退场表验证通过（%d 条线会讲完/八条派系线一条不漏/只剩深夜敲门是天气/"
          "406 只有一夜/眼睛问三次就完/退场不锁后面的门/"
          "本支讲完先重问三题而不是落幕/答完还是这一支才落幕/"
          "见底之后 0%% 走临终、动过刀的先问要不要交上去、一条不剩落全书终）。"
          % len(todo))

    print("楼下的歌声验证通过（三段的门/敲门要上过教堂/巷子的两道门/"
          "讲完就退场/全书终只念一次且要先走完金叶子那条路）。")

    print("第八轮试玩反馈回归通过（唯一一次岔口拒绝后不再问/熟悉度压缩折场景不折回响/"
          "干到什么程度那一行就说什么/重问先问一句变没变）。")
    print("第七轮试玩反馈回归通过（跨档换营那一栏归零/数值报实际增量/"
          "0% 落笔不数格子/岔口结果不逐字重放）。")
    print("河堤验证通过（四段弧线的门/歌手那一项的选项级门/手术之后线就关了/"
          "忘川才让她从第 0 次重来/双六顶格不空挂/书只卖一次）。")

    print("第六轮试玩反馈回归通过（机化率分档/求救芯片有出口/CLI 能开口/碎片提示对得上闸门）。")

    print("第五轮试玩反馈回归通过（战报报实际结算值/誓约认得自己那条豁免/"
          "临终只给进过教堂的人/每世只有一次岔口）。")






    print(GAME.legacy_info())

# ---------------------------------------------------------------------------
# 语言层
#
# 中文是源头，这个文件不因翻译改动一个字。译文活在 <lang>/对照-*.md 里，
# 由 langpack 在这里装进内存 —— 位置在**全部表定义完之后、main() 之前**。
# （门禁的关键词表定义在文件末尾，装早了它们还不存在。2026-08-10 踩过。）
#
#     THESEUS_LANG=en python3 server.py
#
# 装不上就回落中文。**语言层坏掉不许让游戏打不开。**
# ---------------------------------------------------------------------------

if LANG != "zh":
    try:
        sys.path.insert(0, BASE_DIR)
        import langpack as _langpack
        _langpack.install_or_fallback(globals(), LANG)
    except Exception as _e:      # 连 langpack 都导不进来
        sys.stderr.write("⚠ 语言层不可用，全部回落中文：%r\n" % _e)


def main():
    ap = argparse.ArgumentParser(description="《忒修斯之脑》 MCP 游戏服务器")
    ap.add_argument("--cli", action="store_true", help="终端交互试玩")
    ap.add_argument("--selftest", action="store_true", help="随机自动游玩自测")
    ap.add_argument("--coverage", nargs="?", const=2000, type=int, default=None,
                    metavar="N", help="随机跑 N 世（默认 2000），报告没被读到过的文案")
    ap.add_argument("--replay", metavar="JSON", default=None,
                    help="照着 status 里那行「完整重放」重跑一局（用来复现别人报的 bug）")
    args = ap.parse_args()
    if args.replay:
        run_replay(args.replay)
    elif args.coverage:
        run_coverage(args.coverage)
    elif args.selftest:
        run_lang_selftest() if LANG != "zh" else run_selftest()
    elif args.cli:
        run_cli()
    else:
        serve_stdio()

if __name__ == "__main__":
    main()
