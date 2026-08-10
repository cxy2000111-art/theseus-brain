#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《忒修斯之脑》语言层。零依赖，Python 3 标准库。

中文是源头，`server.py` 一个字节都不动。另一种语言活在 `<lang>/对照-*.md` 里，
开局时按 `THESEUS_LANG` 装载：

    python3 server.py                     # 中文
    THESEUS_LANG=en python3 server.py     # 英文

对照文件的格式与 `docgen.py` 的「修改表」**完全一样**（### 编号 位置 /【原文】/【新文】），
所以 `docxio.py` 照样能出 Word、照样能收回来 —— 工具不用重学。
区别只有一处：修改表的【新文】是改写后的中文，对照文件的【新文】是译文。

    python3 langpack.py init en      # 按当前中文生成／增补对照文件（已译的不动）
    python3 langpack.py stale en     # 中文改了、译文没跟上的条目
    python3 langpack.py check        # 自检：单元与编号必须与 docgen 完全一致
    python3 langpack.py check-ui en  # 界面层门禁：缺译与 % 占位符错位

界面层（工具说明、玩法菜单、状态条这些写死在引擎里的字）不走对照文件，
走 `<lang>/ui.py` —— 那是一个 Python 模块，里面定义 `apply(srv)`。

⚠️ 装载失败不许让游戏打不开：任何异常都回落到中文，只在 stderr 上抱怨一句。
"""

import io
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

VOL_FILES = {
    "通": "对照-通用事件.md",
    "阵": "对照-残响与阵营事件.md",
    "终": "对照-终幕与暴露结局.md",
    "系": "对照-碎片终局系统文本.md",
}
VOL_TITLES = {
    "通": "第一册 · 通用事件",
    "阵": "第二册 · 残响与阵营事件",
    "终": "第三册 · 终幕与暴露结局",
    "系": "第四册 · 碎片·终局·系统文本",
}


def _norm(s):
    return re.sub(r"\s+", "", s or "")


# ---------------------------------------------------------------------------
# 单元枚举
#
# 与 docgen.enumerate_units 同序、同编号、同位置名 —— 靠 `check` 子命令锁死。
# 差别只在这里多给一个 setter：docgen 是把新文写回源码，这里是装进内存。
# ---------------------------------------------------------------------------

class Unit(object):
    __slots__ = ("vol", "num", "loc", "ident", "get", "set")

    def __init__(self, vol, num, loc, ident, get, set_):
        self.vol, self.num, self.loc = vol, num, loc
        self.ident, self.get, self.set = ident, get, set_


def _item(d, key):
    """字典项的 (身份, 取, 存)。身份用来去重 —— 变体不带自己的选项时，
    枚举出来的那几条其实指着同一个对象，写第二遍就把第一遍覆盖了。"""
    return (id(d), key), (lambda: d[key]), (lambda v: d.__setitem__(key, v))


def _tuple_item(d, key, idx):
    """元组里的第 idx 个（EXPOSURE_END / FINAL_ENDINGS 这种 (id, 名, 正文)）。"""
    def _set(v):
        t = list(d[key])
        t[idx] = v
        d[key] = tuple(t)
    return (id(d), key, idx), (lambda: d[key][idx]), _set


def _attr(mod, name):
    return (id(mod), name), (lambda: getattr(mod, name)), (lambda v: setattr(mod, name, v))


class _Namespace(object):
    """把 globals() 那个 dict 包成能 getattr/setattr 的东西。

    server.py 装载自己的时候传的是 globals()，不是 sys.modules[__name__] ——
    因为一个模块被 importlib 直接 exec 出来（docgen、langpack、测试脚本都这么干）
    的时候，它还没进 sys.modules，那条路会 KeyError。globals() 永远在。"""

    def __init__(self, d):
        object.__setattr__(self, "_d", d)

    def __getattr__(self, name):
        try:
            return object.__getattribute__(self, "_d")[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, "_d")[name] = value


def _as_ns(srv):
    return _Namespace(srv) if isinstance(srv, dict) else srv


def _opt_units(opts):
    out = []
    for i, opt in enumerate(opts, 1):
        out.append(("选项%d" % i, _item(opt, "text")))
        if "check" in opt or "gate" in opt or "coin" in opt:
            out.append(("选项%d 成功" % i, _item(opt["success"], "narration")))
            out.append(("选项%d 失败" % i, _item(opt["failure"], "narration")))
        else:
            out.append(("选项%d 结果" % i, _item(opt["effects"], "narration")))
    return out


def _one(holder, opts, echoes, voices, tag=""):
    """holder 是「正文住在哪个字典里」—— 基本幕是事件本身，变体是变体自己。"""
    out = [("事件正文%s" % tag, _item(holder, "text"))]
    out += [(loc + tag, gs) for loc, gs in _opt_units(opts)]
    for j, e in enumerate(echoes, 1):
        out.append(("回响%d%s" % (j, tag), _item(e, "text")))
    for skill in voices:
        out.append(("技能之声·%s%s" % (skill, tag), _item(voices, skill)))
    return out


def _ev_units(ev):
    out = _one(ev, ev["options"], ev.get("echoes", []), ev.get("voices", {}))
    for i, v in enumerate(ev.get("variants") or [], 1):
        # 变体没写 text/options/echoes 时，docgen 会把基本幕的那几条再枚举一遍。
        # 位置名照它的来（编号不能错位），但 setter 指向变体自己有的那份；
        # 变体没有的，身份与基本幕重合，装载时按去重规则跳过。
        holder = v if "text" in v else ev
        out += _one(holder, v.get("options", ev["options"]),
                    v.get("echoes", []) or [], v.get("voices", {}) or {},
                    "（变体%d）" % i)
    return out


def walk(srv):
    """返回 [Unit]，顺序与编号跟 docgen 一模一样。"""
    srv = _as_ns(srv)
    vols = {"通": [], "阵": [], "终": [], "系": []}

    for ev in srv.EVENTS:
        vol = "通" if (ev["factions"] == "any" and not ev["id"].startswith("echo_")) else "阵"
        for loc, gs in _ev_units(ev):
            vols[vol].append(("%s · %s" % (ev["id"], loc), gs))

    for key in ("purist", "discreet", "open", "ascension"):
        ev = srv.FINALES[key]
        for loc, gs in _ev_units(ev):
            vols["终"].append(("%s · %s" % (ev["id"], loc), gs))
    for key in ("purist", "discreet", "open", "ascension"):
        eid = srv.EXPOSURE_END[key][0]
        vols["终"].append(("暴露结局 · %s(%s)" % (key, eid),
                           _tuple_item(srv.EXPOSURE_END, key, 1)))

    for fid in srv.FRAGMENT_ORDER:
        frag = srv.FRAGMENTS[fid]
        vols["系"].append(("碎片 %s · 线索" % fid, _item(frag, "hint")))
        vols["系"].append(("碎片 %s ·「%s」场景" % (fid, frag["name"]), _item(frag, "scene")))
    # 渡口开场：FINAL_OPENING 是拼出来的只读表，真正被引擎读的是下面这两个字典。
    for k in srv.FINAL_OPENING_TEXT:
        vols["系"].append(("终局渡口 · 开场 · %s" % k, _item(srv.FINAL_OPENING_TEXT, k)))
    for i in sorted(srv.FINAL_OPTION_TEXT):
        vols["系"].append(("终局渡口 · 选项%d" % i, _item(srv.FINAL_OPTION_TEXT, i)))
    for n in sorted(srv.FINAL_ENDINGS):
        ename = srv.FINAL_ENDINGS[n][1]
        vols["系"].append(("终局结局%d ·「%s」" % (n, ename),
                           _tuple_item(srv.FINAL_ENDINGS, n, 2)))
    for eid in sorted(srv.FINAL_AFTER):
        vols["系"].append(("终局底色 · %s" % eid, _item(srv.FINAL_AFTER, eid)))
    vols["系"].append(("见底 · 临终", _attr(srv, "DEATHBED_TEXT")))
    vols["系"].append(("见底 · 走不动了 · 交上去", _attr(srv, "DRY_CHOICE_TEXT")))
    vols["系"].append(("见底 · 走不动了 · 再往前一寸", _attr(srv, "DRY_CHOICE_STEP_TEXT")))
    for key in sorted(srv.CURTAIN):
        vols["系"].append(("见底 · 落幕 · %s" % key, _item(srv.CURTAIN, key)))
    vols["系"].append(("见底 · 全书终", _attr(srv, "EPILOGUE")))
    for era in srv.ERAS:
        vols["系"].append(("时代 ·「%s」" % era["name"], _item(era, "desc")))
    for ach in srv.ACHIEVEMENTS:
        vols["系"].append(("成就 ·「%s」钥匙说明" % ach["name"], _item(ach, "gift")))

    units = []
    for vol in ("通", "阵", "终", "系"):
        for i, (loc, (ident, get, set_)) in enumerate(vols[vol], 1):
            units.append(Unit(vol, "[%s-%03d]" % (vol, i), loc, ident, get, set_))
    return units


# ---------------------------------------------------------------------------
# 对照文件
# ---------------------------------------------------------------------------

_ROW_RE = re.compile(
    r"^### (\[[^\]]+\]) (.+?)\n【原文】\n(.*?)\n【新文】\n(.*?)(?=\n### |\Z)", re.S | re.M)


def parse_table(path):
    """返回 {位置: (原文, 新文)}。新文留空的也收 —— init 要靠它保住空位。"""
    rows = {}
    with io.open(path, encoding="utf-8") as f:
        text = f.read()
    for m in _ROW_RE.finditer(text):
        loc, old, new = m.group(2).strip(), m.group(3).strip(), m.group(4).strip()
        new = re.sub(r"^（留空.*?）$", "", new, flags=re.M).strip()
        rows[loc] = (old, new)
    return rows


def load_tables(langdir):
    table = {}
    for vol, fname in VOL_FILES.items():
        path = os.path.join(langdir, fname)
        if os.path.exists(path):
            table.update(parse_table(path))
    return table


def write_table(path, vol, rows):
    """rows: [(编号, 位置, 原文, 新文)]"""
    done = sum(1 for r in rows if r[3])
    lines = [
        "# 对照表 · %s" % VOL_TITLES[vol], "",
        "共 %d 条，已译 %d 条。**【原文】是定位用的，不要动。**" % (len(rows), done),
        "【新文】留空＝还没译，装载时这一条回落中文。",
        "中文原文改了而这里的【原文】没跟上时，`python3 langpack.py stale <lang>` 会报出来。",
        "",
    ]
    for num, loc, old, new in rows:
        lines += ["### %s %s" % (num, loc), "【原文】", old, "【新文】", new, ""]
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return done


# ---------------------------------------------------------------------------
# 装载
# ---------------------------------------------------------------------------

def install(srv, lang, verbose=True):
    """把 <lang>/ 的译文装进 srv。返回 (装上的条数, 过期的条数)。"""
    srv = _as_ns(srv)
    langdir = os.path.join(BASE, lang)
    if not os.path.isdir(langdir):
        raise IOError("没有 %s/ 这个目录" % lang)
    table = load_tables(langdir)

    applied, stale, seen = 0, [], set()
    for u in walk(srv):
        row = table.get(u.loc)
        if not row:
            continue
        old, new = row
        if not new:
            continue
        if u.ident in seen:      # 变体与基本幕共用同一个对象，只装第一遍
            continue
        if _norm(old) != _norm(u.get()):
            stale.append(u)      # 中文改过了，这条译文已经不对着原文
            continue
        u.set(new)
        seen.add(u.ident)
        applied += 1

    # 界面层：<lang>/ui.py 里的 apply(srv)
    ui_path = os.path.join(langdir, "ui.py")
    if os.path.exists(ui_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("theseus_ui_" + lang, ui_path)
        ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ui)
        ui.apply(srv)

    if verbose and stale:
        sys.stderr.write("⚠ 语言层：%d 条中文已改动，译文仍是旧的，这些条回落中文：\n" % len(stale))
        for u in stale[:10]:
            sys.stderr.write("    %s %s\n" % (u.num, u.loc))
        if len(stale) > 10:
            sys.stderr.write("    …… 还有 %d 条。跑 python3 langpack.py stale %s 看全部。\n"
                             % (len(stale) - 10, lang))
    return applied, len(stale)


def install_or_fallback(srv, lang):
    """给 server.py 调的那一个。**装不上也不许让游戏打不开。**"""
    try:
        return install(srv, lang)
    except Exception as e:
        sys.stderr.write("⚠ 语言层 %s 装载失败，全部回落中文：%r\n" % (lang, e))
        return (0, 0)


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def _load_server():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "theseus_server_src", os.path.join(BASE, "server.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    os.environ["THESEUS_LANG"] = "zh"      # 抽原文时不许自己装自己
    spec.loader.exec_module(mod)
    return mod


def cmd_init(lang):
    srv = _load_server()
    langdir = os.path.join(BASE, lang)
    os.makedirs(langdir, exist_ok=True)
    old_table = load_tables(langdir)
    units = walk(srv)
    kept = dropped = 0
    for vol in ("通", "阵", "终", "系"):
        rows = []
        for u in (x for x in units if x.vol == vol):
            prev = old_table.get(u.loc)
            new = ""
            if prev and prev[1]:
                if _norm(prev[0]) == _norm(u.get()):
                    new, kept = prev[1], kept + 1
                else:
                    dropped += 1     # 中文变了：译文留在旧文件里，这里先空着
            rows.append((u.num, u.loc, u.get(), new))
        path = os.path.join(langdir, VOL_FILES[vol])
        done = write_table(path, vol, rows)
        print("写出 %s（%d 条，已译 %d 条）" % (path, len(rows), done))
    print("沿用已译 %d 条；中文变动、译文作废 %d 条。" % (kept, dropped))


def cmd_stale(lang):
    srv = _load_server()
    table = load_tables(os.path.join(BASE, lang))
    units = walk(srv)
    locs = set(u.loc for u in units)
    stale = [u for u in units if u.loc in table and table[u.loc][1]
             and _norm(table[u.loc][0]) != _norm(u.get())]
    missing = [u for u in units if not (table.get(u.loc) or ("", ""))[1]]
    orphan = [loc for loc in table if loc not in locs]
    print("单元 %d 条：已译 %d，未译 %d，过期 %d，孤儿 %d。"
          % (len(units), len(units) - len(missing), len(missing), len(stale), len(orphan)))
    for u in stale:
        print("  ⚠ 过期 %s %s" % (u.num, u.loc))
    for loc in orphan:
        print("  ? 孤儿（源码里已经没有这个位置了）：%s" % loc)
    return 1 if (stale or orphan) else 0


def cmd_check():
    """自检：walk 必须与 docgen.enumerate_units 一字不差。
    编号一旦对不上，作者填过的 Word 表就贴不回来了 —— 这道门禁是拦这个的。"""
    srv = _load_server()
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "theseus_docgen", os.path.join(BASE, "docgen.py"))
    dg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dg)
    mine = [(u.vol, u.num, u.loc, u.get()) for u in walk(srv)]
    theirs = [(v, n, l, t) for v, n, l, t in dg.enumerate_units(srv)]
    if len(mine) != len(theirs):
        print("✗ 条数不一致：langpack %d ／ docgen %d" % (len(mine), len(theirs)))
        return 1
    bad = [(a, b) for a, b in zip(mine, theirs) if a != b]
    if bad:
        print("✗ %d 条对不上，头三条：" % len(bad))
        for a, b in bad[:3]:
            print("   langpack %s %s\n   docgen   %s %s" % (a[1], a[2], b[1], b[2]))
        return 1
    print("✓ %d 条单元与 docgen 完全一致（编号、位置、原文）。" % len(mine))
    return 0


_SPEC = re.compile(r"%[#0\- +]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]")


def cmd_check_ui(lang):
    """界面层门禁：T("…") 的每一条原文都得在 <lang>/ui.py 里有对应，
    而且 % 占位符的**个数与顺序必须一模一样** —— 这一条是拦格式串错位的：
    错了不是显示得难看，是运行时直接抛异常，而作者读不出英文，发现不了。"""
    import ast
    import importlib.util
    src = io.open(os.path.join(BASE, "server.py"), encoding="utf-8").read()
    keys, seen = [], set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "T" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            k = node.args[0].value
            if k not in seen:
                seen.add(k)
                keys.append((node.lineno, k))

    ui_path = os.path.join(BASE, lang, "ui.py")
    spec = importlib.util.spec_from_file_location("theseus_ui_check_" + lang, ui_path)
    ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui)
    table = {}
    for name in ("TEXT", "NAMES", "FACTION_DESC"):
        table.update(getattr(ui, name, {}) or {})

    missing = [(ln, k) for ln, k in keys if k not in table]
    bad = []
    for ln, k in keys:
        if k in table and _SPEC.findall(k) != _SPEC.findall(table[k]):
            bad.append((ln, k, _SPEC.findall(k), _SPEC.findall(table[k])))
    print("T() 原文 %d 条：缺译 %d，占位符对不上 %d。"
          % (len(keys), len(missing), len(bad)))
    for ln, k in missing:
        print("  ✗ 缺译 server.py:%d  %s" % (ln, k.replace("\n", "\\n")[:60]))
    for ln, k, a, b in bad:
        print("  ✗ 占位符 server.py:%d  %s\n      原文 %s ／ 译文 %s"
              % (ln, k.replace("\n", "\\n")[:50], a, b))
    return 1 if (missing or bad) else 0


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["init"] and len(argv) == 2:
        cmd_init(argv[1])
    elif argv[:1] == ["stale"] and len(argv) == 2:
        sys.exit(cmd_stale(argv[1]))
    elif argv[:1] == ["check"]:
        sys.exit(cmd_check())
    elif argv[:1] == ["check-ui"] and len(argv) == 2:
        sys.exit(cmd_check_ui(argv[1]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
