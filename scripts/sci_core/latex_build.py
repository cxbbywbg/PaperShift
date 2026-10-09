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
from .latex_parse import read_group, resolve_include, strip_comments
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


def _split_name(name: str) -> tuple[str, str]:
    r"""`'Bo Liu'` → `('Bo', 'Liu')`，供 Springer 的 `\fnm{} \sur{}` 使用。

    规则：最后一个词当姓，其余整体当名（`'Joergen W. Ploeg'` → `('Joergen W.', 'Ploeg')`）。
    单词名猜不出姓，返回 `(原名, "")`。
    """
    toks = [t for t in re.split(r"\s+", (name or "").strip()) if t]
    if len(toks) < 2:
        return (name or "").strip(), ""
    return " ".join(toks[:-1]), toks[-1]


def _split_affiliation(text: str) -> dict:
    r"""把纯文本单位拆成 Springer 的 `\orgdiv` / `\orgname` / `\orgaddress` 字段。

    典型输入（Elsevier / Springer 老模板里都是一行纯文本）：

        School of Automation, Guangdong University of Technology, Guangzhou 510006, China

    规则：按逗号分段 → 第 1 段 `orgdiv`、第 2 段 `orgname`，其余当地址；
    地址里「城市 邮编」连写的段拆开，纯国家名进 `country`。

    这是**启发式**，猜不准的字段留空（不硬塞）——宁可字段少也不要写错单位，
    调用方应标 needs_author 让作者核对。
    """
    parts = [p.strip() for p in re.split(r"[,，]", (text or "")) if p.strip()]
    out = {"orgdiv": "", "orgname": "", "city": "", "postcode": "", "country": ""}
    if not parts:
        return out
    out["orgdiv"] = parts[0]
    if len(parts) >= 2:
        out["orgname"] = parts[1]
    for seg in parts[2:]:
        m = re.match(r"^(.+?)[\s,]+(\d{4,6})$", seg)
        if m:
            out["city"] = out["city"] or m.group(1).strip()
            out["postcode"] = out["postcode"] or m.group(2)
            continue
        if re.fullmatch(r"\d{4,6}", seg):
            out["postcode"] = out["postcode"] or seg
            continue
        if not out["country"]:
            out["country"] = seg
    return out


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
        need_struct = "\\orgdiv" in aff_line_tpl or "\\orgname" in aff_line_tpl
        # 原稿若本来就是 key=value 写法（elsarticle/CAS 的 organization=/city=/country=），
        # 解析阶段已经拆好字段，直接用，别再按逗号瞎切一遍。
        kv_parts: dict[str, dict] = ms.metadata.doc_meta.get("affiliation_parts", {}) or {}
        text_parts: dict[str, dict] = {}
        for k, v in aff_map.items():
            if k in kv_parts:
                text_parts.setdefault(v, kv_parts[k])
        for i, a in enumerate(affils, 1):
            if need_struct:
                parts = text_parts.get(a) or _split_affiliation(a)
                line = _fmt(aff_line_tpl, key=f"aff{i}", value=a, **parts)
            else:
                line = _fmt(aff_line_tpl, key=f"aff{i}", value=a)
            out.append(line)
            changes.record(
                CH_METADATA, f"单位 {i}", after=a,
                reason=(
                    "按目标模板的 \\affiliation 命令重新生成（原命令保留在注释中）"
                    + ("；单位字段取自原稿的 organization/city/country 字段（原稿已是结构化写法，未做二次拆分）"
                       if need_struct and a in text_parts else
                       "；单位字段按逗号位置拆成 orgdiv/orgname/地址，**请核对拆分是否正确**"
                       if need_struct else "")
                ),
                file=path, rule_source=cfg.rule_source("output.latex.metadata"),
                needs_author=need_struct and a not in text_parts,
            )

    author_tpl = meta.get("author_line")
    if author_tpl and authors:
        need_split = "\\fnm" in author_tpl or "\\sur" in author_tpl
        # 谁是通讯作者：原稿有 \cormark/\corref 就按它来；一个都没有时才退化成
        # 「有邮箱就算通讯」——否则每位作者都被标上 `\author*` 星号。
        marked = [a for a in authors if a.get("corresponding")]
        for a in authors:
            aff_key = aff_key_for(a)
            aff_text = aff_text_for(a)
            name = a.get("name", "") or ""
            email = (a.get("email") or "").strip()
            thanks = "\\thanks{" + email + "}" if email else ""
            given, family = _split_name(name) if need_split else (name, "")
            email_cmd = ("\\email{" + email + "}") if email else ""
            line = _fmt(
                author_tpl, name=name, affil_key=aff_key, affil=aff_text,
                thanks=thanks, given=given, family=family, email_cmd=email_cmd,
            )
            is_corr = bool(a.get("corresponding")) if marked else bool(email)
            if need_split and email and is_corr:
                # 通讯作者用 \author* 标出（星号位置必须在 [ 之前）
                line = line.replace("\\author[", "\\author*[", 1)
            out.append(line)
        src = ms.metadata.doc_meta.get("title_page_file")
        reason = "按目标模板的 \\author 命令重新生成（原命令保留在注释中）"
        if src:
            reason += (f"；作者/单位取自同项目的标题页文件 {src}"
                       "（其 \\title 与主文件逐字相同，可确认是同一篇稿子）")
        if need_split:
            reason += "；姓名按「最后一词为姓」拆成 \\fnm/\\sur，**请核对**"
        changes.record(
            CH_METADATA, "作者列表", after=f"{len(authors)} 位作者",
            reason=reason,
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


# 只对「文档类专有、换了文档类就没有意义」的排版命令生效。判据两条同时成立：
#   1. 命令在下面这份清单里（各家的 frontmatter 渲染器 / 脚注标记命令）；
#   2. 目标模板（含 .cls/.sty）确实没有定义它。
# 不满足就一律不动 —— 正文里的用户自定义宏走 `_carry_preamble` 那条路，不在这里处理。
CLASS_BOUND_BODY_CMDS = frozenset({
    # Elsevier CAS / elsarticle
    "printcredits", "cortext", "cormark", "ead", "fntext", "shorttitle",
    "shortauthors", "credit", "corref", "fnmark", "fnref", "nonumnote",
    "tnotetext", "tnotemark", "tnoteref", "dedication", "preprintfooter",
    # Springer Nature
    "equalcont", "spfx", "sfx",
    # IEEEtran
    "IEEEauthorblockN", "IEEEauthorblockA",
})


def op_neutralize_class_commands(text: str, provided_cmds: set[str] | None, path: str,
                                 cfg: JournalConfig, changes: ChangeLog,
                                 bag: IssueBag | None = None) -> str:
    r"""把「原文档类专有、目标文档类没有」的排版命令注释掉。

    这类命令是**原文档类**的 frontmatter 渲染器，换类之后就没有意义，留着只会
    让编译报 `Undefined control sequence`。实测：Elsevier CAS 的 `\printcredits`
    定义在 `cas-common.sty` 里（用来打印各作者的 `\credit{}` CRediT 角色），
    而 Springer `sn-jnl.cls` 里根本没有这个命令 —— 转出来的稿子编译必挂。
    它通常写在声明区（正文里），所以躲不过元信息重写，必须单独处理。

    两条安全约束：

    * **只处理独占一行的写法**。行内出现的保持原样并记 warning —— 注释掉半行
      会把正文切坏，比编译错误更难查。
    * `provided_cmds` 为 None 时**什么都不做**（不知道目标类定义了什么就别猜）。
    """
    if provided_cmds is None:
        return text
    lines = text.split("\n")
    out: list[str] = []
    hit: list[str] = []
    for line in lines:
        stripped = line.strip()
        m = re.fullmatch(r"\\([A-Za-z]+)\s*", stripped)
        if not m or m.group(1) not in CLASS_BOUND_BODY_CMDS or m.group(1) in provided_cmds:
            out.append(line)
            continue
        name = m.group(1)
        hit.append(name)
        out.append(f"% [sci-trans] \\{name} 属于原文档类，目标模板没有定义该命令，已注释（避免编译报 Undefined control sequence）")
        out.append("% " + line)
    if hit:
        changes.record(
            CH_STRUCTURE, "原文档类专有命令",
            before=", ".join("\\" + h for h in hit),
            after="(已注释保留)",
            reason="这些命令由原文档类（如 Elsevier 的 cas-common.sty）定义，目标模板没有；"
                   "不注释会直接编译失败。命令本身只是 frontmatter 的渲染器，"
                   "对应的信息已随元信息重写迁移。",
            file=path, rule_source=cfg.rule_source("output.latex.template"),
        )
        if bag is not None:
            bag.warn(
                "CVT030", CAT_LATEX,
                f"正文里有 {len(hit)} 个原文档类专有命令，已注释：{', '.join('\\\\' + h for h in hit)}",
                location=Location(file=path),
                suggestion="核对注释掉的命令是否确实只是排版渲染器；若其中承载了正文信息，请手动改写。",
            )
    return "\n".join(out)


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

# 从模板目录复制辅助文件时的体积上限 —— 模板常被解压在 Downloads 这类混杂
# 目录里，一个误判的巨文件不该把结果包撑爆。
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


# --------------------------------------------------------------------------
# 结果包文件裁剪：只带「编译真的会用到」的文件
# --------------------------------------------------------------------------
#
# 真实投稿目录里躺着一堆与正文无关的东西：官方模板 kit 的示例文档与说明书、
# `thumbnails/`、废弃图片、没被引用的 `.bib`、作者留档的旧稿副本……无差别复制
# 会把这些连同稿子一起投出去，也会让 Overleaf 里冒出多个候选主文件。
#
# 所以结果包**按引用关系**来收：只带编译真的会读到的文件。
# 原始稿在 `submission/original/` 里完整保留，这是「交付物裁剪」，不是丢数据。

GRAPHICS_SUFFIXES = frozenset({
    ".pdf", ".eps", ".ps", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff",
    ".bmp", ".svg",
})
BIB_SUFFIXES = frozenset({".bib"})
BST_SUFFIXES = frozenset({".bst"})
CLASS_SUFFIXES = frozenset({".cls", ".sty", ".clo", ".ldf", ".fd", ".def", ".cfg"})

_INPUT_REF_RE = re.compile(r"\\(?:input|include|subfile|subfileinclude)\s*\{([^}]*)\}")
_GRAPHICS_REF_RE = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_GRAPHICSPATH_RE = re.compile(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\}\s*)+)\}")
_BIB_REF_RE = re.compile(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_BST_REF_RE = re.compile(r"\\bibliographystyle\s*\{([^}]*)\}")
_CLS_REF_RE = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_DOCCLASS_OPT_RE = re.compile(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
_LOADCLASS_OPT_RE = re.compile(r"\\LoadClass(?:WithOptions)?\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
_STY_REF_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_LOADCLASS_REF_RE = re.compile(r"\\LoadClass(?:WithOptions)?\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_MINTED_REF_RE = re.compile(r"\\inputminted\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*\{([^}]*)\}")
_FILEREAD_REF_RE = re.compile(
    r"\\(?:lstinputlisting|verbatiminput|VerbatimInput|DTLloaddb)\s*"
    r"(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def _norm_ref_stem(name: str) -> str:
    """引用名 → 「无扩展名的小写相对路径」，用于和磁盘上的文件对表。

    `EPPL.pdf`、`./EPPL`、`figs\\Fig1` 都各自归一成一个可比较的键。
    """
    s = (name or "").strip().replace("\\", "/").strip("\"'")
    s = re.sub(r"^\./+", "", s)
    if not s:
        return ""
    return Path(s).with_suffix("").as_posix().lower()


def _drop_note(dropped: list[str], limit: int = 40) -> str:
    """把「被裁掉的文件」写成人能读的一行，放进转换记录供作者核对。"""
    shown = "、".join(dropped[:limit])
    if len(dropped) > limit:
        shown += f" 等（共 {len(dropped)} 个）"
    return shown


def _project_files(root: Path) -> list[Path]:
    """项目根下所有「有资格进结果包」的文件（排除构建产物与元数据目录）。

    判断只针对**相对 root 的路径段**：项目本身可能就躺在 `out/_work/_extracted/`
    这类目录里，拿绝对路径去比对会把整个项目误判成空。
    """
    root = Path(root)
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in (".git", "__pycache__", ".sci-trans", "submission",
                        "_work", "__MACOSX")
               for part in rel.parts):
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(p.match(pat) for pat in SKIP_PATTERNS):
            continue
        out.append(p)
    return out


def _collect_needed_files(root: Path, entry_texts: list[str]) -> set[Path]:
    r"""从入口文本出发，按「引用关系」收集 `root` 下**真正需要**的文件。

    只认「活着的」引用——注释掉的 `%\includegraphics{...}` 不算（否则会把
    作者留档备查的示例图一并带进结果包）。规则：

    * `\input` / `\include` 的子文件，**递归**跟进（子文件里的图与文献库同样要带）；
    * `\includegraphics` 指向的图片（认 `\graphicspath` 前缀，也允许省略扩展名）；
    * `\bibliography` / `\addbibresource` 指向的 `.bib`；
    * `\bibliographystyle` 指向的 `.bst`——**类文件里的也算**。Springer 的
      `sn-jnl.cls` 就是靠 `\if@Mathphys@numrefstyle` 分支去
      `\bibliographystyle{sn-mathphys-num}`，主文件里根本没有这条命令；
    * `\documentclass` / `\usepackage` / `\RequirePackage` / `\LoadClass` 指向的
      `.cls`/`.sty`，并且**继续解析类文件**（它可能再 `\RequirePackage` 本地样式）；
    * `\lstinputlisting` / `\verbatiminput` / `\inputminted` 这类直接读文件的命令。
    """
    root = Path(root)
    if not root.is_dir():
        return set()

    all_files = _project_files(root)
    by_stem: dict[str, list[Path]] = {}
    by_base: dict[str, list[Path]] = {}
    for p in all_files:
        by_stem.setdefault(_norm_ref_stem(p.relative_to(root).as_posix()), []).append(p)
        by_base.setdefault(_norm_ref_stem(p.name), []).append(p)

    def lookup(name: str, exts: frozenset[str] | None) -> list[Path]:
        stem = _norm_ref_stem(name)
        if not stem:
            return []
        cands = list(by_stem.get(stem, []))
        cands += by_base.get(_norm_ref_stem(Path(name.strip().replace("\\", "/")).name), [])
        seen: set[Path] = set()
        hits: list[Path] = []
        for c in cands:
            if c in seen:
                continue
            seen.add(c)
            if exts is not None and c.suffix.lower() not in exts:
                continue
            hits.append(c)
        return hits

    def _safe_read(p: Path) -> str:
        try:
            return read_text(p)
        except Exception:
            return ""

    needed: set[Path] = set()
    parsed: set[Path] = set()
    graphics_paths: list[str] = []
    # 入口文本没有对应的磁盘文件（主文件是现生成的），借一个假路径拿到它所在目录
    queue: list[tuple[str, Path]] = [(t, root / "_entry_.tex") for t in entry_texts if t]

    while queue:
        text, cur = queue.pop(0)
        if cur in parsed:
            continue
        parsed.add(cur)
        body = strip_comments(text)

        for m in _GRAPHICSPATH_RE.finditer(body):
            for sub in re.findall(r"\{([^{}]*)\}", m.group(1)):
                s = sub.strip().replace("\\", "/").strip("/")
                if s and s not in graphics_paths:
                    graphics_paths.append(s)

        # 图片
        for m in _GRAPHICS_REF_RE.finditer(body):
            raw = m.group(1).strip()
            if not raw:
                continue
            hits = lookup(raw, GRAPHICS_SUFFIXES)
            for gp in graphics_paths:
                hits += lookup(f"{gp}/{raw}", GRAPHICS_SUFFIXES)
            needed.update(hits)

        # 子文件：递归跟进
        for m in _INPUT_REF_RE.finditer(body):
            tgt = resolve_include(m.group(1), root, cur)
            if tgt and tgt not in needed:
                needed.add(tgt)
                queue.append((_safe_read(tgt), tgt))

        # 其余按「引用名 → 后缀」查表
        for rx, exts, follow in (
            (_CLS_REF_RE, CLASS_SUFFIXES, True),
            (_STY_REF_RE, CLASS_SUFFIXES, True),
            (_LOADCLASS_REF_RE, CLASS_SUFFIXES, True),
            (_BST_REF_RE, BST_SUFFIXES, False),
            (_BIB_REF_RE, BIB_SUFFIXES, False),
            (_MINTED_REF_RE, None, False),
            (_FILEREAD_REF_RE, None, False),
        ):
            for m in rx.finditer(body):
                for raw in m.group(1).split(","):
                    raw = raw.strip()
                    if not raw:
                        continue
                    for f in lookup(raw, exts):
                        if f in needed:
                            continue
                        needed.add(f)
                        if follow and f.suffix.lower() in (".cls", ".sty", ".clo",
                                                           ".ldf", ".def"):
                            queue.append((_safe_read(f), f))

        # 文献样式常常不是正文指定的，而是**文档类选项**选出来的：
        # `\documentclass[pdflatex,sn-mathphys-num]{sn-jnl}` 对应
        # `sn-mathphys-num.bst`。类文件若用 `\if@Mathphys@numrefstyle` 这类
        # 分支才 `\bibliographystyle`，光扫类文件会把整族 `.bst` 都算进来；
        # 按选项名再兜一道，至少保证选中的那份一定在。
        for rx in (_DOCCLASS_OPT_RE, _LOADCLASS_OPT_RE):
            for m in rx.finditer(body):
                for opt in (m.group(1) or "").split(","):
                    opt = opt.strip()
                    if opt:
                        needed.update(lookup(opt, BST_SUFFIXES))

    # 文献样式收口：期刊类文件常把**整族** `.bst` 各 `\bibliographystyle` 一遍
    # （Springer 的 sn-jnl.cls 就是 9 个 `\if@Xxx@refstyle` 分支各写一次），
    # 但真正生效的只有一个——由正文的 `\bibliographystyle` 或文档类选项决定。
    # 能证明是哪一个时只留那一个；证明不了（如 elsarticle 的 `harv` 选项对应
    # `harvard.bst`，名字对不上）就退回整族——宁可多带，也不让作者编不过。
    strong: set[Path] = set()
    for t in entry_texts:
        body0 = strip_comments(t)
        for m in _BST_REF_RE.finditer(body0):
            for raw in m.group(1).split(","):
                if raw.strip():
                    strong.update(lookup(raw.strip(), BST_SUFFIXES))
        for rx in (_DOCCLASS_OPT_RE, _LOADCLASS_OPT_RE):
            for m in rx.finditer(body0):
                for opt in (m.group(1) or "").split(","):
                    if opt.strip():
                        strong.update(lookup(opt.strip(), BST_SUFFIXES))
    if strong:
        needed = {p for p in needed
                  if p.suffix.lower() not in BST_SUFFIXES or p in strong}
    return needed


def _copy_needed_files(
    root: Path,
    out: Path,
    needed: set[Path],
    skip: set[Path] | None = None,
    log: Log | None = None,
    label: str = "文件",
    max_bytes: int | None = None,
    keep_existing: bool = False,
) -> tuple[list[str], list[str]]:
    """把 `needed` 里的文件按原相对路径复制到 `out`。

    返回 `(已复制, 被裁掉)` 两组相对路径。裁掉的是「本项目里存在、但
    没有被任何活着的引用指向」的文件——示例文档、说明书、缩略图、废弃图片等。

    `keep_existing=True` 用于「原稿已先复制、现在补模板辅助文件」的场景：
    只要 `out` 里**已经有一个同名文件**（哪怕在别的子目录，例如原稿根目录已有
    `sn-mathphys-num.bst`、模板又给了 `bst/sn-mathphys-num.bst`），就保留先来的
    那一份，不再重复放一份进去。
    """
    skip = skip or set()
    existing = {p.name.lower() for p in out.rglob("*") if p.is_file()} if keep_existing else set()
    copied: list[str] = []
    dropped: list[str] = []
    for p in _project_files(root):
        relp = p.relative_to(root)
        if relp in skip:
            continue
        if p not in needed:
            dropped.append(relp.as_posix())
            continue
        if max_bytes is not None:
            try:
                if p.stat().st_size > max_bytes:
                    dropped.append(relp.as_posix())
                    if log:
                        log.warn(f"{label}过大已跳过（{p.stat().st_size // 1024} KB）：{relp.as_posix()}")
                    continue
            except OSError:
                continue
        if keep_existing and p.name.lower() in existing:
            if log:
                log.step(f"{label}同名文件已在结果包里，不重复复制：{relp.as_posix()}")
            continue
        target = out / relp
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        copied.append(relp.as_posix())
        existing.add(p.name.lower())
    return copied, dropped


def _template_support_texts(tpl_head: str, tpl_dir: Path | None) -> list[str]:
    """模板主文件 + 同目录 `.cls`/`.sty`/`.clo` 的文本（用于判断「模板已提供什么」）。"""
    texts = [tpl_head]
    if tpl_dir and tpl_dir.is_dir():
        for p in sorted(tpl_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() not in {".cls", ".sty", ".clo"}:
                continue
            try:
                texts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return texts


def _collect_provided_pkgs(tpl_head: str, tpl_dir: Path | None = None) -> set[str]:
    r"""汇总模板「已经提供」的宏包名。

    两个来源都要看，缺一不可：

    * 模板主文件的 `\usepackage{...}`；
    * 模板目录里 `.cls`/`.sty` 支持文件内部的 `\usepackage`/`\RequirePackage`。

    只看主文件会漏掉绝大多数期刊类 —— Springer `sn-jnl.cls` 在类文件里就
    `\usepackage[numbers,sort&compress]{natbib}`，Elsevier `cas-sc.cls` 同理。
    原稿导言区里那条 `\usepackage[authoryear,longnamesfirst]{natbib}` 若被
    当成「模板没加载」补进结果，就会和类文件里的 natbib **选项冲突**，
    直接 `Option clash for package natbib` 编译失败。
    """
    pkgs: set[str] = set()
    pat = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
    for t in _template_support_texts(tpl_head, tpl_dir):
        for m in pat.finditer(t):
            for x in m.group(1).split(","):
                x = x.strip()
                if x:
                    pkgs.add(x)
    return pkgs


_DEF_NAME_RES = (
    # \newcommand\foo / \newcommand{\foo} / \renewcommand*{\foo} / \providecommand...
    re.compile(r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|"
               r"DeclareMathOperator)\*?\s*\{?\s*\\([A-Za-z@]+)"),
    # \newtheorem{theorem}{Theorem} / \newenvironment{foo} / \renewenvironment{foo}
    re.compile(r"\\(?:newtheorem|newenvironment|renewenvironment)\*?\s*\{([^}]*)\}"),
    # \def\foo#1{...} / \gdef\foo
    re.compile(r"\\(?:g?def)\s*\\([A-Za-z@]+)"),
)


def _defined_name(line: str) -> str:
    """从一行导言区代码里取「被定义的命令名」；不是定义则返回空串。"""
    for rx in _DEF_NAME_RES:
        m = rx.match(line)
        if m:
            return m.group(1).strip()
    return ""


def _statement_span(text: str, start: int) -> int:
    r"""从 `start`（一条导言区定义的起点）读到该语句结束，返回结束下标（不含）。

    与 `PREAMBLE_CARRY_RE` 的**逐行**匹配不同，这里按花括号配平读完整条语句。
    逐行匹配会把跨行的定义截成半句：

        \newcommand*\circled[1]{\tikz[baseline=(char.base)]{
            \node[shape=circle, draw, inner sep=1pt] (char) {#1};}}

    只抄第一行 → 结果里留下**未闭合的 `{`**，而 `scan_commands` 读到配平分组
    才收手，于是从这一行起把**整个文件**的后续内容全吞掉。实测症状：花括号
    1297 vs 1295、25 个章节全变 0 节、标题/摘要/作者全部「未识别」、70 个引用键
    被误报成「不存在」。所以定义必须整条搬。
    """
    i, n = start, len(text)
    depth = 0
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2                      # 跳过转义字符（\% 不是注释）
            continue
        if ch == "%":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
        elif ch == "\n" and depth == 0:
            return i                    # 花括号配平后的第一个换行 = 语句结束
        i += 1
    return n


def _collect_provided_cmds(tpl_head: str, tpl_dir: Path | None = None) -> set[str]:
    r"""汇总模板「已经定义」的命令名（宏包之外的那一半）。

    `sn-jnl.cls` 自己 `\def\fnm`、`\def\sur`、`\newcommand{\orgdiv}`，所以原稿
    导言区里那批 `\providecommand{\fnm}[1]{#1}` 是多余的；`\newtheorem{theorem}`
    若被重复带入，更会直接报 "Command \theorem already defined"。
    与宏包同理，只看模板主文件会漏掉类文件里的定义。
    """
    names: set[str] = set()
    for t in _template_support_texts(tpl_head, tpl_dir):
        for line in t.splitlines():
            n = _defined_name(line.lstrip())
            if n:
                names.add(n)
    return names


def _carry_preamble(src_head: str, tpl_head: str, cfg: JournalConfig, path: str,
                    changes: ChangeLog, tpl_dir: Path | None = None) -> str:
    """把原稿导言区里「模板没有但正文依赖」的宏包与自定义定义带进模板。

    不带这些，正文里的 \\method{} 之类自定义命令会直接编译失败。
    """
    tpl_pkgs = _collect_provided_pkgs(tpl_head, tpl_dir)
    tpl_cmds = _collect_provided_cmds(tpl_head, tpl_dir)
    remove = set(cfg.latex().get("packages_remove") or [])

    carry: list[str] = []
    seen: set[str] = set()

    # 只认**活着的** `\usepackage`。原稿导言区里 `%\usepackage[...]{natbib}`
    # 这类「留档备查」的注释行极其常见，若照抄进结果就会凭空激活一条命令，
    # 与类文件里的同类宏包撞车。用 _live_matches 过滤掉注释里的匹配。
    for m in _live_matches(re.compile(r"\\usepackage\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}"), src_head):
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

    pos = 0
    while True:
        m = PREAMBLE_CARRY_RE.search(src_head, pos)
        if m is None:
            break
        end = _statement_span(src_head, m.start())
        pos = end if end > m.start() else m.end()
        line = src_head[m.start():end].strip()
        if not line or line.startswith("%") or line in seen:
            continue
        name = _defined_name(line)
        if name and name in tpl_cmds:
            # 模板（含 .cls/.sty）已经定义过同名命令，重复带入轻则冗余、
            # 重则 "Command \X already defined" 直接编译失败。
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


def _comment_mask(text: str) -> list[bool]:
    """逐字符标记「是否处于注释中」。

    `_mask_comments` 只给出「看不见注释命令」的副本，但没法回答「这个位置
    是不是注释」——模板里 `%\\bibliography{cas-refs}` 这类示例占位需要靠
    这个标记来区分：**保留在注释里的别去动，活着的才替换**。
    """
    mask = [False] * len(text)
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if text[i] == "%":
            while i < n and text[i] != "\n":
                mask[i] = True
                i += 1
            continue
        i += 1
    return mask


def _live_matches(pattern: re.Pattern, text: str) -> list[re.Match]:
    """返回 text 里**不在注释中**的 pattern 匹配。"""
    mask = _comment_mask(text)
    return [m for m in pattern.finditer(text) if not mask[m.start()]]


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

    ma = re.search(r"\\author\s*\*?\s*(\[[^\]]*\])?\s*\{", masked)
    if ma:
        try:
            inner, _end = read_group(region, ma.end() - 1)
        except Exception:
            inner = ""
        if "\\fnm" in inner or "\\sur" in inner:
            # Springer Nature 风格：姓名要拆成 \fnm{名} \sur{姓}
            tpl["author_line"] = "\\author[%affil_key]{\\fnm{%given} \\sur{%family}}%email_cmd"
        else:
            tpl["author_line"] = "\\author[%affil_key]{%name}" if ma.group(1) else "\\author{%name}"

    maf = re.search(r"\\(affiliation|affil|institute|address)\s*\*?\s*(\[[^\]]*\])?\s*\{", masked)
    if maf:
        cmd = maf.group(1)
        try:
            aff_inner, _end = read_group(region, maf.end() - 1)
        except Exception:
            aff_inner = ""
        if "\\orgdiv" in aff_inner or "\\orgname" in aff_inner or "\\orgaddress" in aff_inner:
            # Springer Nature 风格：单位要拆成 \orgdiv / \orgname / \orgaddress
            tpl["affiliation"] = (
                f"\\{cmd}[%key]{{\\orgdiv{{%orgdiv}}, \\orgname{{%orgname}}, "
                "\\orgaddress{\\city{%city}, \\postcode{%postcode}, \\country{%country}}}"
            )
        else:
            tpl["affiliation"] = (
                f"\\{cmd}[%key]{{%value}}" if maf.group(2) else f"\\{cmd}{{%value}}"
            )

    kw = _first_env_span(masked, KEYWORD_ENVS)
    if kw:
        tpl["keywords_command"] = f"\\begin{{{kw[2]}}}%value\\end{{{kw[2]}}}"
    else:
        # 命令式关键词（Springer 的 \keywords{a, b, c}）——不认这个的话
        # 模板示例关键词会原地留着，新关键词被追加到别处，等于没换。
        if re.search(r"\\keywords?\s*\*?\s*\{", masked):
            tpl["keywords_command"] = "\\keywords{%value}"
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

    # ---- 1. 作者 / 单位命令体系（先算区间：标题可能落在区间里，要并进新块）----
    # 命令名后的 `*` 必须认（Springer 用 `\author*[1,2]{}` 标通讯作者、
    # `\affil*[1]{}` 标第一单位）。不认星号时这些命令整条匹配不上，区间起点
    # 会落到紧跟其后的 `\email{...}` 上——结果是 `\author*[1,2]{...}` 那半截
    # 留在区间外、`\email{...}` 被替换掉，文件被切坏且花括号不配平。
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\\([A-Za-z]+)\s*\*?\s*(?:\[[^\]]*\])?\s*\{", masked):
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

    author_span: tuple[int, int] | None = None
    if spans:
        merged = _merge_spans(spans, region)
        author_span = (merged[0][0], merged[-1][1])

    # ---- 2. 标题 ----
    tm = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{", masked)
    tm_span: tuple[int, int] | None = None
    if tm:
        try:
            _inner, end = read_group(region, tm.end() - 1)
        except Exception:
            end = -1
        if end > 0:
            tm_span = (tm.start(), end)

    title_done = False
    if tm_span:
        if author_span and author_span[0] <= tm_span[0] and tm_span[1] <= author_span[1]:
            # 标题命令落在「作者/单位命令区」区间之内：那一整段会被新块替换，
            # 标题必须并进新块，否则会被连带覆盖掉（曾因此整条标题丢失，
            # 且两个区间重叠导致坐标错位、注释块被截断）。
            if title:
                block = "\\title{" + title + "}" + ("\n" + block if block else "")
                changes.record(
                    CH_METADATA, "标题", after=title,
                    reason="套用目标模板的 \\title 命令（标题文字取自原稿，未改写）",
                    file=path, rule_source=cfg.rule_source("output.latex.metadata.title"),
                )
            else:
                block = ("% [sci-trans] 模板标题占位已移除（原稿未解析到标题）"
                         + ("\n" + block if block else ""))
                changes.record(
                    CH_METADATA, "标题", before=region[tm_span[0]:tm_span[1]][:60], after="(移除占位)",
                    reason="原稿未解析到标题，模板的示例标题已移除，需作者补填",
                    file=path, rule_source=cfg.rule_source("output.latex.metadata.title"),
                    needs_author=True, auto=False,
                )
            title_done = True
        elif title:
            edits.append((tm_span[0], tm_span[1], "\\title{" + title + "}"))
            changes.record(
                CH_METADATA, "标题", after=title,
                reason="套用目标模板的 \\title 命令（标题文字取自原稿，未改写）",
                file=path, rule_source=cfg.rule_source("output.latex.metadata.title"),
            )
            title_done = True
        else:
            edits.append((
                tm_span[0], tm_span[1],
                "% [sci-trans] 模板标题占位已移除（原稿未解析到标题）",
            ))
            changes.record(
                CH_METADATA, "标题", before=region[tm_span[0]:tm_span[1]][:60], after="(移除占位)",
                reason="原稿未解析到标题，模板的示例标题已移除，需作者补填",
                file=path, rule_source=cfg.rule_source("output.latex.metadata.title"),
                needs_author=True, auto=False,
            )
            title_done = True

    # ---- 3. 作者 / 单位命令区落笔 ----
    if author_span:
        s, e = author_span
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

    # ---- 4. 摘要（环境式或命令式）----
    abs_span = _find_env_span(masked, "abstract") or _find_env_span(masked, "abstract*")
    # 命令式摘要（Springer 的 \abstract{...}）：不认这个的话模板示例摘要会
    # 原地留着、原稿摘要被追加到别处，等于没换（还会留下孤立的 \end{abstract}）。
    abs_cmd_m = None if abs_span else re.search(r"\\abstract\s*\*?\s*\{", masked)
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
    elif abs_cmd_m:
        try:
            _abs_inner, abs_end = read_group(region, abs_cmd_m.end() - 1)
        except Exception:
            abs_end = -1
        if abs_end > 0:
            if abstract:
                edits.append((abs_cmd_m.start(), abs_end, "\\abstract{" + abstract + "}"))
                changes.record(
                    CH_METADATA, "摘要",
                    after=abstract[:120] + ("…" if len(abstract) > 120 else ""),
                    reason="套用目标模板的 \\abstract 命令（摘要文字取自原稿，未改写）",
                    file=path, rule_source=cfg.rule_source("output.latex.metadata.abstract_env"),
                )
            else:
                edits.append((
                    abs_cmd_m.start(), abs_end,
                    "% [sci-trans] 模板摘要占位已清空（原稿未解析到摘要）\n"
                    "\\abstract{[待作者填写]}",
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

    # ---- 5. 关键词环境 ----
    kw_span = _first_env_span(masked, KEYWORD_ENVS)
    kw_value = kw_sep.join(keywords) if keywords else ""
    # 命令式关键词（Springer 的 \keywords{a, b, c}）：不认这个的话模板示例
    # 关键词会原地留着、新关键词被追加到别处，等于没换。
    kw_cmd_m = None if kw_span else re.search(r"\\keywords?\s*\*?\s*\{", masked)
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
    elif kw_cmd_m:
        try:
            _kw_inner, kw_end = read_group(region, kw_cmd_m.end() - 1)
        except Exception:
            kw_end = -1
        if kw_end > 0:
            tpl_line = str(meta.get("keywords_command") or "\\keywords{%value}")
            edits.append(
                (kw_cmd_m.start(), kw_end, _fmt(tpl_line, value=kw_value or "[待作者填写]"))
            )
            if keywords:
                changes.record(
                    CH_METADATA, "关键词", after=", ".join(keywords),
                    reason="套用目标模板的关键词命令（关键词取自原稿，未改写）",
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

    # ---- 6. 研究亮点（Elsevier 等要求 3–5 条，每条 ≤85 字符）----
    hl_span = _find_env_span(masked, "highlights")
    if hl_span:
        s, e = hl_span
        hl_raw = list(ms.metadata.doc_meta.get("highlights_raw") or [])
        if hl_raw:
            body_hl = "\n".join("\\item " + h for h in hl_raw)
            edits.append((s, e, f"\\begin{{highlights}}\n{body_hl}\n\\end{{highlights}}"))
            changes.record(
                CH_METADATA, "研究亮点", after=f"{len(hl_raw)} 条",
                reason="套用目标模板的 highlights 环境（亮点文字取自原稿，未改写）",
                file=path, rule_source=cfg.rule_source("output.latex.metadata"),
            )
        else:
            edits.append((
                s, e,
                "% [sci-trans] 模板要求 research highlights（3–5 条、每条 ≤85 字符），"
                "原稿没有；请作者填写或整段删除\n"
                "\\begin{highlights}\n\\item \n\\item \n\\item \n\\end{highlights}",
            ))
            changes.record(
                CH_METADATA, "研究亮点", after="(待作者填写)",
                reason="目标模板带 research highlights 环境（Elsevier 等必填），"
                       "原稿未提供；已保留空模板并列入待办",
                file=path, rule_source=cfg.rule_source("output.latex.metadata"),
                needs_author=True, auto=False,
            )

    # ---- 7. 落笔：替换从后往前，插入从后往前 ----
    #
    # 区间**必须互不重叠**：替换用的是原始坐标，一旦有区间落在另一个区间
    # 内部，先落笔的那个会让后面的坐标全部错位，切出半个单词、把注释块截断
    # （标题曾经就是这样整条丢掉的）。这里做一次防御性校验：后出现且与已接受
    # 区间重叠的一律丢弃（同起点时保留更长的那个），宁可少改也不写出错位的文件。
    ordered = sorted(edits, key=lambda x: (x[0], -x[1]))
    kept: list[tuple[int, int, str]] = []
    for s, e, new in ordered:
        if kept and s < kept[-1][1]:
            continue
        kept.append((s, e, new))

    out = region
    for s, e, new in sorted(kept, key=lambda x: -x[0]):
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


_TEMPLATE_BIB_RE = re.compile(
    r"\\(?:bibliographystyle|bibliography|printbibliography|addbibresource)"
    r"|\\begin\s*\{thebibliography\}"
)


def _template_bib_region(after_front: str) -> str:
    r"""从模板的「frontmatter 之后」部分切出可复用的文献区。

    模板自带的示例正文整段丢弃；起点取**注释之外**的第一条文献命令。
    模板常把示例写法留在注释里（`%\bibliographystyle{model1-num-names}`），
    直接在原文上找 `\bibliographystyle` 会把行首的 `%` 一起切掉，
    结果凭空多出一条生效的文献样式命令。
    """
    hits = _live_matches(_TEMPLATE_BIB_RE, after_front)
    return after_front[hits[0].start():] if hits else ""


def _retarget_bibliography(tail: str, ms: M.Manuscript, cfg: JournalConfig,
                           path: str, changes: ChangeLog) -> str:
    r"""把模板文献区里的**示例文献库**换成原稿的 .bib。

    模板的 `\bibliography{cas-refs}` 指向随包样例文献；原稿自己的
    `\bibliography{references}` 才是要编译的。之前的做法是「往后追加」，
    三个后果一起发生：两条命令同时生效（参考文献从 50 条膨胀到 82 条）、
    追加位置落在 `\end{document}` 之后（等于没写）、注释里的示例命令被
    意外激活。这里改为**替换**首条、注释掉多余的示例库，并把所有追加
    严格排在 `\end{document}` 之前。
    """
    stems: list[str] = []
    for r in (ms.bib_resources or []):
        s = Path(str(r).strip().replace("\\", "/")).name
        if s.lower().endswith(".bib"):
            s = s[:-4]
        if s and s not in stems:
            stems.append(s)
    want = ",".join(stems)

    # \end{document} 单独摘出：任何追加都必须落在它之前
    m_end = re.search(r"\\end\s*\{document\}", tail)
    if m_end:
        head, close = tail[:m_end.start()], tail[m_end.start():]
    else:
        head, close = tail, "\\end{document}\n"

    # --- \bibliography{...}：首条改为原稿文献库，其余（模板样例）注释掉 ---
    bib_hits = _live_matches(re.compile(r"\\bibliography\s*\{([^}]*)\}"), head)
    if bib_hits:
        for i, m in reversed(list(enumerate(bib_hits))):
            cur = m.group(1).strip()
            if i == 0:
                if not want or cur == want:
                    continue
                head = head[:m.start()] + f"\\bibliography{{{want}}}" + head[m.end():]
                changes.record(
                    CH_REFERENCE, "\\bibliography", before=cur or "(空)", after=want,
                    reason="模板的文献库指向随包样例文献，已改为原稿的 .bib 文件",
                    file=path, rule_source=cfg.rule_source("references.bibtex_style"),
                )
            elif cur not in stems:
                head = (head[:m.start()]
                        + "% [sci-trans] 模板示例文献库（已注释，避免样例条目混进参考文献）："
                        + m.group(0) + head[m.end():])
                changes.record(
                    CH_REFERENCE, "\\bibliography", before=cur, after="(已注释)",
                    reason="模板的示例文献库会把样例条目一并编进参考文献，已注释掉",
                    file=path, rule_source=cfg.rule_source("references.bibtex_style"),
                )
    elif want and not _live_matches(re.compile(r"\\begin\s*\{thebibliography\}"), head):
        head = head.rstrip() + f"\n\\bibliography{{{want}}}\n"
        changes.record(
            CH_REFERENCE, "\\bibliography", before="(模板中为注释/占位)", after=want,
            reason="把模板的文献命令指向原稿的 .bib 文件",
            file=path, rule_source=cfg.rule_source("references.bibtex_style"),
        )
    elif _live_matches(re.compile(r"\\begin\s*\{thebibliography\}"), head):
        changes.record(
            CH_REFERENCE, "文献区", before="thebibliography 环境", after="thebibliography 环境（保留）",
            reason="模板用内联 thebibliography 承载示例文献，未自动替换；"
                   "请确认参考文献是否来自原稿的 .bib",
            file=path, rule_source=cfg.rule_source("references.bibtex_style"),
            needs_author=True, auto=False,
        )

    # --- biblatex：\addbibresource{...} ---
    if want:
        add_hits = _live_matches(re.compile(r"\\addbibresource\s*\{([^}]*)\}"), head)
        for i, m in reversed(list(enumerate(add_hits))):
            cur = m.group(1).strip()
            if i == 0:
                target = f"{stems[0]}.bib"
                if cur == target:
                    continue
                head = head[:m.start()] + f"\\addbibresource{{{target}}}" + head[m.end():]
                changes.record(
                    CH_REFERENCE, "\\addbibresource", before=cur, after=target,
                    reason="模板的文献库指向随包样例文献，已改为原稿的 .bib 文件",
                    file=path, rule_source=cfg.rule_source("references.bibtex_style"),
                )
            else:
                head = (head[:m.start()]
                        + "% [sci-trans] 模板示例文献库（已注释）：" + m.group(0) + head[m.end():])

    # --- 文献样式：模板模式以模板自带的样式为准 ---
    # 模板通常随附了对应的 .bst，强行换成配置里的样式会指向不存在的文件；
    # 不一致时保留模板样式并提示人工确认。
    bib_cfg = cfg.latex().get("bibliography", {}) or {}
    target_style = bib_cfg.get("style") or cfg.bib_style
    style_hits = _live_matches(re.compile(r"\\bibliographystyle\s*\{([^}]*)\}"), head)
    if style_hits:
        cur = style_hits[0].group(1).strip()
        if target_style and cur and cur != target_style:
            changes.record(
                CH_REFERENCE, "\\bibliographystyle", before=cur, after=cur,
                reason=f"模板自带文献样式 {cur}，与期刊配置的 {target_style} 不一致；"
                       "已保留模板样式（模板随附对应的 .bst），需人工确认该用哪一个",
                file=path, rule_source=cfg.rule_source("references.bibtex_style"),
                needs_author=True, auto=False,
            )
    elif target_style:
        head = head.rstrip() + f"\n\\bibliographystyle{{{target_style}}}\n"
        changes.record(
            CH_REFERENCE, "\\bibliographystyle", before="(模板未指定)", after=target_style,
            reason="模板未指定参考文献样式，按期刊配置补上",
            file=path, rule_source=cfg.rule_source("references.bibtex_style"),
        )

    return head.rstrip("\n") + "\n\n" + close.lstrip("\n")


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
    结果包里只放**编译真的会读到**的文件（见 `_collect_needed_files`）。
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
    head = _carry_preamble(src_head, head, cfg, main_rel.as_posix(), changes,
                           tpl_dir=tpl_path.parent)

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
    # 模板自带示例正文（frontmatter 之后到文献命令之间）整段丢弃，换成原稿正文。
    # 切分点必须取**没被注释掉**的那条文献命令：模板常写成
    # `%\bibliographystyle{model1-num-names}` 作为示例占位，在原文上直接找
    # 会把行首的 `%` 一起切掉，凭空多出一条生效的命令。
    tail_after = _template_bib_region(after_front)
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

    # ---- 7b. 原文档类专有命令（如 CAS 的 \printcredits）----
    # 这些命令定义在**原**文档类里，目标模板没有；留在正文里编译必挂。
    # 判据用「目标模板 + 它的 .cls/.sty 是否定义过该命令」，可证伪才动手。
    body_new = op_neutralize_class_commands(
        body_new, _collect_provided_cmds(tpl_text, tpl_path.parent),
        main_rel.as_posix(), cfg, changes, bag,
    )

    # ---- 8. 文献样式与文献库 ----
    tail_after = _retarget_bibliography(
        tail_after, ms, cfg, main_rel.as_posix(), changes,
    )
    # 文献区同样过一遍原文档类专有命令：有的稿子把 `\printcredits` 写在
    # `\bibliography` **之后**，落在正文区之外，只处理正文会漏掉。
    tail_after = op_neutralize_class_commands(
        tail_after, _collect_provided_cmds(tpl_text, tpl_path.parent),
        main_rel.as_posix(), cfg, changes, bag,
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

    # ---- 12. 按引用裁剪结果包文件 ----
    # 最终文本已经定稿，现在它才是「该带哪些文件」的唯一依据：原稿侧与模板侧
    # 都只复制被它（及其递归引用到的子文件、类文件）真正指向的东西。
    copied, dropped = _copy_needed_files(
        src_root, out, _collect_needed_files(src_root, [merged]),
        skip={main_rel}, log=log, label="原稿文件",
    )
    log.step(f"已复制 {len(copied)} 个原稿文件到 {out}")
    if dropped:
        changes.record(
            CH_ASSET, "结果包文件裁剪",
            before=f"原稿目录 {len(dropped) + len(copied)} 个文件",
            after=f"只带 {len(copied)} 个（正文真正引用到的）",
            reason="未被任何活着的引用指向的文件不进结果包——官方模板 kit 的示例文档、"
                   "说明书、缩略图、废弃图片、未引用的 .bib 都会污染交付物，"
                   "也会让 Overleaf 里出现多个候选主文件。原稿完整副本仍在 original/ 里。"
                   f"本次未带：{_drop_note(dropped)}",
            file=main_rel.as_posix(), rule_source=cfg.rule_source("output.latex.template"),
        )
        log.step(f"已裁掉 {len(dropped)} 个未被引用的原稿文件（原件保留在 original/）："
                 f"{'、'.join(dropped[:8])}{' …' if len(dropped) > 8 else ''}")

    aux_needed = _collect_needed_files(tpl_path.parent, [merged])
    aux_copied, aux_dropped = _copy_needed_files(
        tpl_path.parent, out, aux_needed, log=log,
        label="模板辅助文件", max_bytes=TEMPLATE_ASSET_MAX_BYTES, keep_existing=True,
    )
    if aux_copied:
        changes.record(
            CH_ASSET, "模板辅助文件", after=f"{len(aux_copied)} 个（{'、'.join(aux_copied[:6])}）",
            reason="目标模板的文档类/文献样式文件随结果包一起提供，保证可独立编译；"
                   "模板包里没被引用到的示例文档、说明书、缩略图一律不带",
            file=main_rel.as_posix(), rule_source=cfg.rule_source("output.latex.template"),
        )
        log.step(f"已复制 {len(aux_copied)} 个模板辅助文件（.cls/.bst 等）")
    if aux_dropped:
        log.step(
            f"模板目录里另有 {len(aux_dropped)} 个文件未复制"
            "（非 LaTeX 支持文件，或模板主文件并未引用，避免污染结果包）"
        )

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

    # ---- 1. 先在内存里把主文件的定点手术做完 ----
    # 顺序上必须先做完手术再决定「带哪些文件」：`op_documentclass` 会把文档类
    # 换成目标期刊的，原稿自己的 `.cls`/`.sty` 就再也不需要了。
    pkgs_add = list(adapter.get("packages_add") or [])
    pkgs_remove = list(adapter.get("packages_remove") or [])
    renames = (adapter.get("adapter") or {}).get("command_renames") or {}

    main_rel_name = main_rel.as_posix()
    main_final = read_text(src_main)
    main_final = op_documentclass(main_final, adapter.get("documentclass", ""), list(adapter.get("class_options") or []), main_rel_name, cfg, changes)
    main_final = op_packages(main_final, pkgs_add, pkgs_remove, main_rel_name, cfg, changes)
    main_final = op_metadata(main_final, ms, cfg, main_rel_name, changes)
    main_final = op_abstract(main_final, cfg, main_rel_name, changes)
    main_final = op_caption_labels(main_final, cfg, main_rel_name, changes)
    main_final = op_bibliography(main_final, cfg, main_rel_name, changes)
    main_final = op_declarations(main_final, ms, cfg, main_rel_name, changes)
    main_final = op_section_numbering(main_final, cfg, main_rel_name, changes)
    for old, new in renames.items():
        if old in main_final:
            n = main_final.count(old)
            main_final = main_final.replace(old, new)
            changes.record(
                CH_STRUCTURE, f"命令重命名 {old} → {new}", before=f"{n} 处",
                reason="目标期刊模板使用不同的命令名（期刊适配器配置）",
                file=main_rel_name, rule_source=cfg.rule_source("output.latex.adapter"),
            )

    # ---- 2. 按引用裁剪并复制项目文件 ----
    copied, dropped = _copy_needed_files(
        src_root, out, _collect_needed_files(src_root, [main_final]),
        skip={main_rel}, log=log, label="项目文件",
    )
    log.step(f"已复制 {len(copied)} 个项目文件到 {out}")
    if dropped:
        changes.record(
            CH_ASSET, "结果包文件裁剪",
            before=f"项目目录 {len(dropped) + len(copied)} 个文件",
            after=f"只带 {len(copied)} 个（正文真正引用到的）",
            reason="未被任何活着的引用指向的文件不进结果包（示例文档、说明书、缩略图、"
                   "废弃图片、未引用的 .bib…）。原稿完整副本仍在 original/ 里。"
                   f"本次未带：{_drop_note(dropped)}",
            file=main_rel_name, rule_source=cfg.rule_source("output.latex.adapter"),
        )
        log.step(f"已裁掉 {len(dropped)} 个未被引用的文件（原件保留在 original/）："
                 f"{'、'.join(dropped[:8])}{' …' if len(dropped) > 8 else ''}")

    write_text(out / main_rel, main_final)

    # ---- 3. 其余 .tex（被 \input 进来的章节文件）继续做章节编号/重命名 ----
    for p in sorted(out.rglob("*.tex")):
        if p == out / main_rel:
            continue
        rel_name = rel(p, out)
        text = read_text(p)
        original = text
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

    # ---- 4. 结果 ----
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
