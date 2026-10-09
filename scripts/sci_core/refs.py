"""参考文献与引用处理。

LaTeX 侧：解析 .bib（BibTeX / BibLaTeX 工作流）、识别引用命令、检查
引用键存在性、未引用条目、文内引用与文末文献一致性。

Word 侧：识别文末参考文献列表。**关键细节**（需求文档第六节）：
Word 里的文献可能是结构化字段（可编辑、可转换），也可能只是普通文本
（自动转换容易误判）。所以这里区分 `confidence = confirmed | needs_review`，
并在报告里把「已可靠识别」与「需要人工确认」分开列。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import model as M
from .issues import CAT_REFERENCE, IssueBag, Location
from .util import Log, normalize_ws, read_text, rel

# 各文献类型必须出现的字段
REQUIRED_FIELDS = {
    "article": ["author", "title", "journal", "year"],
    "inproceedings": ["author", "title", "booktitle", "year"],
    "conference": ["author", "title", "booktitle", "year"],
    "incollection": ["author", "title", "booktitle", "year"],
    "book": ["title", "publisher", "year"],
    "phdthesis": ["author", "title", "school", "year"],
    "mastersthesis": ["author", "title", "school", "year"],
    "techreport": ["author", "title", "institution", "year"],
    "misc": ["title", "year"],
    "unpublished": ["author", "title", "year"],
}
RECOMMENDED_FIELDS = ["doi", "pages", "volume"]


# --------------------------------------------------------------------------
# .bib 解析
# --------------------------------------------------------------------------


def _parse_bib_bibtexparser(text: str) -> list[dict]:
    """优先用 bibtexparser（兼容 v1/v2 API）。"""
    import bibtexparser  # type: ignore

    entries: list[dict] = []
    try:
        if hasattr(bibtexparser, "parse_string"):  # v2
            lib = bibtexparser.parse_string(text)
            for e in getattr(lib, "entries", []):
                fields = {}
                for f in getattr(e, "fields", []):
                    fields[f.key.lower()] = str(f.value)
                entries.append({"key": e.key, "type": str(e.entry_type).lower(), "fields": fields})
            return entries
    except Exception:
        pass
    try:  # v1
        lib = bibtexparser.loads(text)
        for e in lib.entries:
            fields = {k.lower(): v for k, v in e.items() if k.lower() not in ("id", "entrytype")}
            entries.append({"key": e.get("ID", ""), "type": e.get("ENTRYTYPE", "article").lower(), "fields": fields})
    except Exception:
        pass
    return entries


_BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.I)


def _parse_bib_fallback(text: str) -> list[dict]:
    """不依赖第三方库的兜底解析：按 @type{key, ... } 切块并取 field = value。"""
    entries: list[dict] = []
    for m in _BIB_ENTRY_RE.finditer(text):
        etype, key = m.group(1).lower(), m.group(2).strip()
        if etype in ("comment", "preamble", "string"):
            continue
        depth, i = 1, m.end()
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[m.end() : i - 1]
        fields: dict[str, str] = {}
        for fm in re.finditer(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,\n]+)", body):
            name = fm.group(1).lower()
            val = fm.group(2).strip()
            if val.startswith("{"):
                val = val[1:-1]
            elif val.startswith('"'):
                val = val[1:-1]
            fields[name] = normalize_ws(val)
        entries.append({"key": key, "type": etype, "fields": fields})
    return entries


def parse_bib_file(path: str | Path, log: Log | None = None) -> list[M.Reference]:
    log = log or Log(verbose=False)
    p = Path(path)
    text = read_text(p)
    entries = _parse_bib_bibtexparser(text)
    parser_used = "bibtexparser"
    if not entries:
        entries = _parse_bib_fallback(text)
        parser_used = "fallback"
    if not entries and "@" in text:
        log.warn(f"{p.name}: .bib 解析未得到任何条目（parser={parser_used}）")
    out: list[M.Reference] = []
    for e in entries:
        if not e.get("key"):
            continue
        out.append(
            M.Reference(
                key=e["key"],
                origin="bib",
                type=e.get("type", "article"),
                fields=e.get("fields", {}),
                raw="",
                confidence="confirmed",
                span=M.Span(file=str(p)),
            )
        )
    return out


def parse_ris_file(path: str | Path) -> list[M.Reference]:
    """极简 RIS 解析：TY 开始、ER 结束，两字符标签 + 空格 + 值。"""
    p = Path(path)
    records: list[dict] = []
    cur: dict[str, list[str]] = {}
    for line in read_text(p).split("\n"):
        m = re.match(r"^([A-Z][A-Z0-9])\s+-\s?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"TY": [val]}
        elif tag == "ER":
            if cur:
                records.append(cur)
            cur = {}
        elif cur:
            cur.setdefault(tag, []).append(val)
    out: list[M.Reference] = []
    for i, r in enumerate(records, 1):
        fields = {
            "title": (r.get("TI") or r.get("T1") or [""])[0],
            "author": "; ".join(r.get("AU", [])),
            "journal": (r.get("JO") or r.get("JF") or r.get("T2") or [""])[0],
            "year": ((r.get("PY") or r.get("Y1") or [""])[0])[:4],
            "doi": (r.get("DO") or [""])[0],
            "pages": (r.get("SP") or [""])[0],
            "volume": (r.get("VL") or [""])[0],
        }
        key = fields["doi"] or f"ris{i}"
        out.append(
            M.Reference(
                key=re.sub(r"[^A-Za-z0-9]+", "_", key)[:40],
                origin="ris",
                type="article",
                fields={k: v for k, v in fields.items() if v},
                confidence="confirmed",
                span=M.Span(file=str(p)),
            )
        )
    return out


def load_references(ms: M.Manuscript, root: str | Path, log: Log | None = None) -> list[M.Reference]:
    """把 .bib / .ris 里的条目合并进 Manuscript.references。"""
    log = log or Log(verbose=False)
    root = Path(root)
    refs: list[M.Reference] = list(ms.references)
    seen = {r.key for r in refs}
    for name in ms.bib_resources:
        raw = name.strip().replace("\\", "/")
        for cand in (root / raw, root / (raw + ".bib")):
            if cand.is_file():
                for r in parse_bib_file(cand, log):
                    if r.key not in seen:
                        seen.add(r.key)
                        refs.append(r)
                break
    for p in sorted(root.rglob("*.ris")):
        for r in parse_ris_file(p):
            if r.key not in seen:
                seen.add(r.key)
                refs.append(r)
    return refs


# --------------------------------------------------------------------------
# 检查
# --------------------------------------------------------------------------


def check_references(ms: M.Manuscript, bag: IssueBag, root: str | Path, style_hint: str = "") -> None:
    root = Path(root)
    by_key: dict[str, list[M.Reference]] = {}
    for r in ms.references:
        by_key.setdefault(r.key, []).append(r)

    cited = {c.key for c in ms.citations if c.key and c.key != "*"}

    # ---- 1. 引用键存在性 ----
    for c in ms.citations:
        if not c.key or c.key == "*":
            continue
        if c.key not in by_key:
            bag.error(
                "REF001", CAT_REFERENCE,
                f"引用了不存在的文献键：{c.key}",
                location=Location(
                    file=(c.span.file if c.span else ""),
                    line=(c.span.line if c.span else 0),
                    section=c.section or "",
                    object=f"cit:{c.key}",
                ),
                suggestion="在 .bib 中补上该条目，或修正 \\cite 的键名。",
            )

    # ---- 2. 未引用的文献条目 ----
    for key, items in sorted(by_key.items()):
        r = items[0]
        if r.origin in ("bib", "ris") and key not in cited:
            bag.warn(
                "REF010", CAT_REFERENCE,
                f"文献条目未被正文引用：{key}",
                location=Location(file=(r.span.file if r.span else ""), object=f"ref:{key}"),
                evidence=normalize_ws(r.title)[:90],
                suggestion="未引用的条目不会出现在参考文献表中，确认是否漏引或可删除。",
            )

    # ---- 3. 重复条目 ----
    for key, items in sorted(by_key.items()):
        if len(items) > 1:
            bag.error(
                "REF011", CAT_REFERENCE,
                f"重复的文献键：{key}（出现 {len(items)} 次）",
                location=Location(object=f"ref:{key}"),
            )
    by_doi: dict[str, list[str]] = {}
    by_title: dict[str, list[str]] = {}
    for r in ms.references:
        d = (r.fields.get("doi") or "").lower().strip()
        if d:
            by_doi.setdefault(d, []).append(r.key)
        t = re.sub(r"[^a-z0-9]+", "", (r.title or "").lower())
        if t:
            by_title.setdefault(t, []).append(r.key)
    for d, keys in by_doi.items():
        if len(set(keys)) > 1:
            bag.warn(
                "REF012", CAT_REFERENCE,
                f"不同文献键使用了同一个 DOI：{d}",
                location=Location(object=", ".join(sorted(set(keys)))),
                suggestion="可能是同一篇文献被重复录入。",
            )
    for t, keys in by_title.items():
        if len(set(keys)) > 1 and len(t) > 20:
            bag.warn(
                "REF013", CAT_REFERENCE,
                f"疑似重复条目（标题相同）：{', '.join(sorted(set(keys)))}",
                location=Location(object=", ".join(sorted(set(keys)))),
            )

    # ---- 4. 字段完整性 ----
    for r in ms.references:
        if r.origin not in ("bib", "ris"):
            continue
        req = REQUIRED_FIELDS.get(r.type, ["title", "year"])
        missing = [f for f in req if not (r.fields.get(f) or "").strip()]
        if missing:
            bag.warn(
                "REF020", CAT_REFERENCE,
                f"文献 {r.key}（{r.type}）缺少必填字段：{', '.join(missing)}",
                location=Location(file=(r.span.file if r.span else ""), object=f"ref:{r.key}"),
                suggestion="目标期刊的参考文献样式通常要求这些字段完整。",
            )
        if not (r.fields.get("doi") or "").strip():
            bag.info(
                "REF021", CAT_REFERENCE,
                f"文献 {r.key} 缺少 DOI",
                location=Location(object=f"ref:{r.key}"),
                suggestion="越来越多的期刊要求参考文献提供 DOI。",
            )

    # ---- 5. Word 侧：区分可靠识别与需人工确认 ----
    text_refs = [r for r in ms.references if r.origin == "docx_text"]
    if text_refs:
        bag.info(
            "REF030", CAT_REFERENCE,
            f"{len(text_refs)} 条参考文献是从普通文本中解析的，需要人工确认",
            location=Location(object=f"共 {len(text_refs)} 条"),
            evidence="；".join(normalize_ws(r.raw)[:60] for r in text_refs[:3]),
            suggestion=(
                "Word 里若文献不是结构化字段而是普通文本，自动转换可能误判。"
                "请对照原文核对这几条的作者、年份与期刊名。"
            ),
        )
    field_refs = [r for r in ms.references if r.origin == "docx_field"]
    if field_refs:
        bag.info(
            "REF031", CAT_REFERENCE,
            f"{len(field_refs)} 条参考文献来自 Word 结构化文献字段，可较可靠地转换",
            location=Location(object=f"共 {len(field_refs)} 条"),
        )

    # ---- 6. 文内引用编号与文末顺序 ----
    if ms.citations and ms.references:
        if style_hint == "numbered":
            order = []
            for c in ms.citations:
                if c.key not in order:
                    order.append(c.key)
            missing = [k for k in order if k not in by_key]
            if missing:
                bag.error(
                    "REF040", CAT_REFERENCE,
                    f"编号制引用中，正文引用的键在文末文献表里不存在：{', '.join(missing)}",
                    location=Location(object="refs"),
                )
            pos = {k: i for i, k in enumerate(order)}
            listed = [r.key for r in ms.references if r.key in pos]
            if listed != sorted(listed, key=lambda k: pos[k]):
                bag.warn(
                    "REF041", CAT_REFERENCE,
                    "文末文献表的顺序与正文首次引用顺序不一致（编号制要求按引用顺序排列）",
                    location=Location(object="refs"),
                    suggestion="按正文首次出现顺序重排参考文献表。",
                )
        elif style_hint == "author-year":
            for r in ms.references:
                if r.key in cited and not (r.fields.get("year") or "").strip():
                    bag.error(
                        "REF042", CAT_REFERENCE,
                        f"作者—年份制要求年份，但文献 {r.key} 没有 year 字段",
                        location=Location(object=f"ref:{r.key}"),
                    )


# --------------------------------------------------------------------------
# 样式转换（展示层）
# --------------------------------------------------------------------------


def author_year_label(ref: M.Reference) -> str:
    """作者—年份制的文内标注，例如 (Zhang et al., 2024)。"""
    authors = (ref.fields.get("author") or "").strip()
    year = (ref.fields.get("year") or "n.d.").strip()
    if not authors:
        return f"({year})"
    first = re.split(r"\s+and\s+|;|,", authors)[0].strip()
    surname = first.split(",")[0].strip() if "," in first else first.split()[-1]
    n_authors = len([a for a in re.split(r"\s+and\s+|;", authors) if a.strip()])
    if n_authors > 1:
        surname += " et al."
    return f"({surname}, {year})"


def format_reference(ref: M.Reference, style: str = "generic") -> str:
    """把结构化条目渲染成参考文献文本（用于生成 Word 文末列表或 LaTeX 条目）。"""
    f = ref.fields
    authors = f.get("author", "")
    authors = re.sub(r"\s+and\s+", ", ", authors)
    title = f.get("title", "")
    year = f.get("year", "")
    journal = f.get("journal") or f.get("booktitle") or f.get("publisher", "")
    vol = f.get("volume", "")
    num = f.get("number", "")
    pages = f.get("pages", "").replace("--", "–")
    doi = f.get("doi", "")

    if style == "author-year":
        parts = [f"{authors} ({year}).", f"{title}."]
        if journal:
            parts.append(f"{journal},")
        if vol:
            parts.append(f"{vol}" + (f"({num})" if num else ""))
        if pages:
            parts.append(f"{pages}.")
        if doi:
            parts.append(f"https://doi.org/{doi}")
        return normalize_ws(" ".join(parts))

    # 默认：数字制（Vancouver 风格）
    parts = []
    if authors:
        parts.append(authors + ".")
    if title:
        parts.append(title + ".")
    if journal:
        parts.append(journal + ".")
    if year:
        parts.append(year + ";")
    if vol:
        seg = vol
        if num:
            seg += f"({num})"
        if pages:
            seg += f":{pages}"
        parts.append(seg + ".")
    elif pages:
        parts.append(f":{pages}.")
    if doi:
        parts.append(f"doi:{doi}.")
    return normalize_ws(" ".join(parts))
