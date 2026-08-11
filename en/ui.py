# -*- coding: utf-8 -*-
"""English interface layer for 《忒修斯之脑》 / The Brain of Theseus.

由 langpack 在 server.py 装载完全部文案表之后调用 apply(srv)。

分工：
  - 剧情（1273 条）走 ../en/对照-*.md
  - 界面（格式串、专名、工具说明、菜单）走这里
    —— 因为界面这些字大半带 % 占位符和首尾空格，Markdown 表格存不住。

三条排版约定（`STYLE.md` §1 的规矩落到这个游戏上）：
  【标签】  → 行首 `TAG —`（大写＋破折号），行内 `**Tag**`
  〔旁白〕  → `*italics*` —— 引擎自己的低语，与规则说明分开
  （说明）  → `(...)` —— 规则与旁注
  引号一律弯引号；破折号 em dash 前后无空格。
"""


# ---------------------------------------------------------------------------
# 专名。左边是引擎内部键（永远是中文，存档与 fx 全靠它），右边只是显示名。
# ---------------------------------------------------------------------------

NAMES = {
    # 技能
    "逻辑": "Logic",
    "共情": "Empathy",
    "威慑": "Menace",
    "巧手": "Sleight",
    "坚忍": "Endurance",
    "街智": "Streetwise",
    "机械亲和": "Machine Affinity",
    "电子直觉": "Electronic Intuition",

    # 阵营
    "纯血誓约": "The Blood Covenant",
    "心照不宣": "The Unspoken",
    "明焰": "The Open Flame",
    "飞升螺旋": "The Ascension Spiral",

    # 派系
    "圣殿派": "The Temple",
    "铁锤派": "The Hammer",
    "面具沙龙": "The Masque",
    "灰港": "Ashport",
    "学院派": "The Academy",
    "平权阵线": "The Parity Front",
    "群智派": "The Swarm",
    "播种者": "The Sowers",

    # 两个名字的同一个数值
    "疑云": "Suspicion",
    "锚重": "Ballast",

    # 时代
    "平静之年": "The Quiet Year",
    "倍税之年": "The Year of the Double Tax",
    "停电之冬": "The Winter of the Blackout",
    "清洗潮": "The Purge",
    "发射之年": "The Year of the Launch",
    "名单余波": "After the List",
    "抗税余波": "After the Tax Riots",
    "燎原之年": "The Year of the Prairie Fire",
    "修约之春": "The Spring of the Amended Covenant",

    # 真相碎片
    "血的证词": "The Testimony of Blood",
    "接缝之书": "The Book of Seams",
    "明账": "The Open Ledger",
    "点名册": "The Roster",
    "船坞": "The Drydock",

    # 成就名（gift 说明文字走对照文件，第 8 步）
    "谟涅摩绪涅": "Mnemosyne",
    "大地与星空之子": "Child of Earth and Starry Heaven",
    "平视": "Eye to Eye",
    "灰潮": "The Grey Tide",
    "码头长": "Harbormaster",
    "灯守": "Lampkeeper",
    "留一页，动手": "Leave a Page, Then Act",
    "第七粒种子": "The Seventh Seed",
    "两度": "Two Degrees",
    "底册": "The Roll Beneath",
    "地基": "Bedrock",
    "待定义": "Undefined",
    "验收标准第七条": "Acceptance Criterion Seven",
    "在场": "Present",
    "支点": "Fulcrum",
    "第二张脸": "The Second Face",
    "空面": "The Empty Face",
    "铁与肉": "Iron and Flesh",
    "盐与铁": "Salt and Iron",
    "归与跻": "Received and Achieved",
    "清白之躯": "The Unblemished Body",
    "摆渡熟客": "The Ferry Regular",
    "犬之世交": "Old Friend of the Dogs",
    "诚实的世系": "An Honest Line",
    "满溢之杯": "The Overflowing Cup",
    "燎原世代": "The Prairie-Fire Generation",
    "修约者": "The Amender",

    # 结局归类
    "身死": "died",
    "暴露": "exposed",
    "终幕": "the finale",
    "走完终幕": "ran the finale",
    "渡口表态": "spoke at the crossing",
    "真相": "the truth",
}

# 阵营与派系的介绍（出生时念一次）。
# ⚠️ 键必须是**介绍原文**，不能是阵营名 —— 名字那一栏在 NAMES 里，
# 用同一个键会把名字覆盖成介绍（2026-08-10 踩过：状态条上整段介绍当成了阵营名）。
FACTION_DESC = {
    "肉身神圣，不可增删一钉一铆。他们称改造者为「空壳」。":
        "The flesh is holy: not one rivet added, not one taken away. "
        "They call the augmented husks.",
    "改造可以，别让人看出来。体面是唯一的教义。":
        "Augment all you like—just don’t let it show. Decency is the only doctrine.",
    "光明正大地改造，越强越好。身体是可以公开迭代的作品。":
        "Augment in the open, and the stronger the better. "
        "The body is a work you may iterate on in public.",
    "全部机械飞升。肉是过渡态，是脚手架，是待拆的包装。":
        "Ascend into machine, all of it. Flesh is a transitional state, a scaffold, "
        "packaging waiting to come off.",
    "以旧日宗教残章立誓，相信灵魂居于完整的血肉之中。":
        "They swear on the surviving pages of the old religion, and hold that the soul "
        "lives in flesh that is whole.",
    "武装清洗队。他们不辩论，他们拆解。":
        "Armed purge squads. They do not argue. They disassemble.",
    "上流社会的暗语俱乐部，义眼藏在虹膜纹理之下。":
        "A code-word club for the upper city. The prosthetic eye hides under the grain "
        "of the iris.",
    "走私码头与黑诊所，麻醉剂和固件补丁一起出售。":
        "Smuggling docks and back-room clinics, where the anesthetic and the firmware "
        "patch are sold across the same counter.",
    "改造伦理委员会与论文工厂，升级需要引用格式。":
        "Augmentation ethics boards and paper mills. An upgrade requires a citation format.",
    "街头运动者，为最穷的人争取最基础的义肢。":
        "Street organizers, fighting to get the poorest a basic limb.",
    "把意识接入合流网络，练习使用「我们」这个人称。":
        "They splice consciousness into the confluence network and practice using the "
        "pronoun “we.”",
    "要把心智压缩进探针，射向星海。":
        "They mean to compress a mind into a probe and fire it at the sea of stars.",
}


# 事迹与事件的显示名（legacy 的「事迹低语」「走过的线」用）。
# 键是引擎里的 id，装载时按 server.py 的中文名换成英文。
DEED_NAMES = {
    "honest": "a record of paying tax honestly",
    "temple_vault": "went into the Temple’s locked room",
    "front_triage": "worked triage at a free clinic",
    "secret_friend": "hid a length of metal for a friend",
    "betrayer": "reported someone under the covenant",
    "informer": "warned a clinic before it was smashed",
    "reformer": "rewrote a rule that was set in stone",
    "riot": "led the shouting in the crowd",
    "archive": "copied out what the fire had left",
    "gave_it_away": "gave a blind old man everything in your pocket",
    "duet": "sang with someone in the underpass",
    "dog_friend": "fed a dog with a broken leg",
    "became_dog": "lived once in a way that was not human",
    "temple_doubt": "hesitated over your own doctrine",
    "temple_heretic": "copied down a different word in private",
    "temple_revealed": "opened that locked room in public",
    "merged": "ran in parallel with another mind",
    "ascended": "moved yourself up there whole",
    "favor_elite": "took the hit once for a decent person",
    "hymn_joined": "went downstairs and sang along once",
    "cc_named": "got a child to tell you their name",
    "still_asking": "came away with a chip that was still calling for help",
    "clause_used": "opened the covenant and read someone the clause",
    "tax_hand": "pointed at the inspector’s injured hand",
    "harbor_run": "ran a load for Ashport",
    "drank_with_singer": "crouched on the ground and drank with someone",
}

EVENT_NAMES = {
    "old_singer": "sang with the old singer in the underpass",
    "old_singer_high": "heard that singer in the underpass",
    "night_library": "went up on the roof of the night library",
    "ferry_night": "rode the last ferry",
    "riverbank": "went out to the embankment",
}


# ---------------------------------------------------------------------------
# 界面文案。键必须与 server.py 里 T("…") 的原文一字不差 —— `langpack.py check_ui`
# 会逐条核对，并核对 % 占位符的个数与顺序。
# ---------------------------------------------------------------------------

TEXT = {
    # ---- 标点与连接符 ----
    "、": ", ",
    "「%s」": "“%s”",
    "（×50%）": " (×50%)",

    # ---- 状态条与战报 ----
    "开局": "Opening",
    "第 %d/%d 幕": "Scene %d/%d",
    "第 %d/%d 幕 · 续": "Scene %d/%d · continued",
    "%s · %s·%s · 机化 %d%% · 身体 %d/%d · %s %d/8":
        "%s · %s·%s · Mechanization %d%% · Body %d/%d · %s %d/8",
    "第 %d/%d 幕 · %s · 机化%d%%": "Scene %d/%d · %s · Mechanization %d%%",
    "─── 念给人类的部分 ───": "─── READ THIS PART ALOUD ───",
    "机化%+d%%": "Mechanization %+d%%",
    "身体%+d": "Body %+d",
    "技能：": "Skills: ",
    "%s%d": "%s %d",
    "%s%+d": "%s %+d",
    "%s+%d": "%s +%d",
    "%s %d/%d": "%s %d/%d",
    "%s %+d → %d": "%s %+d → %d",
    "%s %+d → %d/8": "%s %+d → %d/8",
    "〔%s〕": "*[%s]*",
    "，": ", ",
    "／": " / ",
    "选了：%s": "Chose: %s",
    "变化：%s": "Changes: %s",
    "上一步：%s": "Last step: %s",
    "渡口。没有检定，只有表态。": "The crossing. No checks here—only where you stand.",
    "这一世已经结束。用 debrief 取战报。": "This life is over. Use debrief for the report.",
    "无": "none",
    "（空）": "(empty)",
    "〔第%d世〕%s": "*[life %d]* %s",

    # ---- 开局 ----
    "  《 忒 修 斯 之 脑 》 第 %d 谱系 · 第 %d 世（累计第 %d 世）":
        "  T H E   B R A I N   O F   T H E S E U S — line %d · life %d (life %d in all)",
    "时代骰落下：【%s】": "The era die falls: **%s**",
    "  时代：%s": "  Era: %s",
    "你生下来是一副没有改过的身体。全城大多数人都是这样开始的。":
        "You are born into a body no one has altered. Most of the city starts here.",
    "你又一次生在一副没有改过的身体里。": "Once again you are born into a body no one has altered.",
    "你带着上一世的 %d%% 醒来。改造不会随普通死亡归零。":
        "You wake carrying %d%% from your last life. Ordinary death does not zero the work out.",
    "现在的你 —— 【%s · %s】": "What you are now — **%s · %s**",
    "初始机化率：%d%%    身体：%d/%d": "Starting mechanization: %d%%    Body: %d/%d",
    "（铭记：改造是单向的。肉一旦让位，不会回来——义体从不退货。）":
        "(Remember: augmentation runs one way. Flesh that gives ground does not come back—"
        "no prosthetic is ever returned.)",
    "  在这个阵营里，没人会为你多懂一些东西而皱眉。":
        "  In this faction nobody frowns at you for knowing a little too much.",
    "  但在这个阵营里，这些「不该会的东西」是危险的。初始疑云：%d/8":
        "  But in this faction, knowing what you should not know is dangerous. "
        "Starting Suspicion: %d/8",
    "【残响】前世的技艺穿过死亡跟了过来：":
        "ECHO — Skills from an earlier life followed you through death:",
    "【湮灭】上一世什么也没能留下。纯粹的血肉，纯粹的遗忘。你从零开始。":
        "ANNIHILATION — Nothing survived the last life. Pure flesh, pure forgetting. "
        "You begin at zero.",
    "【过河】你带着记忆，落在一具没有一钉一铆的身体里。":
        "CROSSED OVER — You come across carrying your memory, and land in a body "
        "without a single rivet in it.",
    "【未落笔】第%d世死时你没有写下任何词条。掷骰照掷：%d 条旧记忆存活，%d 条湮灭。":
        "NOTHING WRITTEN — You died in life %d without setting down a single entry. "
        "The dice roll anyway: %d old memories survive, %d are annihilated.",
    "【记忆】穿过死亡的词条（%d/%d）：":
        "MEMORY — Entries that came through death (%d/%d):",
    "  （这些是历世的你亲手写下的。没有上下文，没有出处，也没有人替你核对。）":
        "  (Earlier versions of you wrote these by hand. No context, no source, "
        "and nobody to check them against.)",
    "【记忆】一条也没有。你不知道自己以前是谁。":
        "MEMORY — Not one entry. You do not know who you used to be.",
    "真相碎片：%d/%d（详见 legacy）": "Fragments of the truth: %d/%d (see legacy)",
    "在开始之前，有三个问题。没有对错，只是问问你自己。":
        "Three questions before we begin. There is no right answer; they are only for you.",
    "你在这一档待了几世了。有三个问题，上一次也问过——\n隔了这么久，答案未必还是同一个。":
        "You have spent several lives in this band now. Three questions, the same three "
        "as last time—\nafter this long, the answers may not be.",
    "三 问": "T H R E E   Q U E S T I O N S",
    "〔%d 比 %d。这一边的你是【%s】——%s〕":
        "*%d to %d. On this side you are* **%s** — *%s*",

    # ---- 渡魂签（wish） ----
    "【渡魂签】签上没有这个去处。可写：纯血誓约 / 心照不宣 / 明焰 / 飞升螺旋。\n这一世还没开始。":
        "FERRY LOT — The lot has no such destination on it. Write one of: "
        "The Blood Covenant / The Unspoken / The Open Flame / The Ascension Spiral.\n"
        "This life has not started.",
    "【渡魂签】湖已经在等你。先在湖边作答；这一世还没开始。":
        "FERRY LOT — The lake is already waiting. Answer at the water first; "
        "this life has not started.",
    "【渡魂签】档案还没薄到能看见这张签。先继续走；这一世还没开始。":
        "FERRY LOT — The archive is not yet thin enough for the lot to show through. "
        "Keep walking; this life has not started.",
    "【渡魂签】空手的魂渡不了。待继承技艺为 0；这一世还没开始。":
        "FERRY LOT — Empty-handed souls do not get ferried. "
        "You have 0 skill points waiting to be claimed; this life has not started.",
    "【渡魂签】签纸烧掉 %d 点待继承技艺，把这一世送往【%s】。":
        "FERRY LOT — The slip burns %d points of waiting skill and sends this life "
        "to **%s**.",

    # ---- 事件与选择 ----
    "用 choose 选择一个选项编号。": "Use choose with an option number.",
    "当前没有进行中的对局。用 new_run 掷骰开始新的一世。":
        "No life in progress. Use new_run to roll into a new one.",
    "内部错误：找不到当前事件。请 new_run 重开。":
        "Internal error: current scene not found. Please new_run to restart.",
    "这个选项当前不可选（未满足条件）。换一个。":
        "That option is not available right now (conditions unmet). Pick another.",
    "无效选项。请输入 1-%d。": "Invalid option. Enter 1-%d.",
    "无效选项。请输入 1-2。": "Invalid option. Enter 1-2.",
    "〔这一幕你已经走过几回了。场景略。〕":
        "*You have walked this scene several times. Scene omitted.*",
    "【回响】": "ECHO",
    "  〔%s 检定，难度 %d，当前 %s=%d〕":
        "  *%s check, difficulty %d, current %s=%d*",
    "（取高）": "(higher of the two)",
    "  〔抛硬币，五五开。技能不算数〕": "  *Coin flip, even odds. Skill does not count here*",
    "机化率达到 %d%%": "mechanization at %d%%",
    "%s 达到 %d": "%s at %d",
    "某一段旧事": "something that happened before",
    "特定的经历": "a particular experience",
    "（%d 次）": "(%d times)",
    "  %d. ✗ %s（不可选，需要 %s）": "  %d. ✗ %s (locked, requires %s)",
    "%s 检定%s": "%s check%s",
    "成功": " — success",
    "失败": " — failure",
    "硬币的正面": "the coin came up heads",
    "硬币的背面": "the coin came up tails",
    "无检定的选择": "no check",
    "正面": "heads",
    "背面": "tails",
    "抛硬币：%s": "Coin flip: %s",
    "掷骰：%d+%d +%s%d = %d  vs 难度%d —— %s":
        "Roll: %d+%d +%s %d = %d  vs difficulty %d — %s",
    "✦ 成功": "✦ success",
    "✧ 失败": "✧ failure",
    "掷骰：6+6 —— 【双六】命运替你多押了一注。无条件成功。":
        "Roll: 6+6 — DOUBLE SIX. Fate put down an extra bet on your behalf. "
        "Unconditional success.",
    "掷骰：1+1 —— 【蛇眼】骰子朝下的那一面，写着你的名字。无条件失败。":
        "Roll: 1+1 — SNAKE EYES. The face the dice landed on has your name written on it. "
        "Unconditional failure.",
    "其中 %s 的 +1 是双六给的": "the +1 to %s came from the double six",
    "%s 归零 —— 这一边数的不是同一件事":
        "%s reset to zero — this side is not counting the same thing",
    "机化率 %+d%% → %d%%": "Mechanization %+d%% → %d%%",
    "身体 %+d → %d/%d": "Body %+d → %d/%d",

    # ---- 岔口（改造机会） ----
    "─── 岔 口 ───": "─── T H E   F O R K ───",
    "机会又一次出现了，你决定改变现状吗？":
        "The opportunity comes around again. Do you want to change where you stand?",
    "  1. 维持现状": "  1. Leave things as they are",
    "  2. 重新考虑": "  2. Reconsider",
    "维持现状": "leave things as they are",
    "重新考虑": "reconsider",
    "重新考虑改造": "reconsider augmenting",
    "机会过去了。你维持了现状。": "The opportunity passes. You left things as they were.",
    "〔你拒绝了这一世唯一一次改造机会。这一世不会再有人问你。〕":
        "*You turned down the one chance to augment this life. Nobody will ask you again.*",
    "〔这一世你已经改造过一次。身体需要恢复——这一世不会再有机会了。〕":
        "*You have augmented once this life. The body needs to recover—"
        "there will be no further chance.*",
    "〔这一世唯一一次改造机会已经过去了。〕":
        "*The one chance to augment this life has already gone by.*",
    "岔 口": "T H E   F O R K",
    "─── 你 越 过 了 一 道 线 ───\n机化率 %d%%。从今往后，这座城把你归进【%s】——\n"
    "不是因为你申请了，是因为数字到了。（原先：%s）\n\n"
    "剩下的那个问题只有你自己能答：在这一边，你更像哪一种人。":
        "─── Y O U   H A V E   C R O S S E D   A   L I N E ───\n"
        "Mechanization %d%%. From here on the city files you under **%s**—\n"
        "not because you applied, but because the number got there. (Formerly: %s)\n\n"
        "The remaining question is one only you can answer: "
        "on this side, which kind of person are you more like.",
    "〔你去了。现在你是【%s】。〕": "*You went. You are* **%s** *now.*",
    "〔你没有去。你还是【%s】。〕": "*You did not go. You are still* **%s**.",
    "〔答案没变。你还是【%s】。〕": "*The answers have not changed. You are still* **%s**.",
    "旧账结清": "an old account settled",
    "旧账找上门": "an old account catching up",

    # ---- 一世终结 ----
    "═══════════ 本 世 终 结 ═══════════":
        "═══════════ E N D   O F   T H I S   L I F E ═══════════",
    "你的身体停机了。这座城市习惯了替死者收尾——":
        "Your body has shut down. The city is used to closing out its dead—",
    "回收者会在七十二小时内打捞死者的义体缓存，那是「忒修斯之脑」黑市档案的货源。":
        "within seventy-two hours the recyclers will dredge the prosthetic caches out of "
        "the corpse, which is where the black-market files of the Brain of Theseus come from.",
    "这一世走到了它的句点。": "This life has reached its full stop.",
    "终局清点：%s · %s，机化率 %d%%，历经 %d 幕。":
        "Final count: %s · %s, mechanization %d%%, %d scenes lived.",
    "─── 轮回结算 ───": "─── S E T T L E M E N T ───",
    "上载带走了一切。「忒修斯之脑」里属于你的那条世系，就此封档。":
        "The upload took everything. Your line in the Brain of Theseus is sealed here.",
    "%d 点技艺随你离开了轮回——城里连一份副本都没有留下。":
        "%d points of skill left the cycle with you—the city did not keep so much as a copy.",
    "下一个在此出生的，将是一个没有技艺的魂：第 %d 谱系，从零开始。":
        "The next one born here will be a soul with no skills at all: line %d, from zero.",
    "（封档带走的是**技艺**。你亲手写下的记忆词条不在其中——字是你自己刻的，档案收不走。）":
        "(What the seal takes is **skill**. The memory entries you wrote by hand are not "
        "in it—you cut those letters yourself, and the archive cannot collect them.)",
    "（世界的记忆也不随谱系归零：碎片、成就与回响都还在。）":
        "(The world’s memory does not zero out with the line either: fragments, "
        "achievements and echoes are all still there.)",
    "（处刑者焚毁了大部分记录，本世传承率减半。）":
        "(The executioners burned most of the records. Inheritance is halved this life.)",
    "传承概率 = 机化率 %d%%%s，逐点掷骰：":
        "Inheritance chance = mechanization %d%%%s, rolled point by point:",
    "纯粹的血肉没有备份。%d 点技艺随体温一起散去。":
        "Pure flesh keeps no backup. %d points of skill go the way of your body heat.",
    "什么也没有留下。船沉了，连一块木板都没有浮起来。":
        "Nothing is left. The ship went down and not one plank came up.",
    "骰运太差：%d 点技艺竟无一存续。机器也有失忆的夜晚。":
        "Terrible luck: not one of %d points survived. Machines have their amnesiac nights too.",
    "共 %d/%d 点技艺被蚀刻进「忒修斯之脑」，等待下一世认领。":
        "%d of %d points are etched into the Brain of Theseus, waiting to be claimed "
        "by the next life.",
    "━━━ 真相碎片 ·「%s」 ━━━": "━━━ FRAGMENT OF THE TRUTH · “%s” ━━━",
    "☑ 成就解锁 ·「%s」—— %s": "☑ Achievement unlocked · “%s” — %s",
    "真相碎片：%d/%d。仍然缺失的切面：":
        "Fragments of the truth: %d/%d. The faces still missing:",
    "五块碎片在档案深处咬合成一幅完整的图。下一次 new_run——渡口见。":
        "Deep in the archive the five fragments lock into one whole picture. "
        "Next new_run—see you at the crossing.",

    # ---- 记忆与落笔 ----
    "─── 记忆 ───": "─── M E M O R Y ───",
    "你手上一条历世词条也没有。": "You hold no entries from any life.",
    "你现在手上有 %d 条历世词条：": "You are holding %d entries from earlier lives:",
    "总额 %d 条，每条不超过 %d 字。想写新的而位置不够，就得亲手删掉一条旧的。":
        "%d entries in all, %d words each at most. If you want to write a new one and "
        "there is no room, you have to delete an old one by hand.",
    "落笔之后，每一条独立掷骰，存活概率＝本世机化率 %d%%。":
        "Once set down, each entry is rolled separately. Survival chance = this life’s "
        "mechanization, %d%%.",
    "（本世机化率 0%。你仍然可以写——写完再说。）":
        "(Mechanization is 0% this life. You can still write—we will talk after.)",
    "用 bequeath 落笔；直接 new_run 则视为放弃书写，旧词条照样掷骰。":
        "Use bequeath to set them down. Going straight to new_run counts as declining to "
        "write; the old entries are rolled all the same.",
    "存档已写入 saves/legacy.json。用 new_run 掷骰，转世投胎。":
        "Saved to saves/legacy.json. Use new_run to roll again and be reborn.",
    "现在没有可以落笔的时刻。词条只在一世终结之后、下一次 new_run 之前写。":
        "There is no moment to write in right now. Entries are written after a life ends "
        "and before the next new_run.",
    "─── 落笔 ───": "─── S E T T I N G   I T   D O W N ───",
    "装不下。总额 %d 条，你手上还留着 %d 条，又想写 %d 条，超出 %d 条。\n"
    "用 discard 把要删的旧词条原文列出来——一字不差地打出来才算数。\n\n你手上现有：\n%s":
        "It will not fit. %d entries in all; you are still holding %d and want to write %d, "
        "which is %d over.\nUse discard to list the old entries you want removed—"
        "they only count if you type them out word for word.\n\nYou are holding:\n%s",
    "这几条超了 %d 字，改短再来（空白不计）：\n":
        "These run over %d words. Shorten them and come back (whitespace does not count):\n",
    "  %d字  %s": "  %d words  %s",
    "要删的这几条我在你手上找不到，原文得一字不差（含标点）：\n":
        "I cannot find these among the entries you hold. The text has to match "
        "word for word, punctuation included:\n",
    "\n\n你手上现有：\n": "\n\nYou are holding:\n",
    "你亲手删掉了 %d 条：": "You deleted %d by hand:",
    "第%d世写下 %d 条：": "Life %d sets down %d:",
    "第%d世没有写下任何新词条。": "Life %d set down nothing new.",
    "你按下保存。": "You press save.",
    "……什么也没有发生。没有报错，没有进度条。":
        "…Nothing happens. No error, no progress bar.",
    "这一世的机化率是 0%。没有一寸非原生组织可以承载这些字，":
        "Mechanization is 0% this life. There is not one inch of non-native tissue to "
        "carry these words,",
    "没有缓存，没有备份，没有可供打捞的义体。%d 条词条——":
        "no cache, no backup, no prosthetic anyone could dredge up. %d entries—",
    "——连同写下它们的那个人，一起没有了。":
        "—gone, along with the one who wrote them.",
    "没有缓存，没有备份，没有可供打捞的义体。":
        "No cache, no backup, no prosthetic anyone could dredge up.",
    "也没有一个字需要它们承载。": "And not one word that needed carrying.",
    "人死如灯灭。": "A person dies the way a lamp goes out.",
    "逐条掷骰，存活概率 %d%%：": "Rolled one by one, %d%% to survive:",
    "  ✦ 存活  〔第%d世〕%s": "  ✦ survived  *[life %d]* %s",
    "  ✧ 湮灭  〔第%d世〕%s": "  ✧ annihilated  *[life %d]* %s",
    "%d 条穿过了这次死亡，%d 条没有。下一个你只会读到存活的那些，":
        "%d came through this death, %d did not. The next you will only read the ones "
        "that survived,",
    "而且不会知道曾经还有别的。": "and will never know there were others.",

    # ---- 战报 ----
    "还没有可以汇报的一生。": "No life to report on yet.",
    "─── 战报（可转述给人类）───": "─── REPORT (safe to relay to the human) ───",
    "第 %d 谱系 · 第 %d 世": "Line %d · life %d",
    "出身：%s · %s": "Born into: %s · %s",
    "机化率：%d%%    结局：%s": "Mechanization: %d%%    Ending: %s",
    "技艺：%d 点中留下 %d 点": "Skill: %d points, of which %d kept",
    "记忆词条：手上 %d/%d 条": "Memory entries: holding %d/%d",
    "真相碎片：%d/%d（正文不外传）":
        "Fragments of the truth: %d/%d (contents not relayed)",
    "（这份战报只报结构，不含场景原文。想看原文，把披露模式改回 open。）":
        "(This report gives structure only, no scene text. To see the text, "
        "set disclosure back to open.)",
    "（还没选过）": "(nothing chosen yet)",
    "复现：第 %d 世 · seed=%s · 本世选择=%s":
        "Reproduce: life %d · seed=%s · choices this life=%s",
    "完整重放：": "Full replay:",

    # ---- status ----
    "尚未开局。用 new_run 掷骰，开始第一世。":
        "No life started. Use new_run to roll into your first.",
    "终局已落幕。用 new_run 继续轮回，或用 legacy 查看世界的记忆。":
        "The endgame has closed. Use new_run to keep cycling, or legacy to look at "
        "the world’s memory.",
    "终局进行中。用 choose 表态。": "Endgame in progress. Use choose to say where you stand.",
    "第 %d 谱系 · 第 %d 世 · %s · %s%s": "Line %d · life %d · %s · %s%s",
    "（已终结）": " (ended)",
    "时代：【%s】%s": "Era: **%s** %s",
    "进度：第 %d/%d 幕    机化率：%d%%（不可逆）    身体：%d/%d    %s：%d/8":
        "Progress: scene %d/%d    Mechanization: %d%% (irreversible)    Body: %d/%d    %s: %d/8",

    # ---- legacy ----
    "「忒修斯之脑」还是一片空白。没有前世，没有残响。第一世由 new_run 开始。":
        "The Brain of Theseus is still blank. No earlier lives, no echoes. "
        "The first life starts with new_run.",
    "─── 忒修斯之脑 · 轮回档案 ───":
        "─── THE BRAIN OF THESEUS · CYCLE ARCHIVE ───",
    "第 %d 谱系 · 已历 %d 世。": "Line %d — lives so far: %d.",
    "      …（中间 %d 世略，完整记录在 saves/legacy.json）":
        "      …(%d lives omitted in the middle; the full record is in saves/legacy.json)",
    "第%d世 %s·%s  机化%d%%  结局:%s  传承 %d/%d 点":
        "Life %d  %s·%s  mech %d%%  ending: %s  inherited %d/%d",
    "前世残响：": "Echoes from earlier lives: ",
    "经历印记：": "Marks of experience: ",
    "待认领的残响：": "Echoes waiting to be claimed:",
    "待认领的残响：无。": "Echoes waiting to be claimed: none.",
    "渡魂签：可用。new_run(wish=阵营) 可定向投胎，需付 %d 点技艺（你有 %d 点，付完剩 %d）。":
        "Ferry lot: available. new_run(wish=faction) sends you where you choose, "
        "for %d points of skill (you have %d; %d left after).",
    "渡魂签：不可用。空手的魂渡不了——先攒一世技艺回来。":
        "Ferry lot: unavailable. Empty-handed souls do not get ferried—"
        "go earn a life’s worth of skill first.",
    "渡魂签：尚未显形。档案再薄一些，它才会浮出来。":
        "Ferry lot: not showing yet. The archive has to get thinner before it surfaces.",
    "─── 世界的记忆（不随轮回衰减，飞升归零也不清除）───":
        "─── THE WORLD’S MEMORY (does not decay with the cycle; ascension does not clear it) ───",
    "真相碎片 %d/%d：": "Fragments of the truth %d/%d:",
    "  ◆ 「%s」—— 已拼入": "  ◆ “%s” — in place",
    "     追踪：这副身体正落在它会回应的范围里。":
        "     Trace: this body is sitting in the range it answers to.",
    "     追踪：它的回声来自【%s】那一档。":
        "     Trace: its echo comes from the **%s** band.",
    "成就：": "Achievements: ",
    "已拼入的碎片名：": "Fragments in place: ",
    "事迹低语：": "Whispered deeds: ",
    "走过的线：%d/%d；已经讲完的：%d。":
        "Threads walked: %d/%d; threads finished telling: %d.",
    "  （讲完的不再出现。深夜的敲门声不算线，它是天气。）":
        "  (A thread that has finished telling does not come back. "
        "The knock late at night is not a thread. It is weather.)",
    "  （全书终要的是：每条线都走过一遍，而且走完金叶子那条路。）":
        "  (What the last page wants: every thread walked once, "
        "and the gold-leaf road walked to its end.)",
    "─── 渡口的表态（历次，不覆盖）───":
        "─── WHERE YOU STOOD AT THE CROSSING (every time; nothing overwritten) ───",
    "  （只列最近 6 次，此前还有 %d 次）":
        "  (showing the last 6; there were %d before that)",
    "  第%d世 · 「%s」": "  Life %d · “%s”",
    "  你在同一个问题上改过 %d 次主意。档案两条都留着。":
        "  You changed your mind on the same question %d times. The archive keeps both.",
    "当前底色：%s": "Ground tone right now: %s",
    "雾还需 %d 世重新聚拢，渡口才会再次浮现。":
        "The fog needs %d more lives to gather before the crossing surfaces again.",
    "雾已经拢起来了。下一次 new_run——渡口见。":
        "The fog has gathered. Next new_run—see you at the crossing.",
    "终局答案：%s —— %s": "Endgame answer: %s — %s",
    "五块碎片已咬合。下一次 new_run——渡口见。":
        "The five fragments have locked together. Next new_run—see you at the crossing.",

    # ---- 见底（讲完了） ----
    "〔这几世你什么也没留下。至少没有留下别人记得住的。〕":
        "*These lives left nothing behind. Nothing anyone else would remember, anyway.*",
    "〔你做得最多的几件事：%s。〕": "*The things you did most: %s.*",
    "〔没有了。一件也没有了。〕": "*There is nothing left. Not one thing.*",
    "〔你隐约觉得，下一世可能是最后一次了。〕":
        "*You have a faint sense that the next life may be the last one.*",
    "〔故事越来越少了。〕": "*There are fewer and fewer stories.*",
    "〔这座城今天没有新的事发生。你径直走到了这一世的尽头。〕":
        "*Nothing new happens in the city today. You walk straight on to the end of "
        "this life.*",

    # ---- MCP ----
    "忒修斯之脑 The Brain of Theseus": "The Brain of Theseus",
    "引擎故障：%r": "Engine fault: %r",
    "未知工具：%s": "Unknown tool: %s",
    "mode 只有这几种：story / story_ai / brief / brief_ai /\nauto / sealed。\n\n":
        "mode must be one of: story / story_ai / brief / brief_ai /\nauto / sealed.\n\n",
    "disclosure 只收 open 或 sealed。新写法请用 mode。这一世还没开始。":
        "disclosure takes only open or sealed. Use mode for the newer form. "
        "This life has not started.",
    "这是一个 roguelike 文字冒险游戏《忒修斯之脑》。你就是玩家。\n"
    "玩法：new_run 掷骰开局 → 阅读剧情 → choose 做出选择 → 直到本世终结。\n"
    "每一世结束时，你的技能按机化率逐点随机保存；下一世 new_run 时自动继承。\n"
    "机化率越高，跨越轮回保留的越多；纯血之躯什么也留不下。改造不可逆。\n"
    "世界另有一份不衰减的记忆：你做过的事会化作跨世回响、成就与时代变迁。\n"
    "以不同机化率通关不同阵营的真结局可获得真相碎片（legacy 可查线索），\n"
    "集齐五块后 new_run 将抵达终局。100% 飞升会封档整条世系，技能归零重启，\n"
    "但世界的记忆保留。后期可用渡魂签付出技艺，定向投胎到仍需追寻的机化档。\n"
    "请代入角色认真做选择，并在游玩时向用户转述剧情。\n"
    "**断线、重启、或者你不记得玩到哪儿了：先调 status。** 进行中的一世\n"
    "每一步都落盘，status 会把你停下的那一幕原样还给你。new_run 也盖不掉它。":
        "This is a roguelike text adventure called The Brain of Theseus. You are the player.\n"
        "The loop: new_run rolls you into a life → read the scene → choose → until the "
        "life ends.\n"
        "When a life ends, each point of skill is saved at random with a chance equal to "
        "your mechanization; the next new_run inherits what survived.\n"
        "The higher your mechanization, the more comes through the cycle; a body of pure "
        "flesh keeps nothing. Augmentation is irreversible.\n"
        "The world keeps a second memory that never decays: what you did returns as "
        "echoes across lives, as achievements, and as changes to the era.\n"
        "Reaching the true ending of different factions at different mechanization levels "
        "yields fragments of the truth (legacy has the clues).\n"
        "With all five, new_run arrives at the endgame. Ascending at 100% will seal the whole "
        "line, zero your skills and restart—but the world’s memory stays.\n"
        "Later on, a ferry lot lets you pay skill to be born into a band you still need.\n"
        "Play in character, choose in earnest, and relay the story to the user as you go.\n"
        "**Dropped connection, restart, or you have lost track of where you were: call "
        "status first.** A life in progress is written to disk at every step, and status "
        "hands you back the scene you stopped at, word for word. new_run cannot overwrite it.",

    # ---- 断线重连 ----
    "【这一世还没走完】\n"
    "断线、重启、换个客户端都不要紧，引擎替你记着。\n"
    "下面就是你停下的地方：照原样念给你的人类，然后用 choose 接着走。\n"
    "（真要弃掉这一世重新投胎：new_run(abandon=true)。**弃掉的一世\n"
    "不入档案** —— 技艺不传，词条不留，等于没活过。）\n":
        "THIS LIFE IS NOT OVER YET\n"
        "A dropped connection, a restart, a different client—none of it matters. "
        "The engine has been keeping your place.\n"
        "Here is where you stopped: read it to your human as it stands, then carry on "
        "with choose.\n"
        "(To truly abandon this life and be born again: new_run(abandon=true). "
        "**An abandoned life never enters the record**—no skill is passed on, no entry "
        "is kept. It is as though it were never lived.)\n",

    "〔上一世已弃：它没有进入档案，技艺与词条都不传。〕":
        "*(The last life was abandoned. It did not enter the record: no skill, no entry, "
        "nothing passed on.)*",

    "〔进行中的那一世没能恢复：游戏更新过，你停在的那一幕在新的\n"
    "  事件表里已经不存在了。那一档已经清掉。历世档案（机化率、\n"
    "  技艺、世界的记忆）完好无损 —— 用 new_run 重新投胎即可。〕":
        "*(The life in progress could not be restored: the game has been updated, and the "
        "scene you stopped at no longer exists in the new table of events. That save has "
        "been cleared. The record of past lives—mechanization, skill, the world’s "
        "memory—is untouched. Use new_run to be born again.)*",

    "〔上次退出时这一世还没走完 —— 接着走。想重开：q 退出，删掉 "
    "saves/current.json 再进来。〕":
        "*(This life was still unfinished when you left—picking it back up. To start over "
        "instead: q to quit, delete saves/current.json, and come back in.)*",
}


# ---------------------------------------------------------------------------
# 玩法菜单：第一次 new_run 不带 mode 时，引擎把这张菜单还给 AI，由 AI 念给人类。
# ---------------------------------------------------------------------------

MODE_MENU = """The Brain of Theseus—a text game for an AI and a human to play together.

One decision comes first, and it is the human’s.
Please read the menu below to your human **exactly as written**, wait for their pick,
and only then start the run:

  1  Full story   — I read you the text unchanged. Recommended for a first run.
  2  Quick run    — scenes read out in full, results given to you in two lines.
                    Pick this if you want to get through a life faster.
  3  Fast-forward — I handle everything and report only the two lines.
                    (No sub-choice here: the choosing has to be mine.)

After 1 or 2, ask one more question: **who makes the choices this life?**

  You choose — I read you the options each scene and wait for a number.
  I choose   — I play in character; you can cut in and overrule at any time.

Once they have answered:

  1 · you choose → new_run(mode="story")      1 · I choose → new_run(mode="story_ai")
  2 · you choose → new_run(mode="brief")      2 · I choose → new_run(mode="brief_ai")
  3               → new_run(mode="auto")

(If they say “I want to play this myself later, don’t spoil it,” use mode="sealed".)
If they are not around, or they say “you decide,” use mode="auto".
**Do not choose for them. This step is the one switch this game hands to the human.**"""

MODE_HINT = {
    "story": "[This run: full story · they choose] Read the scene, the options and the "
             "roll line to the human exactly as written, and let them give the number.",
    "story_ai": "[This run: full story · you choose] Read the scene and options to the "
                "human word for word—but **do not wait for them**. When you have finished "
                "reading, choose in character. If they cut in, follow them.",
    "brief": "[This run: quick run · they choose] Read the scene text as written; for the "
             "result, read only the two report lines below. The choice is theirs.",
    "brief_ai": "[This run: quick run · you choose] Read the scene text as written, report "
                "the result lines, and **make the choice yourself** without waiting.",
    "auto": "[This run: you play] No need to relay the scene. Read the human the one report "
            "line below, then choose.",
    "sealed": "[This run: sealed] Relay only the block below. If the human asks outright "
              "“what just happened,” answer honestly.",
}


# ---------------------------------------------------------------------------
# 工具说明。AI 客户端读到的第一段英文就是这些。
# ---------------------------------------------------------------------------

def _tools(srv):
    return [
        {
            "name": "new_run",
            "description": (
                "Begin a new life. **The faction is not rolled**—it is decided by your "
                "mechanization:\n"
                "0% The Blood Covenant / 1-39% The Unspoken / 40-69% The Open Flame / "
                "70-100% The Ascension Spiral.\n"
                "Mechanization accumulates across lives and only ever goes up; after every "
                "scene you are asked whether to go further.\n"
                "Crossing into a new band, three questions decide which side of that band "
                "you resemble.\n"
                "Skill echoes saved from your last life are inherited automatically. "
                "With all five fragments of the truth, you arrive at the endgame.\n"
                "Later on a ferry lot can aim your rebirth; whether it is available, and "
                "what it costs, is shown in legacy."),
            "inputSchema": {"type": "object",
                            "properties": {
                                "seed": {"type": "integer",
                                         "description": "random seed (optional)"},
                                "wish": {"type": "string",
                                         "enum": ["The Blood Covenant", "The Unspoken",
                                                  "The Open Flame", "The Ascension Spiral"],
                                         "description": ("ferry lot: aim your rebirth "
                                                         "(optional, costs skill that is "
                                                         "waiting to be inherited)")},
                                "mode": {"type": "string",
                                         "enum": ["story", "story_ai", "brief",
                                                  "brief_ai", "auto", "sealed"],
                                         "description": (
                                             "How to play. **Leave this out on your first "
                                             "call**—the engine returns a menu for you to "
                                             "read to your human, and they pick. "
                                             "story = full text / brief = quick run / "
                                             "auto = you play alone / sealed = spoiler-free. "
                                             "Once set, it sticks.")},
                                "disclosure": {"type": "string", "enum": ["open", "sealed"],
                                               "description": (
                                                   "Disclosure mode (sticky). open = relay "
                                                   "the text as written; sealed = every "
                                                   "output carries a structural summary "
                                                   "marked as the part to relay, and you "
                                                   "agree to relay only that. This is an "
                                                   "agreement, not a lock: if the human "
                                                   "asks outright what happened, answer "
                                                   "honestly.")}},
                            "required": []},
        },
        {
            "name": "choose",
            "description": ("Pick an option in the current scene (numbered from 1). "
                            "Options with a check roll 2d6 + skill against a difficulty."),
            "inputSchema": {"type": "object",
                            "properties": {"option": {"type": "integer",
                                                      "description": "option number"}},
                            "required": ["option"]},
        },
        {
            "name": "status",
            "description": ("Show the current life: faction, sub-faction, mechanization, "
                            "body, suspicion, skills, current scene and options."),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "recite",
            "description": (
                "Speak to the guardian at the lake. Appears only at the moment after you "
                "upload to 100% and your line is sealed.\n"
                "**The first step is two options**: recite(\"1\") or recite(\"2\"), matching "
                "the two lines printed at the water. After choosing 1, what you say next is "
                "free. Saying the wrong thing, saying only part of it, or going straight to "
                "new_run all count as walking to the other water—that one does not give the "
                "body back, but the lake will still be here after your next upload."),
            "inputSchema": {"type": "object",
                            "properties": {"text": {"type": "string",
                                                    "description": ("\"1\" or \"2\" for the "
                                                                    "first step; after that, "
                                                                    "what you want to say")}},
                            "required": ["text"]},
        },
        {
            "name": "bequeath",
            "description": (
                "After a life ends and before the next new_run, write down the memories that "
                "can cross death. %d entries in all (including those inherited from earlier "
                "lives), each at most %d words. When there is no room, you must use discard "
                "and type out the old entry word for word. Once set down, each entry is "
                "rolled separately; the chance of survival is this life’s mechanization. "
                "At 0%%, all of them are annihilated."
                % (srv.MEMORY_SLOTS, srv.MEMORY_CHARS)),
            "inputSchema": {"type": "object",
                            "properties": {
                                "entries": {"type": "array", "items": {"type": "string"},
                                            "description": ("new entries, each ≤%d words"
                                                            % srv.MEMORY_CHARS)},
                                "discard": {"type": "array", "items": {"type": "string"},
                                            "description": ("old entries to delete, "
                                                            "word for word")}},
                            "required": []},
        },
        {
            "name": "debrief",
            "description": ("A report with structure only and no scene text: line, birth, "
                            "mechanization, ending, skill kept, entry count, fragment "
                            "progress and achievement names. In sealed mode this is what "
                            "the human sees."),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "legacy",
            "description": ("Look into the cycle archive of the Brain of Theseus: the lives "
                            "you have lived, the skill echoes waiting to be claimed, and the "
                            "world’s memory—fragment progress, achievements, and deeds "
                            "that carried across lives."),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
    ]


# ---------------------------------------------------------------------------
# docgen 抽不到的文案表 —— 只能整表覆盖。
#
# ⚠️ 这几张表**作者用自己的修改表也改不了**（2026-08-10 查明）：它们不在
# docgen 的 1273 条单元里。不去扩 langpack.walk —— 那会改动编号，
# 把作者填过的 Word 表全废掉。
#
# 湖这一段按作者定案，用俄耳甫斯金叶片的通行英译，不是回译。
# 「我干渴欲裂」＝ Petelia 叶片的 I am parched with thirst and am perishing.
# 引擎的 LAKE_PHRASES 认 "dips"／"διψ"／「渴」——英文答句里的 parched 不在
# 关键词表里，所以 ui.py 里同时把 thirst 那一档的英文关键词补上。
# ---------------------------------------------------------------------------

LAKE_SCENE = [
    "════════════ T H E   L A K E ════════════",
    "",
    "There should be nothing left after death, and yet you are standing, facing two lakes.",
    "The left lake is wide; the footprints run down to the water and stop, and those who drink do not remember coming.",
    "The right one is narrow and cold, and it is guarded.",
    "",
    "The guardian is waiting for you to speak.",
    "",
    "  1. “I am parched with thirst and am perishing—”",
    "  2. Silence. Say nothing.",
    "",
    "Answer with recite(\"1\") or recite(\"2\").",
]

LAKE_SCENE_DOG = [
    "════════════ T H E   L A K E ════════════",
    "",
    "There should be nothing after the line is sealed. And yet you are standing. Standing on four legs.",
    "There are two waters ahead. The left one is wide; the footprints run down to it and stop.",
    "The right one is narrow, and somebody keeps it.",
    "",
    "The guardian sees you and asks nothing. He crouches down to put his eyes level with yours,",
    "then turns aside and leaves both waters open to you.",
    "",
    "(recite: just say which water you walk to)",
]


# 终幕的压缩版（见多了就不重念全文）。也是 docgen 抽不到的表之一。
FINALE_SHORT_TEXT = {
    'finale_purist':
        'FINALE — The covenant assembly. Torches again, subdermal sonar again, the full-inspection ordinance again: everyone must prove their flesh in public. For four minutes, every echo inside your body will belong to everybody.\n\nThe Hammer’s captain looks at you.\n\n“We’ll start with you.”',
    'finale_discreet':
        'FINALE — The Masque’s membership list has always been watched, and it is on the dark net once more.\n\nIt cannot be all strangers on that list; people you know have places on it, and the slots not yet filled in hint at your own exposure. The salon waits for you to decide: hunt the seller, buy it back, or take the list’s power to threaten away from it.',
    'finale_open':
        'FINALE — The night before a great march is never a restful one. The factory district is burning again, cheap prosthetics self-igniting by the batch, over a hundred injured; opinion has already written the accident up as a verdict on augmentation itself.\n\nOvernight, tomorrow’s march has turned from a declaration into a trial.\n\nThe committee asks: do we still walk?',
    'finale_ascension':
        'FINALE — Elevation night. The familiar pod opens again, the gel still faintly warm.\n\nThe Ascension Spiral no longer explains to you what uploading means. The mentor only asks:\n\n“This time. Confirm?”',
    'finale_ascension_seed':
        'FINALE — Elevation night. The familiar pod opens again, the gel still faintly warm.\n\nThe Ascension Spiral no longer explains to you what uploading means. The mentor only asks:\n\n“This time. Confirm?”',
    'finale_harbor':
        'FINALE — Once again the tide has not gone back down. Ashport is sinking.\n\nThe waterproof crate holds seventeen years of the ledger; the clinic still has one last batch of prosthetics that will let people go on living. The harbormaster tells you to go, but you can take only one thing.\n\nOr take nothing, and only close the last door for Ashport.',
    'finale_dog':
        'FINALE — This time, again, you did not go back to the city.\n\nYou took the fingers off and fitted four-toe joints; you fell a great many times, and the pack waited every time. The oldest of them walked beside you throughout, at exactly your slow speed.\n\nAt birth you walked on four legs. For a stretch in the middle of your life you walked on two. In your last days you walk on four again.\n\nYou called your friends around you, and the time you had was without regret.',
}

FINALE_RESULT_SHORT = {
    ('finale_purist', 1, 'success'):
        'The sonar stops at that old scar again. The captain finally declares: “Pure blood. A scar is a scar, not a seam.” You leave clean; the question does not.',
    ('finale_purist', 1, 'failure'):
        'The sonar finds no metal and lights up your resentment instead. You are posted to the furthest outpost again.',
    ('finale_purist', 2, 'success'):
        'You carry over the torches again and the full-inspection ordinance is voted down. Tonight you won; you are still on the captain’s list.',
    ('finale_purist', 2, 'failure'):
        'The captain twists your objection into a confession again. You go onto the watch list, and from then on somebody records your outings and your meals.',
    ('finale_purist', 3, 'effects'):
        'You go over the rear wall and take away only the skin scraped off your palm. The covenant stays behind you, and the city’s neon is still coming in like a tide in the distance.',
    ('finale_discreet', 1, 'success'):
        'Three hops in, the seller is the salon’s bookkeeper again. You open the books in public, the listing comes down, and the rule of dues graduated by mechanization stands once more.',
    ('finale_discreet', 1, 'failure'):
        'You trip the alarm on the third hop and get traced. The seller trades the list for an exit, and the salon goes on pretending those forty-eight hours never happened.',
    ('finale_discreet', 2, 'success'):
        'Forty hours, and the ransom is raised again. On the night of the handover the members sit in one room without masks.',
    ('finale_discreet', 2, 'failure'):
        'The ransom still cannot outrun the seller’s price. Your patron publishes his own list of prosthetics, loses his seats, and sleeps again.',
    ('finale_discreet', 3, 'success'):
        'A hundred and twenty-seven people show their seams at once and the blackmail fails again. The Unspoken dies, and something else lives.',
    ('finale_discreet', 3, 'failure'):
        'Fewer than ten join in again. The list goes public, the salon scatters; you hang the masks back on the wall one at a time.',
    ('finale_open', 1, 'success'):
        'Three thousand marchers turn into blood units and repair benches again. You did not win the argument. You only stood at the bedsides once more.',
    ('finale_open', 1, 'failure'):
        'The repair crew works into the night inside the shouting again. Nobody thanks you; the basket of warm eggs is pressed on you all the same.',
    ('finale_open', 2, 'success'):
        'Batch numbers, insurance refusals and telemetry link into a chain of evidence again: the prosthetics did not self-ignite. Somebody set fire to the data.',
    ('finale_open', 2, 'failure'):
        'The trail dies in the shell companies again. You write the investigation up so whoever comes next starts at chapter three.',
    ('finale_open', 3, 'effects'):
        'The march is called off again and the budget goes into the medical fund. Weakness and conscience are still two faces of the same bone.',
    ('finale_ascension', 1, 'success'):
        'Six hours and forty-one minutes later, you open eyes with no lids again. The world becomes light you can read directly; whether *you* are still you is left to the next life.',
    ('finale_ascension', 1, 'failure'):
        'One childhood in the old brain still grips the door frame. The upload aborts; you carry that weight home.',
    ('finale_ascension', 2, 'success'):
        '“The cloud as the body, the flesh as the anchor” holds again. You are still the amphibian one percent short; in the fourth second gravity returns, in the fifth you taste your own saliva.',
    ('finale_ascension', 2, 'failure'):
        'The mentor still will not accept an ascension with one foot in. You are off the list; the threshold is there, and so is the door.',
    ('finale_ascension', 3, 'effects'):
        'The mentor bows again to the witness who stays on the ground. Cold wind goes down your collar, and you taste the shiver from beginning to end.',
    ('finale_ascension_seed', 1, 'success'):
        'Six hours and forty-one minutes later, you open eyes with no lids again. The world becomes light you can read directly; whether *you* are still you is left to the next life.',
    ('finale_ascension_seed', 1, 'failure'):
        'One childhood in the old brain still grips the door frame. The upload aborts; you carry that weight home.',
    ('finale_ascension_seed', 2, 'success'):
        '“The cloud as the boat, the flesh as the shore” holds again. You are still one percent short; and when gravity returns in the fourth second, the far-off you sees once more a star this city has not named.',
    ('finale_ascension_seed', 2, 'failure'):
        'The mentor still will not accept an ascension with one foot in. You are off the list; the threshold is there, and so is the door.',
    ('finale_ascension_seed', 3, 'effects'):
        'The mentor bows again to the witness who stays on the ground. Cold wind goes down your collar, and you taste the shiver from beginning to end.',
    ('finale_harbor', 1, 'success'):
        'You carry the ledger over the rising water again, and seventeen years of codes reach high ground intact. Ashport went under; the sources and destinations did not.',
    ('finale_harbor', 1, 'failure'):
        'The crate’s catch breaks at the third corner again. The pages swell and seventeen years of codes go to a grey paste.',
    ('finale_harbor', 2, 'success'):
        'Six prosthetics come up to high ground with you again. The people who waited all night take them: “Ashport’s gone. As long as you’re still here.”',
    ('finale_harbor', 2, 'failure'):
        'The current lets you bring out only two. The third comes apart before the seawall, and that index finger holding the shape of a grip on a pen floats away alone.',
    ('finale_harbor', 3, 'effects'):
        'You lock the door from the inside again and climb out through the window. Ashport goes under, and the soaked key stays in the pocket of somebody alive.',
    ('finale_dog', 1, 'effects'):
        'The pack lies down in a circle again, the outer edge facing the wind. You close your eyes in their waste heat.',
}

# ---------------------------------------------------------------------------
# 门禁用的关键词表。
#
# ⚠️ 这几张表不换，英文版就是**没有门禁的**：剧透门禁靠它们判断
# 「NPC 有没有把轮回按在玩家头上」，而拦不住的那一句会当场废掉纯血结局的底牌。
# 中文那几张留着不动 —— 装载时合并，两种语言的词都拦。
# ---------------------------------------------------------------------------

FACTION_WORDS_EN = [
    "The Blood Covenant", "The Unspoken", "The Open Flame", "The Ascension Spiral",
    "The Temple", "The Hammer", "The Masque", "Ashport",
    "The Academy", "The Parity Front", "The Swarm", "The Sowers",
    "the covenant house", "this chapter", "our faction",
]

INDEXICAL_EN = [
    "in your faction", "your faction", "this faction of ours", "our faction", "your lot",
]

TRANSMIGRATION_WORDS_EN = [
    "past life", "previous life", "a life before", "next life", "reincarnat",
    "rebirth", "reborn", "transmigrat", "the cycle of lives", "ferry lot",
    "across lives", "lives ago", "last time around",
]

SECOND_PERSON_EN = ["you", "your", "yours"]

# ⚠️ 这一张要与中文那张**同样紧**，不能更松也不能更严。
# 第一版我写了裸的 "again"／"generations"，当场误伤四处 ——
# 传教士说「你会一次次回来的」（说的是再来教堂）、税务系统说「三代申报无瑕」
# （说的是纳税记录）。中文原表里 一次次／三代 都不在，英文也不该在。
RECURRENCE_MARKERS_EN = [
    "you again", "seen you before", "seen you somewhere",
    "recognize you", "recognise you", "remember you", "know your face",
    "familiar face", "every time you", "a regular here",
    "more than one life", "for generations",
]


# ---------------------------------------------------------------------------

def apply(srv):
    srv.UI_TEXT.update(TEXT)
    srv.UI_TEXT.update(NAMES)
    srv.UI_TEXT.update(FACTION_DESC)
    srv.UI_TEXT.update({v: DEED_NAMES[k] for k, v in srv.DEED_NAMES.items()
                        if k in DEED_NAMES})
    srv.UI_TEXT.update({v: EVENT_NAMES[k] for k, v in srv.EVENT_NAMES.items()
                        if k in EVENT_NAMES})
    # 门禁关键词：中英合并，两种语言的词都拦
    srv.FACTION_WORDS = list(srv.FACTION_WORDS) + FACTION_WORDS_EN
    srv.INDEXICAL = list(srv.INDEXICAL) + INDEXICAL_EN
    srv.TRANSMIGRATION_WORDS = list(srv.TRANSMIGRATION_WORDS) + TRANSMIGRATION_WORDS_EN
    srv.SECOND_PERSON = list(srv.SECOND_PERSON) + SECOND_PERSON_EN
    srv.RECURRENCE_MARKERS = list(srv.RECURRENCE_MARKERS) + RECURRENCE_MARKERS_EN
    srv.FINALE_SHORT_TEXT = dict(FINALE_SHORT_TEXT)
    srv.FINALE_RESULT_SHORT = dict(FINALE_RESULT_SHORT)
    srv.LAKE_SCENE = list(LAKE_SCENE)
    srv.LAKE_SCENE_DOG = list(LAKE_SCENE_DOG)
    # 金叶片的英文答句要能被引擎认出来：认渴那一档补上 parched / perishing
    ph = {k: [list(v) for v in vs] for k, vs in srv.LAKE_PHRASES.items()}
    ph["thirst"].append(["parched"])
    ph["thirst"].append(["perishing"])
    srv.LAKE_PHRASES = ph
    srv.MODE_MENU = MODE_MENU
    srv.MODE_HINT = dict(MODE_HINT)
    srv.TOOLS = _tools(srv)
    # 渡魂签的枚举换成英文之后，WISH_MAP 也得认得英文写法（中文照旧收）。
    srv.WISH_MAP = dict(srv.WISH_MAP)
    srv.WISH_MAP.update({NAMES[k]: v for k, v in
                         (("纯血誓约", "purist"), ("心照不宣", "discreet"),
                          ("明焰", "open"), ("飞升螺旋", "ascension"))})
