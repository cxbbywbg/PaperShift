"""统一论文内容模型（Manuscript）。

设计原则（对应需求文档 1.3）：

* **承载内容与关联关系**：每个图/表/公式/引用都持有稳定标识符，
  以及「它在哪一节」「谁引用了它」这两类关系。
* **不强行抹平 Word 与 LaTeX 的差异**：无法无损统一的元素
  （复杂公式、Word 域、LaTeX 宏）保留原始表示（`raw` / `native` 字段），
  由各自的适配器在输出阶段处理。

所有对象都能 `to_dict()`，整个 Manuscript 可序列化为 JSON，
这是「先解析成模型、再基于模型转换」这条流水线的契约。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"

# 块类型
BLOCK_PARAGRAPH = "paragraph"
BLOCK_HEADING = "heading"
BLOCK_LIST = "list"
BLOCK_QUOTE = "quote"
BLOCK_VERBATIM = "verbatim"
BLOCK_MATH = "display_math"
BLOCK_RAW = "raw"

# 来源格式
FMT_LATEX = "latex"
FMT_DOCX = "docx"


# --------------------------------------------------------------------------
# 基础结构
# --------------------------------------------------------------------------


@dataclass
class Span:
    """元素在源文件中的位置，用于把检查结果定位回文件/行。"""

    file: str = ""
    line: int = 0
    col: int = 0

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "col": self.col}

    def label(self) -> str:
        if not self.file:
            return ""
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass
class Block:
    """章节内的一个内容块。"""

    id: str
    kind: str
    text: str = ""
    style: str = ""
    span: Span | None = None
    native: dict = field(default_factory=dict)  # 原始表示（如 OMML XML、LaTeX 片段）

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = self.span.to_dict() if self.span else None
        return d


@dataclass
class Section:
    id: str  # sec:1 / sec:1.2
    level: int  # 1=section 2=subsection 3=subsubsection
    heading: str
    number: str = ""
    parent: str | None = None
    labels: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    span: Span | None = None
    starred: bool = False  # \section*{} —— 不编号

    @property
    def words(self) -> int:
        return sum(len(b.text.split()) for b in self.blocks)

    @property
    def chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "heading": self.heading,
            "number": self.number,
            "parent": self.parent,
            "labels": list(self.labels),
            "starred": self.starred,
            "span": self.span.to_dict() if self.span else None,
            "words": self.words,
            "chars": self.chars,
            "blocks": [b.to_dict() for b in self.blocks],
        }


@dataclass
class Figure:
    id: str  # fig:1
    number: int = 0
    label: str | None = None  # LaTeX \label
    caption: str = ""
    files: list[str] = field(default_factory=list)
    subfigures: list[dict] = field(default_factory=list)
    env: str = "figure"
    placement: str = ""
    width: str = ""
    section: str | None = None
    referenced_in: list[str] = field(default_factory=list)
    caption_in_source: bool = False
    span: Span | None = None
    native: dict = field(default_factory=dict)

    @property
    def anchors(self) -> list[str]:
        out = [self.id]
        if self.label:
            out.append(self.label)
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = self.span.to_dict() if self.span else None
        return d


@dataclass
class Table:
    id: str  # tab:1
    number: int = 0
    label: str | None = None
    caption: str = ""
    env: str = "table"
    colspec: str = ""
    n_cols: int = 0
    n_rows: int = 0
    footnotes: list[str] = field(default_factory=list)
    section: str | None = None
    referenced_in: list[str] = field(default_factory=list)
    float_placement: str = ""
    span: Span | None = None
    raw: str = ""
    native: dict = field(default_factory=dict)

    @property
    def anchors(self) -> list[str]:
        out = [self.id]
        if self.label:
            out.append(self.label)
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = self.span.to_dict() if self.span else None
        return d


@dataclass
class Equation:
    id: str  # eq:1
    number: int = 0
    label: str | None = None
    env: str = "equation"
    source: str = ""  # 公式源码（LaTeX）或 OMML 文本（Word）
    section: str | None = None
    referenced_in: list[str] = field(default_factory=list)
    span: Span | None = None
    native: dict = field(default_factory=dict)

    @property
    def anchors(self) -> list[str]:
        out = [self.id]
        if self.label:
            out.append(self.label)
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = self.span.to_dict() if self.span else None
        return d


@dataclass
class Citation:
    key: str
    command: str = "cite"  # cite / parencite / textcite / ref
    section: str | None = None
    span: Span | None = None
    resolved: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = self.span.to_dict() if self.span else None
        return d


@dataclass
class Reference:
    """文末文献条目。origin 标明它是怎么被识别出来的，这决定置信度。"""

    key: str
    origin: str = "bib"  # bib | latex_manual | docx_field | docx_text
    type: str = "article"
    fields: dict = field(default_factory=dict)
    raw: str = ""
    used: bool = False
    confidence: str = "confirmed"  # confirmed | needs_review
    span: Span | None = None

    @property
    def title(self) -> str:
        return self.fields.get("title", "")

    @property
    def year(self) -> str:
        return str(self.fields.get("year", ""))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = self.span.to_dict() if self.span else None
        return d


@dataclass
class Declaration:
    """投稿声明：基金、利益冲突、数据可用性、伦理、作者贡献等。"""

    kind: str
    present: bool = False
    text: str = ""
    section: str | None = None
    span: Span | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = self.span.to_dict() if self.span else None
        return d


@dataclass
class Metadata:
    title: str = ""
    authors: list[dict] = field(default_factory=list)  # {name, affil, email, orcid}
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    abstract_structured: dict[str, str] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    corresponding: str = ""
    running_title: str = ""
    doc_meta: dict = field(default_factory=dict)
    native: dict = field(default_factory=dict)
    # ---- 原文（LaTeX 源码）----
    # 上面 title/abstract/keywords 是 detex 之后的纯文本，用于字数统计与内容比较；
    # 模板移植必须用下面的原文，否则 \method{}、41.7\%、$1.9\times$ 会被抹掉。
    title_raw: str = ""
    abstract_raw: str = ""
    keywords_raw: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# 论文
# --------------------------------------------------------------------------


@dataclass
class Manuscript:
    source_format: str = ""
    source_path: str = ""
    main_file: str = ""
    project_files: list[str] = field(default_factory=list)
    metadata: Metadata = field(default_factory=Metadata)
    sections: list[Section] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    equations: list[Equation] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    declarations: list[Declaration] = field(default_factory=list)
    crossrefs: list[dict] = field(default_factory=list)  # {id, command, target, section, span}
    # LaTeX 特有
    documentclass: str = ""
    class_options: list[str] = field(default_factory=list)
    packages: list[dict] = field(default_factory=list)  # {name, options}
    bib_resources: list[str] = field(default_factory=list)
    bib_style: str = ""
    bib_tool: str = "bibtex"  # bibtex | biblatex
    custom_macros: list[str] = field(default_factory=list)
    # Word 特有
    page_setup: dict = field(default_factory=dict)  # {w_cm,h_cm,orient,margins_cm}
    styles_used: dict = field(default_factory=dict)  # 样式名 -> 出现次数
    has_tracked_changes: bool = False
    has_comments: bool = False
    has_fields: bool = False
    # 解析诊断
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    # ---- 统计 ----
    @property
    def body_words(self) -> int:
        return sum(s.words for s in self.sections)

    @property
    def body_chars(self) -> int:
        return sum(s.chars for s in self.sections)

    @property
    def abstract_words(self) -> int:
        return len(self.metadata.abstract.split())

    def top_sections(self) -> list[Section]:
        return [s for s in self.sections if s.level == 1]

    def section_by_id(self, sid: str) -> Section | None:
        return next((s for s in self.sections if s.id == sid), None)

    # ---- 标签索引：label/编号 -> 对象 ----
    def label_index(self) -> dict[str, str]:
        r"""LaTeX \label 或自动编号 -> 对象 id。用于解析 \ref / 交叉引用。"""
        idx: dict[str, str] = {}
        for f in self.figures:
            idx[f"fig:{f.number}"] = f.id
            if f.label:
                idx[f.label] = f.id
        for t in self.tables:
            idx[f"tab:{t.number}"] = t.id
            if t.label:
                idx[t.label] = t.id
        for e in self.equations:
            idx[f"eq:{e.number}"] = e.id
            if e.label:
                idx[e.label] = e.id
        for s in self.sections:
            for lb in s.labels:
                idx[lb] = s.id
        return idx

    def objects_by_id(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for coll in (self.figures, self.tables, self.equations, self.sections):
            for obj in coll:
                out[obj.id] = obj
        return out

    def stats(self) -> dict:
        return {
            "sections": len(self.sections),
            "top_sections": len(self.top_sections()),
            "figures": len(self.figures),
            "tables": len(self.tables),
            "equations": len(self.equations),
            "citations": len(self.citations),
            "unique_citation_keys": len({c.key for c in self.citations}),
            "crossrefs": len(self.crossrefs),
            "references": len(self.references),
            "declarations": len(self.declarations),
            "body_words": self.body_words,
            "body_chars": self.body_chars,
            "abstract_words": self.abstract_words,
            "keywords": len(self.metadata.keywords),
        }

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "source_format": self.source_format,
            "source_path": self.source_path,
            "main_file": self.main_file,
            "project_files": list(self.project_files),
            "metadata": self.metadata.to_dict(),
            "sections": [s.to_dict() for s in self.sections],
            "figures": [f.to_dict() for f in self.figures],
            "tables": [t.to_dict() for t in self.tables],
            "equations": [e.to_dict() for e in self.equations],
            "citations": [c.to_dict() for c in self.citations],
            "references": [r.to_dict() for r in self.references],
            "declarations": [d.to_dict() for d in self.declarations],
            "crossrefs": list(self.crossrefs),
            "latex": {
                "documentclass": self.documentclass,
                "class_options": list(self.class_options),
                "packages": list(self.packages),
                "bib_resources": list(self.bib_resources),
                "bib_style": self.bib_style,
                "bib_tool": self.bib_tool,
                "custom_macros": list(self.custom_macros),
            },
            "docx": {
                "page_setup": dict(self.page_setup),
                "styles_used": dict(self.styles_used),
                "has_tracked_changes": self.has_tracked_changes,
                "has_comments": self.has_comments,
                "has_fields": self.has_fields,
            },
            "warnings": list(self.warnings),
            "stats": self.stats(),
        }


# --------------------------------------------------------------------------
# 结构化标题识别（Word 侧用）
# --------------------------------------------------------------------------

_HEADING_PATTERNS = [
    (1, re.compile(r"^\s*(\d+)[.\s]+([A-Z\u4e00-\u9fff].*)$")),
    (2, re.compile(r"^\s*(\d+\.\d+)[.\s]+([A-Z\u4e00-\u9fff].*)$")),
    (3, re.compile(r"^\s*(\d+\.\d+\.\d+)[.\s]+([A-Z\u4e00-\u9fff].*)$")),
]

COMMON_HEADINGS = {
    "abstract", "introduction", "background", "related work", "method",
    "methods", "methodology", "materials and methods", "results",
    "results and discussion", "discussion", "conclusion", "conclusions",
    "acknowledgment", "acknowledgments", "acknowledgements", "references",
    "appendix", "supplementary material", "摘要", "引言", "结论", "参考文献",
}


def guess_heading_number(text: str) -> tuple[int, str, str] | None:
    """从纯文本标题里猜出 (level, number, title)。识别不了返回 None。"""
    t = (text or "").strip()
    for level, pat in _HEADING_PATTERNS:
        m = pat.match(t)
        if m:
            return level, m.group(1), m.group(2).strip()
    low = re.sub(r"[.:：]\s*$", "", t.lower()).strip()
    if low in COMMON_HEADINGS:
        return 1, "", t
    return None


# --------------------------------------------------------------------------
# 章节编号
# --------------------------------------------------------------------------


def renumber_sections(sections: list[Section]) -> None:
    """按出现顺序重算 section 编号（用于期刊要求「重新编号」的场景）。"""
    counters = [0, 0, 0, 0, 0]
    for s in sections:
        lvl = max(1, min(s.level, 5))
        counters[lvl - 1] += 1
        for i in range(lvl, 5):
            counters[i] = 0
        s.number = ".".join(str(c) for c in counters[:lvl] if c)
        if lvl > 1:
            parent_num = ".".join(str(c) for c in counters[: lvl - 1])
            s.parent = next(
                (p.id for p in sections if p.number == parent_num and p.level == lvl - 1),
                None,
            )
