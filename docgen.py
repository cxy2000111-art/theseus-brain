#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《忒修斯之脑》文案工作台。

提取：从 server.py 提取全部文案，生成带编号的修改表（四册）与通读稿。
贴回：读取填好的修改表，按「原文」定位源码里的字符串（跨行拼接也能对上），
      用「新文」原位替换，并自动按原缩进折行。

    python3 docgen.py extract [输出目录]        # 默认 ./docs
    python3 docgen.py apply 修改表-*.md ...     # 贴回后自动跑三道门禁请手动执行 --selftest

修改表格式（对人和 AI 都友好，允许多行文本）：

    ### [通-001] rain_market · 事件正文
    【原文】
    ……
    【新文】
    （留空＝沿用原文；只写【删除】暂不支持，请改写为极短句）

规则见《文案重写须知》。改完必跑：python3 server.py --selftest
"""

import ast
import io
import importlib.util
import os
import re
import sys
import tokenize

BASE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(BASE, "server.py")


def load_server():
    spec = importlib.util.spec_from_file_location("theseus_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 单元枚举：顺序确定，同一份 server.py 永远得到同一套编号
# ---------------------------------------------------------------------------

def _opt_units(prefix, ev_or_opts):
    units = []
    for i, opt in enumerate(ev_or_opts, 1):
        units.append(("选项%d" % i, opt["text"]))
        if "check" in opt or "gate" in opt or "coin" in opt:
            units.append(("选项%d 成功" % i, opt["success"]["narration"]))
            units.append(("选项%d 失败" % i, opt["failure"]["narration"]))
        else:
            units.append(("选项%d 结果" % i, opt["effects"]["narration"]))
    return units


def enumerate_units(srv):
    """返回 [(册名, 编号, 位置, 原文)]。"""
    vols = {"通": [], "阵": [], "终": [], "系": []}

    def _one(ev, opts, echoes, voices, tag=""):
        out = [("事件正文%s" % tag, ev["text"])]
        out += [(loc + tag, txt) for loc, txt in _opt_units(ev["id"], opts)]
        for j, e in enumerate(echoes, 1):
            out.append(("回响%d%s" % (j, tag), e["text"]))
        for skill, line in voices.items():
            out.append(("技能之声·%s%s" % (skill, tag), line))
        return out

    def ev_units(ev):
        out = _one(ev, ev["options"], ev.get("echoes", []), ev.get("voices", {}))
        for i, v in enumerate(ev.get("variants") or [], 1):
            vv = dict(ev); vv["text"] = v.get("text", ev["text"])
            out += _one(vv, v.get("options", ev["options"]),
                        v.get("echoes", []), v.get("voices", {}), "（变体%d）" % i)
        return out

    for ev in srv.EVENTS:
        vol = "通" if (ev["factions"] == "any" and not ev["id"].startswith("echo_")) else "阵"
        for loc, text in ev_units(ev):
            vols[vol].append(("%s · %s" % (ev["id"], loc), text))

    for key in ("purist", "discreet", "open", "ascension"):
        ev = srv.FINALES[key]
        for loc, text in ev_units(ev):
            vols["终"].append(("%s · %s" % (ev["id"], loc), text))
    for key in ("purist", "discreet", "open", "ascension"):
        eid, text = srv.EXPOSURE_END[key]
        vols["终"].append(("暴露结局 · %s(%s)" % (key, eid), text))

    for fid in srv.FRAGMENT_ORDER:
        frag = srv.FRAGMENTS[fid]
        vols["系"].append(("碎片 %s · 线索" % fid, frag["hint"]))
        vols["系"].append(("碎片 %s ·「%s」场景" % (fid, frag["name"]), frag["scene"]))
    # 渡口的开场：船长、四个声音、五（六）个选项。
    # 这一段此前写死在 _start_final 里，从来没被抽出来过 —— 也就没人改得了它。
    for label, text in srv.FINAL_OPENING:
        vols["系"].append(("终局渡口 · %s" % label, text))
    for n in sorted(srv.FINAL_ENDINGS):
        eid, ename, scene = srv.FINAL_ENDINGS[n]
        vols["系"].append(("终局结局%d ·「%s」" % (n, ename), scene))
    for eid, line in sorted(srv.FINAL_AFTER.items()):
        vols["系"].append(("终局底色 · %s" % eid, line))
    # 见底那三段：临终、两种落幕、全书终。它们也写死在代码里，也得能改。
    vols["系"].append(("见底 · 临终", srv.DEATHBED_TEXT))
    vols["系"].append(("见底 · 走不动了 · 交上去", srv.DRY_CHOICE_TEXT))
    vols["系"].append(("见底 · 走不动了 · 再往前一寸", srv.DRY_CHOICE_STEP_TEXT))
    for key in sorted(srv.CURTAIN):
        vols["系"].append(("见底 · 落幕 · %s" % key, srv.CURTAIN[key]))
    vols["系"].append(("见底 · 全书终", srv.EPILOGUE))
    for era in srv.ERAS:
        vols["系"].append(("时代 ·「%s」" % era["name"], era["desc"]))
    for ach in srv.ACHIEVEMENTS:
        vols["系"].append(("成就 ·「%s」钥匙说明" % ach["name"], ach["gift"]))

    result = []
    for tag, rows in vols.items():
        for i, (loc, text) in enumerate(rows, 1):
            result.append((tag, "[%s-%03d]" % (tag, i), loc, text))
    return result


VOL_TITLES = {
    "通": "第一册 · 通用事件",
    "阵": "第二册 · 残响与阵营事件",
    "终": "第三册 · 终幕与暴露结局",
    "系": "第四册 · 碎片·终局·系统文本",
}


def extract(outdir):
    srv = load_server()
    units = enumerate_units(srv)
    os.makedirs(outdir, exist_ok=True)

    for tag, title in VOL_TITLES.items():
        rows = [u for u in units if u[0] == tag]
        lines = ["# 修改表 · %s" % title, "",
                 "共 %d 条。规则：【新文】留空＝沿用原文；写了就整段替换。" % len(rows),
                 "编号、位置、【原文】三样不要动——贴回是按原文定位的。",
                 "手机编辑请关自动更正（智能引号会让「」变成引号，贴不回去）。", ""]
        for _, num, loc, text in rows:
            lines += ["### %s %s" % (num, loc), "【原文】", text, "【新文】", "", ""]
        path = os.path.join(outdir, "修改表-%s.md" % title.split(" · ")[1])
        open(path, "w", encoding="utf-8").write("\n".join(lines))
        print("写出", path, "(%d 条)" % len(rows))

    lines = ["# 《忒修斯之脑》通读稿", "",
             "按引擎顺序排列的全部文案，编号与修改表一致。只读，不用改这份。", ""]
    for tag in VOL_TITLES:
        lines += ["", "═" * 30, "## %s" % VOL_TITLES[tag], ""]
        for _, num, loc, text in (u for u in units if u[0] == tag):
            lines += ["%s %s" % (num, loc), text, ""]
    path = os.path.join(outdir, "通读稿.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    total = len(units)
    chars = sum(len(u[3]) for u in units)
    print("写出", path)
    print("合计 %d 条，%d 字。" % (total, chars))


# ---------------------------------------------------------------------------
# 贴回：tokenize 找出源码里的字符串拼接段，按归一化原文匹配，原位替换
# ---------------------------------------------------------------------------

def _norm(s):
    return re.sub(r"\s+", "", s)


def string_runs(src):
    """返回 [(start_off, end_off, 合并后的文本, 首token列)]，相邻拼接的字符串并为一段。"""
    line_off = [0]
    for line in src.splitlines(keepends=True):
        line_off.append(line_off[-1] + len(line))
    toks = tokenize.generate_tokens(io.StringIO(src).readline)
    runs, cur = [], None
    for t in toks:
        if t.type == tokenize.STRING:
            so = line_off[t.start[0] - 1] + t.start[1]
            eo = line_off[t.end[0] - 1] + t.end[1]
            val = ast.literal_eval(t.string)
            if cur is not None and _norm(src[cur[1]:so]) == "":
                cur = (cur[0], eo, cur[2] + val, cur[3])
            else:
                if cur:
                    runs.append(cur)
                cur = (so, eo, val, t.start[1])
        elif t.type in (tokenize.NL, tokenize.COMMENT):
            continue
        else:
            if cur:
                runs.append(cur)
                cur = None
    if cur:
        runs.append(cur)
    return runs


def _fmt_literal(text, col):
    segs = text.split("\n")
    lits = []
    for i, seg in enumerate(segs):
        body = seg.replace("\\", "\\\\").replace('"', '\\"')
        if i < len(segs) - 1:
            body += "\\n"
        lits.append('"%s"' % body)
    return ("\n" + " " * col).join(lits)


def parse_table(path):
    text = open(path, encoding="utf-8").read()
    rows = []
    for m in re.finditer(
            r"^### (\[[^\]]+\]) (.+?)\n【原文】\n(.*?)\n【新文】\n(.*?)(?=\n### |\Z)",
            text, re.S | re.M):
        num, loc, old, new = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
        new = re.sub(r"^（留空.*?）$", "", new.strip(), flags=re.M).strip()
        if new:
            rows.append((num, loc, old.strip(), new))
    return rows


def apply_tables(paths):
    src = open(SERVER, encoding="utf-8").read()
    applied, skipped, failed = [], [], []
    for path in paths:
        for num, loc, old, new in parse_table(path):
            if _norm(new) == _norm(old):
                skipped.append(num)
                continue
            runs = string_runs(src)
            hit = [r for r in runs if _norm(r[2]) == _norm(old)]
            if not hit:
                failed.append((num, loc))
                continue
            so, eo, _, col = hit[0]
            src = src[:so] + _fmt_literal(new, col) + src[eo:]
            applied.append(num)
    open(SERVER, "w", encoding="utf-8").write(src)
    print("贴回 %d 条；原样跳过 %d 条；对不上 %d 条。" % (len(applied), len(skipped), len(failed)))
    for num, loc in failed:
        print("  ✗ 对不上：%s %s（原文被上一条改过？或引号被自动更正？）" % (num, loc))
    print("语法检查：", end="")
    try:
        compile(src, SERVER, "exec")
        print("通过。接下来请跑：python3 server.py --selftest")
    except SyntaxError as e:
        print("失败！%s —— 请勿运行，先修复或回滚。" % e)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "extract":
        extract(sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "docs"))
    elif len(sys.argv) >= 3 and sys.argv[1] == "apply":
        apply_tables(sys.argv[2:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
