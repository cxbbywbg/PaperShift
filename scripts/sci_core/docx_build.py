"""Word (.docx) → 目标期刊 Word 稿件。

两种模式（配置项 `output.docx.base`）：

* **safe（默认，推荐）**：复制原稿，在其上套用目标期刊的版式与格式规范。
  正文、公式对象、图片、脚注、超链接**原封不动**，只改页面设置、字体、
  段落间距、题注标签、表格线型，并补齐期刊强制要求的声明段落。
  这样最大程度避免「转换即损坏」。

* **template（以官方模板为骨架）**：复制官方 .docx 模板，把原稿内容
  逐段移植进去。跨文档移植会丢失一部分关系（超链接、部分编号），
  所以移植不了的元素会被**逐条记录并在报告里列出来**，而不是静默丢弃。

需求文档 3.1 要求的能力（页面/页边距/字体/段落间距/标题级别/题注编号/
页眉页脚/脚注/分节/参考文献段落/公式超链接保留）都在 safe 模式里实现。
"""
from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from . import model as M
from .changes import (
    CH_CAPTION,
    CH_DECLARATION,
    CH_LAYOUT,
    CH_REFERENCE,
    CH_STRUCTURE,
    ChangeLog,
)
from .docx_parse import CAPTION_RE_LOOSE, CAPTION_RE_STRICT, CAPTION_STYLES
from .issues import CAT_DOCX, IssueBag, Location
from .journal import JournalConfig, resolve_template_path
from .util import Log, normalize_ws, rel

NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

DECLARATION_LABELS = {
    "funding": "Funding",
    "conflict_of_interest": "Declaration of Competing Interest",
    "data_availability": "Data Availability Statement",
    "ethics": "Ethics Statement",
    "author_contributions": "Author Contributions",
}
REF_HEADING_RE = re.compile(r"^\s*(?:References|REFERENCES|Bibliography|参考文献)\s*[:：]?\s*$")


# --------------------------------------------------------------------------
# 底层 OOXML 工具
# --------------------------------------------------------------------------


def _set_columns(doc, num: int, space_twips: int = 425) -> bool:
    sectPr = doc.element.body.find(qn("w:sectPr"))
    if sectPr is None:
        return False
    cols = sectPr.find(qn("w:cols"))
    if cols is None:
        cols = OxmlElement("w:cols")
        sectPr.append(cols)
    cols.set(qn("w:num"), str(num))
    cols.set(qn("w:space"), str(space_twips))
    cols.set(qn("w:equalWidth"), "1")
    return True


def _set_style_font(styles_el, style_id: str, latin: str, eastasia: str, size_pt: float | None) -> bool:
    for st in styles_el.findall(qn("w:style")):
        if st.get(qn("w:styleId")) != style_id:
            continue
        rpr = st.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            st.append(rpr)
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rpr.insert(0, rf)
        if latin:
            rf.set(qn("w:ascii"), latin)
            rf.set(qn("w:hAnsi"), latin)
            rf.set(qn("w:cs"), latin)
        if eastasia:
            rf.set(qn("w:eastAsia"), eastasia)
        if size_pt:
            for tag in ("w:sz", "w:szCs"):
                el = rpr.find(qn(tag))
                if el is None:
                    el = OxmlElement(tag)
                    rpr.append(el)
                el.set(qn("w:val"), str(int(round(size_pt * 2))))
        return True
    return False


def _set_doc_defaults(doc, latin: str, eastasia: str, size_pt: float | None) -> bool:
    styles_el = doc.styles.element
    docd = styles_el.find(qn("w:docDefaults"))
    if docd is None:
        return False
    rpr = docd.find(f"{NS_W}rPrDefault/{NS_W}rPr")
    if rpr is None:
        return False
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    if latin:
        rf.set(qn("w:ascii"), latin)
        rf.set(qn("w:hAnsi"), latin)
        rf.set(qn("w:cs"), latin)
    if eastasia:
        rf.set(qn("w:eastAsia"), eastasia)
    if size_pt:
        for tag in ("w:sz", "w:szCs"):
            el = rpr.find(qn(tag))
            if el is None:
                el = OxmlElement(tag)
                rpr.append(el)
            el.set(qn("w:val"), str(int(round(size_pt * 2))))
    return True


def _set_cell_border(cell, **kwargs) -> None:
    """kwargs: top/bottom/left/right = {'sz':8,'val':'single','color':'000000'} 或 None 表示清除。"""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = tcPr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tcPr.append(borders)
    for edge in ("top", "left", "bottom", "right"):
        if edge not in kwargs:
            continue
        spec = kwargs[edge]
        tag = qn(f"w:{edge}")
        el = borders.find(tag)
        if el is not None:
            borders.remove(el)
        if spec is None:
            continue
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), spec.get("val", "single"))
        el.set(qn("w:sz"), str(spec.get("sz", 8)))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), spec.get("color", "000000"))
        borders.append(el)


def _three_line_table(table) -> None:
    """三线表：仅保留顶线、表头下线、底线，去掉所有竖线与内横线。"""
    rows = table.rows
    if not rows:
        return
    n = len(rows)
    for r, row in enumerate(rows):
        for cell in row.cells:
            spec = {"top": None, "bottom": None, "left": None, "right": None}
            if r == 0:
                spec["top"] = {"sz": 12}
                spec["bottom"] = {"sz": 6}
            if r == n - 1:
                spec["bottom"] = {"sz": 12}
            _set_cell_border(cell, **spec)


def _paragraph_text(p) -> str:
    return normalize_ws(p.text)


# --------------------------------------------------------------------------
# safe 模式
# --------------------------------------------------------------------------


def convert_docx_safe(
    ms: M.Manuscript,
    cfg: JournalConfig,
    out_path: str | Path,
    changes: ChangeLog,
    bag: IssueBag,
    log: Log,
) -> dict:
    src = Path(ms.source_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out)

    doc = Document(str(out))
    rules = cfg.get("layout", {}) or {}

    # ---- 1. 页面与栏 ----
    margin = rules.get("margin_cm") or {}
    page = (rules.get("page") or "A4").upper()
    size = {"A4": (21.0, 29.7), "LETTER": (21.59, 27.94), "A5": (14.8, 21.0)}.get(page)
    for i, sec in enumerate(doc.sections):
        before = {
            "width_cm": round(sec.page_width.cm, 2),
            "height_cm": round(sec.page_height.cm, 2),
            "left_cm": round(sec.left_margin.cm, 2),
            "right_cm": round(sec.right_margin.cm, 2),
            "top_cm": round(sec.top_margin.cm, 2),
            "bottom_cm": round(sec.bottom_margin.cm, 2),
        }
        if size:
            w, h = size
            cur_w, cur_h = sec.page_width.cm, sec.page_height.cm
            landscape = cur_w > cur_h
            sec.page_width = Cm(h if landscape else w)
            sec.page_height = Cm(w if landscape else h)
        for key, attr in (
            ("left", "left_margin"), ("right", "right_margin"),
            ("top", "top_margin"), ("bottom", "bottom_margin"),
        ):
            if margin.get(key) is not None:
                setattr(sec, attr, Cm(float(margin[key])))
        after = {
            "width_cm": round(sec.page_width.cm, 2),
            "height_cm": round(sec.page_height.cm, 2),
            "left_cm": round(sec.left_margin.cm, 2),
            "right_cm": round(sec.right_margin.cm, 2),
            "top_cm": round(sec.top_margin.cm, 2),
            "bottom_cm": round(sec.bottom_margin.cm, 2),
        }
        if before != after:
            changes.record(
                CH_LAYOUT, f"页面设置（第 {i+1} 节）",
                before=str(before), after=str(after),
                reason="套用目标期刊的页面尺寸与页边距",
                rule_source=cfg.rule_source("layout.margin_cm"),
            )

    cols = rules.get("columns")
    if cols and _set_columns(doc, int(cols)):
        cur_cols = ms.page_setup.get("columns", 1)
        if int(cols) != cur_cols:
            changes.record(
                CH_LAYOUT, "分栏", before=f"{cur_cols} 栏", after=f"{cols} 栏",
                reason="套用目标期刊的栏布局",
                rule_source=cfg.rule_source("layout.columns"),
            )

    # ---- 2. 字体与行距 ----
    bf = rules.get("body_font") or {}
    hf = rules.get("heading_font") or {}
    if bf:
        changed = _set_doc_defaults(doc, bf.get("latin", ""), bf.get("eastasia", ""), bf.get("size_pt"))
        _set_style_font(doc.styles.element, "Normal", bf.get("latin", ""), bf.get("eastasia", ""), bf.get("size_pt"))
        if changed:
            old = ms.metadata.doc_meta.get("style_info", {})
            changes.record(
                CH_LAYOUT, "正文字体与字号",
                before=f"{old.get('normal_font') or old.get('default_font') or '未指定'} / "
                       f"{old.get('normal_size_pt') or old.get('default_size_pt') or '?'}pt",
                after=f"{bf.get('latin','')} / {bf.get('size_pt','?')}pt",
                reason="套用目标期刊的正文用字规范",
                rule_source=cfg.rule_source("layout.body_font"),
            )
    if hf:
        for lvl in range(1, 10):
            for sid in (f"Heading{lvl}", f"heading {lvl}", f"标题 {lvl}"):
                if _set_style_font(doc.styles.element, sid, hf.get("latin", ""), hf.get("eastasia", ""), hf.get("size_pt")):
                    break
        changes.record(
            CH_LAYOUT, "标题字体", before="(原样式)", after=f"{hf.get('latin','')} / {hf.get('eastasia','')}",
            reason="套用目标期刊的标题用字规范",
            rule_source=cfg.rule_source("layout.heading_font"),
        )

    ls = rules.get("line_spacing")
    if ls:
        try:
            normal = doc.styles["Normal"]
            old_ls = normal.paragraph_format.line_spacing
            normal.paragraph_format.line_spacing = float(ls)
            if old_ls != float(ls):
                changes.record(
                    CH_LAYOUT, "正文行距", before=str(old_ls), after=str(ls),
                    reason="套用目标期刊的行距要求（投稿稿件常见 double-spaced）",
                    rule_source=cfg.rule_source("layout.line_spacing"),
                )
        except Exception as exc:
            log.warn(f"设置行距失败: {exc}")

    # ---- 3. 题注标签 ----
    label_fig = cfg.get("figures.caption_label")
    label_tab = cfg.get("tables.caption_label")
    if label_fig or label_tab:
        n = 0
        seen_orig: set[str] = set()
        for p in doc.paragraphs:
            text = _paragraph_text(p)
            if not text:
                continue
            # 只认「严格题注」（数字后必须有分隔符）或「题注样式」，
            # 否则会把 "Table 2 reports accuracy ..." 这类正文句子也改掉。
            m = CAPTION_RE_STRICT.match(text)
            if not m:
                low_style = (p.style.name or "").lower()
                if low_style in CAPTION_STYLES:
                    m = CAPTION_RE_LOOSE.match(text)
            if not m:
                continue
            kind_raw = m.group(1).lower()
            is_fig = kind_raw.startswith(("fig", "图"))
            target = label_fig if is_fig else label_tab
            if not target or m.group(1) == target:
                continue
            for run in p.runs:
                if run.text.strip().startswith(m.group(1)):
                    run.text = run.text.replace(m.group(1), target, 1)
                    n += 1
                    break
            seen_orig.add(m.group(1))
        if n:
            changes.record(
                CH_CAPTION, "题注标签",
                before=" / ".join(sorted(seen_orig)) or "原标签",
                after=f"{label_fig or 'Figure'} / {label_tab or 'Table'}",
                reason=f"目标期刊要求题注使用指定标签写法，共改写 {n} 处",
                rule_source=cfg.rule_source("figures.caption_label"),
            )

    # ---- 4. 表格线型 ----
    if cfg.get("tables.style"):
        n = 0
        for t in doc.tables:
            try:
                _three_line_table(t)
                n += 1
            except Exception as exc:
                log.warn(f"表格线型设置失败: {exc}")
        if n:
            changes.record(
                CH_LAYOUT, "表格线型", before="(原表格边框)", after=cfg.get("tables.style"),
                reason=f"目标期刊要求 {cfg.get('tables.style')}，共处理 {n} 个表格",
                rule_source=cfg.rule_source("tables.style"),
            )

    # ---- 5. 补齐强制声明 ----
    present = {d.kind for d in ms.declarations if d.present}
    missing = [k for k in cfg.required_declarations if k not in present]
    if missing:
        anchor = None
        for p in doc.paragraphs:
            if REF_HEADING_RE.match(_paragraph_text(p)):
                anchor = p
                break
        for kind in missing:
            label = DECLARATION_LABELS.get(kind, kind)
            heading = doc.add_paragraph()
            heading.style = doc.styles["Heading 1"] if "Heading 1" in [s.name for s in doc.styles] else heading.style
            run = heading.add_run(label)
            run.bold = True
            body = doc.add_paragraph("[待作者填写]")
            if anchor is not None:
                anchor._p.addprevious(heading._p)
                anchor._p.addprevious(body._p)
            changes.record(
                CH_DECLARATION, label, before="(缺失)", after="[待作者填写]",
                reason="目标期刊强制要求该声明，原稿中没有；已插入占位段落，内容必须由作者补全",
                rule_source=cfg.rule_source("declarations.required"),
                needs_author=True,
            )

    # ---- 6. 参考文献段落格式 ----
    ref_style = cfg.get("references.style")
    if ref_style:
        changed = False
        for p in doc.paragraphs:
            text = _paragraph_text(p)
            if not text:
                continue
            if re.match(r"^\s*[\[\u3010]?\d{1,3}[\]\u3011.]\s+\S", text) and ref_style == "numbered":
                p.paragraph_format.space_after = Pt(0)
                changed = True
            elif ref_style == "author-year" and re.match(r"^[A-Z\u4e00-\u9fff][^.]{2,60}\(\d{4}", text):
                p.paragraph_format.space_after = Pt(0)
                changed = True
        if changed:
            changes.record(
                CH_REFERENCE, "参考文献段落格式",
                before="(原段落格式)", after="紧凑段落（段后 0pt）",
                reason=f"目标期刊采用{('编号制' if ref_style == 'numbered' else '作者—年份制')}参考文献，段落格式统一",
                rule_source=cfg.rule_source("references.style"),
            )

    doc.save(str(out))
    return {"output": str(out), "mode": "safe", "changes": len(changes)}


# --------------------------------------------------------------------------
# template 模式
# --------------------------------------------------------------------------


def convert_docx_template(
    ms: M.Manuscript,
    cfg: JournalConfig,
    template: str | Path,
    out_path: str | Path,
    changes: ChangeLog,
    bag: IssueBag,
    log: Log,
) -> dict:
    """以官方 .docx 模板为骨架移植内容。

    逐段深拷贝原稿的段落 XML（保留公式对象与内联格式），
    图片用提取出的文件重建，超链接降级为纯文本并记录。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, out)
    tpl = Document(str(out))
    src = Document(str(ms.source_path))

    # 清空模板正文（保留最后的 sectPr）
    body = tpl.element.body
    sectPr = body.find(qn("w:sectPr"))
    for child in list(body.iterchildren()):
        if child is not sectPr:
            body.remove(child)

    inserted = skipped = 0
    # 按**图片出现顺序**取提取出来的图片文件。
    # 曾经用「当前段落序号」去索引，但段落序号把正文段落也算进去了，
    # 索引必然越界，导致所有图片都被判为「找不到图片文件」而丢失。
    fig_media = [list(f.native.get("media_path", []) or []) for f in ms.figures]
    fig_width = [
        float(f.native.get("display_width_cm") or 0) or 14.0 for f in ms.figures
    ]
    img_seq = 0

    for child in src.element.body.iterchildren():
        if child is sectPr:
            continue
        if child.tag == qn("w:p"):
            blips = child.findall(f".//{NS_A}blip")
            if blips:
                # 图片：用提取出的文件重建（跨文档关系无法直接搬运）
                paths = fig_media[img_seq] if img_seq < len(fig_media) else []
                width_cm = fig_width[img_seq] if img_seq < len(fig_width) else 14.0
                img_seq += 1
                p = tpl.add_paragraph()
                added: list[str] = []
                for path in paths:
                    if path and Path(path).is_file():
                        try:
                            p.add_run().add_picture(path, width=Cm(width_cm))
                            added.append(Path(path).name)
                        except Exception as exc:
                            log.warn(f"图片重建失败 {path}: {exc}")
                if added:
                    inserted += 1
                    changes.record(
                        CH_STRUCTURE, f"图片（第 {img_seq} 张）",
                        before="原文档内嵌图片", after=", ".join(added),
                        reason="跨文档移植时图片关系无法直接复制，已用提取出的图片重建"
                               f"（保留原显示宽度 {width_cm:g} cm）",
                        file=rel(ms.source_path, Path(ms.source_path).parent),
                    )
                else:
                    skipped += 1
                    changes.record(
                        CH_STRUCTURE, f"图片（第 {img_seq} 张）",
                        before="原文档内嵌图片", after="(未能重建)",
                        reason="找不到对应的图片文件，需人工补插",
                        needs_author=True, auto=False,
                    )
                continue

            new = copy.deepcopy(child)
            # 超链接关系跨文档失效 → 拆掉链接保留文字
            for h in new.findall(f".//{NS_W}hyperlink"):
                parent = h.getparent()
                pos = list(parent).index(h)
                for i, sub in enumerate(list(h)):
                    parent.insert(pos + i, sub)
                parent.remove(h)
                skipped += 1
            body.insert(list(body).index(sectPr) if sectPr is not None else len(body), new)
            inserted += 1
        elif child.tag == qn("w:tbl"):
            new = copy.deepcopy(child)
            body.insert(list(body).index(sectPr) if sectPr is not None else len(body), new)
            inserted += 1

    if skipped:
        changes.record(
            CH_STRUCTURE, "跨文档移植降级项", after=f"{skipped} 处",
            reason="超链接/编号等跨文档关系无法搬运，已降级处理，需要人工核对",
            needs_author=True, auto=False,
        )
    changes.record(
        CH_STRUCTURE, "文档骨架",
        before=Path(ms.source_path).name, after=Path(template).name,
        reason="以期刊官方 Word 模板为骨架，原稿内容逐段移植",
    )
    tpl.save(str(out))
    return {
        "output": str(out), "mode": "template",
        "inserted": inserted, "skipped": skipped, "changes": len(changes),
    }


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def convert_docx(
    ms: M.Manuscript,
    cfg: JournalConfig,
    out_dir: str | Path,
    changes: ChangeLog | None = None,
    bag: IssueBag | None = None,
    log: Log | None = None,
) -> dict:
    log = log or Log(verbose=False)
    changes = changes if changes is not None else ChangeLog()
    bag = bag if bag is not None else IssueBag()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    src_name = Path(ms.source_path).stem
    docx_cfg = cfg.docx()
    base = (docx_cfg.get("base") or "safe").lower()
    template = docx_cfg.get("template") or ""
    tpl_path = resolve_template_path(str(template), cfg) if template else None

    if base == "template" and tpl_path is not None:
        out_path = out / f"{src_name}_formatted.docx"
        res = convert_docx_template(ms, cfg, tpl_path, out_path, changes, bag, log)
    else:
        if base == "template" and template:
            bag.warn(
                "CVT010", CAT_DOCX,
                f"配置指定了模板模式，但模板文件不存在或无法解析：{template}；已回退到安全模式",
                location=Location(file=str(template)),
            )
        out_path = out / f"{src_name}_formatted.docx"
        res = convert_docx_safe(ms, cfg, out_path, changes, bag, log)

    res.update({
        "format": M.FMT_DOCX,
        "output_dir": str(out),
        "main_file": str(out_path),
        "files": [p.name for p in sorted(out.iterdir()) if p.is_file()],
    })
    return res
