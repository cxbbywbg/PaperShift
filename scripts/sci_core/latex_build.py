r"""LaTeX 项目 → 目标期刊 LaTeX 模板。

**关键设计：不对 .tex 做全文替换，也不重写正文。**

需求文档第四节明确要求「不能简单地对 .tex 文件进行全文字符串替换」。
这里的做法是：

1. 复制整个项目（图片、.bib、.cls/.sty 一并带走）；
2. 只在**已知结构的位置**做定点手术：
   - 导言区：\documentclass / \usepackage 增删
   - 元信息区：作者、单位、关键词按目标模板的命令体系**重新生成**，
     原命令整段保留在注释里（可核对、可回滚）
   - 摘要：只换包裹环境，内容原样
   - 文献：\bibliographystyle / biblatex 工作流切换
   - 章节：按期刊要求决定是否取消编号
   - 声明：补齐期刊强制要求的声明段落（占位，标记待作者填写）
3. **正文段落、公式、图表源码逐字保留**；
4. 每一步都落成一条 Change，含原因与规则来源。

期刊差异通过配置里的 `output.latex.adapter` 表达，不为某本期刊改代码。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import model as M
from .changes import (
    CH_ASSET,
    CH_CAPTION,
    CH_DECLARATION,
    CH_METADATA,
    CH_PREAMBLE,
    CH_REFERENCE,
    CH_STRUCTURE,
    ChangeLog,
)
from .issues import CAT_FILE, CAT_LATEX, IssueBag, Location
from .journal import JournalConfig
from .latex_parse import read_group, strip_comments
from .util import Log, normalize_ws, read_text, rel, write_text

# 复制项目时跳过的构建产物
SKIP_PATTERNS = (
    "*.aux", "*.log", "*.out", "*.toc", "*.bbl", "*.blg", "*.synctex.gz",
    "*.fls", "*.fdb_latexmk", "*.nav", "*.snm", "*.vrb", "*.bcf", "*.run.xml",
)

AUTHOR_COMMANDS = (
    "author", "Author", "affil", "affiliation", "affiliations", "institute",
    "address", "keywords", "keyword", "keyWords", "correspondingauthor",
    "runningtitle", "shorttitle", "markboth", "authorrunning", "titlerunning",
)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, max(0, pos)) + 1


def _fmt(template: str, **kw) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("%" + k, str(v))
    return out


# --------------------------------------------------------------------------
# 定点手术
# --------------------------------------------------------------------------


def op_documentclass(text: str, cls: str, opts: list[str], path: str, cfg: JournalConfig, changes: ChangeLog) -> str:
    m = re.search(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}", text)
    if not m:
        changes.record(
            CH_PREAMBLE, "\\documentclass", before="(缺失)", after=f"{cls}",
            reason="目标期刊要求使用其官方文档类", file=path,
            rule_source=cfg.rule_source("output.latex.documentclass"),
        )
        return text
    old_cls = m.group(2).strip()
    old_opts = [o.strip() for o in (m.group(1) or "").split(",") if o.strip()]
    new_cls = cls or old_cls
    new_opts = list(opts) if opts else old_opts
    if new_cls == old_cls and new_opts == old_opts:
        return text
    replacement = "\\documentclass" + (f"[{','.join(new_opts)}]" if new_opts else "") + f"{{{new_cls}}}"
    changes.record(
        CH_PREAMBLE, "\\documentclass",
        before=m.group(0), after=replacement,
        reason=f"切换到目标期刊的文档类（原 {old_cls} → {new_cls}）",
        file=path, line=_line_of(text, m.start()),
        rule_source=cfg.rule_source("output.latex.documentclass"),
    )
    return text[: m.start()] + replacement + text[m.end() :]


def op_packages(text: str, add: list[str], remove: list[str], path: str, cfg: JournalConfig, changes: ChangeLog) -> str:
    head_end = text.find("\\begin{document}")
    head = text[: head_end if head_end > 0 else len(text)]
    tail = text[len(head) :]

    # 已加载的宏包
    present: set[str] = set()
    for m in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", head):
        for p in m.group(1).split(","):
            present.add(p.strip())

    # 移除（注释掉而非删除，保留痕迹）
    for pkg in remove:
        if pkg not in present:
            continue
        pat = re.compile(
            r"^([ \t]*)\\(usepackage|RequirePackage)(\s*(?:\[[^\]]*\])?\s*\{[^}]*\b"
            + re.escape(pkg) + r"\b[^}]*\})",
            re.M,
        )

        def _comment(mo, pkg=pkg):
            changes.record(
                CH_PREAMBLE, f"\\usepackage{{{pkg}}}",
                before=mo.group(0).strip(),
                after="% " + mo.group(0).strip(),
                reason="该宏包与目标模板冲突（模板已自带或功能重复），已注释保留",
                file=path, line=_line_of(head, mo.start()),
                rule_source=cfg.rule_source("output.latex.packages_remove"),
            )
            return f"{mo.group(1)}% [sci-trans] 与目标模板冲突，已注释：{mo.group(0).strip()}"

        head = pat.sub(_comment, head)

    # 添加
    to_add = [p for p in add if p and p not in present]
    if to_add:
        lines = [f"\\usepackage{{{p}}}" for p in to_add]
        block = "% [sci-trans] 目标期刊模板所需的宏包\n" + "\n".join(lines) + "\n"
        last = None
        for m in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", head):
            last = m
        if last:
            insert_at = head.find("\n", last.end())
            insert_at = len(head) if insert_at < 0 else insert_at + 1
        else:
            m2 = re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", head)
            insert_at = (head.find("\n", m2.end()) + 1) if m2 else 0
        head = head[:insert_at] + block + head[insert_at:]
        for p in to_add:
            changes.record(
                CH_PREAMBLE, f"\\usepackage{{{p}}}", before="(无)", after=f"\\usepackage{{{p}}}",
                reason="目标期刊模板要求该宏包", file=path,
                rule_source=cfg.rule_source("output.latex.packages_add"),
            )
    return head + tail


def _metadata_block(ms: M.Manuscript, cfg: JournalConfig, changes: ChangeLog, path: str,
                    kw_sep: str = ", ", meta_override: dict | None = None) -> str:
    """按目标模板的命令体系生成作者/单位/关键词块。"""
    adapter = cfg.latex()
    meta = dict(meta_override) if meta_override is not None else (adapter.get("metadata", {}) or {})
    out: list[str] = []

    authors = ms.metadata.authors or []
    affils = ms.metadata.affiliations or []
    aff_map: dict[str, str] = ms.metadata.doc_meta.get("affiliation_map", {}) or {}

    def aff_key_for(author: dict) -> str:
        """把作者的 \\affil{1,2} 标记映射到 \\affiliation[affN] 的键（支持多单位）。

        支持三种原稿写法：数字标记 \\affil{1}、模板键 \\author[aff1]{}、
        以及直接内嵌单位文本 \\author{...\\thanks{School of X}}。
        """
        raw = (author.get("affil") or "").strip()
        if not raw:
            raw = (author.get("affil_text") or "").strip()
        keys: list[str] = []
        for token in re.split(r"[,\s]+", raw):
            token = token.strip()
            if not token:
                continue
            text = aff_map.get(token, "")
            if text and text in affils:
                k = f"aff{affils.index(text) + 1}"
            else:
                m = re.fullmatch(r"aff(\d+)", token)
                if m and 1 <= int(m.group(1)) <= max(1, len(affils)):
                    k = f"aff{int(m.group(1))}"
                else:
                    k = ""
                    for i, t in enumerate(affils, 1):
                        if t and (t in raw or raw in t):
                            k = f"aff{i}"
                            break
            if k and k not in keys:
                keys.append(k)
        return ",".join(keys) if keys else "aff1"

    def aff_text_for(author: dict) -> str:
        """作者的完整单位文本，供 \\author{Name\\thanks{单位}} 这类模板使用。"""
        raw = (author.get("affil") or "").strip()
        texts: list[str] = []
        for key in re.split(r"[,\s]+", raw):
            key = key.strip()
            text = aff_map.get(key, "")
            if text and text not in texts:
                texts.append(text)
        if texts:
            return " and ".join(texts)
        # 作者自带单位文本（如 \author{Name\thanks{School of ...}}）
        direct = (author.get("affil_text") or "").strip()
        return direct

    aff_line_tpl = meta.get("affiliation")
    if aff_line_tpl and affils:
        for i, a in enumerate(affils, 1):
            out.append(_fmt(aff_line_tpl, key=f"aff{i}", value=a))
            changes.record(
                CH_METADATA, f"单位 {i}", after=a,
                reason="按目标模板的 \\affiliation 命令重新生成（原命令保留在注释中）",
                file=path, rule_source=cfg.rule_source("output.latex.metadata"),
            )

    author_tpl = meta.get("author_line")
    if author_tpl and authors:
        for a in authors:
            aff_key = aff_key_for(a)
            aff_text = aff_text_for(a)
            thanks = ""
            if a.get("email"):
                thanks = "\\thanks{" + a["email"] + "}"
            line = _fmt(
                author_tpl, name=a.get("name", ""), affil_key=aff_key,
                affil=aff_text, thanks=thanks,
            )
            out.append(line)
        changes.record(
            CH_METADATA, "作者列表", after=f"{len(authors)} 位作者",
            reason="按目标模板的 \\author 命令重新生成（原命令保留在注释中）",
            file=path, rule_source=cfg.rule_source("output.latex.metadata"),
            needs_author=True,
        )

    kw_tpl = meta.get("keywords_command")
    _kw_raw = _meta_texts(ms)[2]
    if kw_tpl and _kw_raw:
        kw_value = kw_sep.join(_kw_raw)
        out.append(_fmt(kw_tpl, value=kw_value))
        changes.record(
            CH_METADATA, "关键词", after=", ".join(ms.metadata.keywords),
            reason="按目标模板的关键词命令重新生成",
            file=path, rule_source=cfg.rule_source("output.latex.metadata"),
        )

    corr_tpl = meta.get("corresponding")
    if corr_tpl and ms.metadata.corresponding:
        out.append(_fmt(corr_tpl, value=ms.metadata.corresponding))

    extra = adapter.get("extra_preamble")
    if extra:
        out.append(str(extra).strip())
        changes.record(
            CH_PREAMBLE, "期刊附加导言区", after=str(extra).strip()[:120],
            reason="目标期刊模板要求的特殊命令",
            file=path, rule_source=cfg.rule_source("output.latex.extra_preamble"),
        )
    return "\n".join(x for x in out if x)


def op_metadata(text: str, ms: M.Manuscript, cfg: JournalConfig, path: str, changes: ChangeLog) -> str:
    """替换作者/单位/关键词命令区；原命令整段注释保留。"""
    head_end = text.find("\\begin{document}")
    if head_end < 0:
        return text
    head, tail = text[:head_end], text[head_end:]

    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\\([A-Za-z]+)\s*(?:\[[^\]]*\])?\s*\{", head):
        if m.group(1) in AUTHOR_COMMANDS:
            try:
                _inner, end = read_group(head, m.end() - 1)
            except Exception:
                continue
            spans.append((m.start(), end))
    if not spans:
        # 没有可替换的作者命令 → 在 \begin{document} 之前插入生成的块
        block = _metadata_block(ms, cfg, changes, path)
        if block:
            return head.rstrip() + "\n\n% [sci-trans] 目标期刊模板的作者/单位/关键词命令\n" + block + "\n\n" + tail
        return text

    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 2:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    original = "\n".join("% " + ln for ln in head[merged[0][0] : merged[-1][1]].split("\n"))
    block = _metadata_block(ms, cfg, changes, path)
    new_region = (
        "% [sci-trans] 原作者/单位/关键词命令（保留备查，未删除）：\n"
        + original
        + "\n% [sci-trans] 以下为按目标期刊模板重新生成的元信息：\n"
        + block
    )
    changes.record(
        CH_METADATA, "作者/单位/关键词命令区",
        before=normalize_ws(head[merged[0][0] : merged[-1][1]])[:200],
        after=normalize_ws(block)[:200],
        reason="不同模板的作者信息命令体系不同，需按目标模板重新生成；原文完整保留在注释中",
        file=path, line=_line_of(head, merged[0][0]),
        rule_source=cfg.rule_source("output.latex.metadata"),
        needs_author=True,
    )
    return head[: merged[0][0]] + new_region + head[merged[-1][1] :] + tail


def op_abstract(text: str, cfg: JournalConfig, path: str, changes: ChangeLog) -> str:
    target = (cfg.latex().get("metadata", {}) or {}).get("abstract_env", "abstract")
    m = re.search(r"\\begin\s*\{(abstract\*?)\}", text)
    if not m:
        return text
    old = m.group(1)
    if old == target:
        return text
    me = re.search(r"\\end\s*\{" + re.escape(old) + r"\}", text)
    if not me:
        return text
    new = text[: m.start()] + f"\\begin{{{target}}}" + text[m.end() : me.start()] + f"\\end{{{target}}}" + text[me.end() :]
    changes.record(
        CH_STRUCTURE, "摘要环境", before=f"\\begin{{{old}}}", after=f"\\begin{{{target}}}",
        reason="目标模板使用不同的摘要环境（摘要内容未改动）",
        file=path, line=_line_of(text, m.start()),
        rule_source=cfg.rule_source("output.latex.metadata.abstract_env"),
    )
    return new


def op_bibliography(text: str, cfg: JournalConfig, path: str, changes: ChangeLog) -> str:
    bib = (cfg.latex().get("bibliography", {}) or {})
    target_style = bib.get("style") or cfg.bib_style
    tool = bib.get("tool", "bibtex")

    m = re.search(r"\\bibliographystyle\s*\{([^}]*)\}", text)
    if m and target_style and m.group(1).strip() != target_style:
        text = text[: m.start()] + f"\\bibliographystyle{{{target_style}}}" + text[m.end() :]
        changes.record(
            CH_REFERENCE, "\\bibliographystyle",
            before=m.group(0), after=f"\\bibliographystyle{{{target_style}}}",
            reason="目标期刊要求其自带的参考文献样式",
            file=path, line=_line_of(text, m.start()),
            rule_source=cfg.rule_source("references.bibtex_style"),
        )
    elif target_style and not m and "\\bibliography{" in text:
        text = text.replace("\\bibliography{", f"\\bibliographystyle{{{target_style}}}\n\\bibliography{{", 1)
        changes.record(
            CH_REFERENCE, "\\bibliographystyle", before="(缺失)", after=target_style,
            reason="原稿未指定文献样式，按目标期刊补上",
            file=path, rule_source=cfg.rule_source("references.bibtex_style"),
        )

    # biblatex 工作流切换（风险较高，标记需作者确认）
    if tool == "biblatex" and "\\bibliography{" in text:
        changes.record(
            CH_REFERENCE, "文献工作流", before="BibTeX (\\bibliography)",
            after="BibLaTeX (\\addbibresource + \\printbibliography)",
            reason="目标期刊模板采用 BibLaTeX 工作流；此项改动风险较高，请编译后核对",
            file=path, rule_source=cfg.rule_source("references.style"),
            needs_author=True, auto=False,
        )
    return text


def op_section_numbering(text: str, cfg: JournalConfig, path: str, changes: ChangeLog) -> str:
    mode = (cfg.latex().get("section_style") or "numbered").lower()
    if mode not in ("unnumbered", "starred", "none"):
        return text
    count = 0

    def _star(m):
        nonlocal count
        count += 1
        return f"\\{m.group(1)}*{{"

    new = re.sub(r"\\(section|subsection|subsubsection)\s*\{", _star, text)
    if count:
        changes.record(
            CH_STRUCTURE, "章节编号", before=f"{count} 处带编号的 \\section",
            after=f"{count} 处 \\section*（不编号）",
            reason="目标期刊要求章节不编号",
            file=path, rule_source=cfg.rule_source("output.latex.section_style"),
        )
    return new


def op_declarations(text: str, ms: M.Manuscript, cfg: JournalConfig, path: str, changes: ChangeLog) -> str:
    required = cfg.required_declarations
    if not required:
        return text
    present = {d.kind for d in ms.declarations if d.present}
    missing = [k for k in required if k not in present]
    if not missing:
        return text
    labels = {
        "funding": "Funding",
        "conflict_of_interest": "Declaration of Competing Interest",
        "data_availability": "Data Availability Statement",
        "ethics": "Ethics Statement",
        "author_contributions": "Author Contributions",
    }
    # 插入锚点：投稿声明属于正文末尾，必须排在参考文献命令**之前**。
    # 注意顺序——\bibliographystyle 在 \bibliography 之前，若把 \bibliography
    # 排在前面匹配，声明会被夹到这两条命令中间，既不合规范也容易编译报错。
    anchor = None
    for pat in (
        r"\\bibliographystyle\s*\{",
        r"\\begin\s*\{thebibliography\}",
        r"\\printbibliography",
        r"\\bibliography\s*\{",
        r"\\end\s*\{document\}",
    ):
        m = re.search(pat, text)
        if m:
            anchor = m
            break
    decl_tpl = cfg.latex().get("declarations") or {}
    block = ["% [sci-trans] 目标期刊强制要求的声明（内容需作者填写）"]
    for k in missing:
        label = labels.get(k, k)
        tpl = decl_tpl.get(k) if isinstance(decl_tpl, dict) else None
        if tpl:
            block.append(_fmt(str(tpl), value="[待作者填写]"))
        else:
            block.append(f"\\section*{{{label}}}")
            block.append("[待作者填写]")
        changes.record(
            CH_DECLARATION, label, before="(缺失)", after="[待作者填写]",
            reason="目标期刊要求该声明，原稿中没有；已插入占位段落，内容必须由作者补全",
            file=path, rule_source=cfg.rule_source("declarations.required"),
            needs_author=True,
        )
    block_text = "\n".join(block) + "\n"
    if anchor:
        head = text[: anchor.start()].rstrip("\n")
        return f"{head}\n\n{block_text}\n{text[anchor.start():]}"
    return text.rstrip("\n") + "\n\n" + block_text


def op_caption_labels(text: str, cfg: JournalConfig, path: str, changes: ChangeLog) -> str:
    """题注标签改写（例如 Figure → Fig.）。

    只在 \\caption 参数**开头**是 Figure/Table 字样时才改，避免误伤正文；
    可选参数（短题注）与其余内容原样保留。
    """
    label = cfg.get("figures.caption_label")
    if not label:
        return text
    edits: list[tuple[int, int, str]] = []
    for m in re.finditer(r"\\caption\s*(?:\[[^\]]*\])?\s*\{", text):
        brace = m.end() - 1
        inner, end = read_group(text, brace)
        mm = re.match(r"^(\s*)(Figure|Fig\.|TABLE|Table)(\s*\d)", inner)
        if mm and mm.group(2) != label:
            new_inner = mm.group(1) + label + inner[mm.end(2) :]
            edits.append((brace + 1, end - 1, new_inner))
    for s, e, new in reversed(edits):
        text = text[:s] + new + text[e:]
    if edits:
        changes.record(
            CH_CAPTION, "题注标签", before="Figure / Table", after=label,
            reason=f"目标期刊要求题注使用 {label} 标签，共改写 {len(edits)} 处",
            file=path, rule_source=cfg.rule_source("figures.caption_label"),
        )
    return text


def op_graphicspath(text: str, path: str, changes: ChangeLog) -> str:
    """确认图片路径可用；不改变目录结构，只做提示。"""
    return text


# --------------------------------------------------------------------------
# template 模式：以官方 LaTeX 模板为骨架
# --------------------------------------------------------------------------


class TemplateError(Exception):
    """模板结构无法识别 —— 调用方应回退到定点手术模式，而不是硬写。"""


# 参考文献命令：正文结束的标志，也是声明与文献块的插入锚点
BIB_ANCHORS = (
    r"\\bibliographystyle\s*\{",
    r"\\begin\s*\{thebibliography\}",
    r"\\printbibliography",
    r"\\addbibresource\s*\{",
    r"\\bibliography\s*\{",
    r"\\end\s*\{document\}",
)

KEYWORD_ENVS = ("keyword", "keywords", "IEEEkeywords", "keyWords")

# 元信息区里属于「作者/单位」体系的命令：这些按目标模板的命令体系整体重建。
# 注意不要放 "begin"/"abstract"/"keyword" —— 那会把 \begin{abstract} 这类
# 环境起始标记误当成作者命令吞掉。
FRONT_AUTHOR_CMDS = frozenset({
    "author", "authors", "affiliation", "affiliations", "affil", "address",
    "institute", "ead", "email", "corref", "cortext", "fntext", "tnotetext",
    "tnoteref", "fnref", "nonumnote", "thanks", "correspondingauthor",
    "markboth", "authorrunning", "titlerunning", "runningtitle", "shorttitle",
    "pacs", "subjclass", "dedication", "preprint", "preprintfooter",
})

# 原稿导言区里需要一并带入模板的自定义定义（正文依赖它们）
PREAMBLE_CARRY_RE = re.compile(
    r"^[ \t]*\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|"
    r"DeclareMathOperator\*?|newtheorem|graphicspath|DeclarePairedDelimiter|"
    r"newenvironment|renewenvironment|def)\b.*$",
    re.M,
)

TEX_SPECIALS = {
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}",
}

# 从模板目录复制辅助文件时只认这些后缀 —— 模板常被解压在 Downloads 这类
# 混杂目录里，无差别复制会污染结果包（也有隐私问题）。
TEMPLATE_ASSET_SUFFIXES = frozenset({
    # LaTeX 支持文件
    ".cls", ".sty", ".bst", ".bbx", ".cbx", ".def", ".cfg", ".clo", ".fd",
    ".ldf", ".ist", ".dtx", ".ins", ".eps", ".bib",
    # 模板自带的图片/示例图
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff",
})
TEMPLATE_ASSET_MAX_BYTES = 20 * 1024 * 1024


def _tex_escape(s: str) -> str:
    """Word 来源的纯文本转义成 LaTeX。LaTeX 来源不做转义（本来就是 LaTeX）。"""
    out: list[str] = []
    for ch in s or "":
        if ch in TEX_SPECIALS:
            out.append(TEX_SPECIALS[ch])
        elif ch == "~":
            out.append(r"\textasciitilde{}")
        elif ch == "^":
            out.append(r"\textasciicircum{}")
        elif ch == "\\":
            out.append(r"\textbackslash{}")
        else:
            out.append(ch)
    return "".join(out)


def _meta_texts(ms: M.Manuscript) -> tuple[str, str, list[str]]:
    """返回 (标题, 摘要, 关键词)。

    LaTeX 来源一律用**原文**（title_raw / abstract_raw / keywords_raw）——
    纯文本字段是 detex 的结果，直接拿来做模板移植会把 `\\method{}`、
    `41.7\\%`、`$1.9\\times$` 抹成 ` `、`41.7`、`1.9$times$`。
    没有原文时（如 Word 来源）退回纯文本并做 LaTeX 转义。
    """
    if ms.source_format == M.FMT_LATEX:
        title = ms.metadata.title_raw or ms.metadata.title or ""
        abstract = ms.metadata.abstract_raw or ms.metadata.abstract or ""
        kws = list(ms.metadata.keywords_raw) or list(ms.metadata.keywords or [])
        return title, abstract, kws
    return (
        _tex_escape(ms.metadata.title or ""),
        _tex_escape(ms.metadata.abstract or ""),
        [_tex_escape(k) for k in (ms.metadata.keywords or [])],
    )


def _find_env_span(text: str, env: str, start: int = 0) -> tuple[int, int] | None:
    """返回 \\begin{env} 起点到 \\end{env} 终点的区间（含两端）；找不到返回 None。"""
    mb = re.compile(r"\\begin\s*\{" + re.escape(env) + r"\}").search(text, start)
    if not mb:
        return None
    me = re.compile(r"\\end\s*\{" + re.escape(env) + r"\}").search(text, mb.end())
    if not me:
        return None
    return mb.start(), me.end()


def _first_env_span(text: str, envs, start: int = 0) -> tuple[int, int, str] | None:
    best: tuple[int, int, str] | None = None
    for env in envs:
        sp = _find_env_span(text, env, start)
        if sp and (best is None or sp[0] < best[0]):
            best = (sp[0], sp[1], env)
    return best


def _merge_spans(spans: list[tuple[int, int]], text: str) -> list[list[int]]:
    """把相邻命令的区间合并成整块（只隔空白即视为相邻）。"""
    spans = sorted(spans)
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and not text[merged[-1][1]:s].strip():
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _resolve_template(spec: str, cfg: JournalConfig | None = None,
                      log: Log | None = None):
    """兼容入口：实际解析逻辑在 journal.resolve_template（含压缩包解包）。"""
    from .journal import resolve_template

    return resolve_template(spec, cfg, fmt="latex", log=log)


def _resolve_template_path(spec: str, cfg: JournalConfig | None = None) -> Path | None:
    """只要路径的旧入口，保留给外部调用。"""
    return _resolve_template(spec, cfg).path


def _copy_project(src_root: Path, out: Path, skip: set[Path] | None = None) -> int:
    """复制原稿项目文件（图片、.bib、章节源码），跳过构建产物。"""
    skip = skip or set()
    copied = 0
    for p in sorted(src_root.rglob("*")):
        if p.is_dir():
            continue
        if any(part in (".git", "__pycache__", ".sci-trans", "submission") for part in p.parts):
            continue
        if any(p.match(pat) for pat in SKIP_PATTERNS):
            continue
        relp = p.relative_to(src_root)
        if relp in skip:
            continue
        target = out / relp
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        copied += 1
    return copied


def _copy_template_assets(tpl_dir: Path, out: Path, log: Log) -> list[str]:
    """复制模板目录里的 **LaTeX 支持文件**（.cls/.bst/.sty/图片等）。

    两条硬约束：

    * **不复制 .tex** —— 结果包的主文件由本模块生成，多带一份模板 .tex
      会干扰后续「主文件识别」，也会让质检扫到无关文件。
    * **只复制 LaTeX 支持文件后缀** —— 官方模板常常解压在 `~/Downloads`
      这类混着大量无关文件的目录里；无差别复制会把整个下载目录塞进结果包，
      既污染交付物也有隐私问题。
    """
    copied: list[str] = []
    skipped = 0
    if not tpl_dir.is_dir():
        return copied
    for p in sorted(tpl_dir.rglob("*")):
        if p.is_dir() or p.name.startswith(".") or "__MACOSX" in p.parts:
            continue
        if p.suffix.lower() == ".tex":
            continue
        if p.suffix.lower() not in TEMPLATE_ASSET_SUFFIXES:
            skipped += 1
            continue
        try:
            if p.stat().st_size > TEMPLATE_ASSET_MAX_BYTES:
                skipped += 1
                log.warn(f"模板辅助文件过大已跳过（{p.stat().st_size // 1024} KB）：{p.name}")
                continue
        except OSError:
            continue
        target = out / p.relative_to(tpl_dir)
        if target.exists():
            log.warn(f"模板辅助文件已存在同名文件，保留原稿版本：{target.name}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        copied.append(str(p.relative_to(tpl_dir)))
    if skipped:
        log.step(f"模板目录里另有 {skipped} 个非 LaTeX 支持文件未复制（避免污染结果包）")
    return copied


def _carry_preamble(src_head: str, tpl_head: str, cfg: JournalConfig, path: str,
                    changes: ChangeLog) -> str:
    """把原稿导言区里「模板没有但正文依赖」的宏包与自定义定义带进模板。

    不带这些，正文里的 \\method{} 之类自定义命令会直接编译失败。
    """
    tpl_pkgs: set[str] = set()
    for m in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", tpl_head):
        for x in m.group(1).split(","):
            tpl_pkgs.add(x.strip())
    remove = set(cfg.latex().get("packages_remove") or [])

    carry: list[str] = []
    seen: set[str] = set()

    for m in re.finditer(r"\\usepackage\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}", src_head):
        names = [x.strip() for x in m.group(2).split(",") if x.strip()]
        missing = [n for n in names if n not in tpl_pkgs and n not in remove]
        if not missing:
            continue
        opt = f"[{m.group(1)}]" if m.group(1) else ""
        line = f"\\usepackage{opt}{{{','.join(missing)}}}"
        if line in seen:
            continue
        seen.add(line)
        carry.append(line)
        for n in missing:
            changes.record(
                CH_PREAMBLE, f"\\usepackage{{{n}}}", before="(模板未加载)",
                after=f"\\usepackage{opt}{{{n}}}",
                reason="原稿正文依赖该宏包，目标模板未加载；已补入以保证结果可编译",
                file=path, rule_source=cfg.rule_source("output.latex.base"),
            )

    for m in PREAMBLE_CARRY_RE.finditer(src_head):
        line = m.group(0).strip()
        if not line or line.startswith("%") or line in seen:
            continue
        seen.add(line)
        carry.append(line)
        changes.record(
            CH_PREAMBLE, "原稿自定义宏/设置", after=line[:100],
            reason="原稿正文依赖该定义，目标模板中没有；已带入导言区（位置在 \\begin{document} 之前）",
            file=path, rule_source=cfg.rule_source("output.latex.base"),
        )

    if not carry:
        return tpl_head
    block = (
        "% [sci-trans] 以下导言区命令来自原稿：模板未提供，但正文依赖它们\n"
        + "\n".join(carry) + "\n"
    )
    anchor = re.search(r"\\begin\s*\{document\}", tpl_head)
    if anchor:
        return tpl_head[: anchor.start()].rstrip("\n") + "\n" + block + "\n" + tpl_head[anchor.start():]
    return tpl_head + "\n" + block


def _mask_comments(text: str) -> str:
    """把注释内容替换成等长空格（保留换行），让正则不会匹配到被注释掉的命令。

    注释掉的 `% \\begin{abstract}` 若被当成真命令，会把整段元信息吞掉——
    这是实测踩过的坑。
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2          # 跳过转义字符（如 \% 不是注释开始）
            continue
        if ch == "%":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)


def _infer_metadata_templates(region: str) -> dict:
    """从模板自带的示例元信息里推断命令写法，供「没有期刊配置」时兜底。

    模板里写了 `\\author[aff1]{Template Author}`，就说明该文档类的作者命令是
    带标签的形式；照这个写法生成，比硬编码某本期刊的语法可靠。配置里显式
    写了 metadata 时以配置为准。
    """
    masked = _mask_comments(region)
    tpl: dict = {}
    if re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{", masked):
        tpl["title"] = "\\title{%value}"

    ma = re.search(r"\\author\s*(\[[^\]]*\])?\s*\{", masked)
    if ma:
        tpl["author_line"] = "\\author[%affil_key]{%name}" if ma.group(1) else "\\author{%name}"

    maf = re.search(r"\\(affiliation|affil|institute|address)\s*(\[[^\]]*\])?\s*\{", masked)
    if maf:
        cmd = maf.group(1)
        tpl["affiliation"] = (
            f"\\{cmd}[%key]{{%value}}" if maf.group(2) else f"\\{cmd}{{%value}}"
        )

    kw = _first_env_span(masked, KEYWORD_ENVS)
    if kw:
        tpl["keywords_command"] = f"\\begin{{{kw[2]}}}%value\\end{{{kw[2]}}}"
    return tpl


def _rewrite_front_region(region: str, ms: M.Manuscript, cfg: JournalConfig, path: str,
                          changes: ChangeLog) -> str:
    """在模板的元信息区里做定点替换，保留模板原有的其它命令。

    处理：标题、作者/单位命令体系、摘要环境、关键词环境。
    做法是**先在原始文本上算好所有替换区间，再从后往前落笔** —— 这样前面
    的替换不会让后面的位置失效，也不会出现「替换出来的内容又被下一次
    搜索命中」的问题。查找一律在「注释已屏蔽」的副本上进行。
    """
    # 命令写法：先按模板示例推断，再用期刊配置覆盖（配置更明确）
    meta = _infer_metadata_templates(region)
    meta.update(cfg.latex().get("metadata") or {})
    title, abstract, keywords = _meta_texts(ms)
    kw_sep = meta.get("keyword_sep") or (r" \sep " if r"\sep" in region else ", ")

    masked = _mask_comments(region)
    edits: list[tuple[int, int, str]] = []   # (start, end, new_text)，均为原始坐标
    inserts: list[tuple[int, str]] = []      # 需要补入的内容及其插入位置

    # ---- 1. 标题 ----
    tm = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{", masked)
    title_done = False
    if tm:
        try:
            _inner, end = read_group(region, tm.end() - 1)
        except Exception:
            end = -1
        if end > 0:
            if title:
                edits.append((tm.start(), end, "\\title{" + title + "}"))
                changes.record(
                    CH_METADATA, "标题", after=title,
                    reason="套用目标模板的 \\title 命令（标题文字取自原稿，未改写）",
                    file=path, rule_source=cfg.rule_source("output.latex.metadata.title"),
                )
            else:
                edits.append((
                    tm.start(), end,
                    "% [sci-trans] 模板标题占位已移除（原稿未解析到标题）",
                ))
                changes.record(
                    CH_METADATA, "标题", before=region[tm.start():end][:60], after="(移除占位)",
                    reason="原稿未解析到标题，模板的示例标题已移除，需作者补填",
                    file=path, rule_source=cfg.rule_source("output.latex.metadata.title"),
                    needs_author=True, auto=False,
                )
            title_done = True

    # ---- 2. 作者 / 单位命令体系 ----
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\\([A-Za-z]+)\s*(?:\[[^\]]*\])?\s*\{", masked):
        if m.group(1) not in FRONT_AUTHOR_CMDS:
            continue
        try:
            _inner, end = read_group(region, m.end() - 1)
        except Exception:
            continue
        spans.append((m.start(), end))

    block = _metadata_block(ms, cfg, changes, path, kw_sep=kw_sep, meta_override=meta)
    # 生成块里的关键词行由「关键词环境」分支单独处理，这里先摘出来避免重复
    kw_cmd = meta.get("keywords_command")
    if kw_cmd and block:
        kw_prefix = str(kw_cmd).split("%")[0]
        kept = [ln for ln in block.split("\n") if not ln.startswith(kw_prefix)]
        block = "\n".join(kept).strip("\n")

    if spans:
        merged = _merge_spans(spans, region)
        s, e = merged[0][0], merged[-1][1]
        repl = (
            "% [sci-trans] 模板示例作者/单位命令（保留备查）：\n"
            + "\n".join("% " + ln for ln in region[s:e].split("\n")).rstrip()
            + "\n% [sci-trans] 按目标模板命令体系重新生成的作者/单位：\n"
            + block
        )
        edits.append((s, e, repl))
        changes.record(
            CH_METADATA, "作者/单位命令区",
            before=normalize_ws(region[s:e])[:180], after=normalize_ws(block)[:180],
            reason="目标模板使用自己的作者/单位命令体系；已按模板重建，示例内容完整保留在注释中",
            file=path, rule_source=cfg.rule_source("output.latex.metadata"),
            needs_author=True,
        )
    elif block:
        inserts.append((len(region), block + "\n"))
        changes.record(
            CH_METADATA, "作者/单位命令区", after=normalize_ws(block)[:180],
            reason="目标模板元信息区没有作者/单位命令，已按配置追加",
            file=path, rule_source=cfg.rule_source("output.latex.metadata"),
            needs_author=True,
        )

    if not title_done and title:
        inserts.append((0, "\\title{" + title + "}\n"))
        changes.record(
            CH_METADATA, "标题", after=title,
            reason="目标模板元信息区没有 \\title 命令，已补入",
            file=path, rule_source=cfg.rule_source("output.latex.metadata.title"),
        )

    # ---- 3. 摘要环境 ----
    abs_span = _find_env_span(masked, "abstract") or _find_env_span(masked, "abstract*")
    if abs_span:
        s, e = abs_span
        env_name = "abstract*" if region[s:s + 24].lstrip("\\begin{").startswith("abstract*") else "abstract"
        if abstract:
            edits.append((s, e, f"\\begin{{{env_name}}}\n{abstract}\n\\end{{{env_name}}}"))
            changes.record(
                CH_METADATA, "摘要", after=abstract[:120] + ("…" if len(abstract) > 120 else ""),
                reason="套用目标模板的摘要环境（摘要文字取自原稿，未改写）",
                file=path, rule_source=cfg.rule_source("output.latex.metadata.abstract_env"),
            )
        else:
            edits.append((
                s, e,
                f"% [sci-trans] 模板摘要占位已清空（原稿未解析到摘要）\n"
                f"\\begin{{{env_name}}}\n[待作者填写]\n\\end{{{env_name}}}",
            ))
            changes.record(
                CH_METADATA, "摘要", after="[待作者填写]",
                reason="原稿未解析到摘要，模板示例摘要已清空，需作者补填",
                file=path, rule_source=cfg.rule_source("output.latex.metadata.abstract_env"),
                needs_author=True, auto=False,
            )
    elif abstract:
        inserts.append((len(region), "\\begin{abstract}\n" + abstract + "\n\\end{abstract}\n"))
        changes.record(
            CH_METADATA, "摘要", after=abstract[:120],
            reason="目标模板没有摘要环境，已按通用写法补入",
            file=path, rule_source=cfg.rule_source("output.latex.metadata.abstract_env"),
        )

    # ---- 4. 关键词环境 ----
    kw_span = _first_env_span(masked, KEYWORD_ENVS)
    kw_value = kw_sep.join(keywords) if keywords else ""
    if kw_span:
        s, e, env = kw_span
        body = kw_value or "[待作者填写]"
        edits.append((s, e, f"\\begin{{{env}}}\n{body}\n\\end{{{env}}}"))
        if keywords:
            changes.record(
                CH_METADATA, "关键词", after=", ".join(keywords),
                reason="套用目标模板的关键词环境（关键词取自原稿，未改写）",
                file=path, rule_source=cfg.rule_source("output.latex.metadata.keywords"),
            )
        else:
            changes.record(
                CH_METADATA, "关键词", after="[待作者填写]",
                reason="原稿未解析到关键词，模板示例关键词已清空，需作者补填",
                file=path, rule_source=cfg.rule_source("output.latex.metadata.keywords"),
                needs_author=True, auto=False,
            )
    elif keywords:
        line = meta.get("keywords_command") or "\\begin{keyword}%value\\end{keyword}"
        inserts.append((len(region), _fmt(str(line), value=kw_value) + "\n"))
        changes.record(
            CH_METADATA, "关键词", after=", ".join(keywords),
            reason="目标模板没有关键词环境，已按配置补入",
            file=path, rule_source=cfg.rule_source("output.latex.metadata.keywords"),
        )

    # ---- 5. 落笔：替换从后往前，插入从后往前 ----
    out = region
    for s, e, new in sorted(edits, key=lambda x: -x[0]):
        out = out[:s] + new + out[e:]
    for pos, new in sorted(inserts, key=lambda x: -x[0]):
        out = out[:pos] + new + out[pos:]
    return out


def _body_region(main_text: str) -> tuple[str, str]:
    """从原稿主文件里切出 (正文, 文献区)。

    正文起点取「元信息结束」之后（\\maketitle / \\end{abstract} / \\end{keyword}
    / \\end{frontmatter} 中最靠后的一个）；终点取第一条参考文献命令。
    """
    doc = re.search(r"\\begin\s*\{document\}", main_text)
    base = doc.end() if doc else 0
    ends: list[int] = []
    for pat in (
        r"\\maketitle",
        r"\\end\s*\{abstract\*?\}",
        r"\\end\s*\{(?:keyword|keywords|IEEEkeywords)\}",
        r"\\end\s*\{frontmatter\}",
    ):
        for m in re.finditer(pat, main_text):
            if m.end() >= base:
                ends.append(m.end())
    body_start = max(ends) if ends else base

    body_end = len(main_text)
    for pat in BIB_ANCHORS:
        m = re.search(pat, main_text[body_start:])
        if m:
            body_end = min(body_end, body_start + m.start())

    body = main_text[body_start:body_end]
    tail = main_text[body_end:]
    tail = re.sub(r"\\end\s*\{document\}.*$", "", tail, flags=re.S)
    return body.strip("\n"), tail.strip("\n")


def convert_latex_template(
    ms: M.Manuscript,
    cfg: JournalConfig,
    template: str | Path,
    out_dir: str | Path,
    changes: ChangeLog,
    bag: IssueBag,
    log: Log,
) -> dict:
    """以官方 LaTeX 模板为骨架移植原稿内容。

    保留模板的 `\\documentclass` 与宏包体系（即期刊真正要求的版式），
    把原稿的标题、作者、单位、摘要、关键词、正文、声明、文献命令移植进去；
    模板目录里的 `.cls`/`.bst`/`.sty` 一并复制，使结果包可独立编译。
    """
    tpl_path = Path(template)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    tpl_text = read_text(tpl_path)
    src_root = Path(ms.main_file).parent
    src_main = Path(ms.main_file)
    main_rel = src_main.relative_to(src_root)
    main_text = read_text(src_main)

    doc_m = re.search(r"\\begin\s*\{document\}", tpl_text)
    if not doc_m:
        raise TemplateError(
            f"模板 {tpl_path.name} 里找不到 \\begin{{document}}，无法定位正文插入点"
        )

    # ---- 1. 复制原稿项目（主文件除外，稍后由本函数生成）----
    copied = _copy_project(src_root, out, skip={main_rel})
    log.step(f"已复制 {copied} 个原稿文件到 {out}")

    # ---- 2. 复制模板辅助文件（.cls/.bst/.sty 等）----
    aux = _copy_template_assets(tpl_path.parent, out, log)
    if aux:
        changes.record(
            CH_ASSET, "模板辅助文件", after=f"{len(aux)} 个（{', '.join(aux[:6])}）",
            reason="目标模板的文档类/文献样式文件随结果包一起提供，保证可独立编译",
            file=main_rel.as_posix(), rule_source=cfg.rule_source("output.latex.template"),
        )
        log.step(f"已复制 {len(aux)} 个模板辅助文件（.cls/.bst 等）")

    # ---- 3. 定位模板结构 ----
    i_doc_end = doc_m.end()
    fm = _find_env_span(tpl_text, "frontmatter", i_doc_end)
    if fm:
        head = tpl_text[: fm[0]]                       # 导言区 + \begin{document}（含 \maketitle 等）
        front_src = tpl_text[fm[0]: fm[1]]             # \begin{frontmatter} … \end{frontmatter}
        after_front = tpl_text[fm[1]:]
        fm_open, fm_close = "\\begin{frontmatter}", "\\end{frontmatter}"
    else:
        # 无 frontmatter：以第一个 \section 为正文起点，之前全部视为「头部」
        sec = re.search(r"\\(?:section|chapter)\*?\s*\{", tpl_text[i_doc_end:])
        cut = i_doc_end + sec.start() if sec else i_doc_end
        head = tpl_text[:cut]
        front_src = ""
        after_front = tpl_text[cut:]
        fm_open = fm_close = ""

    # ---- 4. 把原稿依赖的导言区定义带进模板 ----
    src_head_end = main_text.find("\\begin{document}")
    src_head = main_text[:src_head_end] if src_head_end > 0 else ""
    head = _carry_preamble(src_head, head, cfg, main_rel.as_posix(), changes)

    # ---- 5. 元信息区重写 ----
    if fm:
        inner = front_src[len(fm_open): -len(fm_close)]
        inner_new = _rewrite_front_region(inner, ms, cfg, main_rel.as_posix(), changes)
        front_new = fm_open + "\n" + inner_new.strip("\n") + "\n" + fm_close + "\n"
    else:
        # 无 frontmatter：在 head 里替换元信息命令，并补出摘要/关键词
        head = _rewrite_front_region(head, ms, cfg, main_rel.as_posix(), changes)
        front_new = ""

    # ---- 6. 正文与文献 ----
    body, ms_bib = _body_region(main_text)
    if not body.strip():
        bag.warn(
            "CVT021", CAT_LATEX, "原稿正文为空，模板骨架里未插入任何正文内容",
            location=Location(file=main_rel.as_posix()),
            suggestion="确认原稿主文件里 \\begin{document} 之后确实有正文。",
        )

    body_new = body
    # 模板自带示例正文（frontmatter 之后到文献命令之间）整段丢弃，换成原稿正文
    tail_after = re.sub(
        r"\A.*?(?=\\(?:bibliographystyle|bibliography|begin\s*\{thebibliography\}|printbibliography|addbibresource))",
        "", after_front, flags=re.S,
    ) if re.search(r"\\(?:bibliographystyle|bibliography|begin\s*\{thebibliography\}|printbibliography|addbibresource)", after_front) else ""
    if not tail_after.strip():
        # 模板尾部没有可复用的文献区 → 用原稿的文献区
        tail_after = ms_bib
    else:
        changes.record(
            CH_STRUCTURE, "文献区", before="模板示例文献",
            after="保留模板的文献命令，指向原稿文献库",
            reason="目标模板的文献命令（\\bibliographystyle/\\bibliography）被保留",
            file=main_rel.as_posix(), rule_source=cfg.rule_source("output.latex.template"),
        )

    # ---- 7. 声明补齐（排在参考文献命令之前）----
    body_new = op_declarations(body_new, ms, cfg, main_rel.as_posix(), changes)

    # ---- 8. 文献样式 ----
    # 模板模式以**模板自带的样式**为准：模板通常随附了对应的 .bst，
    # 强行换成配置里的样式会指向一个不存在的文件。不一致时提示人工确认。
    bib_cfg = cfg.latex().get("bibliography", {}) or {}
    target_style = bib_cfg.get("style") or cfg.bib_style
    m_style = re.search(r"\\bibliographystyle\s*\{([^}]*)\}", tail_after)
    if m_style:
        cur = m_style.group(1).strip()
        if target_style and cur and cur != target_style:
            changes.record(
                CH_REFERENCE, "\\bibliographystyle", before=cur, after=cur,
                reason=f"模板自带文献样式 {cur}，与期刊配置的 {target_style} 不一致；"
                       "已保留模板样式（模板随附对应的 .bst），需人工确认该用哪一个",
                file=main_rel.as_posix(), rule_source=cfg.rule_source("references.bibtex_style"),
                needs_author=True, auto=False,
            )
    elif target_style:
        tail_after = tail_after.rstrip() + f"\n\\bibliographystyle{{{target_style}}}\n"
        changes.record(
            CH_REFERENCE, "\\bibliographystyle", before="(模板未指定)", after=target_style,
            reason="模板未指定参考文献样式，按期刊配置补上",
            file=main_rel.as_posix(), rule_source=cfg.rule_source("references.bibtex_style"),
        )
    # 原稿的文献库路径要能被找到（模板里通常是注释掉的）
    for bibres in (ms.bib_resources or []):
        stem = Path(bibres.strip().replace("\\", "/")).name
        if f"\\bibliography{{{stem}}}" not in tail_after:
            tail_after = tail_after.rstrip() + f"\n\\bibliography{{{stem}}}\n"
            changes.record(
                CH_REFERENCE, "\\bibliography", before="(模板中为注释/占位)", after=stem,
                reason="把模板的文献命令指向原稿的 .bib 文件",
                file=main_rel.as_posix(), rule_source=cfg.rule_source("references.bibtex_style"),
            )

    # ---- 9. 章节编号策略 ----
    body_new = op_section_numbering(body_new, cfg, main_rel.as_posix(), changes)
    # ---- 10. 题注标签 ----
    body_new = op_caption_labels(body_new, cfg, main_rel.as_posix(), changes)

    # 模板模式不重复添加期刊配置要求的宏包：模板的导言区就是权威，
    # 重复 `\usepackage` 容易触发 option clash。这里明确说明"没做这件事"。
    if cfg.latex().get("packages_add"):
        changes.record(
            CH_PREAMBLE, "期刊配置要求的宏包", before=", ".join(cfg.latex()["packages_add"]),
            after="(未强制添加)",
            reason="模板模式以官方模板的导言区为准；模板已声明的宏包不重复添加，"
                   "以免选项冲突。若模板缺少某必需宏包，请手动补入。",
            file=main_rel.as_posix(), rule_source=cfg.rule_source("output.latex.packages_add"),
        )

    # ---- 11. 拼装 ----
    parts = [head.rstrip("\n")]
    if front_new.strip():
        parts.append(front_new.rstrip("\n"))
    parts.append(body_new.strip("\n"))
    if tail_after.strip():
        parts.append(tail_after.strip("\n"))
    else:
        parts.append("\\end{document}")
    merged = "\n\n".join(p for p in parts if p) + "\n"
    if "\\end{document}" not in merged:
        merged = merged.rstrip("\n") + "\n\n\\end{document}\n"

    out_main = out / main_rel
    write_text(out_main, merged)

    changes.record(
        CH_STRUCTURE, "文档骨架",
        before=Path(ms.main_file).name, after=tpl_path.name,
        reason="以期刊官方 LaTeX 模板为骨架，原稿内容移植进去；模板的 documentclass 与宏包体系保持不变",
        file=main_rel.as_posix(), rule_source=cfg.rule_source("output.latex.template"),
    )

    return {
        "format": M.FMT_LATEX,
        "mode": "template",
        "template": str(tpl_path),
        "output_dir": str(out),
        "main_file": str(out_main),
        "files": [rel(p, out) for p in sorted(out.rglob("*")) if p.is_file()],
        "changes": changes.to_list(),
        "change_count": len(changes),
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def convert_latex(
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

    # ---- 0. 模式分派 ----
    adapter = cfg.latex()
    base = (adapter.get("base") or "surgery").lower()
    if base == "template":
        tpl_spec = adapter.get("template") or ""
        tpl_res = _resolve_template(tpl_spec, cfg, log)
        tpl_path = tpl_res.path
        if tpl_path is None:
            reason = f"（{tpl_res.reason}）" if tpl_res.reason else ""
            bag.warn(
                "CVT012", CAT_LATEX,
                f"配置指定了模板模式，但模板无法作为骨架使用：{tpl_spec!r}{reason}；已回退到定点手术模式",
                location=Location(file=str(tpl_spec)),
                suggestion=(
                    "确认 output.latex.template 指向官方模板的主 .tex 文件；"
                    "若给的是官网下载的压缩包（.zip），包内需含 .tex 主文件，"
                    "包内有多份时用 kit.zip::模板文件名 指定。"
                ),
            )
        else:
            if tpl_res.note:
                bag.warn(
                    "CVT013", CAT_FILE,
                    f"模板包内有多份候选主文件，已自动选用 {tpl_res.note}；如不是想要的那份，请用 kit.zip::包内路径 明确指定",
                    location=Location(file=str(tpl_res.archive or tpl_path)),
                    suggestion="不同候选往往对应不同引用样式（如 elsarticle 的 num / harv），选错会产出不符合投稿要求的稿子。",
                )
            try:
                return convert_latex_template(ms, cfg, tpl_path, out, changes, bag, log)
            except TemplateError as exc:
                bag.warn(
                    "CVT011", CAT_LATEX,
                    f"模板结构无法识别（{exc}）；已回退到定点手术模式",
                    location=Location(file=str(tpl_path)),
                    suggestion="确认该文件是官方 LaTeX 模板的主文件（含 \\begin{document}）。",
                )

    src_root = Path(ms.main_file).parent
    src_main = Path(ms.main_file)
    main_rel = src_main.relative_to(src_root)

    # ---- 1. 复制项目 ----
    copied = _copy_project(src_root, out)
    log.step(f"已复制 {copied} 个项目文件到 {out}")

    # ---- 2. 逐文件定点手术 ----
    pkgs_add = list(adapter.get("packages_add") or [])
    pkgs_remove = list(adapter.get("packages_remove") or [])
    renames = (adapter.get("adapter") or {}).get("command_renames") or {}

    for p in sorted(out.rglob("*.tex")):
        rel_name = rel(p, out)
        text = read_text(p)
        original = text
        try:
            is_main = p.relative_to(out) == main_rel
        except ValueError:
            is_main = False

        if is_main:
            text = op_documentclass(text, adapter.get("documentclass", ""), list(adapter.get("class_options") or []), rel_name, cfg, changes)
            text = op_packages(text, pkgs_add, pkgs_remove, rel_name, cfg, changes)
            text = op_metadata(text, ms, cfg, rel_name, changes)
            text = op_abstract(text, cfg, rel_name, changes)
            text = op_caption_labels(text, cfg, rel_name, changes)
            text = op_bibliography(text, cfg, rel_name, changes)
            text = op_declarations(text, ms, cfg, rel_name, changes)

        text = op_section_numbering(text, cfg, rel_name, changes)

        for old, new in renames.items():
            if old in text:
                n = text.count(old)
                text = text.replace(old, new)
                changes.record(
                    CH_STRUCTURE, f"命令重命名 {old} → {new}", before=f"{n} 处",
                    reason="目标期刊模板使用不同的命令名（期刊适配器配置）",
                    file=rel_name, rule_source=cfg.rule_source("output.latex.adapter"),
                )

        if text != original:
            write_text(p, text)

    # ---- 3. 结果 ----
    out_main = out / main_rel
    if not out_main.is_file():
        bag.error(
            "CVT001", CAT_LATEX, f"转换后未找到主文件 {main_rel.as_posix()}",
            location=Location(file=main_rel.as_posix()),
        )
    return {
        "format": M.FMT_LATEX,
        "output_dir": str(out),
        "main_file": str(out_main),
        "files": [rel(p, out) for p in sorted(out.rglob("*")) if p.is_file()],
        "changes": changes.to_list(),
        "change_count": len(changes),
    }


def summarize_project(path: str | Path) -> dict:
    p = Path(path)
    files = [f for f in p.rglob("*") if f.is_file()]
    by_ext: dict[str, int] = {}
    for f in files:
        by_ext[f.suffix.lower()] = by_ext.get(f.suffix.lower(), 0) + 1
    return {"total_files": len(files), "by_ext": by_ext}
