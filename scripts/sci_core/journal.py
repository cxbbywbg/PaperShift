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

import hashlib
import os
import re
import shutil
import tempfile
import zipfile
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

# 官网下载的模板几乎都是压缩包（Elsevier 的 elsarticle kit、Springer 的
# svjour3 kit…），Overleaf 下载的原稿同理。这里在解析阶段统一解包，让
# `--template kit.zip` 等价于「手工解压后指向包内主文件」。
ARCHIVE_SUFFIXES = {".zip"}

# 解包缓存放系统临时目录：模板解析这一层拿不到「结果包工作目录」，
# 且同一模板会被 convert / run 反复解析。目录名带内容指纹，模板换版本自动重建。
ARCHIVE_CACHE_DIR = Path(tempfile.gettempdir()) / "sci-trans-templates"
_ARCHIVE_MARKER = ".sci-trans-extracted"

# macOS 资源叉、Office 临时文件，不能当模板候选
_JUNK_DIRS = {"__MACOSX"}
_JUNK_PREFIXES = ("._", "~$")

# 主文件命名信号，**分正负两档**。
#
# 官方 kit 里 template / main / manuscript 是模板本体，sample / example / doc /
# manual 是示例与说明书。这两类既不能只看体积、也不能只看名字：
#   * 按体积——实测 Elsevier CAS 包里最大的合法文档是 18.5 KB 的
#     `doc/elsdoc-cas.tex`（使用说明书），真正的模板 `cas-sc-template.tex`
#     只有 4.9 KB，是候选里最小的；
#   * 按名字全等匹配——文件名是 `cas-sc-template`，stem 不等于 `template`，
#     四个候选全部不命中，最后退回路径字典序，选到 `cas-dc-sample.tex`。
# 所以改成「分词 + 正负计分」，体积只在同档时做兜底。
_MAIN_NAME_GOOD = ("main", "template", "manuscript", "article", "paper", "submission")
_MAIN_NAME_BAD = (
    "sample", "example", "demo", "doc", "manual", "guide", "readme",
    "instruction", "note",
)

# 版式标记词：文件名里带这些 token 的，视为「同一份模板的不同版式变体」。
# Elsevier 的 cas-sc / cas-dc（单栏 / 双栏）、elsarticle 的 num / harv
# （数字制 / 著者年制）都是这一形态：结构完全一致，只有文档类或引用样式不同。
_VARIANT_TOKENS = frozenset({
    "sc", "dc", "onecolumn", "twocolumn", "onecol", "twocol", "1col", "2col",
    "single", "double", "singlecolumn", "doublecolumn",
    "num", "numeric", "numbered", "harv", "authoryear", "names",
})

# 变体产出顺序：常见「默认版式」排前面（单栏、数字引用制），双栏与著者年制
# 排后面。不定义的话会退化成路径字典序——`dc` 会跑到 `sc` 前面、`harv` 会跑到
# `num` 前面，结果包编号与作者预期相反。
_VARIANT_ORDER = (
    "sc", "onecolumn", "onecol", "1col", "single", "singlecolumn",
    "num", "numeric", "numbered",
    "dc", "twocolumn", "twocol", "2col", "double", "doublecolumn",
)

# 一次最多展开几个变体：同名不同版式的文件理论上可以很多，全出会淹掉结果包。
MAX_TEMPLATE_VARIANTS = 4


def _variant_rank(label: str) -> tuple:
    if label in _VARIANT_ORDER:
        return (0, _VARIANT_ORDER.index(label))
    return (1, 0)


def _file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _split_stem(p: Path) -> list[str]:
    return [t for t in re.split(r"[-_.\s]+", p.stem.lower()) if t]


def _name_score(p: Path) -> int:
    """文件名「像不像模板本体」的信号，正负分档。"""
    score = 0
    for t in _split_stem(p):
        if t in _MAIN_NAME_GOOD:
            score += 1
        elif t in _MAIN_NAME_BAD:
            score -= 1
    return score


def _variant_key(p: Path) -> tuple[str, str]:
    r"""把候选归入「同族」，并给出该变体的版式标签。

    族键 = 所在目录 + **剥掉版式标记后**的名字主干，于是：

    * `cas-sc-template.tex` 与 `cas-dc-template.tex` → 同族，标签 `sc` / `dc`
    * `cas-sc-sample.tex` → 主干含 `sample`，属于另一个族，不会被混进来
    * `elsarticle-num.tex` 与 `elsarticle-harv.tex` → 同族（`num-names` 也同族）
    """
    tokens = _split_stem(p)
    kept = [t for t in tokens if t not in _VARIANT_TOKENS]
    label = "-".join(t for t in tokens if t in _VARIANT_TOKENS)
    return (f"{str(p.parent).lower()}|{'-'.join(kept)}", label)


@dataclass
class TemplateResolution:
    """模板解析结果。

    只返回 Path 无法把「为什么没解析出来」带进质检报告，所以这里连
    原因与提醒一起返回，由调用方决定是回退还是照常转换。
    """

    path: Path | None = None
    reason: str = ""          # 解析失败的原因（用于报告）
    note: str = ""            # 解析成功但需要提醒（如包内有多份候选）
    archive: Path | None = None  # 若来自压缩包，记下包路径
    variant: str = ""         # 同族变体的版式标签（sc / dc / num / harv…）


def _is_junk(p: Path) -> bool:
    return any(part in _JUNK_DIRS for part in p.parts) or p.name.startswith(_JUNK_PREFIXES)


def _pick_archive_main(root: Path, fmt: str) -> tuple[Path | None, list[Path]]:
    """从解包目录里挑出模板主文件，返回 (主文件或 None, 全部候选)。

    候选多于一个时必须提醒用户：同一个模板包里可能有好几份版式不同、
    引用样式也不同的模板（elsarticle kit 里 num / harv / num-names 就是
    三份不同的模板），猜错会静默产出不符合投稿要求的稿子。
    """
    pattern = "*.docx" if fmt == "docx" else "*.tex"
    cands = [p for p in root.rglob(pattern) if not _is_junk(p)]
    if not cands:
        return None, []

    def rank(p: Path) -> tuple:
        depth = len(p.relative_to(root).parts)
        if fmt == "docx":
            # Word 没有 \documentclass 这种骨架信号，靠命名 + 体积
            return (-_name_score(p), depth, -_file_size(p), str(p))
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        # 同时含 \documentclass 与 \begin{document} 的才当得起「骨架」
        strong = ("\\documentclass" in text) + ("\\begin{document}" in text)
        # 顺序：先骨架、再命名分档、再浅目录，体积只做同档兜底
        return (-strong, -_name_score(p), depth, -_file_size(p), str(p))

    ordered = sorted(cands, key=rank)
    return ordered[0], ordered


def _extract_archive(archive: Path, log: Log | None = None) -> Path:
    """把模板压缩包解到缓存目录，返回解包根目录（重复调用复用缓存）。"""
    try:
        st = archive.stat()
    except OSError as exc:
        raise ValueError(f"无法读取压缩包：{archive}（{exc}）") from exc

    key = hashlib.sha1(
        f"{archive.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:12]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", archive.stem)[:40] or "template"
    dest = ARCHIVE_CACHE_DIR / f"{safe}-{key}"

    if (dest / _ARCHIVE_MARKER).is_file():
        if log:
            log.step(f"模板压缩包复用已解包缓存：{dest}")
        return dest

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError(f"模板压缩包无法解开：{archive}（{exc}）") from exc

    # 标记写完才算解包成功，避免把半截目录当缓存复用
    (dest / _ARCHIVE_MARKER).write_text(archive.name, encoding="utf-8")
    if log:
        log.step(f"已解包模板压缩包 {archive.name} → {dest}")
    return dest


def resolve_template(
    spec: str,
    cfg: JournalConfig | None = None,
    fmt: str = "",
    log: Log | None = None,
) -> TemplateResolution:
    """解析模板：单文件、压缩包，或压缩包内指定文件。

    * 单文件 —— LaTeX 的 `.tex` 主文件 / Word 的 `.docx`
    * 压缩包 —— 官网下载的模板包，自动解包并取包内主文件
    * 压缩包内指定 —— `kit.zip::elsarticle-template-num.tex`，包内有多份
      模板、需要明确指定用哪一份时用

    查找顺序：绝对路径 → 相对期刊配置所在目录 → 相对当前工作目录 →
    相对技能根目录（内置模板可以用 `assets/templates/xxx.tex` 这种相对写法）。
    """
    if not spec:
        return TemplateResolution(reason="未提供模板路径")

    raw = str(spec)
    inner = ""
    if "::" in raw:
        raw, inner = raw.split("::", 1)
        inner = inner.strip().lstrip("/\\")

    p = Path(raw)
    cands: list[Path] = []
    if p.is_absolute():
        cands.append(p)
    else:
        if cfg is not None and cfg.path:
            cands.append(Path(cfg.path).parent / p)
        cands.append(Path.cwd() / p)
        cands.append(skill_root() / p)

    hit: Path | None = None
    for c in cands:
        try:
            if c.is_file():
                hit = c.resolve()
                break
        except OSError:
            continue
    if hit is None:
        return TemplateResolution(
            reason=f"文件不存在（找过：{'、'.join(str(c) for c in cands)}）"
        )

    if hit.suffix.lower() not in ARCHIVE_SUFFIXES:
        if inner:
            return TemplateResolution(
                reason=f"{hit.name} 不是压缩包，不能用 :: 指定包内文件"
            )
        return TemplateResolution(path=hit)

    # ---- 压缩包：解包后取主文件 ----
    try:
        root = _extract_archive(hit, log)
    except ValueError as exc:
        return TemplateResolution(reason=str(exc), archive=hit)

    if not fmt:  # 调用方没说是哪条流水线时，按包内实际内容判断
        fmt = "latex" if any(not _is_junk(x) for x in root.rglob("*.tex")) else "docx"

    if inner:
        target = (root / inner).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return TemplateResolution(
                reason=f"包内路径越界：{inner}", archive=hit
            )
        if not target.is_file():
            return TemplateResolution(
                reason=f"包内找不到指定文件：{inner}", archive=hit
            )
        return TemplateResolution(path=target, archive=hit)

    main, all_cands = _pick_archive_main(root, fmt)
    if main is None:
        want = ".docx" if fmt == "docx" else ".tex"
        return TemplateResolution(
            reason=f"压缩包里没有 {want} 主文件（包内可能只是说明文档或宏包）",
            archive=hit,
        )
    note = ""
    if len(all_cands) > 1:
        note = str(main.relative_to(root))
        if log:
            names = "、".join(str(x.relative_to(root)) for x in all_cands[:8])
            log.warn(
                f"模板包内有 {len(all_cands)} 份候选主文件，已选用 {note}；候选：{names}"
            )
    return TemplateResolution(path=main, note=note, archive=hit)


def resolve_template_path(
    spec: str,
    cfg: JournalConfig | None = None,
    fmt: str = "",
    log: Log | None = None,
) -> Path | None:
    """`resolve_template` 的薄封装：只要路径、不要原因。"""
    return resolve_template(spec, cfg, fmt, log).path


def find_template_variants(
    spec: str,
    cfg: JournalConfig | None = None,
    fmt: str = "",
    log: Log | None = None,
) -> list[TemplateResolution]:
    r"""把一个模板解析成一组「同族变体」，供多变体产出使用。

    返回列表的语义：

    * 长度 1 —— 没有变体概念（单文件模板、用 `::` 明确指定了包内文件、
      或包里只有一份候选），调用方按单份处理
    * 长度 >1 —— 包内有同族的多份版式（Elsevier 的 cas-sc / cas-dc、
      elsarticle 的 num / harv…），每份一个元素

    选族规则：只考虑**成员数 ≥ 2** 的族（单成员谈不上变体），在其中有
    「模板本体」命名信号的族优先——CAS 包里 `cas-?-template` 与
    `cas-?-sample` 都成族，必须挑 template 那族，否则会拿示例去投。
    """
    if "::" in str(spec):
        # 作者已经明确说了用哪一份，不再替他展开
        return [resolve_template(spec, cfg, fmt, log)]

    base = resolve_template(spec, cfg, fmt, log)
    if base.path is None or base.archive is None:
        return [base]

    try:
        root = _extract_archive(base.archive, log)  # 命中缓存，不会重复解包
    except ValueError:
        return [base]

    if not fmt:
        fmt = "latex" if any(not _is_junk(x) for x in root.rglob("*.tex")) else "docx"
    pattern = "*.docx" if fmt == "docx" else "*.tex"
    cands = [p for p in root.rglob(pattern) if not _is_junk(p)]
    if not cands:
        return [base]

    families: dict[str, list[tuple[Path, str]]] = {}
    for p in cands:
        key, label = _variant_key(p)
        families.setdefault(key, []).append((p, label))

    multi = {k: v for k, v in families.items() if len(v) >= 2}
    if not multi:
        return [base]

    best_key = sorted(
        multi,
        key=lambda k: (
            -(sum(_name_score(p) for p, _ in multi[k]) / len(multi[k])),
            -len(multi[k]),
            k,
        ),
    )[0]
    members = sorted(multi[best_key], key=lambda t: (_variant_rank(t[1]), t[1]))

    if len(members) > MAX_TEMPLATE_VARIANTS:
        if log:
            log.warn(
                f"模板包内有 {len(members)} 份同族版式，只展开前 {MAX_TEMPLATE_VARIANTS} 份"
            )
        members = members[:MAX_TEMPLATE_VARIANTS]

    if log:
        log.step(
            "模板包内识别到 "
            + "、".join(label or p.stem for p, label in members)
            + f" 共 {len(members)} 份同族版式，将各产出一份结果包"
        )
    return [
        TemplateResolution(
            path=p,
            note=str(p.relative_to(root)),
            archive=base.archive,
            variant=label or p.stem,
        )
        for p, label in members
    ]


def with_variant(
    cfg: JournalConfig, label: str, template_path: str | Path
) -> JournalConfig:
    """派生出一个「版式变体」配置：id 带版式后缀，模板指向该变体文件。

    同族版式会展开成多份结果包，靠 id 后缀区分目录，否则后一份会覆盖前一份。
    """
    import copy as _copy

    raw = _copy.deepcopy(cfg.raw)
    label = label or "variant"
    raw["id"] = f"{cfg.id}-{label}"
    out = raw.setdefault("output", {})
    for side in ("latex", "docx"):
        d = out.get(side)
        if isinstance(d, dict) and d.get("base") == "template":
            d["template"] = str(template_path)
    new = _from_raw(raw, cfg.path)
    new.notes = (
        (cfg.notes + "\n" if cfg.notes else "")
        + f"本次运行按模板包内的「{label}」版式转换（模板：{Path(template_path).name}）"
    )
    return new


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
