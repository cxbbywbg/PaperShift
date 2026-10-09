"""报告渲染：质检报告、差异报告、转换记录。

需求文档第九节要求每次运行输出四份东西：转换后的源文件、PDF 预览、
质量检查报告、差异报告、转换记录。这里负责后三份的 Markdown 渲染。

质检报告刻意**不写成一句"检查通过"**：它列出每类检查执行了什么、
命中多少问题、每条问题的位置与规范来源，以及需要人工确认的清单。
"""
from __future__ import annotations

import platform
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, model as M
from .changes import ChangeLog
from .issues import CATEGORIES, ERROR, INFO, LEVEL_LABEL, WARNING, IssueBag
from .journal import JournalConfig, STATUS_LABEL
from .util import rel

# 每类检查「做了什么」，用于报告里的通过项说明
CHECK_SCOPE = {
    "文件完整性": "必要文件是否存在、资源路径是否正确",
    "章节结构": "必要章节是否缺失、标题层级是否异常、图表公式是否被正文引用",
    "版式": "页面尺寸、栏布局、页边距、字体与段落规范",
    "摘要与篇幅": "摘要字数、结构化摘要栏目、关键词数量、正文长度、文献条数上限",
    "图片": "有效分辨率、格式、印刷宽度、图号与图注、重复与未引用图片",
    "表格": "编号、表题、正文引用、列数与溢出风险、跨页、表注",
    "公式": "编号、引用、编译错误、溢出风险、Word 公式对象可编辑性",
    "参考文献": "引用键存在性、未引用条目、重复条目、字段完整性、文内与文末一致性",
    "LaTeX 编译": "括号与环境配对、未定义引用、缺失资源、编译错误与 Overfull",
    "Word 文档": "修订记录、批注、脚注尾注、超链接、文献字段类型",
    "投稿声明": "基金、利益冲突、数据可用性、伦理、作者贡献等声明是否齐备",
    "内容与元信息": "标题、作者、单位、摘要、关键词等元信息是否可识别",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def tool_versions() -> dict:
    out = {"sci-paper-reformatter": __version__, "python": sys.version.split()[0], "platform": platform.platform()}
    try:
        import docx
        out["python-docx"] = getattr(docx, "__version__", "?")
    except Exception:
        pass
    try:
        import yaml
        out["PyYAML"] = yaml.__version__
    except Exception:
        pass
    try:
        from .util import probe_toolchain
        tc = probe_toolchain()
        out["tex"] = tc["latex_any"]
        out["docx_to_pdf"] = tc["docx_to_pdf"]
        out["latex_engine"] = next((k for k, v in tc["latex"].items() if v), "")
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------
# 质检报告
# --------------------------------------------------------------------------


def render_qc_report(
    ms: M.Manuscript,
    cfg: JournalConfig,
    bag: IssueBag,
    stage: str = "转换后",
    extra: dict | None = None,
    root: str | Path | None = None,
) -> str:
    extra = extra or {}
    counts = bag.counts()
    L: list[str] = []
    L.append(f"# 质量检查报告（{stage}）")
    L.append("")
    L.append("## 一、本次检查的基本信息")
    L.append("")
    L.append("| 项目 | 内容 |")
    L.append("|---|---|")
    L.append(f"| 稿件 | `{ms.source_path}` |")
    L.append(f"| 源格式 | {ms.source_format} |")
    L.append(f"| 目标期刊配置 | {cfg.name}（`{cfg.id}`） |")
    L.append(f"| 配置核对状态 | **{STATUS_LABEL.get(cfg.status, cfg.status)}** |")
    L.append(f"| 官方指南 | {cfg.guide_url or '（未记录）'} |")
    L.append(f"| 规范获取日期 | {cfg.retrieved or '（未记录）'} |")
    L.append(f"| 检查时间 | {_now()} |")
    if extra.get("journal_hint"):
        L.append(f"| 期刊提示 | {extra['journal_hint']} |")
    L.append("")

    L.append("## 二、结论")
    L.append("")
    verdict = "✅ 未发现必须处理的错误" if counts.get(ERROR, 0) == 0 else "❌ 存在必须处理的错误"
    L.append(f"**{verdict}**")
    L.append("")
    L.append(f"- 🔴 错误：{counts.get(ERROR, 0)}")
    L.append(f"- 🟡 警告：{counts.get(WARNING, 0)}")
    L.append(f"- 🔵 提示（需作者确认）：{counts.get(INFO, 0)}")
    L.append("")
    if counts.get(ERROR, 0):
        L.append("> 错误项必须处理完毕后再投稿。")
        L.append("")

    # 各类检查执行情况
    L.append("## 三、检查项与结果")
    L.append("")
    L.append("| 检查类别 | 检查内容 | 问题数 |")
    L.append("|---|---|---|")
    by_cat = bag.by_category()
    for cat in CATEGORIES:
        items = by_cat.get(cat, [])
        if not items and cat not in CHECK_SCOPE:
            continue
        n = len(items)
        mark = "✅ 通过" if n == 0 else f"{n}"
        L.append(f"| {cat} | {CHECK_SCOPE.get(cat, '')} | {mark} |")
    L.append("")

    # 明细
    L.append("## 四、问题明细")
    L.append("")
    if not bag.items:
        L.append("本次检查未发现任何问题。")
        L.append("")
    for level in (ERROR, WARNING, INFO):
        items = [i for i in bag.sorted() if i.level == level]
        if not items:
            continue
        L.append(f"### {LEVEL_LABEL[level]}（{len(items)}）")
        L.append("")
        cur_cat = None
        for i in items:
            if i.category != cur_cat:
                cur_cat = i.category
                L.append(f"**{cur_cat}**")
                L.append("")
            L.append(i.line_md())
        L.append("")

    # 需人工确认
    needs = [i for i in bag.sorted() if i.level == INFO and (i.suggestion or i.rule_source)]
    if needs:
        L.append("## 五、需要人工确认的清单")
        L.append("")
        L.append("下面这些项无法由脚本判定，必须由作者核对：")
        L.append("")
        for i in needs:
            L.append(f"- [ ] `{i.code}` {i.message}" + (f"（`{i.location.label()}`）" if i.location.label() else ""))
        L.append("")

    # 规范来源附录
    sources: dict[str, dict] = {}
    for i in bag.items:
        rs = i.rule_source or {}
        key = rs.get("field", "")
        if key and rs.get("source_url"):
            sources.setdefault(key, rs)
    L.append("## 六、本次判定用到的期刊规范来源")
    L.append("")
    if sources:
        L.append("| 配置项 | 来源 | 说明 | 核对状态 |")
        L.append("|---|---|---|---|")
        for k, v in sorted(sources.items()):
            L.append(
                f"| `{k}` | {v.get('source_url','')} | {v.get('note','')} | "
                f"{STATUS_LABEL.get(v.get('verified',''), v.get('verified',''))} |"
            )
    else:
        L.append("本次没有产生带来源的判定（或期刊配置未记录 rule_sources）。")
    L.append("")

    L.append("## 七、工具链")
    L.append("")
    for k, v in tool_versions().items():
        L.append(f"- {k}: `{v}`")
    if not tool_versions().get("tex"):
        L.append("")
        L.append("> 未检测到 TeX 发行版：LaTeX 项目**未做真实编译**，只完成了静态检查。")
        L.append("> 若需要 PDF 预览与真实的编译错误/警告，请安装 TeX Live 或 MiKTeX 后重跑。")
    if not tool_versions().get("docx_to_pdf"):
        L.append("")
        L.append("> 未检测到 LibreOffice/pandoc：**未生成 Word 的 PDF 预览**。")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 转换记录
# --------------------------------------------------------------------------


def render_conversion_log(
    changes: ChangeLog,
    cfg: JournalConfig,
    ms: M.Manuscript,
    extra: dict | None = None,
) -> str:
    extra = extra or {}
    L: list[str] = []
    L.append("# 转换记录")
    L.append("")
    L.append("## 一、转换参数（可复现）")
    L.append("")
    L.append("| 项目 | 值 |")
    L.append("|---|---|")
    L.append(f"| 转换时间 | {_now()} |")
    L.append(f"| 源文件 | `{ms.source_path}` |")
    L.append(f"| 源格式 | {ms.source_format} |")
    L.append(f"| 期刊配置 id | `{cfg.id}` |")
    L.append(f"| 期刊配置版本 | {cfg.retrieved or '未记录'} |")
    L.append(f"| 配置核对状态 | {STATUS_LABEL.get(cfg.status, cfg.status)} |")
    L.append(f"| 配置来源文件 | `{cfg.path or '(内置)'}` |")
    if extra.get("config_hash"):
        L.append(f"| 配置指纹(sha256) | `{extra['config_hash'][:16]}…` |")
    if extra.get("input_hash"):
        L.append(f"| 输入指纹(sha256) | `{extra['input_hash'][:16]}…` |")
    for k, v in (extra.get("tools") or {}).items():
        L.append(f"| {k} | `{v}` |")
    L.append("")

    L.append("## 二、迁移结果汇总")
    L.append("")
    mig = changes.summary()
    L.append(f"- ✅ 已迁移 / 已改动：**{mig['changed']}** 项")
    L.append(f"- ⏭️ 未迁移（原稿里没有这项内容，已跳过）：**{mig['skipped']}** 项")
    if mig["required_missing"]:
        L.append(f"- ⚠️ 其中目标期刊规范**强制要求**、但原稿没有的："
                 f"**{'、'.join(mig['required_missing'])}**")
    else:
        L.append("- 其中没有「期刊强制要求却缺失」的字段。")
    L.append("")
    L.append("> 跳过不等于丢弃：原稿没有的字段**不会**被写成占位文字塞进稿子，"
             "模板里的示例内容会被注释保留（便于对照），正文与数据一律原样保留。")
    L.append("")

    L.append("## 三、改动统计")
    L.append("")
    counts = changes.counts()
    if counts:
        L.append("| 类别 | 条数 |")
        L.append("|---|---|")
        for k, v in counts.items():
            L.append(f"| {k} | {v} |")
    else:
        L.append("本次转换未做任何改动。")
    L.append("")

    L.append("## 四、改动明细")
    L.append("")
    for kind, items in changes.by_kind().items():
        L.append(f"### {kind}（{len(items)}）")
        L.append("")
        for c in items:
            L.append(c.line_md())
        L.append("")

    # 未迁移清单：原稿里确实没有的字段。以前这些字段会被写成 `[待作者填写]`
    # 占位塞进结果，既让差异报告误报「内容被改了」，也没告诉作者"你到底缺什么"。
    L.append("## 五、未迁移清单（原稿没有、因而跳过的内容）")
    L.append("")
    if changes.skips:
        req = [s for s in changes.skips if s.required]
        if req:
            L.append("**必须先补上（目标期刊规范要求）：**")
            L.append("")
            for s in req:
                L.append(s.line_md())
            L.append("")
        opt = [s for s in changes.skips if not s.required]
        if opt:
            L.append("**其余跳过项（请确认是否确实不需要）：**")
            L.append("")
            for s in opt:
                L.append(s.line_md())
            L.append("")
        L.append("> 这些内容在原稿里没有找到，因此**没有生成任何占位文字**；"
                 "模板中对应的示例内容已注释保留在源文件里（搜 `[sci-trans]` 可见）。")
    else:
        L.append("原稿的元信息与文献信息齐全，本次没有需要跳过的字段。")
    L.append("")

    needs = changes.needs_author()
    L.append("## 六、需要作者确认的改动")
    L.append("")
    if needs:
        for c in needs:
            L.append(f"- [ ] **{c.kind}** · `{c.target}`：{c.reason}")
    else:
        L.append("本次转换没有涉及需要作者确认的实质性改动。")
    L.append("")

    L.append("## 七、未做的改动（明确说明）")
    L.append("")
    L.append("- 未改写论文正文段落（除非上方明细中明确列出）。")
    L.append("- 未删除公式、表格、图片或引用。")
    L.append("- 未改动任何科学数据与结论。")
    L.append("- 无法自动转换的元素保留原始内容，并在质检报告中列为警告/提示。")
    # 转换器显式标记为「未执行」的项（例如模板模式下不重复添加宏包），
    # 让「没做什么」也能被核对，而不是只写一句套话。
    skipped = [c for c in changes.items if c.after.startswith("(未")]
    for c in skipped:
        L.append(f"- **{c.kind}** · `{c.target}`：{c.reason or '未执行'}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 结果包索引
# --------------------------------------------------------------------------


def render_package_index(
    cfg: JournalConfig,
    ms: M.Manuscript,
    files: dict,
    bag_before: IssueBag | None,
    bag_after: IssueBag | None,
    changes: ChangeLog,
    diff: dict | None,
) -> str:
    L: list[str] = []
    L.append(f"# 转投结果包 · {cfg.name}")
    L.append("")
    L.append(f"生成时间：{_now()}")
    L.append("")
    L.append("## 内容清单")
    L.append("")
    L.append("| 文件 | 说明 |")
    L.append("|---|---|")
    for name, desc in files.items():
        L.append(f"| `{name}` | {desc} |")
    L.append("")
    # 编译入口：结果包里可能有多个 .tex（原稿项目自带的标题页、回复信等），
    # 说明清楚哪个才是要编译的主文件，省得在 Overleaf 里选错。
    main_name = Path(ms.main_file).name if getattr(ms, "main_file", "") else ""
    if main_name:
        L.append("## 编译入口")
        L.append("")
        L.append(f"主文件是 **`src/{main_name}`**（上传 Overleaf 后在菜单里把它设为 Main document）。")
        L.append("")
    L.append("## 概览")
    L.append("")
    if bag_after is not None:
        c = bag_after.counts()
        L.append(f"- 质检：🔴 {c.get(ERROR,0)} / 🟡 {c.get(WARNING,0)} / 🔵 {c.get(INFO,0)}")
    if bag_before is not None:
        c0 = bag_before.counts()
        L.append(f"- 原稿基线：🔴 {c0.get(ERROR,0)} / 🟡 {c0.get(WARNING,0)} / 🔵 {c0.get(INFO,0)}")
    L.append(f"- 转换改动：{len(changes)} 处，其中需作者确认 {len(changes.needs_author())} 处")
    mig = changes.summary()
    if mig["skipped"]:
        tail = (f"，其中期刊强制要求的：{'、'.join(mig['required_missing'])}"
                if mig["required_missing"] else "")
        L.append(f"- 未迁移（原稿没有的内容，已跳过）：{mig['skipped']} 项{tail}"
                 "（详见 `conversion-log.md` 第五节）")
    if diff is not None:
        from .diff import conclusion_text
        L.append(f"- 内容差异：{conclusion_text(diff)}")
    L.append("")
    if cfg.status != "verified":
        L.append("> ⚠️ 本次使用的期刊配置尚未逐条核对官方投稿指南，所有格式判定都需要人工复核。")
        L.append("")
    return "\n".join(L)
