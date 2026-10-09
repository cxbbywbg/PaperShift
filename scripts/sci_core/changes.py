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


@dataclass
class Skip:
    """一次「原稿里没有这项内容，于是整条没迁移」的记录。

    与 `Change` 相对：`Change` 记的是「改了什么」，`Skip` 记的是「本该有、
    但原稿没有，所以略过了什么」。两者必须分开记——

    以前的做法是「原稿没有摘要就往结果里写 `\\abstract{[待作者填写]}`」，
    于是差异报告把空摘要和原稿摘要一比，报出「摘要内容被改了」；作者看到
    的是一堆占位符，而不是一张「我缺哪些字段」的清单。跳过清单把这件事
    说清楚：哪些字段没迁到、为什么、期刊是否强制要求。

    `required` 只在**期刊配置里写得出来**时才为真（`article_types.required_sections`
    含 abstract、`abstract.keywords_min > 0` 等）；查不到就不当"必须"——
    本项目的原则是「不猜规则」。
    """

    target: str
    reason: str
    source: str = ""          # 原稿中该字段的状态（便于作者定位）
    required: bool = False    # 目标期刊规范是否明确要求该字段

    def to_dict(self) -> dict:
        return asdict(self)

    def line_md(self) -> str:
        out = f"- `{self.target}` —— {self.reason}"
        if self.source:
            out += f"\n  - 原稿状态：{self.source}"
        if self.required:
            out += "\n  - ⚠️ 目标期刊规范要求该字段，投稿前必须补上"
        return out


class ChangeLog:
    def __init__(self) -> None:
        self.items: list[Change] = []
        self._seen: set[tuple] = set()
        self.skips: list[Skip] = []
        self._seen_skips: set[tuple] = set()

    def add(self, change: Change) -> Change:
        key = (change.kind, change.target, change.before, change.after, change.file, change.line)
        if key in self._seen:
            return change
        self._seen.add(key)
        self.items.append(change)
        return change

    def record(self, kind, target, before="", after="", reason="", **kw) -> Change:
        return self.add(Change(kind=kind, target=target, before=before, after=after, reason=reason, **kw))

    def skip(self, target: str, reason: str, source: str = "", required: bool = False) -> Skip:
        """记一条「原稿没有该内容，故未迁移」。同一 (target, reason) 只记一次。"""
        key = (target, reason, source)
        if key in self._seen_skips:
            return Skip(target=target, reason=reason, source=source, required=required)
        self._seen_skips.add(key)
        s = Skip(target=target, reason=reason, source=source, required=required)
        self.skips.append(s)
        return s

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

    def required_skips(self) -> list[Skip]:
        return [s for s in self.skips if s.required]

    def summary(self) -> dict:
        """迁移结果汇总：改了多少、略过了多少、其中多少是期刊强制要求的。

        这是「迁移结果打印」与「未迁移清单」两处共用的唯一口径。
        """
        return {
            "changed": len(self.items),
            "skipped": len(self.skips),
            "needs_author": len(self.needs_author()),
            "required_missing": [s.target for s in self.skips if s.required],
        }

    def to_list(self) -> list[dict]:
        return [c.to_dict() for c in self.items]

    def skips_to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.skips]

    def counts(self) -> dict:
        out = {k: 0 for k in KINDS}
        for c in self.items:
            out[c.kind] = out.get(c.kind, 0) + 1
        return {k: v for k, v in out.items() if v}

    def __len__(self) -> int:
        return len(self.items)
