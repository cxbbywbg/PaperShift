"""变更记录。

需求文档反复强调：**默认不改写论文正文**、**不擅自删改内容**、
**所有实质性修改都要记录差异**。所以转换过程中的每一次改动都落成一条 Change，
带上「改了什么、为什么改、依据哪条期刊规则」，供差异报告与转换记录使用。

`auto` 用来区分「脚本自动做的格式改动」与「需要作者确认的实质性改动」。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# 变更类别
CH_METADATA = "元信息"
CH_PREAMBLE = "导言区"
CH_LAYOUT = "版式"
CH_STRUCTURE = "章节结构"
CH_CAPTION = "题注"
CH_REFERENCE = "参考文献"
CH_DECLARATION = "投稿声明"
CH_ASSET = "资源路径"
CH_CONTENT = "正文内容"
CH_DOCMETA = "文档属性"

KINDS = [CH_METADATA, CH_PREAMBLE, CH_LAYOUT, CH_STRUCTURE, CH_CAPTION,
         CH_REFERENCE, CH_DECLARATION, CH_ASSET, CH_CONTENT, CH_DOCMETA]


@dataclass
class Change:
    kind: str
    target: str
    before: str = ""
    after: str = ""
    reason: str = ""
    file: str = ""
    line: int = 0
    rule_source: dict = field(default_factory=dict)
    auto: bool = True
    needs_author: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def line_md(self) -> str:
        out = f"- **{self.kind}** · `{self.target}`"
        if self.before or self.after:
            b = (self.before or "").replace("\n", " ")[:110]
            a = (self.after or "").replace("\n", " ")[:110]
            out += f"\n  - 变更前：`{b}`\n  - 变更后：`{a}`"
        if self.reason:
            out += f"\n  - 原因：{self.reason}"
        if self.rule_source:
            bits = [x for x in (
                self.rule_source.get("journal", ""),
                self.rule_source.get("field", ""),
                self.rule_source.get("verified", ""),
            ) if x]
            if bits:
                out += f"\n  - 依据：{' · '.join(bits)}"
            if self.rule_source.get("source_url"):
                out += f" · {self.rule_source['source_url']}"
        if self.needs_author:
            out += "\n  - ⚠️ 需作者确认"
        if self.file:
            out += f"\n  - 位置：`{self.file}" + (f":{self.line}`" if self.line else "`")
        return out


class ChangeLog:
    def __init__(self) -> None:
        self.items: list[Change] = []
        self._seen: set[tuple] = set()

    def add(self, change: Change) -> Change:
        key = (change.kind, change.target, change.before, change.after, change.file, change.line)
        if key in self._seen:
            return change
        self._seen.add(key)
        self.items.append(change)
        return change

    def record(self, kind, target, before="", after="", reason="", **kw) -> Change:
        return self.add(Change(kind=kind, target=target, before=before, after=after, reason=reason, **kw))

    def extend(self, changes) -> None:
        for c in changes:
            self.add(c)

    def by_kind(self) -> dict[str, list[Change]]:
        out: dict[str, list[Change]] = {}
        for c in self.items:
            out.setdefault(c.kind, []).append(c)
        return out

    def needs_author(self) -> list[Change]:
        return [c for c in self.items if c.needs_author]

    def to_list(self) -> list[dict]:
        return [c.to_dict() for c in self.items]

    def counts(self) -> dict:
        out = {k: 0 for k in KINDS}
        for c in self.items:
            out[c.kind] = out.get(c.kind, 0) + 1
        return {k: v for k, v in out.items() if v}

    def __len__(self) -> int:
        return len(self.items)
