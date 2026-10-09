"""自动质量检查引擎。

需求文档第七节：**每次转换后必经步骤，不是可选功能**。
检查结果必须区分三类（错误 / 警告 / 提示），并且能定位到文件、章节或图表；
同时每条判据都要带上是期刊规范里的哪一条。

这里把各模块的检查汇总到同一个 IssueBag：
版式、摘要与篇幅、章节结构、声明、参考文献、图片/表格/公式，
再按输入格式追加 LaTeX 静态检查/编译 或 Word 专项检查。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import model as M
from .assets import check_assets
from .issues import (
    CAT_ABSTRACT,
    CAT_CONTENT,
    CAT_DECLARATION,
    CAT_DOCX,
    CAT_FILE,
    CAT_LAYOUT,
    CAT_LATEX,
    CAT_REFERENCE,
    CAT_STRUCTURE,
    IssueBag,
    Location,
)
from .journal import JournalConfig
from .util import Log, normalize_ws

REQUIRED_META = {
    "title": "标题",
    "authors": "作者",
    "abstract": "摘要",
    "keywords": "关键词",
}

SECTION_ALIASES = {
    "introduction": ("introduction", "引言", "前言", "background"),
    "methods": ("method", "methods", "methodology", "materials and methods", "方法", "材料与方法"),
    "results": ("result", "results", "结果"),
    "discussion": ("discussion", "讨论"),
    "conclusion": ("conclusion", "conclusions", "结论", "总结"),
    "abstract": ("abstract", "摘要"),
}


# --------------------------------------------------------------------------
# 元信息与结构
# --------------------------------------------------------------------------


def check_metadata(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag) -> None:
    f = ms.metadata
    loc = Location(file=ms.source_path)
    if not f.title:
        bag.error("MET001", CAT_CONTENT, "未识别到论文标题", location=loc,
                  suggestion="确认标题样式或 \\title{} 是否存在。")
    if not f.authors:
        bag.warn("MET002", CAT_CONTENT, "未识别到作者信息", location=loc,
                 suggestion="Word 中作者常为普通段落，LaTeX 中为 \\author{}；请人工核对。")
    elif any(not a.get("affil") for a in f.authors):
        n = sum(1 for a in f.authors if not a.get("affil"))
        bag.info("MET003", CAT_CONTENT, f"{n} 位作者未能识别到单位标记", location=loc,
                 suggestion="换模板时需要作者与单位的对应关系，请人工核对。")
    if not f.abstract:
        bag.error("MET004", CAT_CONTENT, "未识别到摘要", location=loc,
                  rule_source=cfg.rule_source("abstract.max_words"))
    if not f.keywords:
        bag.warn("MET005", CAT_CONTENT, "未识别到关键词", location=loc,
                 rule_source=cfg.rule_source("abstract.keywords_min"))


def check_abstract_and_length(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag) -> None:
    loc = Location(file=ms.source_path)
    n = ms.abstract_words
    limit = cfg.abstract_max_words
    if limit and n:
        if n > int(limit):
            bag.warn(
                "ABS001", CAT_ABSTRACT,
                f"摘要 {n} 词，超出目标期刊上限 {limit} 词（超 {n - int(limit)} 词）",
                location=loc, rule_source=cfg.rule_source("abstract.max_words"),
                suggestion="压缩摘要背景与方法部分，保留结果与结论的定量信息。",
            )
        elif n < max(30, int(limit) * 0.4):
            bag.info(
                "ABS002", CAT_ABSTRACT,
                f"摘要仅 {n} 词，明显短于上限 {limit} 词，可能信息量不足",
                location=loc, rule_source=cfg.rule_source("abstract.max_words"),
            )
    kmin = cfg.get("abstract.keywords_min")
    kmax = cfg.get("abstract.keywords_max")
    nk = len(ms.metadata.keywords)
    if kmin and nk and nk < int(kmin):
        bag.warn("ABS010", CAT_ABSTRACT, f"关键词仅 {nk} 个，少于要求的 {kmin} 个",
                 location=loc, rule_source=cfg.rule_source("abstract.keywords_min"))
    if kmax and nk > int(kmax):
        bag.warn("ABS011", CAT_ABSTRACT, f"关键词 {nk} 个，多于允许的 {kmax} 个",
                 location=loc, rule_source=cfg.rule_source("abstract.keywords_max"))

    if cfg.get("abstract.structured"):
        labels = cfg.get("abstract.required_labels") or []
        if not ms.metadata.abstract_structured:
            bag.warn(
                "ABS020", CAT_ABSTRACT,
                "目标期刊要求结构化摘要，但原稿摘要未分栏",
                location=loc, rule_source=cfg.rule_source("abstract.structured"),
                suggestion="需把现有内容重组为 " + "/".join(labels) + " 等栏目；"
                           "注意这属于实质性内容修改，须作者确认，不能凭空补造数据。",
            )
        else:
            missing = [
                lb for lb in labels
                if not any(lb.lower() in k.lower() for k in ms.metadata.abstract_structured)
            ]
            if missing:
                bag.warn(
                    "ABS021", CAT_ABSTRACT,
                    f"结构化摘要缺少栏目：{', '.join(missing)}",
                    location=loc, rule_source=cfg.rule_source("abstract.required_labels"),
                )

    bw = ms.body_words
    blimit = cfg.body_max_words
    if blimit and bw > int(blimit):
        bag.warn(
            "LEN001", CAT_ABSTRACT,
            f"正文约 {bw} 词，超出目标期刊篇幅上限 {blimit} 词（超 {bw - int(blimit)} 词）",
            location=loc, rule_source=cfg.rule_source("body.max_words"),
            suggestion="需要精简正文或把部分内容移入补充材料；这是实质性修改，须作者确认。",
        )
    ref_max = cfg.ref_max
    if ref_max and len(ms.references) > int(ref_max):
        bag.warn(
            "LEN002", CAT_REFERENCE,
            f"参考文献 {len(ms.references)} 条，超出上限 {ref_max} 条",
            location=loc, rule_source=cfg.rule_source("references.max_refs"),
        )


def check_structure(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag) -> None:
    loc = Location(file=ms.source_path)
    max_level = cfg.heading_levels_max
    if max_level:
        deep = [s for s in ms.sections if s.level > int(max_level)]
        if deep:
            bag.warn(
                "STR010", CAT_STRUCTURE,
                f"{len(deep)} 个标题层级超过目标期刊允许的 {max_level} 级",
                location=Location(
                    file=deep[0].span.file if deep[0].span else "",
                    line=deep[0].span.line if deep[0].span else 0,
                    section=deep[0].id,
                ),
                evidence="；".join(f"{s.heading}（L{s.level}）" for s in deep[:4]),
                rule_source=cfg.rule_source("body.heading_levels_max"),
                suggestion="合并过深的子标题，或改用段落内小标题。",
            )
    required = cfg.required_sections
    if required:
        headings = " | ".join(s.heading.lower() for s in ms.sections if s.level == 1)
        missing = []
        for req in required:
            # 摘要不是章节，单独判
            if req.lower() in ("abstract", "摘要"):
                if not ms.metadata.abstract:
                    missing.append(req)
                continue
            aliases = SECTION_ALIASES.get(req, (req,))
            if not any(a in headings for a in aliases):
                missing.append(req)
        if missing:
            bag.warn(
                "STR011", CAT_STRUCTURE,
                f"目标期刊要求的章节未找到：{', '.join(missing)}",
                location=loc,
                rule_source=cfg.rule_source("article_types"),
                suggestion="确认是否确实缺少该章节，或使用了不同的标题写法。",
            )
    numbering = cfg.section_numbering
    if numbering is False and any(s.number for s in ms.sections):
        bag.info(
            "STR012", CAT_STRUCTURE, "目标期刊要求章节不编号，但原稿章节带编号",
            location=loc, rule_source=cfg.rule_source("body.section_numbering"),
        )
    if not ms.sections:
        bag.error("STR001", CAT_STRUCTURE, "未识别到任何章节结构", location=loc)


def check_layout(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag) -> None:
    """比较原稿版式与目标规范。Word 有实测值，LaTeX 只能给提示。"""
    loc = Location(file=ms.source_path)
    if ms.source_format != M.FMT_DOCX or not ms.page_setup:
        return
    ps = ms.page_setup
    page = (cfg.get("layout.page") or "A4").upper()
    expect = {"A4": (21.0, 29.7), "LETTER": (21.59, 27.94), "A5": (14.8, 21.0)}.get(page)
    if expect:
        w, h = expect
        got = (ps.get("width_cm", 0), ps.get("height_cm", 0))
        if abs(got[0] - w) > 0.3 or abs(got[1] - h) > 0.3:
            bag.warn(
                "LAY001", CAT_LAYOUT,
                f"页面尺寸为 {got[0]}×{got[1]} cm，目标期刊要求 {page}（{w}×{h} cm）",
                location=loc, rule_source=cfg.rule_source("layout.page"),
            )
    margins = cfg.get("layout.margin_cm") or {}
    for key, field in (("left", "margin_left_cm"), ("right", "margin_right_cm"),
                       ("top", "margin_top_cm"), ("bottom", "margin_bottom_cm")):
        if margins.get(key) is None:
            continue
        got = ps.get(field, 0)
        if got and abs(float(got) - float(margins[key])) > 0.25:
            bag.warn(
                "LAY002", CAT_LAYOUT,
                f"{key} 页边距 {got}cm，目标规范要求 {margins[key]}cm",
                location=loc, rule_source=cfg.rule_source("layout.margin_cm"),
            )
    cols = cfg.get("layout.columns")
    if cols and ps.get("columns") and int(ps["columns"]) != int(cols):
        bag.warn(
            "LAY003", CAT_LAYOUT,
            f"栏数为 {ps['columns']}，目标期刊要求 {cols} 栏",
            location=loc, rule_source=cfg.rule_source("layout.columns"),
        )
    info = ms.metadata.doc_meta.get("style_info", {}) or {}
    bf = cfg.get("layout.body_font") or {}
    if bf.get("size_pt") and info.get("normal_size_pt"):
        if abs(float(info["normal_size_pt"]) - float(bf["size_pt"])) > 0.6:
            bag.warn(
                "LAY004", CAT_LAYOUT,
                f"正文字号 {info['normal_size_pt']}pt，目标规范要求 {bf['size_pt']}pt",
                location=loc, rule_source=cfg.rule_source("layout.body_font"),
            )
    ls = cfg.get("layout.line_spacing")
    if ls and info.get("normal_line_spacing"):
        if abs(float(info["normal_line_spacing"]) - float(ls)) > 0.15:
            bag.warn(
                "LAY005", CAT_LAYOUT,
                f"正文行距 {info['normal_line_spacing']}，目标规范要求 {ls}",
                location=loc, rule_source=cfg.rule_source("layout.line_spacing"),
            )


def check_declarations(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag) -> None:
    present = {d.kind for d in ms.declarations if d.present}
    required = cfg.required_declarations
    optional = cfg.get("declarations.optional") or []
    labels = {
        "funding": "基金资助声明",
        "conflict_of_interest": "利益冲突声明",
        "data_availability": "数据可用性声明",
        "ethics": "伦理声明",
        "author_contributions": "作者贡献声明",
    }
    for kind in required:
        if kind not in present:
            bag.warn(
                "DEC001", CAT_DECLARATION,
                f"缺少目标期刊强制要求的{labels.get(kind, kind)}（{kind}）",
                location=Location(file=ms.source_path),
                rule_source=cfg.rule_source("declarations.required"),
                suggestion="这是需要作者提供的信息，系统不会代为编造，请在稿件中补全。",
            )
    for kind in optional:
        if kind not in present:
            bag.info(
                "DEC002", CAT_DECLARATION,
                f"目标期刊建议提供{labels.get(kind, kind)}（{kind}），当前稿件没有",
                location=Location(file=ms.source_path),
                rule_source=cfg.rule_source("declarations.optional"),
            )
    for d in ms.declarations:
        if d.present and not (d.text or "").strip():
            bag.info(
                "DEC003", CAT_DECLARATION,
                f"检测到{labels.get(d.kind, d.kind)}，但未能提取到内容文本",
                location=Location(
                    file=d.span.file if d.span else "", line=d.span.line if d.span else 0
                ),
                suggestion="请人工确认该声明是否完整。",
            )


def check_docx_specific(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag) -> None:
    loc = Location(file=ms.source_path)
    if ms.has_tracked_changes:
        bag.error(
            "DOC001", CAT_DOCX, "文档包含修订记录（修订痕迹），投稿前必须接受或拒绝全部修订",
            location=loc,
            suggestion="Word 中「审阅 → 接受所有修订」后再投稿；否则期刊系统可能解析异常。",
        )
    if ms.has_comments:
        bag.warn(
            "DOC002", CAT_DOCX, "文档包含批注，批注不会随投稿文件一起提交",
            location=loc,
            suggestion="导出投稿版本时请先删除所有批注。",
        )
    notes = ms.metadata.doc_meta.get("notes_count")
    if notes:
        bag.info(
            "DOC003", CAT_DOCX,
            f"文档含 {notes} 处脚注/尾注，转换后请核对编号与位置",
            location=loc,
        )
    hyper = ms.metadata.doc_meta.get("hyperlink_count")
    if hyper:
        bag.info(
            "DOC004", CAT_DOCX, f"文档含 {hyper} 个超链接，跨格式转换时可能丢失",
            location=loc,
        )
    if ms.source_format == M.FMT_DOCX and cfg.columns > 1:
        bag.info(
            "DOC005", CAT_DOCX,
            f"目标期刊为 {cfg.columns} 栏排版，Word 单栏稿件需转换栏布局",
            location=loc, rule_source=cfg.rule_source("layout.columns"),
        )
    # 文献是否为结构化字段
    text_refs = [r for r in ms.references if r.origin == "docx_text"]
    if text_refs and ms.references:
        bag.info(
            "DOC006", CAT_DOCX,
            f"参考文献列表由普通文本解析（{len(text_refs)}/{len(ms.references)} 条），"
            "不是 Word 结构化文献字段",
            location=loc,
            suggestion="普通文本的文献条目自动转换容易误判，请在报告中标为「需人工确认」的条目上逐条核对。",
        )


def check_file_integrity(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag, root: str | Path) -> None:
    root = Path(root)
    loc = Location(file=ms.source_path)
    if ms.source_format == M.FMT_LATEX:
        if not ms.main_file:
            bag.error("FIL001", CAT_FILE, "未识别到主 .tex 文件", location=loc)
        if not Path(ms.main_file).is_file():
            bag.error("FIL002", CAT_FILE, f"主文件不存在：{ms.main_file}", location=loc)
        if ms.metadata.doc_meta.get("bib_root") and ms.bib_resources:
            for r in ms.bib_resources:
                raw = r.strip().replace("\\", "/")
                if not ((root / raw).is_file() or (root / (raw + ".bib")).is_file()):
                    bag.error(
                        "REF002", CAT_REFERENCE, f"文献库文件缺失：{r}.bib",
                        location=loc,
                        suggestion="把 .bib 一并打包，或修正 \\bibliography 的路径。",
                    )
    for w in ms.warnings:
        bag.warn("FIL010", CAT_FILE, w, location=loc)


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def run_qc(
    ms: M.Manuscript,
    cfg: JournalConfig,
    root: str | Path,
    bag: IssueBag | None = None,
    log: Log | None = None,
    known_bib_keys: set[str] | None = None,
    compile_result: dict | None = None,
    stage: str = "转换后",
) -> IssueBag:
    """对一份 Manuscript 跑完整质检。"""
    if bag is None:
        bag = IssueBag()
    log = log or Log(verbose=False)
    root = Path(root)

    check_file_integrity(ms, cfg, bag, root)
    check_metadata(ms, cfg, bag)
    check_structure(ms, cfg, bag)
    check_abstract_and_length(ms, cfg, bag)
    check_declarations(ms, cfg, bag)

    if ms.source_format == M.FMT_DOCX:
        check_layout(ms, cfg, bag)
        check_docx_specific(ms, cfg, bag)
    else:
        from .latex_check import static_check, report_compile

        static_check(root, ms, known_bib_keys, bag, log)
        if compile_result is not None:
            report_compile(compile_result, bag, root, Path(ms.main_file).name)

    # 图片 / 表格 / 公式
    check_assets(ms, cfg, bag, root, log)

    # 参考文献
    from .refs import check_references

    check_references(ms, bag, root, style_hint=cfg.ref_style)

    # 期刊配置本身的可靠性提示
    if cfg.status != "verified":
        bag.info(
            "CFG001", CAT_CONTENT,
            f"期刊配置「{cfg.name}」尚未逐条核对官方指南，本次转换的规范依据需要人工复核",
            location=Location(file=cfg.path or cfg.id),
            rule_source=cfg.rule_source("status"),
            suggestion=(
                "请打开官方 Guide for Authors 核对配置，核对完成后把配置里的 status 改为 verified。"
            ),
        )
    return bag


def summarize(bag: IssueBag) -> dict:
    c = bag.counts()
    return {
        "errors": c.get("error", 0),
        "warnings": c.get("warning", 0),
        "infos": c.get("info", 0),
        "total": len(bag),
        "pass": c.get("error", 0) == 0,
        "by_category": {k: len(v) for k, v in bag.by_category().items()},
    }
