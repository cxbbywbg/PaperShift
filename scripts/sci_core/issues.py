"""质检问题的统一表示。

需求文档第七节要求检查结果必须区分三类，并且**不能只给一句"检查通过"**，
而要能定位到文件/章节/图表。所以 Issue 里同时带：

* `level`  —— error / warning / info
* `code`   —— 稳定问题码（便于回归对比与抑制）
* `location` —— file / line / section / object 四层定位
* `rule_source` —— 这条判据来自期刊规范的哪一条、来源 URL、验证状态
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

ERROR = "error"
WARNING = "warning"
INFO = "info"

LEVEL_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}
LEVEL_LABEL = {ERROR: "错误", WARNING: "警告", INFO: "提示"}

# 检查类别（与需求文档第七节的表格一一对应）
CAT_FILE = "文件完整性"
CAT_STRUCTURE = "章节结构"
CAT_LAYOUT = "版式"
CAT_ABSTRACT = "摘要与篇幅"
CAT_FIGURE = "图片"
CAT_TABLE = "表格"
CAT_EQUATION = "公式"
CAT_REFERENCE = "参考文献"
CAT_LATEX = "LaTeX 编译"
CAT_DOCX = "Word 文档"
CAT_DECLARATION = "投稿声明"
CAT_CONTENT = "内容与元信息"

CATEGORIES = [
    CAT_FILE, CAT_STRUCTURE, CAT_LAYOUT, CAT_ABSTRACT, CAT_FIGURE,
    CAT_TABLE, CAT_EQUATION, CAT_REFERENCE, CAT_LATEX, CAT_DOCX,
    CAT_DECLARATION, CAT_CONTENT,
]


@dataclass
class Location:
    file: str = ""
    line: int = 0
    section: str = ""
    object: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}

    def label(self) -> str:
        parts = []
        if self.file:
            parts.append(f"{self.file}:{self.line}" if self.line else self.file)
        if self.section:
            parts.append(f"§{self.section}")
        if self.object:
            parts.append(self.object)
        return " ".join(parts)


@dataclass
class Issue:
    level: str
    code: str
    category: str
    message: str
    location: Location = field(default_factory=Location)
    rule_source: dict = field(default_factory=dict)
    suggestion: str = ""
    auto_fixed: bool = False
    evidence: str = ""

    @property
    def sort_key(self):
        return (LEVEL_ORDER.get(self.level, 9), self.category, self.location.file, self.location.line)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["location"] = self.location.to_dict()
        return d

    def line_md(self) -> str:
        loc = self.location.label()
        src = ""
        if self.rule_source:
            j = self.rule_source.get("journal", "")
            fld = self.rule_source.get("field", "")
            ver = self.rule_source.get("verified", "")
            url = self.rule_source.get("source_url", "")
            bits = [x for x in (j, fld, ver) if x]
            src = " · ".join(bits)
            if url:
                src += f" · [{url}]({url})"
        out = f"- `{self.code}` **{self.message}**"
        if loc:
            out += f"\n  - 位置：`{loc}`"
        if self.evidence:
            out += f"\n  - 证据：{self.evidence}"
        if self.suggestion:
            out += f"\n  - 建议：{self.suggestion}"
        if src:
            out += f"\n  - 规范依据：{src}"
        if self.auto_fixed:
            out += "\n  - 已自动修复"
        return out


class IssueBag:
    """问题收集器，顺带做去重。"""

    def __init__(self) -> None:
        self.items: list[Issue] = []
        self._seen: set[tuple] = set()

    def add(self, issue: Issue) -> Issue:
        key = (
            issue.level, issue.code, issue.message,
            issue.location.file, issue.location.line, issue.location.object,
        )
        if key in self._seen:
            return issue
        self._seen.add(key)
        self.items.append(issue)
        return issue

    def error(self, code, category, message, **kw) -> Issue:
        return self.add(Issue(ERROR, code, category, message, **kw))

    def warn(self, code, category, message, **kw) -> Issue:
        return self.add(Issue(WARNING, code, category, message, **kw))

    def info(self, code, category, message, **kw) -> Issue:
        return self.add(Issue(INFO, code, category, message, **kw))

    def extend(self, issues) -> None:
        for i in issues:
            self.add(i)

    def sorted(self) -> list[Issue]:
        return sorted(self.items, key=lambda i: i.sort_key)

    def counts(self) -> dict:
        c = {ERROR: 0, WARNING: 0, INFO: 0}
        for i in self.items:
            c[i.level] = c.get(i.level, 0) + 1
        return c

    def by_category(self) -> dict:
        out: dict[str, list[Issue]] = {}
        for i in self.sorted():
            out.setdefault(i.category, []).append(i)
        return out

    def to_list(self) -> list[dict]:
        return [i.to_dict() for i in self.sorted()]

    def __len__(self) -> int:
        return len(self.items)
