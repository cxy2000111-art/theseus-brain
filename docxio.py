#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《忒修斯之脑》修改表 ↔ Word 表格。零依赖，Python 3 标准库。

作者在 WPS/Word 里填最右一列，所以修改表要能出成 .docx，也要能收回来。
生成的表格与她第一册用过的那份版式一致：横向 A4，四列，表头深色，
表头行跨页重复。

    python3 docxio.py todocx docs/修改表-通用事件.md out.docx [--only 编号,编号…]
                                                              [--title 标题]
    python3 docxio.py fromdocx 填好的.docx docs/修改表-通用事件.md
    python3 docxio.py cover 她填过的.docx docs/修改表-通用事件.md
        # 列出 md 里「她没过目过」的编号（位置对不上或文本被引擎改过），
        # 拿这份编号列表喂 todocx --only，就只让她看新东西。

回收（fromdocx）时做两件保护：
  1. 智能引号还原成「」——手机和 WPS 的自动更正会把「」换成 “”，
     而 lint_spoilers 靠「」找 NPC 台词。每一处还原都会打印出来。
  2. 新文与原文一字不差时视为「没改」，不写进 md，避免 apply 空转。
"""

import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}

BASE = os.path.dirname(os.path.abspath(__file__))
FONT = "Noto Sans CJK SC"
COLS = (1150, 2500, 5100, 5100)
HEADERS = ("编号", "位置", "原文（勿改）", "新文（写这里）")


# ---------------------------------------------------------------------------
# 修改表（md）
# ---------------------------------------------------------------------------

ROW_RE = re.compile(
    r"^### (\[[^\]]+\]) (.+?)\n【原文】\n(.*?)\n【新文】\n(.*?)(?=\n### |\Z)", re.S | re.M)


def read_table(path):
    """返回 [(编号, 位置, 原文, 新文)]。"""
    text = open(path, encoding="utf-8").read()
    return [(m.group(1), m.group(2).strip(), m.group(3).strip(), m.group(4).strip())
            for m in ROW_RE.finditer(text)]


def write_new_text(path, updates):
    """把 {编号: 新文} 写进 md 的【新文】段，其余原样保留。"""
    text = open(path, encoding="utf-8").read()

    def repl(m):
        num, loc, old, new = m.group(1), m.group(2), m.group(3), m.group(4)
        if num in updates:
            new = updates[num]
        return "### %s %s\n【原文】\n%s\n【新文】\n%s\n" % (num, loc, old, new)

    open(path, "w", encoding="utf-8").write(ROW_RE.sub(repl, text))


def norm(s):
    return re.sub(r"\s+", "", s or "")


# ---------------------------------------------------------------------------
# 生成 .docx
# ---------------------------------------------------------------------------

def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _runs(text, bold=False, size=20, color=None):
    rpr = ['<w:rFonts w:ascii="%s" w:hAnsi="%s" w:eastAsia="%s" w:cs="%s"/>'
           % (FONT, FONT, FONT, FONT)]
    if bold:
        rpr.append("<w:b/><w:bCs/>")
    if color:
        rpr.append('<w:color w:val="%s"/>' % color)
    rpr.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size, size))
    rpr = "<w:rPr>%s</w:rPr>" % "".join(rpr)
    out = []
    for line in (text or "").split("\n"):
        out.append('<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
                   '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>'
                   % (rpr, esc(line)))
    return "".join(out)


def _cell(text, width, bold=False, fill=None, color=None):
    shd = ('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % fill) if fill else ""
    return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s'
            '<w:tcMar><w:top w:w="60" w:type="dxa"/><w:left w:w="90" w:type="dxa"/>'
            '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tcMar>'
            '</w:tcPr>%s</w:tc>' % (width, shd, _runs(text, bold=bold, color=color)))


def build_document(title, notes, rows):
    body = [_runs(title, bold=True, size=28)]
    for n in notes:
        body.append(_runs(n, size=20, color="595959"))
    body.append(_runs("", size=20))

    tbl = ['<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/>'
           '<w:tblInd w:w="0" w:type="dxa"/><w:tblBorders>' % sum(COLS)]
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tbl.append('<w:%s w:val="single" w:color="BFBFBF" w:sz="4" w:space="0"/>' % side)
    tbl.append('</w:tblBorders><w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>')
    for c in COLS:
        tbl.append('<w:gridCol w:w="%d"/>' % c)
    tbl.append("</w:tblGrid>")

    tbl.append('<w:tr><w:trPr><w:tblHeader/></w:trPr>')
    for h, c in zip(HEADERS, COLS):
        tbl.append(_cell(h, c, bold=True, fill="EDE6DC"))
    tbl.append("</w:tr>")

    for num, loc, old, new in rows:
        tbl.append("<w:tr>")
        tbl.append(_cell(num, COLS[0]))
        tbl.append(_cell(loc, COLS[1]))
        tbl.append(_cell(old, COLS[2]))
        tbl.append(_cell(new, COLS[3]))
        tbl.append("</w:tr>")
    tbl.append("</w:tbl>")
    body.append("".join(tbl))
    body.append(_runs("", size=20))

    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="%s"><w:body>%s'
            '<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
            '<w:pgMar w:top="800" w:right="800" w:bottom="800" w:left="800" '
            'w:header="708" w:footer="708" w:gutter="0"/>'
            '<w:docGrid w:linePitch="360" w:charSpace="0"/></w:sectPr>'
            "</w:body></w:document>" % (W, "".join(body)))


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>")

RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>")

DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>")

STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="%s"><w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="%s" w:hAnsi="%s" w:eastAsia="%s" w:cs="%s"/>'
    '<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="0"/></w:pPr></w:pPrDefault>'
    '</w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/><w:qFormat/></w:style></w:styles>'
    % (W, FONT, FONT, FONT, FONT))


def write_docx(path, title, notes, rows):
    doc = build_document(title, notes, rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/document.xml", doc)


# ---------------------------------------------------------------------------
# 读回 .docx
# ---------------------------------------------------------------------------

def _para_text(p):
    parts = []
    for node in p.iter():
        tag = node.tag.split("}")[1]
        if tag == "t":
            parts.append(node.text or "")
        elif tag in ("br", "cr"):
            parts.append("\n")
        elif tag == "tab":
            parts.append("\t")
    return "".join(parts)


def _cell_text(tc):
    return "\n".join(_para_text(p) for p in tc.findall("w:p", NS))


def read_docx_rows(path):
    """返回 [(编号, 位置, 原文, 新文)]，只取编号形如 [X-000] 的行。"""
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    rows = []
    for tbl in root.iter("{%s}tbl" % W):
        for tr in tbl.findall("w:tr", NS):
            cells = [_cell_text(tc).strip() for tc in tr.findall("w:tc", NS)]
            if len(cells) >= 4 and re.match(r"^\[[^\]]+\]$", cells[0]):
                rows.append(tuple(cells[:4]))
    return rows


QUOTE_FIX = [("“", "「"), ("”", "」")]


def unsmarten(text):
    """把自动更正吃掉的「」还原。返回 (新文本, 改了几处)。"""
    n = 0
    for a, b in QUOTE_FIX:
        n += text.count(a)
        text = text.replace(a, b)
    return text, n


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_todocx(md_path, out_path, only=None, title=None):
    rows = read_table(md_path)
    if only:
        # 编号在表里是 [系-011]，命令行上写 系-011 更顺手 —— 两种都收。
        def _key(x):
            return str(x).strip().strip("[]")
        keep = {_key(x) for x in only}
        rows = [r for r in rows if _key(r[0]) in keep]
        if not rows:
            print("警告：--only 一条都没匹配上。表里的编号形如 %s"
                  % (read_table(md_path)[0][0] if read_table(md_path) else "?"))
    name = title or os.path.basename(md_path).replace("修改表-", "").replace(".md", "")
    notes = ["共 %d 条。只填最右一列；编号、位置、原文三列请不要改动。" % len(rows),
             "不需要改的行留空即可，留空表示沿用原文。",
             "手机／WPS 请关自动更正：「」被换成引号会让 NPC 台词的门禁失效"
             "（真被换了也能自动还原，会逐条报给你）。"]
    write_docx(out_path, "修改表 · %s" % name, notes, rows)
    print("写出 %s（%d 条）" % (out_path, len(rows)))


def cmd_fromdocx(docx_path, md_path):
    doc_rows = read_docx_rows(docx_path)
    table = read_table(md_path)
    md_rows = {r[0]: r for r in table}
    # 编号漂了还能靠**原文**认人：原文在 md 里唯一的那些，照样收得回来。
    # （引擎一增删条目，编号就整体平移；而她那份 docx 是平移之前出的。
    #  只认编号就会把新文悄悄写到隔壁条目上 —— 错得没有任何声音。）
    by_old = {}
    for r in table:
        by_old.setdefault(norm(r[2]), []).append(r[0])
    updates, fixed, blank, same, unknown, drifted = {}, [], 0, 0, [], []
    rescued = []
    for num, loc, old, new in doc_rows:
        if num not in md_rows:
            unknown.append((num, loc))
            continue
        if not new.strip():
            blank += 1
            continue
        if norm(old) != norm(md_rows[num][2]):
            hit = by_old.get(norm(old)) or []
            if len(hit) == 1:
                rescued.append((num, hit[0]))
                num = hit[0]          # 认原文，不认编号
            else:
                drifted.append((num, loc, md_rows[num][1]))
                continue
        new, n = unsmarten(new)
        if n:
            fixed.append((num, n))
        if norm(new) == norm(md_rows[num][2]):
            same += 1
            continue
        updates[num] = new
    write_new_text(md_path, updates)
    print("收回 %s → %s" % (os.path.basename(docx_path), os.path.basename(md_path)))
    print("  写进【新文】%d 条；留空 %d 条；与原文一字不差 %d 条（当没改）。"
          % (len(updates), blank, same))
    for num, n in fixed:
        print("  ⟳ %s 的 %d 个引号还原成了「」" % (num, n))
    for num, loc in unknown:
        print("  ✗ md 里没有这个编号：%s %s" % (num, loc))
    if rescued:
        print("  ⟳ 编号漂了 %d 条，靠原文认了回来（%s…）"
              % (len(rescued), "、".join("%s→%s" % r for r in rescued[:3])))
    for num, loc, now in drifted:
        print("  ✗ 编号漂了，原文也认不回来：%s 你表里是「%s」，现在这个编号是「%s」"
              % (num, loc, now))
    if drifted:
        print("  → 这份 docx 是引擎改动之前出的。重出一份待填表，把新文搬过去再收。")
    if updates:
        print("接下来：python3 docgen.py apply %s && python3 server.py --selftest" % md_path)


def cmd_cover(docx_path, md_path, mode="unwritten"):
    """她填过的那份 docx 之外，md 里还有哪些条目该退回给她。

    mode="unwritten"（默认）—— **只有她亲手写过新文的才算她写过**。
        她留空的那些不是「认可原文」，是没写完，照样退回去。
    mode="unreviewed" —— 只退她没过目过的（留空＝沿用原文，当作认可）。
    """
    written, seen = {}, {}
    for num, loc, old, new in read_docx_rows(docx_path):
        seen[loc] = new if new.strip() else old
        if new.strip():
            written[loc] = new
    rows = read_table(md_path)
    missing = []
    for num, loc, old, _ in rows:
        if mode == "unwritten":
            hers = written.get(loc)
            if hers is None:
                missing.append((num, loc, "她没写过（留空或没见过）"))
            elif norm(hers) != norm(old):
                missing.append((num, loc, "她写过，但之后被引擎改动了"))
        else:
            fin = seen.get(loc)
            if fin is None or norm(fin) != norm(old):
                missing.append((num, loc, "位置不在她表里" if fin is None else "文本被引擎改过"))
    print("md 共 %d 条；不必退回 %d 条；退回给她 %d 条。（mode=%s）"
          % (len(rows), len(rows) - len(missing), len(missing), mode))
    for num, loc, why in missing:
        print("  · %s %s — %s" % (num, loc, why))
    print("\n--only 用的编号列表：")
    print(",".join(n for n, _, _ in missing))


def main():
    a = sys.argv[1:]
    if len(a) >= 3 and a[0] == "todocx":
        only = title = None
        rest = a[3:]
        while rest:
            if rest[0] == "--only":
                only = [s.strip() for s in rest[1].split(",") if s.strip()]
                rest = rest[2:]
            elif rest[0] == "--title":
                title = rest[1]
                rest = rest[2:]
            else:
                rest = rest[1:]
        cmd_todocx(a[1], a[2], only, title)
    elif len(a) >= 3 and a[0] == "fromdocx":
        cmd_fromdocx(a[1], a[2])
    elif len(a) >= 3 and a[0] == "cover":
        mode = "unwritten"
        if "--mode" in a:
            mode = a[a.index("--mode") + 1]
        cmd_cover(a[1], a[2], mode)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
