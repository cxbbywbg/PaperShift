"""期刊规范配置库。

设计要点（对应需求文档第三节）：

* **每本期刊独立配置**，期刊名、版式、引用规则一律不进转换脚本。
* **每条规则可追溯**：配置里的 `rule_sources` 用点号路径记录来源 URL、
  获取日期与验证状态；质检报告会把命中的规则来源一起列出来。
* **不猜规则**：来源缺失或相互冲突的项标 `needs_confirmation`，
  质检时作为「提示：需要作者/编辑确认」输出，而不是当成硬性要求执行。

配置查找顺序（后面的覆盖前面的）：
  1. 内置 `<skill>/assets/journals/*.yaml`
  2. 用户级 `~/.workbuddy-ai/sci-paper-reformatter/journals/*.yaml`
  3. 项目级 `<cwd>/.sci-trans/journals/*.yaml`
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .util import Log, slug, write_text

VERIFIED = "verified"
NEEDS_CONFIRMATION = "needs_confirmation"
PARTIAL = "partial"

STATUS_LABEL = {
    VERIFIED: "已核对官方指南",
    NEEDS_CONFIRMATION: "待确认（未逐条核对官方指南）",
    PARTIAL: "部分核对",
}


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def builtin_dir() -> Path:
    return skill_root() / "assets" / "journals"


def user_dir() -> Path:
    override = os.environ.get("SCI_TRANS_JOURNALS")
    if override:
        return Path(override)
    return Path.home() / ".workbuddy-ai" / "sci-paper-reformatter" / "journals"


def project_dir(cwd: str | Path | None = None) -> Path:
    return Path(cwd or Path.cwd()) / ".sci-trans" / "journals"


def search_dirs(cwd: str | Path | None = None) -> list[Path]:
    return [builtin_dir(), user_dir(), project_dir(cwd)]


# --------------------------------------------------------------------------
# 配置对象
# --------------------------------------------------------------------------


@dataclass
class JournalConfig:
    id: str
    name: str = ""
    publisher: str = ""
    issn: str = ""
    guide_url: str = ""
    retrieved: str = ""
    status: str = NEEDS_CONFIRMATION
    notes: str = ""
    source_documents: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    path: str = ""

    # ---- 访问 ----
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def has(self, dotted: str) -> bool:
        return self.get(dotted, None) is not None

    def rule_source(self, dotted: str) -> dict:
        """取某条规则的来源信息；支持逐级回退到父路径。"""
        sources = self.raw.get("rule_sources") or {}
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            key = ".".join(parts[:i])
            if key in sources:
                s = dict(sources[key])
                s.setdefault("journal", self.name or self.id)
                s.setdefault("field", dotted)
                s.setdefault("verified", self.status)
                s.setdefault("source_url", self.guide_url)
                return s
        return {
            "journal": self.name or self.id,
            "field": dotted,
            "verified": self.status,
            "source_url": self.guide_url,
        }

    # ---- 便捷读取（带默认值，避免到处写 get(...) or ...）----
    @property
    def ref_style(self) -> str:
        return self.get("references.style", "numbered")

    @property
    def bib_style(self) -> str:
        return self.get("references.bibtex_style", "")

    @property
    def columns(self) -> int:
        return int(self.get("layout.columns", 1) or 1)

    @property
    def abstract_max_words(self) -> int | None:
        v = self.get("abstract.max_words")
        return int(v) if v else None

    @property
    def body_max_words(self) -> int | None:
        v = self.get("body.max_words")
        return int(v) if v else None

    @property
    def required_declarations(self) -> list[str]:
        return list(self.get("declarations.required", []) or [])

    @property
    def ref_max(self) -> int | None:
        v = self.get("references.max_refs")
        return int(v) if v else None

    @property
    def heading_levels_max(self) -> int | None:
        v = self.get("body.heading_levels_max")
        return int(v) if v else None

    @property
    def section_numbering(self) -> bool | None:
        v = self.get("body.section_numbering")
        return None if v is None else bool(v)

    @property
    def required_sections(self) -> list[str]:
        """取第一个 article_type 的 required_sections（配置里是嵌套的）。"""
        ats = self.get("article_types") or []
        if isinstance(ats, list) and ats and isinstance(ats[0], dict):
            return [str(x) for x in (ats[0].get("required_sections") or [])]
        return []

    def latex(self) -> dict:
        return dict(self.get("output.latex", {}) or {})

    def docx(self) -> dict:
        return dict(self.get("output.docx", {}) or {})

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.raw, allow_unicode=True, sort_keys=False, width=100)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "publisher": self.publisher,
            "issn": self.issn,
            "guide_url": self.guide_url,
            "retrieved": self.retrieved,
            "status": self.status,
            "status_label": STATUS_LABEL.get(self.status, self.status),
            "path": self.path,
            "raw": self.raw,
        }


# --------------------------------------------------------------------------
# 加载 / 保存
# --------------------------------------------------------------------------


def _from_raw(raw: dict, path: str = "") -> JournalConfig:
    return JournalConfig(
        id=str(raw.get("id") or Path(path).stem),
        name=str(raw.get("name") or ""),
        publisher=str(raw.get("publisher") or ""),
        issn=str(raw.get("issn") or ""),
        guide_url=str(raw.get("guide_url") or ""),
        retrieved=str(raw.get("retrieved") or ""),
        status=str(raw.get("status") or NEEDS_CONFIRMATION),
        notes=str(raw.get("notes") or ""),
        source_documents=list(raw.get("source_documents") or []),
        raw=raw,
        path=path,
    )


def load(journal_id: str, cwd: str | Path | None = None, log: Log | None = None) -> JournalConfig:
    """按 id 加载期刊配置，项目级 > 用户级 > 内置。"""
    log = log or Log(verbose=False)
    for d in reversed(search_dirs(cwd)):
        if not d.is_dir():
            continue
        for cand in (d / f"{journal_id}.yaml", d / f"{journal_id}.yml"):
            if cand.is_file():
                try:
                    raw = yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                except Exception as exc:
                    raise ValueError(f"期刊配置解析失败 {cand}: {exc}") from exc
                if not isinstance(raw, dict):
                    raise ValueError(f"期刊配置格式错误（应为 YAML 映射）: {cand}")
                raw.setdefault("id", journal_id)
                return _from_raw(raw, str(cand))
    raise FileNotFoundError(
        f"找不到期刊配置 '{journal_id}'。已搜索：{', '.join(str(d) for d in search_dirs(cwd))}\n"
        f"可用期刊：{', '.join(j['id'] for j in list_journals(cwd))}"
    )


def load_file(path: str | Path) -> JournalConfig:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw.setdefault("id", p.stem)
    return _from_raw(raw, str(p))


def save(cfg: JournalConfig, path: str | Path | None = None, cwd: str | Path | None = None) -> str:
    target = Path(path) if path else (user_dir() / f"{cfg.id}.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, cfg.to_yaml())
    return str(target)


def list_journals(cwd: str | Path | None = None) -> list[dict]:
    """列出所有可用期刊配置（同名时以优先级高的为准）。"""
    found: dict[str, dict] = {}
    for d in search_dirs(cwd):  # 内置先扫，后扫的覆盖
        if not d.is_dir():
            continue
        for p in sorted(list(d.glob("*.yaml")) + list(d.glob("*.yml"))):
            if p.name.startswith("_"):
                continue
            try:
                cfg = load_file(p)
            except Exception:
                continue
            found[cfg.id] = {
                "id": cfg.id,
                "name": cfg.name,
                "publisher": cfg.publisher,
                "issn": cfg.issn,
                "status": cfg.status,
                "status_label": STATUS_LABEL.get(cfg.status, cfg.status),
                "retrieved": cfg.retrieved,
                "guide_url": cfg.guide_url,
                "ref_style": cfg.ref_style,
                "columns": cfg.columns,
                "path": str(p),
            }
    return sorted(found.values(), key=lambda x: x["id"])


# --------------------------------------------------------------------------
# 校验与脚手架
# --------------------------------------------------------------------------


REQUIRED_TOP = ["id", "name"]
RECOMMENDED_TOP = [
    "publisher", "guide_url", "retrieved", "status",
    "layout", "abstract", "figures", "tables", "equations",
    "references", "declarations",
]


def validate(cfg: JournalConfig) -> list[dict]:
    """返回问题列表：[{level, field, message}]。"""
    problems: list[dict] = []
    for k in REQUIRED_TOP:
        if not cfg.raw.get(k):
            problems.append({"level": "error", "field": k, "message": f"缺少必填项 {k}"})
    for k in RECOMMENDED_TOP:
        if not cfg.raw.get(k):
            problems.append({"level": "warning", "field": k, "message": f"建议补充 {k}"})

    if cfg.status not in (VERIFIED, NEEDS_CONFIRMATION, PARTIAL):
        problems.append(
            {"level": "error", "field": "status", "message": f"status 取值非法：{cfg.status}"}
        )
    if cfg.status == NEEDS_CONFIRMATION:
        problems.append(
            {
                "level": "warning",
                "field": "status",
                "message": "该配置尚未逐条核对官方投稿指南，转换结果必须人工复核",
            }
        )
    if not cfg.guide_url:
        problems.append(
            {"level": "warning", "field": "guide_url", "message": "缺少官方指南链接，规则无法追溯"}
        )

    style = cfg.get("references.style")
    if style and style not in ("numbered", "author-year"):
        problems.append(
            {"level": "error", "field": "references.style", "message": f"未知引用制：{style}"}
        )

    cols = cfg.get("layout.columns")
    if cols is not None and cols not in (1, 2):
        problems.append({"level": "warning", "field": "layout.columns", "message": "栏数通常为 1 或 2"})

    for k in ("figures.min_dpi_photo", "figures.min_dpi_line"):
        v = cfg.get(k)
        if v is not None and not isinstance(v, (int, float)):
            problems.append({"level": "error", "field": k, "message": "DPI 必须是数字"})

    # 规则来源覆盖检查
    sources = cfg.raw.get("rule_sources") or {}
    if not sources:
        problems.append(
            {
                "level": "warning",
                "field": "rule_sources",
                "message": "没有记录任何规则来源，无法追溯「为什么做这项修改」",
            }
        )
    for key in sources:
        if not cfg.get(key, None) and key not in ("status",):
            problems.append(
                {
                    "level": "warning",
                    "field": f"rule_sources.{key}",
                    "message": f"规则来源指向了不存在的配置项：{key}",
                }
            )
    return problems


SCAFFOLD_TEMPLATE = """# 期刊配置：__NAME__
# 由 sci-paper-reformatter 生成。请逐条核对官方 Guide for Authors 后再把
# status 改成 verified，并把 rule_sources 里的 URL 与获取日期补齐。
id: __JID__
name: "__NAME__"
publisher: ""
issn: ""
guide_url: ""
retrieved: "__TODAY__"
status: needs_confirmation   # verified | partial | needs_confirmation
notes: ""

source_documents: []
#  - "Guide for Authors (PDF, 2026-01)"
#  - "官方 LaTeX 模板 author-kit.zip"

article_types:
  - id: research-article
    name: "Research Article"
    required_sections: [abstract, introduction, methods, results, conclusion]

output:
  formats: [docx, latex, pdf_preview]
  docx:
    base: safe          # safe=在原稿副本上套规范；template=以官方模板为骨架
    template: ""        # 官方 .docx 模板路径（可选）
  latex:
    documentclass: ""   # 例如 elsarticle / IEEEtran / svjour3
    class_options: []
    packages_add: []
    packages_remove: []
    section_style: numbered     # numbered | unnumbered
    adapter: {}                 # 特殊期刊的自定义映射

layout:
  columns: 1
  page: A4
  margin_cm: {top: 2.5, bottom: 2.5, left: 2.5, right: 2.5}
  body_font: {latin: "Times New Roman", eastasia: "宋体", size_pt: 12}
  heading_font: {latin: "Times New Roman", eastasia: "黑体", size_pt: 12}
  line_spacing: 2.0
  paragraph_space_after_pt: 0

abstract:
  max_words: 250
  structured: false
  required_labels: [Background, Methods, Results, Conclusions]
  keywords_min: 3
  keywords_max: 6

body:
  max_words: 8000
  heading_levels_max: 3
  section_numbering: true

figures:
  formats: [tiff, eps, pdf, png]
  min_dpi_photo: 300
  min_dpi_line: 1000
  max_width_cm: 17.0
  caption_position: below
  caption_label: "Fig."

tables:
  caption_position: above
  caption_label: "Table"
  max_width_cm: 17.0
  style: "三线表"
  footnotes_required: true

equations:
  numbering: continuous      # continuous | section | none
  editable_required: true

references:
  style: numbered            # numbered | author-year
  style_name: ""
  max_refs: 60
  doi_required: true
  bibtex_style: ""
  biblatex_style: ""

declarations:
  required: [funding, conflict_of_interest, data_availability]
  optional: [author_contributions, ethics]

rule_sources:
  layout.columns:
    url: ""
    note: ""
    verified: needs_confirmation
  references.style:
    url: ""
    note: ""
    verified: needs_confirmation
  figures.min_dpi_photo:
    url: ""
    note: ""
    verified: needs_confirmation
"""


def scaffold(journal_id: str, name: str = "", cwd: str | Path | None = None, out: str | Path | None = None) -> str:
    jid = slug(journal_id) or "journal"
    text = (
        SCAFFOLD_TEMPLATE
        .replace("__JID__", jid)
        .replace("__NAME__", name or jid)
        .replace("__TODAY__", date.today().isoformat())
    )
    target = Path(out) if out else (user_dir() / f"{jid}.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, text)
    return str(target)


# --------------------------------------------------------------------------
# 模板相关：只用官方模板、不提供期刊配置时的最小配置
# --------------------------------------------------------------------------

TEMPLATE_ONLY_ID = "template-only"


def resolve_template_path(spec: str, cfg: JournalConfig | None = None) -> Path | None:
    """解析模板文件路径。

    依次尝试：绝对路径 → 相对期刊配置所在目录 → 相对当前工作目录 →
    相对技能根目录（这样内置模板可以用 `assets/templates/xxx.tex` 这种相对写法）。
    """
    if not spec:
        return None
    p = Path(spec)
    cands: list[Path] = []
    if p.is_absolute():
        cands.append(p)
    else:
        if cfg is not None and cfg.path:
            cands.append(Path(cfg.path).parent / p)
        cands.append(Path.cwd() / p)
        cands.append(skill_root() / p)
    for c in cands:
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            continue
    return None


def from_template(template: str | Path, fmt: str = "") -> JournalConfig:
    """只给了官方模板、没给期刊配置时的最小配置。

    这种情况下**没有可依据的期刊规范**，所以只做结构移植与通用检查，
    不做任何期刊特定的合规判定；所有规则来源都标 needs_confirmation，
    避免使用者误以为「已按某期刊规范转换」。
    """
    raw = {
        "id": TEMPLATE_ONLY_ID,
        "name": f"仅按官方模板转换（{Path(template).name}）",
        "publisher": "",
        "issn": "",
        "guide_url": "",
        "retrieved": date.today().isoformat(),
        "status": NEEDS_CONFIRMATION,
        "notes": (
            "未提供期刊配置：本次只按官方模板做结构移植，不做期刊规范判定。"
            "投稿前请核对目标期刊的 Guide for Authors。"
        ),
        "output": {
            "formats": ["docx", "latex", "pdf_preview"],
            "docx": {"base": "template", "template": str(template)},
            "latex": {"base": "template", "template": str(template)},
        },
        "rule_sources": {},
    }
    if fmt:
        raw["notes"] += f"（输入格式：{fmt}）"
    return _from_raw(raw, "")


def with_template(cfg: JournalConfig, template: str | Path, fmt: str = "") -> JournalConfig:
    """把配置里的模板路径/模式覆盖成 --template 指定的官方模板（不落盘）。

    只覆盖**与本次输入格式对应**的那一侧，另一侧原样保留——否则拿一份
    .tex 模板去覆盖 Word 侧配置会留下一个永远打不开的模板路径。
    """
    import copy as _copy

    raw = _copy.deepcopy(cfg.raw)
    out = raw.setdefault("output", {})
    sides = ["latex"] if fmt == "latex" else (["docx"] if fmt == "docx" else ["latex", "docx"])
    for side in sides:
        d = out.setdefault(side, {})
        d["base"] = "template"
        d["template"] = str(template)
    new = _from_raw(raw, cfg.path)
    new.notes = (
        (cfg.notes + "\n" if cfg.notes else "")
        + f"本次运行由 --template 覆盖模板为 {template}"
    )
    return new


# --------------------------------------------------------------------------
# 规范化：把配置摊平成质检引擎好用的规则表
# --------------------------------------------------------------------------


def effective_rules(cfg: JournalConfig) -> dict:
    """把配置整理成一张扁平规则表，供 qc.py 直接查。"""
    r = {
        "columns": cfg.get("layout.columns"),
        "page": cfg.get("layout.page"),
        "margin_cm": cfg.get("layout.margin_cm"),
        "body_font": cfg.get("layout.body_font"),
        "heading_font": cfg.get("layout.heading_font"),
        "line_spacing": cfg.get("layout.line_spacing"),
        "paragraph_space_after_pt": cfg.get("layout.paragraph_space_after_pt"),
        "abstract_max_words": cfg.get("abstract.max_words"),
        "abstract_structured": cfg.get("abstract.structured"),
        "abstract_required_labels": cfg.get("abstract.required_labels"),
        "keywords_min": cfg.get("abstract.keywords_min"),
        "keywords_max": cfg.get("abstract.keywords_max"),
        "body_max_words": cfg.get("body.max_words"),
        "heading_levels_max": cfg.get("body.heading_levels_max"),
        "section_numbering": cfg.get("body.section_numbering"),
        "figure_formats": cfg.get("figures.formats"),
        "figure_min_dpi_photo": cfg.get("figures.min_dpi_photo"),
        "figure_min_dpi_line": cfg.get("figures.min_dpi_line"),
        "figure_max_width_cm": cfg.get("figures.max_width_cm"),
        "figure_caption_position": cfg.get("figures.caption_position"),
        "figure_caption_label": cfg.get("figures.caption_label"),
        "table_caption_position": cfg.get("tables.caption_position"),
        "table_caption_label": cfg.get("tables.caption_label"),
        "table_max_width_cm": cfg.get("tables.max_width_cm"),
        "table_style": cfg.get("tables.style"),
        "equation_numbering": cfg.get("equations.numbering"),
        "equation_editable_required": cfg.get("equations.editable_required"),
        "ref_style": cfg.get("references.style"),
        "ref_style_name": cfg.get("references.style_name"),
        "ref_max": cfg.get("references.max_refs"),
        "ref_doi_required": cfg.get("references.doi_required"),
        "declarations_required": cfg.get("declarations.required") or [],
        "declarations_optional": cfg.get("declarations.optional") or [],
        "required_sections": None,
    }
    ats = cfg.get("article_types") or []
    if ats and isinstance(ats, list):
        r["required_sections"] = ats[0].get("required_sections")
        r["article_type_name"] = ats[0].get("name")
    return r


def rule_path_for(field_name: str) -> str:
    """把 effective_rules 的键映射回配置里的点号路径，用于取 rule_sources。"""
    mapping = {
        "columns": "layout.columns",
        "page": "layout.page",
        "margin_cm": "layout.margin_cm",
        "body_font": "layout.body_font",
        "line_spacing": "layout.line_spacing",
        "abstract_max_words": "abstract.max_words",
        "abstract_structured": "abstract.structured",
        "keywords_min": "abstract.keywords_min",
        "keywords_max": "abstract.keywords_max",
        "body_max_words": "body.max_words",
        "heading_levels_max": "body.heading_levels_max",
        "figure_min_dpi_photo": "figures.min_dpi_photo",
        "figure_min_dpi_line": "figures.min_dpi_line",
        "figure_max_width_cm": "figures.max_width_cm",
        "figure_caption_label": "figures.caption_label",
        "table_caption_label": "tables.caption_label",
        "table_max_width_cm": "tables.max_width_cm",
        "equation_numbering": "equations.numbering",
        "ref_style": "references.style",
        "ref_max": "references.max_refs",
        "ref_doi_required": "references.doi_required",
        "declarations_required": "declarations.required",
    }
    return mapping.get(field_name, field_name)
