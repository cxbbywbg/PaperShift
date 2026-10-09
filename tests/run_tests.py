#!/usr/bin/env python3
"""端到端自检：两条链路（LaTeX / Word）各自跑通解析→转换→质检→差异→结果包。

用法：
    python tests/run_tests.py            # 跑全部
    python tests/run_tests.py --keep     # 保留中间产物

退出码 0 表示全部通过。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from sci_core import docx_parse, journal as J, latex_parse, refs as refs_mod  # noqa: E402
from sci_core.changes import ChangeLog  # noqa: E402
from sci_core.diff import _canon_affil, diff_manuscripts, render_diff_md  # noqa: E402
from sci_core.pipeline import run_pipeline  # noqa: E402
from sci_core.qc import run_qc, summarize  # noqa: E402
from sci_core.util import Log  # noqa: E402

LATEX_FIX = ROOT / "tests" / "fixtures" / "latex_demo"
DOCX_FIX = ROOT / "tests" / "fixtures" / "docx_demo" / "manuscript.docx"
TEMPLATE_KIT = ROOT / "tests" / "fixtures" / "template_kit"
OUT = ROOT / ".test_out"

PASS, FAIL = [], []

DECL_LABELS = {
    "funding": "Funding",
    "conflict_of_interest": "Declaration of Competing Interest",
    "data_availability": "Data Availability Statement",
    "ethics": "Ethics Statement",
    "author_contributions": "Author Contributions",
}


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not cond:
        line += f"  → {detail}"
    print(line)


def codes(bag) -> set[str]:
    return {i.code for i in bag.items}


# --------------------------------------------------------------------------


def test_latex_parse() -> None:
    print("\n== LaTeX 解析 ==")
    ms = latex_parse.parse_latex(LATEX_FIX)
    ms.references = refs_mod.load_references(ms, LATEX_FIX)
    st = ms.stats()
    check("主文件识别", Path(ms.main_file).name == "main.tex", ms.main_file)
    check("子文件递归解析", len(ms.project_files) == 5, str(ms.project_files))
    check("文档类与选项", ms.documentclass == "article", ms.documentclass)
    check("标题识别", ms.metadata.title.startswith("Adaptive Spectral"), ms.metadata.title)
    check("作者识别（3 位，单位不粘连）",
          [a["name"] for a in ms.metadata.authors] == ["Wei Zhang", "Li Chen", "Marcus Hale"],
          str([a["name"] for a in ms.metadata.authors]))
    check("单位识别（3 个，取的是名称而非编号）",
          len(ms.metadata.affiliations) == 3 and "Harbin Institute" in ms.metadata.affiliations[0],
          str(ms.metadata.affiliations))
    check("关键词识别", len(ms.metadata.keywords) == 5, str(ms.metadata.keywords))
    check("结构化摘要栏目", set(ms.metadata.abstract_structured) >= {"Background", "Methods", "Results", "Conclusions"},
          str(list(ms.metadata.abstract_structured)))
    check("章节数 = 12（声明不计入正文章节）", st["sections"] == 12, str(st["sections"]))
    check("声明章节未被当成正文结构",
          not any(s.heading.strip().lower() in
                  ("funding", "declaration of competing interest") for s in ms.sections),
          str([s.heading for s in ms.sections]))
    check("章节稳定 id", all(s.id.startswith("sec:") for s in ms.sections))
    check("图 4 个", st["figures"] == 4, str(st["figures"]))
    check("表 2 个", st["tables"] == 2, str(st["tables"]))
    check("公式 3 个（align 内多式拆开）", st["equations"] == 3, str(st["equations"]))
    check("引用去重 12 个键", st["unique_citation_keys"] == 12, str(st["unique_citation_keys"]))
    check("交叉引用被记录", st["crossrefs"] >= 10, str(st["crossrefs"]))
    check("文献条目 12 条", len(ms.references) == 12, str(len(ms.references)))
    check("图被正文引用（fig:1 → sec:2.2）",
          any(f.id == "fig:1" and f.referenced_in for f in ms.figures))
    check("未引用的图被识别（fig:energy）",
          any(f.id == "fig:3" and not f.referenced_in for f in ms.figures))
    check("未引用的公式被识别（eq:retained）",
          any(e.label == "eq:retained" and not e.referenced_in for e in ms.equations))
    check("声明识别（COI 有，funding / data availability 无）",
          {d.kind for d in ms.declarations if d.present} == {"conflict_of_interest"},
          str([(d.kind, d.present) for d in ms.declarations]))


def test_latex_qc() -> None:
    print("\n== LaTeX 质检（缺陷注入是否被抓到）==")
    ms = latex_parse.parse_latex(LATEX_FIX)
    ms.references = refs_mod.load_references(ms, LATEX_FIX)
    cfg = J.load("ieee-trans", ROOT)
    bag = run_qc(ms, cfg, LATEX_FIX, None, Log(verbose=False),
                 {r.key for r in ms.references if r.origin == "bib"}, None, "测试")
    c = codes(bag)
    check("抓到缺失图片（ablation.png）", "LTX020" in c)
    check("抓到不存在的引用键", "REF001" in c)
    check("抓到未引用文献条目", "REF010" in c)
    check("抓到不存在的交叉引用", "LTX005" in c)
    check("抓到未被引用的公式", "EQ040" in c)
    check("抓到未被正文引用的图", "FIG050" in c)
    check("抓到低分辨率图片", "FIG020" in c)
    check("抓到大列数表格（6 列 / 估算 11cm 超版心）", "TAB020" in c)
    check("抓到缺失的强制声明（funding）",
          "DEC001" in c and any(i.code == "DEC001" and "funding" in i.message for i in bag.items))
    check("声明问题带规范来源与作者补全提示",
          all(i.rule_source.get("source_url") and i.suggestion
              for i in bag.items if i.code == "DEC001"))
    check("报告带期刊规范来源",
          any(i.rule_source.get("source_url") for i in bag.items))
    check("问题可定位到文件/行",
          any(i.location.file and i.location.line for i in bag.items))
    s = summarize(bag)
    print(f"     → {s}")
    check("三类分级都有内容",
          all(bag.counts().get(k, 0) > 0 for k in ("error", "warning", "info")), str(bag.counts()))


def test_latex_convert_roundtrip() -> None:
    print("\n== LaTeX 转换与往返一致性 ==")
    for jid in ("ieee-trans", "springer-generic"):
        # 每轮重新解析，避免转换过程对模型的改动串到下一轮
        ms = latex_parse.parse_latex(LATEX_FIX)
        ms.references = refs_mod.load_references(ms, LATEX_FIX)
        out = OUT / f"rt-{jid}"
        if out.exists():
            shutil.rmtree(out)
        from sci_core import latex_build
        from sci_core.changes import ChangeLog
        from sci_core.issues import IssueBag

        ch = ChangeLog()
        cfg = J.load(jid, ROOT)
        res = latex_build.convert_latex(ms, cfg, out, ch, IssueBag(), Log(verbose=False))
        after = latex_parse.parse_latex(out)
        after.references = refs_mod.load_references(after, out)
        d = diff_manuscripts(ms, after)
        missing = [k for k in cfg.required_declarations
                   if k not in {x.kind for x in ms.declarations if x.present}]
        body_ok = (
            not d["sections"]["added"] and not d["sections"]["removed"]
            and len(d["sections"]["changed"]) == 0
            and not d["figures"]["removed"] and not d["tables"]["removed"]
            and not d["equations"]["removed"] and not d["citations"]["removed"]
            and not d["references"]["removed"]
        )
        check(f"{jid}: 正文/图表/引用/文献零丢失", body_ok,
              f"sec+{len(d['sections']['added'])}/-{len(d['sections']['removed'])} "
              f"changed={len(d['sections']['changed'])} "
              f"removed_fig={d['figures']['removed']} removed_ref={d['references']['removed']}")
        check(f"{jid}: documentclass 已切换",
              after.documentclass == cfg.latex().get("documentclass"),
              after.documentclass)
        check(f"{jid}: 图表数量保持 4/2",
              len(after.figures) == 4 and len(after.tables) == 2,
              f"{len(after.figures)}/{len(after.tables)}")
        check(f"{jid}: 参考文献样式已切换",
              after.bib_style == cfg.bib_style,
              f"{after.bib_style} vs {cfg.bib_style}")
        check(f"{jid}: 改动有记录且带原因",
              len(ch) > 0 and all(c.reason for c in ch.items), str(len(ch)))
        check(f"{jid}: 原始作者命令被注释保留",
              "原作者/单位/关键词命令" in (out / "main.tex").read_text(encoding="utf-8"))

        # 缺声明 → 自动补占位，但必须记录为「需作者确认」，且不能混进正文章节
        if missing:
            decl_added = d["declarations"]["added"]
            check(f"{jid}: 缺失声明被补占位（{', '.join(missing)}）",
                  set(missing) <= set(decl_added), str(decl_added))
            check(f"{jid}: 补入的声明被标记为需作者确认",
                  any(c.needs_author for c in ch.items),
                  str([(c.target, c.needs_author) for c in ch.items]))
            check(f"{jid}: 声明补入不被误报为「新增章节」",
                  d["declarations_only_added"] and not d["sections"]["added"],
                  f"only_decl={d['declarations_only_added']} added_sec={d['sections']['added']}")
            check(f"{jid}: 差异报告把声明单独归类",
                  "投稿声明" in render_diff_md(d) and "待作者填写" in render_diff_md(d))

        # 回归：转换稿必须是**可编译的 LaTeX**，不能出现花括号/环境不配平
        # （曾经的 bug：作者行模板多一个 `}` → LTX001）
        from sci_core.latex_check import static_check
        ltx = static_check(out, after, {r.key for r in after.references if r.origin == "bib"},
                           bag=None, log=Log(verbose=False))
        bad = {i.code for i in ltx.items} & {"LTX001", "LTX002", "LTX003"}
        check(f"{jid}: 转换稿花括号/环境配平（无 LTX001-003）", not bad,
              str([i.message for i in ltx.items if i.code in bad]))

        # 回归：补入的声明必须排在参考文献命令**之前**，
        # 否则会被夹在 \bibliographystyle 与 \bibliography 中间
        main_txt = (out / "main.tex").read_text(encoding="utf-8")
        for k in missing:
            pos_decl = main_txt.find("\\section*{" + DECL_LABELS.get(k, k) + "}")
            pos_bib = min(
                [p for p in (main_txt.find("\\bibliographystyle"),
                             main_txt.find("\\begin{thebibliography}"),
                             main_txt.find("\\printbibliography")) if p >= 0] or [len(main_txt)]
            )
            check(f"{jid}: 补入的声明 {k} 排在参考文献命令之前",
                  0 <= pos_decl < pos_bib, f"decl@{pos_decl} bib@{pos_bib}")


def test_docx_parse() -> None:
    print("\n== Word 解析 ==")
    ms = docx_parse.parse_docx(DOCX_FIX, media_dir=OUT / "media")
    st = ms.stats()
    check("标题识别", ms.metadata.title.startswith("Adaptive Spectral"), ms.metadata.title)
    check("作者识别", [a["name"] for a in ms.metadata.authors] == ["Wei Zhang", "Li Chen", "Marcus Hale"],
          str([a["name"] for a in ms.metadata.authors]))
    check("关键词未被并入摘要", len(ms.metadata.keywords) == 4, str(ms.metadata.keywords))
    check("摘要识别", ms.abstract_words > 50, str(ms.abstract_words))
    check("页面设置", ms.page_setup.get("width_cm") == 21.0 and ms.page_setup.get("columns") == 1,
          str(ms.page_setup))
    check("图 4 个且图片已落盘",
          st["figures"] == 4 and all(f.native.get("media_path") for f in ms.figures))
    check("表 2 个", st["tables"] == 2, str(st["tables"]))
    check("OMML 公式识别", st["equations"] == 1, str(st["equations"]))
    check("正文编号引用被识别", {c.key for c in ms.citations} == {"ref1", "ref2", "ref3", "ref4"},
          str({c.key for c in ms.citations}))
    check("Figure 2 / Table 2 这类正文引用未被误判为题注",
          any(f.id == "fig:2" and f.referenced_in for f in ms.figures)
          and any(t.id == "tab:2" and t.referenced_in for t in ms.tables))
    check("未被引用的图被识别（fig:4）",
          any(f.id == "fig:4" and not f.referenced_in for f in ms.figures))
    check("参考文献识别为「普通文本」需人工确认",
          all(r.origin == "docx_text" and r.confidence == "needs_review" for r in ms.references),
          str([(r.key, r.origin) for r in ms.references]))
    check("声明未被编号为正文章节（不再是 sec:5/sec:6）",
          not any("funding" in s.heading.lower() or "competing" in s.heading.lower()
                  for s in ms.sections),
          str([s.heading for s in ms.sections]))
    check("声明仍被正确识别",
          {d.kind for d in ms.declarations if d.present} == {"funding", "conflict_of_interest"},
          str([(d.kind, d.present) for d in ms.declarations]))
    # 回归：图/表/公式上的章节归属必须是**重写后的稳定 id**，
    # 不能残留 sec:lineNN 这种临时 id（否则质检报告定位不到章节）
    refs = [x.section for x in (*ms.figures, *ms.tables, *ms.equations)]
    check("图/表/公式的章节归属是稳定 id",
          all(r and not r.startswith("sec:line") for r in refs), str(refs))
    check("章节归属确实指向存在的章节",
          all(r in {s.id for s in ms.sections} for r in refs), str(refs))


def test_docx_convert() -> None:
    print("\n== Word 转换（安全模式）==")
    ms = docx_parse.parse_docx(DOCX_FIX, media_dir=OUT / "media")
    from sci_core import docx_build
    from sci_core.changes import ChangeLog
    from sci_core.issues import IssueBag

    out = OUT / "docx-conv"
    if out.exists():
        shutil.rmtree(out)
    ch = ChangeLog()
    res = docx_build.convert_docx(ms, J.load("ieee-trans", ROOT), out, ch, IssueBag(), Log(verbose=False))
    after = docx_parse.parse_docx(Path(res["main_file"]), media_dir=OUT / "media2")
    check("页面尺寸/页边距已按期刊规范落地",
          abs(after.page_setup["margin_left_cm"] - 1.6) < 0.05
          and abs(after.page_setup["margin_top_cm"] - 1.9) < 0.05,
          str(after.page_setup))
    check("栏数已切换为 2 栏", after.page_setup.get("columns") == 2, str(after.page_setup.get("columns")))
    check("正文字号已按规范调整",
          after.metadata.doc_meta.get("style_info", {}).get("normal_size_pt") == 10.0,
          str(after.metadata.doc_meta.get("style_info")))
    d = diff_manuscripts(ms, after)
    check("正文/图表/公式/引用零丢失", d["identical"], str(d["sections"]["changed"]))
    check("题注标签已改写", "Fig." in (out / "manuscript_formatted.docx").read_bytes().decode("utf-8", "ignore")
          or any("题注标签" in c.target for c in ch.items))
    check("改动有记录", len(ch) >= 5, str(len(ch)))


def test_pipeline() -> None:
    print("\n== 完整流水线（结果包）==")
    out = OUT / "pkg"
    if out.exists():
        shutil.rmtree(out)
    res = run_pipeline(DOCX_FIX, "springer-generic", out, ROOT, make_pdf=False, log=Log(verbose=False))
    jdir = Path(res["journal_dir"])
    check("结果包目录结构",
          (jdir / "src").exists() or (jdir / "manuscript_formatted.docx").exists())
    check("质检报告生成", (jdir / "report.md").is_file())
    check("基线报告生成", (jdir / "report-baseline.md").is_file())
    check("差异报告生成", (jdir / "diff.md").is_file())
    check("转换记录生成", (jdir / "conversion-log.md").is_file())
    check("结构化结果生成", (jdir / "result.json").is_file())
    check("原稿被完整保留（不可覆盖）",
          (Path(res["submission_dir"]) / "original" / DOCX_FIX.name).is_file())
    check("manifest.json 生成", Path(res["manifest"]).is_file())
    check("PDF 不可用时给出降级说明",
          not res["pdf"].get("available") and bool(res["pdf"].get("reason")))


def test_template_mode() -> None:
    """模板模式：以「官网下载的官方模板」为骨架移植原稿内容。

    这是最常见的真实用法——作者手里有一份目标期刊的官方模板和一份自己的稿件，
    要的是「把稿件内容装进模板」，而不是「在原稿上改几个命令」。
    """
    print("\n== 模板模式（套用官方模板骨架）==")
    tpl_tex = TEMPLATE_KIT / "myjournal-template.tex"

    # ---- 1) LaTeX + 外部官方模板（只给模板，不给期刊配置）----
    out = OUT / "tpl-latex"
    res = run_pipeline(LATEX_FIX, "", out, cwd=ROOT, make_pdf=False,
                       log=Log(verbose=False), template=str(tpl_tex))
    src = Path(res["journal_dir"]) / "src"
    t = (src / "main.tex").read_text(encoding="utf-8")
    live = "\n".join(ln for ln in t.split("\n") if not ln.lstrip().startswith("%"))

    check("模板模式：保留模板的 documentclass",
          "\\documentclass[preprint,12pt]{myjournal}" in t)
    check("模板模式：frontmatter 结构完整且位置正确",
          "\\begin{frontmatter}" in t and "\\end{frontmatter}" in t
          and t.find("\\begin{document}") < t.find("\\begin{frontmatter}"))
    check("模板模式：标题取自原稿", "\\title{Adaptive Spectral Truncation" in t)
    check("模板模式：模板示例标题已清除", "Official template placeholder title" not in live)
    check("模板模式：作者按模板命令体系重建",
          "\\author[aff1]{Wei Zhang}" in t and "\\author[aff3]{Marcus Hale}" in t)
    check("模板模式：模板示例作者被注释保留（可回溯）",
          "% \\author[1]{Placeholder Author}" in t)
    check("模板模式：摘要用原稿原文（宏与转义未丢）",
          "\\method{}" in t and "41.7\\%" in t and "1.9$\\times$" in t)
    check("模板模式：模板示例摘要已清除",
          "This abstract comes from the official template" not in live)
    check("模板模式：关键词用模板分隔符 \\sep",
          "edge inference \\sep model compression" in t)
    check("模板模式：模板示例正文被替换（lorem ipsum 消失）", "Lorem ipsum" not in t)
    check("模板模式：原稿正文被引入",
          "\\input{sections/intro}" in t or "\\section{Introduction}" in t)
    check("模板模式：模板自带文献样式被保留（不强行覆盖）",
          "\\bibliographystyle{myjournal-num}" in t)
    check("模板模式：模板辅助文件随包提供（.cls/.bst）",
          (src / "myjournal.cls").is_file() and (src / "myjournal-num.bst").is_file())
    check("模板模式：不把模板自身的 .tex 混进结果包",
          not (src / "myjournal-template.tex").exists())
    check("模板模式：模板目录里的无关文件不被复制（避免污染/泄露）",
          not (src / "readme.txt").exists()
          and not (src / "unrelated-archive.zip").exists(),
          str(sorted(p.name for p in src.iterdir())))
    check("模板模式：结构唯一（无重复 frontmatter / end{document}）",
          t.count("\\begin{frontmatter}") == 1 and t.count("\\end{document}") == 1)
    check("模板模式：未提供期刊配置时只做结构移植",
          res["journal_id"] == "template-only")

    ms_before = latex_parse.parse_latex(LATEX_FIX)
    ms_before.references = refs_mod.load_references(ms_before, LATEX_FIX)
    ms_after = latex_parse.parse_latex(src)
    ms_after.references = refs_mod.load_references(ms_after, src)
    d = diff_manuscripts(ms_before, ms_after)
    check("模板模式：正文/图表/引用零丢失",
          not d["sections"]["added"] and not d["sections"]["removed"]
          and not d["sections"]["changed"] and not d["figures"]["removed"]
          and not d["tables"]["removed"] and not d["equations"]["removed"]
          and not d["citations"]["removed"],
          f"changed={len(d['sections']['changed'])} fig_removed={d['figures']['removed']}")
    check("模板模式：作者与单位未丢失",
          [a["name"] for a in ms_after.metadata.authors]
          == [a["name"] for a in ms_before.metadata.authors]
          and len(ms_after.metadata.affiliations) == len(ms_before.metadata.affiliations),
          f"{ms_after.metadata.authors}")
    check("模板模式：作者-单位对应关系未丢（affil 标记仍在）",
          all(a.get("affil") for a in ms_after.metadata.authors),
          str([(a["name"], a.get("affil")) for a in ms_after.metadata.authors]))

    # ---- 2) 内置 elsarticle 骨架 + 期刊配置 ----
    out2 = OUT / "tpl-els"
    res2 = run_pipeline(LATEX_FIX, "elsevier-generic", out2, cwd=ROOT,
                        make_pdf=False, log=Log(verbose=False))
    src2 = Path(res2["journal_dir"]) / "src"
    t2 = (src2 / "main.tex").read_text(encoding="utf-8")
    check("elsarticle：frontmatter 结构正确（不再裸放元信息）",
          "\\begin{frontmatter}" in t2 and "\\end{frontmatter}" in t2
          and t2.find("\\begin{document}") < t2.find("\\title{Adaptive"))
    check("elsarticle：作者/单位/摘要/关键词齐备",
          "\\author[aff1]{Wei Zhang}" in t2 and "\\begin{abstract}" in t2
          and "\\begin{keyword}" in t2 and "\\sep" in t2)
    check("elsarticle：补入的声明排在参考文献命令之前",
          0 <= t2.find("\\section*{Funding}") < t2.find("\\bibliographystyle"))
    ms2 = latex_parse.parse_latex(src2)
    ms2.references = refs_mod.load_references(ms2, src2)
    d2 = diff_manuscripts(ms_before, ms2)
    check("elsarticle：正文/图表/公式零丢失（差异仅来自补入的声明）",
          not d2["sections"]["changed"] and not d2["figures"]["removed"]
          and not d2["tables"]["removed"] and not d2["equations"]["removed"],
          f"changed={len(d2['sections']['changed'])}")

    # ---- 3) Word + 官方模板 ----
    out3 = OUT / "tpl-docx"
    res3 = run_pipeline(DOCX_FIX, "", out3, cwd=ROOT, make_pdf=False,
                        log=Log(verbose=False), template=str(DOCX_FIX))
    m3 = docx_parse.parse_docx(Path(res3["main_file"]), Log(verbose=False),
                              media_dir=OUT / "tpl-docx-media")
    check("Word 模板模式：图片全部保留（4 张）", len(m3.figures) == 4, str(len(m3.figures)))
    check("Word 模板模式：表格保留（2 张）", len(m3.tables) == 2, str(len(m3.tables)))
    ms_src = docx_parse.parse_docx(DOCX_FIX, Log(verbose=False),
                                  media_dir=OUT / "tpl-docx-src-media")
    d3 = diff_manuscripts(ms_src, m3)
    check("Word 模板模式：零内容差异", d3["identical"],
          f"fig={d3['figures']} tab={d3['tables']}")

    # ---- 4) 模板路径写错时必须降级并说明，不能静默失败 ----
    out4 = OUT / "tpl-missing"
    res4 = run_pipeline(LATEX_FIX, "elsevier-generic", out4, cwd=ROOT, make_pdf=False,
                        log=Log(verbose=False), template=str(OUT / "nope-not-here.tex"))
    rpt = (Path(res4["journal_dir"]) / "report.md").read_text(encoding="utf-8")
    check("模板缺失：给出降级说明（CVT012）而不是静默失败", "CVT012" in rpt)


def _make_zip(dest: Path, entries: dict[str, object]) -> Path:
    """按 {包内路径: 源文件 Path 或文本} 打一个 zip。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w") as z:
        for inner, src in entries.items():
            if isinstance(src, Path):
                z.write(src, inner)
            else:
                z.writestr(inner, str(src))
    return dest


def test_template_archive() -> None:
    """模板是压缩包时的处理。

    真实场景：Overleaf 导出的原稿是 zip，期刊官网下载的模板 kit 也是 zip。
    用户要的是「把我这个包里的内容装进那个包里的模板」，不该逼他手工解压。
    """
    print("\n== 模板压缩包（官网 kit / Overleaf 导出）==")
    # 把解包缓存也关进测试目录，别往系统临时目录里丢东西
    orig_cache = J.ARCHIVE_CACHE_DIR
    J.ARCHIVE_CACHE_DIR = OUT / "tpl-cache"
    tpl_tex = TEMPLATE_KIT / "myjournal-template.tex"
    base = tpl_tex.read_text(encoding="utf-8")
    zips = OUT / "zips"

    # 模拟官网 kit：顶层目录 + 宏包 + 主文件 + 干扰文件 + macOS 垃圾
    kit = _make_zip(zips / "journal-kit.zip", {
        "kit/myjournal.cls": TEMPLATE_KIT / "myjournal.cls",
        "kit/myjournal-num.bst": TEMPLATE_KIT / "myjournal-num.bst",
        "kit/myjournal-template.tex": tpl_tex,
        "kit/readme.txt": "作者须知，不是模板\n",
        "kit/__MACOSX/._myjournal-template.tex": "junk",
    })

    # ---- 基线：直接指向 .tex；对照：指向压缩包 ----
    res_base = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip-base", cwd=ROOT,
                            make_pdf=False, log=Log(verbose=False), template=str(tpl_tex))
    res_zip = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip", cwd=ROOT,
                           make_pdf=False, log=Log(verbose=False), template=str(kit))
    base_src = Path(res_base["journal_dir"]) / "src"
    zip_src = Path(res_zip["journal_dir"]) / "src"

    check("模板压缩包：自动解包取主文件，产出与直接指向 .tex 完全一致",
          (zip_src / "main.tex").read_text(encoding="utf-8")
          == (base_src / "main.tex").read_text(encoding="utf-8"))
    check("模板压缩包：包内 .cls/.bst 随结果包提供（可独立编译）",
          (zip_src / "myjournal.cls").is_file()
          and (zip_src / "myjournal-num.bst").is_file())
    check("模板压缩包：包内干扰文件与 macOS 垃圾不被复制",
          not (zip_src / "readme.txt").exists()
          and not any("__MACOSX" in p.parts for p in zip_src.rglob("*")))
    check("模板压缩包：包内只有一份模板时不报多候选提醒",
          "CVT013" not in (Path(res_zip["journal_dir"]) / "report.md").read_text(encoding="utf-8"))

    # ---- 包内同族版式（elsarticle kit 的 num / harv 就是这样）：各产出一份 ----
    multi = _make_zip(zips / "multi.zip", {
        "kit/myjournal.cls": TEMPLATE_KIT / "myjournal.cls",
        "kit/elsarticle-template-num.tex": base,
        "kit/elsarticle-template-harv.tex": base.replace("myjournal-num", "myjournal-harv"),
    })
    res_m = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip-multi", cwd=ROOT,
                         make_pdf=False, log=Log(verbose=False), template=str(multi))
    check("模板包内同族版式（num / harv）默认各产出一份结果包",
          res_m.get("multi") and res_m.get("variant_count") == 2,
          f"multi={res_m.get('multi')} n={res_m.get('variant_count')}")
    ids = [r["journal_id"] for r in res_m.get("variants", [])]
    check("变体结果包目录带版式后缀，互不覆盖",
          ids == ["template-only-num", "template-only-harv"], str(ids))
    styles = []
    for r in res_m.get("variants", []):
        t = (Path(r["journal_dir"]) / "src" / "main.tex").read_text(encoding="utf-8")
        mm = re.search(r"\\bibliographystyle\{([^}]*)\}", t)
        styles.append(mm.group(1) if mm else "")
    check("每个变体套用自己那份模板的文献样式",
          styles == ["myjournal-num", "myjournal-harv"], str(styles))

    # ---- 包内多份候选但**不同族**：没有变体可展开，仍按 CVT013 提醒 ----
    mixed = _make_zip(zips / "mixed.zip", {
        "kit/alpha-template.tex": base,
        "kit/beta-guide.tex": base,
    })
    res_x = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip-mixed", cwd=ROOT,
                         make_pdf=False, log=Log(verbose=False), template=str(mixed))
    check("包内多份候选但不同族时，仍报 CVT013 提醒人工过目",
          "CVT013" in (Path(res_x["journal_dir"]) / "report.md").read_text(encoding="utf-8"))

    res_p = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip-pick", cwd=ROOT, make_pdf=False,
                         log=Log(verbose=False),
                         template=f"{multi}::kit/elsarticle-template-num.tex")
    t_p = (Path(res_p["journal_dir"]) / "src" / "main.tex").read_text(encoding="utf-8")
    check("模板压缩包：zip::包内路径 可指定用哪一份模板（不再展开变体）",
          "\\bibliographystyle{myjournal-num}" in t_p
          and not res_p.get("multi")
          and "CVT013" not in (Path(res_p["journal_dir"]) / "report.md").read_text(encoding="utf-8"))

    # ---- 失败路径：必须降级并说清原因，不能静默 ----
    broken = zips / "broken.zip"
    broken.write_bytes(b"not a zip at all")
    res_b = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip-broken", cwd=ROOT,
                         make_pdf=False, log=Log(verbose=False), template=str(broken))
    rpt_b = (Path(res_b["journal_dir"]) / "report.md").read_text(encoding="utf-8")
    check("模板压缩包：坏包降级并写明原因（CVT012）",
          "CVT012" in rpt_b and "无法解开" in rpt_b)

    notex = _make_zip(zips / "no-tex.zip", {"a/readme.txt": "只有说明文档\n"})
    res_n = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip-notex", cwd=ROOT,
                         make_pdf=False, log=Log(verbose=False), template=str(notex))
    rpt_n = (Path(res_n["journal_dir"]) / "report.md").read_text(encoding="utf-8")
    check("模板压缩包：包内没有主文件时降级并写明原因（CVT012）",
          "CVT012" in rpt_n and "没有 .tex 主文件" in rpt_n)

    # ---- Word 侧同样支持 ----
    word_zip = _make_zip(zips / "word-template.zip", {
        "word/manuscript-template.docx": DOCX_FIX,
        "word/guide.txt": "作者须知\n",
    })
    res_w = run_pipeline(DOCX_FIX, "", OUT / "tpl-zip-docx", cwd=ROOT,
                         make_pdf=False, log=Log(verbose=False), template=str(word_zip))
    m_w = docx_parse.parse_docx(Path(res_w["main_file"]), Log(verbose=False),
                                media_dir=OUT / "tpl-zip-docx-media")
    ms_w = docx_parse.parse_docx(DOCX_FIX, Log(verbose=False),
                                 media_dir=OUT / "tpl-zip-docx-src")
    d_w = diff_manuscripts(ms_w, m_w)
    check("Word 模板压缩包：走模板模式、零内容差异、图片全保",
          d_w["identical"] and len(m_w.figures) == 4, f"fig={len(m_w.figures)}")

    # ---- 用户的实际场景：原稿 zip + 模板 zip ----
    paper_zip = _make_zip(
        zips / "overleaf-export.zip",
        {f"my-paper/{p.relative_to(LATEX_FIX).as_posix()}": p
         for p in sorted(LATEX_FIX.rglob("*")) if p.is_file()},
    )
    res_c = run_pipeline(paper_zip, "", OUT / "tpl-zip-both", cwd=ROOT,
                         make_pdf=False, log=Log(verbose=False), template=str(kit))
    src_c = Path(res_c["journal_dir"]) / "src"
    check("原稿压缩包 + 模板压缩包：端到端可用且结果与解压后一致",
          (src_c / "main.tex").read_text(encoding="utf-8")
          == (base_src / "main.tex").read_text(encoding="utf-8")
          and (src_c / "myjournal.cls").is_file())

    from sci_core.journal import _extract_archive

    c1 = _extract_archive(kit)
    c2 = _extract_archive(kit)
    check("模板压缩包：同一模板重复解析命中解包缓存（不重复解包）",
          c1 == c2 and (c1 / "kit/myjournal-template.tex").is_file(), str(c1))

    # ---- 主文件识别：命名分档优先，体积只做兜底 ----
    # 反例集：说明书 20 KB（比模板大 4 倍）、字体映射表 200 KB（根本不是文档）
    pick = OUT / "pickroot"
    if pick.exists():
        shutil.rmtree(pick, ignore_errors=True)
    (pick / "doc").mkdir(parents=True)
    (pick / "doc" / "elsdoc-cas.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n" + "x" * 20000 + "\n\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    (pick / "doc" / "glyphtounicode.tex").write_text(
        "% 字体映射表\n" + "y" * 200000, encoding="utf-8", newline="\n",
    )
    (pick / "cas-dc-sample.tex").write_text(
        "\\documentclass{cas-dc}\n\\begin{document}\n" + "z" * 15000 + "\n\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    (pick / "cas-sc-template.tex").write_text(
        "\\documentclass{cas-sc}\n\\begin{document}\n\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    main_pick, _all_pick = J._pick_archive_main(pick, "latex")
    check("模板主文件识别：按体积会选到说明书/示例，按命名分档选到 template",
          main_pick is not None and main_pick.name == "cas-sc-template.tex",
          main_pick.name if main_pick else "None")
    check("模板主文件识别：200 KB 的字体映射表（无 \\documentclass）不会当选",
          main_pick is not None and "glyphtounicode" not in main_pick.name,
          main_pick.name if main_pick else "None")

    J.ARCHIVE_CACHE_DIR = orig_cache


def test_main_tex_pick() -> None:
    """原稿主文件识别：多候选时按体积挑，而不是字母序。

    真实教训——作者的正文一定是包里最大的 .tex（实测 106.9 KB，是第二名的
    8 倍）。但主文件叫 `draft_v3.tex` 而包里另有 `Response_to_Reviewers.tex`
    时，大写 `R` 的 ASCII 比小写 `d` 小，字母序会把**审稿回复信**当正文。
    """
    print("\n== 原稿主文件识别（多候选）==")
    proj = OUT / "mainpick"
    if proj.exists():
        shutil.rmtree(proj, ignore_errors=True)
    proj.mkdir(parents=True)
    (proj / "Response_to_Reviewers.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n回复审稿人\n\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    (proj / "Title_Page.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n标题页\n\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    (proj / "draft_v3.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n" + "正文" * 5000 + "\n\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    picked = latex_parse.find_main_tex(proj)
    check("原稿主文件识别：多候选时选体积最大的（正文），不是字母序第一个",
          picked is not None and Path(picked).name == "draft_v3.tex",
          Path(picked).name if picked else "None")

    # 被 \input 引用的文件不当主文件——即使它体积最大
    (proj / "supp.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n" + "附录" * 20000 + "\n\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    (proj / "draft_v3.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n" + "正文" * 5000
        + "\n\\input{supp}\n\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    picked2 = latex_parse.find_main_tex(proj)
    check("原稿主文件识别：被 \\input 引用的文件不当主文件（即使它体积最大）",
          picked2 is not None and Path(picked2).name == "draft_v3.tex",
          Path(picked2).name if picked2 else "None")


def test_springer_template() -> None:
    r"""Springer Nature 模板（sn-jnl）的命令式元信息。

    这套模板跟 Elsevier CAS 写法完全不同，踩过的坑**都会静默产出坏文件**：
    `\author*`/`\affil*` 带星号、姓名要拆 `\fnm/\sur`、单位要拆
    `\orgdiv/\orgname/\orgaddress`、摘要与关键词都是**命令式**（不是环境）、
    `.bst` 放在 `bst/` 子目录。
    """
    print("\n== Springer Nature 模板（命令式元信息）==")
    tpl_dir = ROOT / "tests" / "fixtures" / "springer_kit" / "sn-article-template"
    res = run_pipeline(LATEX_FIX, "", OUT / "sn-tpl", cwd=ROOT,
                       make_pdf=False, log=Log(verbose=False),
                       template=str(tpl_dir / "sn-article.tex"))
    src = Path(res["journal_dir"]) / "src"
    t = (src / "main.tex").read_text(encoding="utf-8")

    # 1) 花括号配平 —— \author* 不被识别时区间起点会落到 \email 上，把文件切坏
    check("Springer 模板：转换稿花括号配平（\\author* 不再切坏 \\email）",
          t.count("{") == t.count("}"),
          f"{{={t.count('{')} }}={t.count('}')}")
    # 2) 不能留下孤立的 \end{abstract}
    check("Springer 模板：没有孤立的 \\end{abstract}",
          t.count("\\begin{abstract}") == t.count("\\end{abstract}"),
          f"begin={t.count('\\begin{abstract}')} end={t.count('\\end{abstract}')}")
    # 3) 命令式摘要：模板示例摘要必须被换掉
    check("Springer 模板：示例摘要被替换（命令式 \\abstract）",
          "The abstract serves both as a general introduction" not in t
          and "Deploying convolutional neural networks" in t)
    # 4) 命令式关键词：模板示例关键词必须被换掉
    check("Springer 模板：示例关键词被替换（命令式 \\keywords）",
          "keyword1" not in t and "edge inference, model compression" in t)
    # 5) 姓名拆成 \fnm/\sur
    check("Springer 模板：姓名拆成 \\fnm{名} \\sur{姓}",
          "\\fnm{Wei} \\sur{Zhang}" in t and "\\fnm{Marcus} \\sur{Hale}" in t)
    # 6) 单位拆成 orgdiv/orgname/orgaddress
    check("Springer 模板：单位拆成 \\orgdiv/\\orgname/\\orgaddress",
          "\\orgdiv{School of Automation}" in t
          and "\\orgname{Harbin Institute of Technology}" in t
          and "\\city{Harbin}" in t and "\\postcode{150001}" in t
          and "\\country{China}" in t)
    # 7) 模板示例命令完整保留在注释里（可回溯）
    check("Springer 模板：示例作者/单位命令完整保留在注释里",
          "% \\author[2,3]{\\fnm{Second} \\sur{Author}}" in t
          and "% \\affil[2]{\\orgdiv{Department}" in t)
    # 8) bst/ 子目录里的 .bst 必须随包（编译必需）
    check("Springer 模板：bst/ 子目录里的 .bst 随包复制（编译必需）",
          (src / "bst" / "sn-mathphys-num.bst").is_file())
    check("Springer 模板：主文件同目录的 .cls 随包复制",
          (src / "sn-jnl.cls").is_file())
    # 9) 结论不该说「检测到内容差异」——正文没动，只是元信息换了写法
    check("Springer 模板：结论是「正文内容未变；元信息重新表示」",
          "正文内容未变" in res.get("diff_conclusion", ""),
          res.get("diff_conclusion", ""))
    # 10) detex 不能把 \fnm{Bo} \sur{Liu} 拼成 BoLiu（会让差异报告误报）
    check("detex 保留命令之间的空格（\\fnm{X} \\sur{Y} → X Y）",
          latex_parse.detex(r"\fnm{Bo} \sur{Liu}") == "Bo Liu",
          repr(latex_parse.detex(r"\fnm{Bo} \sur{Liu}")))
    # 11) 类文件（.cls）里已加载的宏包不能重复带入 —— 否则
    #     `Option clash for package natbib` 直接编译失败。
    mig = ""
    if "% [sci-trans] 以下导言区命令来自原稿" in t:
        mig = t.split("% [sci-trans] 以下导言区命令来自原稿")[1].split("\\begin{document}")[0]
    check("Springer 模板：.cls 已加载的 natbib/graphicx 不被重复带入",
          "natbib" not in mig and "\\usepackage{graphicx}" not in mig,
          mig.strip()[:160])
    # 12) 模板没提供的自定义宏仍要带进来（不能一刀切全丢）
    check("Springer 模板：模板没有的自定义宏仍被带入",
          "\\newcommand{\\method}" in mig and "\\graphicspath" in mig,
          mig.strip()[:160])


# 真实投稿里踩过的元信息/文献区坑（Springer sn-jnl 原稿 + Elsevier CAS 模板）
SNJ_MANUSCRIPT = r"""\documentclass[pdflatex,sn-mathphys-num]{snjtest}
\usepackage{amsmath}
\usepackage{xcolor}

\providecommand{\orgdiv}[1]{#1}

\begin{document}

\title[Short Regression Title]{A Long Regression Title: Command-Form Abstract}

\abstract{This abstract uses the \emph{command} form with markup \textcolor{red}{and color}, and 20\% coverage. It must survive the template移植 verbatim.}

\keywords{alpha learning, beta transfer, gamma weighting}

\maketitle

% Main text
\section{Introduction}\label{introduction}
Body text citing \cite{alpha2020} and pointing at \autoref{eq:one}.

\begin{equation}\label{eq:one}
E = mc^2
\end{equation}

\section{Conclusion}\label{conclusion}
Done.

\bibliographystyle{snjtest-num}
\bibliography{myrefs}

\end{document}
"""

SNJ_TITLE_PAGE = r"""\documentclass[12pt]{article}
\usepackage{authblk}

\title{\textbf{A Long Regression Title: Command-Form Abstract}}

\author[1]{Alice Alpha\thanks{Corresponding author. Email: alice@example.edu}}
\author[1]{Bob Beta}
\author[2]{Carol Gamma}

\affil[1]{School of Alpha, Alpha University, Alpha City, China}
\affil[2]{Institute of Gamma, Gamma University, Gamma City, China}

\begin{document}
\maketitle
\end{document}
"""

CAS_STYLE_TEMPLATE = r"""%% 测试用「CAS 风格官方模板」夹具。
%% 关键结构：**没有 frontmatter 环境**，元信息命令直接放在 \begin{document} 之后；
%% \title 夹在 \shorttitle 与 \fntext 之间，会被「作者/单位命令区」整段覆盖；
%% 文献区里同时有注释掉的示例样式和活着的示例文献库。
\documentclass[a4paper,fleqn]{casstyle}

\usepackage[authoryear,longnamesfirst]{natbib}

\begin{document}
\let\WriteBookmarks\relax

% Short title
\shorttitle{}

% Short author
\shortauthors{}

% Main title of the paper
\title [mode = title]{}

% Title footnote mark
% eg: \tnotemark[1]
\tnotemark[1]

% First author
%
% Options: Use if required
% eg: \author[1,3]{Author Name}[type=editor,
%       style=chinese]
\author[1]{}%[<options>]

% Corresponding author indication
\cormark[1]

% Email id of the first author
\ead{}

% Address/affiliation
\affiliation[1]{organization={},
            addressline={},
            city={},
            postcode={},
            state={},
            country={}}

% Corresponding author text
\cortext[1]{Corresponding author}

% Footnote text
\fntext[1]{}

% Here goes the abstract
\begin{abstract}
Here goes the abstract \nocite{*}%% Remove this line from your manuscript.
\end{abstract}

% Research highlights
\begin{highlights}
\item 
\item 
\item 
\end{highlights}

% Keywords
% Each keyword is seperated by \sep
\begin{keywords}
 \sep \sep \sep
\end{keywords}

\maketitle

% Main text
\section{}
Template sample body text that must be replaced by the manuscript body.

%% Loading bibliography style file
%\bibliographystyle{casstyle-num-names}
\bibliographystyle{casstyle-num}

% Loading bibliography database
\bibliography{sample-refs}

\end{document}
"""


def test_frontmatter_regression() -> None:
    """真实投稿踩过的元信息/文献区缺陷，逐条钉住。

    三条都是「产出看起来正常、实际错了」的类型，必须靠回归测试守着：

    1. 摘要写成 `\\abstract{...}` 命令（Springer sn-jnl）而不是 abstract 环境
       → 解析器不认，摘要整条丢失，产出变成「[待作者填写]」。
    2. 模板的 `\\title` 夹在「作者/单位命令区」区间之内 → 两个替换区间重叠，
       后落笔的用旧坐标切片，标题被吞掉、注释块被切出半个单词（`ng author text`）。
    3. 模板的示例文献库 `\\bibliography{sample-refs}` 没被替换，原稿的
       `\\bibliography{myrefs}` 只是往后追加 → 两条同时生效（参考文献膨胀），
       而且追加位置落在 `\\end{document}` 之后，等于没写。
    """
    print("\n== 元信息与文献区回归（真实投稿形态）==")
    fix = OUT / "regress-snj"
    if fix.exists():
        shutil.rmtree(fix)
    fix.mkdir(parents=True)
    (fix / "Manuscript.tex").write_text(SNJ_MANUSCRIPT, encoding="utf-8", newline="\n")
    (fix / "Title_Page.tex").write_text(SNJ_TITLE_PAGE, encoding="utf-8", newline="\n")
    (fix / "myrefs.bib").write_text(
        "@article{alpha2020, title={Alpha}, author={A. Alpha}, year={2020}}\n"
        "@article{beta2021, title={Beta}, author={B. Beta}, year={2021}}\n",
        encoding="utf-8", newline="\n",
    )
    (fix / "snjtest.cls").write_text("\\NeedsTeXFormat{LaTeX2e}\n", encoding="utf-8")
    (fix / "snjtest-num.bst").write_text("% stub\n", encoding="utf-8")

    kit = OUT / "regress-cas-kit"
    if kit.exists():
        shutil.rmtree(kit)
    kit.mkdir(parents=True)
    (kit / "cas-style-template.tex").write_text(CAS_STYLE_TEMPLATE, encoding="utf-8", newline="\n")
    (kit / "casstyle.cls").write_text("\\NeedsTeXFormat{LaTeX2e}\n", encoding="utf-8")
    (kit / "casstyle-num.bst").write_text("% stub\n", encoding="utf-8")
    (kit / "sample-refs.bib").write_text(
        "@article{sample1, title={Sample One}, year={1999}}\n"
        "@article{sample2, title={Sample Two}, year={2000}}\n"
        "@article{sample3, title={Sample Three}, year={2001}}\n",
        encoding="utf-8", newline="\n",
    )

    # ---- 1) 命令式摘要 + 独立标题页 ----
    ms_src = latex_parse.parse_latex(fix)
    check("sn-jnl：命令式 \\abstract{...} 被解析（不再是空摘要）",
          len(ms_src.metadata.abstract) > 80
          and "20\\%" in ms_src.metadata.abstract_raw,
          repr(ms_src.metadata.abstract[:60]))
    check("sn-jnl：\\title[短]{长} 取的是长标题",
          ms_src.metadata.title.startswith("A Long Regression Title"), ms_src.metadata.title)
    check("独立标题页：作者/单位被补入（\\title 与主文件逐字相同才认）",
          [a["name"] for a in ms_src.metadata.authors]
          == ["Alice Alpha", "Bob Beta", "Carol Gamma"],
          str(ms_src.metadata.authors))
    check("独立标题页：单位标签映射正确（\\author[2] → 第 2 个单位）",
          len(ms_src.metadata.affiliations) == 2
          and ms_src.metadata.doc_meta["affiliation_map"].get("2", "").startswith("Institute of Gamma"),
          str(ms_src.metadata.affiliations))
    check("独立标题页：通讯作者脚注不被当成单位",
          not any("Corresponding author" in a for a in ms_src.metadata.affiliations),
          str(ms_src.metadata.affiliations))
    check("独立标题页：作者自带的单位标记不因脚注而丢失",
          [a.get("affil") for a in ms_src.metadata.authors] == ["1", "1", "2"],
          str([(a["name"], a.get("affil")) for a in ms_src.metadata.authors]))

    # ---- 2) 换到 CAS 风格模板 ----
    res = run_pipeline(fix, "", OUT / "regress-out", cwd=ROOT, make_pdf=False,
                       log=Log(verbose=False),
                       template=str(kit / "cas-style-template.tex"))
    src = Path(res["journal_dir"]) / "src"
    t = (src / "Manuscript.tex").read_text(encoding="utf-8")
    live = "\n".join(ln for ln in t.split("\n") if not ln.lstrip().startswith("%"))

    check("CAS 风格：标题落在新块里且只出现一次（不被作者块覆盖）",
          t.count("\\title{A Long Regression Title: Command-Form Abstract}") == 1
          and "A Long Regression Title" in live,
          str([ln for ln in live.split("\n") if "\\title" in ln]))
    check("CAS 风格：摘要原文（宏与 \\% 未丢）",
          "\\textcolor{red}{and color}" in t and "20\\%" in t
          and "This abstract uses the" in live)
    check("CAS 风格：模板示例摘要被替换", "Here goes the abstract" not in live)
    check("CAS 风格：作者按模板命令体系重建且单位对应正确",
          "\\author[aff1]{Alice Alpha}" in t and "\\author[aff2]{Carol Gamma}" in t
          and "\\affiliation[aff2]{Institute of Gamma" in t,
          str([ln for ln in live.split("\n") if "\\author[" in ln]))
    check("CAS 风格：模板示例作者命令完整保留在注释里（逐行加 %、零截断）",
          "\n% \\shorttitle{}\n" in t and "\n% \\fntext[1]{}\n" in t
          and "\nng author text\n" not in t)
    # 最强的一条：注释保留区必须是模板原文「逐行加 % 」的忠实副本，
    # 任何截断/坐标错位都会让这段对不上。
    tpl_text = (kit / "cas-style-template.tex").read_text(encoding="utf-8")
    _s0 = tpl_text.index("\\shorttitle{}")
    _e0 = tpl_text.index("\\fntext[1]{}") + len("\\fntext[1]{}")
    _expected = "\n".join("% " + ln for ln in tpl_text[_s0:_e0].split("\n"))
    check("CAS 风格：注释保留区与模板原文逐字一致（无错位/无截断）",
          _expected in t)
    check("CAS 风格：关键词用模板分隔符 \\sep",
          "alpha learning \\sep beta transfer \\sep gamma weighting" in t)
    check("CAS 风格：模板示例正文被替换", "Template sample body text" not in t)
    check("CAS 风格：模板要求的 research highlights 原稿没有 → 明确列为待作者填写",
          "[sci-trans] 模板要求 research highlights" in t
          and any("研究亮点" in x for x in
                  (Path(res["journal_dir"]) / "conversion-log.md")
                  .read_text(encoding="utf-8").split("- [ ]")))

    # ---- 3) 文献区 ----
    check("文献区：模板示例文献库被替换（不是往后追加）",
          "\\bibliography{myrefs}" in live and "\\bibliography{sample-refs}" not in live,
          str([ln for ln in live.split("\n") if "\\bibliography" in ln]))
    check("文献区：追加内容落在 \\end{document} 之前",
          t.count("\\end{document}") == 1
          and t.find("\\bibliography{myrefs}") < t.find("\\end{document}"))
    check("文献区：注释里的示例文献样式没有被意外激活",
          "\\bibliographystyle{casstyle-num-names}" not in live
          and "\\bibliographystyle{casstyle-num}" in live,
          str([ln for ln in live.split("\n") if "\\bibliographystyle" in ln]))

    # ---- 3b) 原稿自带 highlights 时要原样搬进模板 ----
    fix_hl = OUT / "regress-hl"
    if fix_hl.exists():
        shutil.rmtree(fix_hl)
    shutil.copytree(fix, fix_hl)
    _hl = (fix_hl / "Manuscript.tex").read_text(encoding="utf-8").replace(
        "\\maketitle",
        "\\begin{highlights}\n\\item Alpha finding\n\\item Beta finding\n"
        "\\end{highlights}\n\n\\maketitle",
    )
    (fix_hl / "Manuscript.tex").write_text(_hl, encoding="utf-8", newline="\n")
    res_hl = run_pipeline(fix_hl, "", OUT / "regress-hl-out", cwd=ROOT, make_pdf=False,
                          log=Log(verbose=False),
                          template=str(kit / "cas-style-template.tex"))
    t_hl = (Path(res_hl["journal_dir"]) / "src" / "Manuscript.tex").read_text(encoding="utf-8")
    check("CAS 风格：原稿自带 highlights 时原样搬进模板（不丢条目）",
          "\\item Alpha finding" in t_hl and "\\item Beta finding" in t_hl,
          str([ln for ln in t_hl.split("\n") if "item " in ln][:6]))

    # ---- 4) 内容零丢失 ----
    ms_before = latex_parse.parse_latex(fix)
    ms_before.references = refs_mod.load_references(ms_before, fix)
    ms_after = latex_parse.parse_latex(src)
    ms_after.references = refs_mod.load_references(ms_after, src)
    d = diff_manuscripts(ms_before, ms_after)
    check("回归：正文/图表/公式/引用零丢失",
          not d["sections"]["changed"] and not d["figures"]["removed"]
          and not d["tables"]["removed"] and not d["equations"]["removed"]
          and not d["citations"]["removed"],
          f"changed={len(d['sections']['changed'])}")
    check("回归：参考文献条数不变（示例库未混入）",
          len(ms_before.references) == len(ms_after.references),
          f"{len(ms_before.references)} -> {len(ms_after.references)}")
    check("回归：摘要与标题在转换稿里仍可解析回来",
          ms_after.metadata.title == ms_before.metadata.title
          and ms_after.metadata.abstract == ms_before.metadata.abstract)

    # ---- 5) 嵌套在分组参数里的内容：scan_commands 读到配平分组会整块跳过 ----
    nested = OUT / "regress-labels"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "panel-a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (nested / "panel-b.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (nested / "main.tex").write_text(
        "\\documentclass{article}\n\\usepackage{subfig}\n\\usepackage{xcolor}\n"
        "\\begin{document}\n"
        "\\section{One}\\label{sec:one}\n"
        "See \\autoref{fig:sub} and \\ref{alg:one}.\n"
        "\\begin{figure}\\centering\n"
        "\\subfloat[Panel A\\label{fig:sub}]{\\includegraphics[width=0.5\\textwidth]{panel-a.png}}\n"
        "\\subfloat[Panel B\\label{fig:sub2}]{\\includegraphics[width=0.5\\textwidth]{panel-b.png}}\n"
        "\\caption{Overall}\\label{fig:overall}\n\\end{figure}\n"
        "\\begin{algorithm}\\caption{Alg}\\label{alg:one}\\end{algorithm}\n"
        "\\section{Two}\\label{sec:two}\n"
        # 引用写在分组参数内部：scan_commands 读到配平分组会整块跳过
        "\\textcolor{red}{As shown in \\autoref{fig:overall} and \\cite{alpha2020}, ...}\n"
        "\\end{document}\n",
        encoding="utf-8", newline="\n",
    )
    ms_n = latex_parse.parse_latex(nested)
    ms_n.references = refs_mod.load_references(ms_n, nested)
    check("嵌套 \\subfloat[..]{\\includegraphics{..}} 里的图片文件被收集到",
          ms_n.figures and ms_n.figures[0].files == ["panel-a.png", "panel-b.png"],
          str([f.files for f in ms_n.figures]))
    check("分组参数内部的 \\cite 不被漏掉",
          "alpha2020" in {c.key for c in ms_n.citations},
          str(sorted({c.key for c in ms_n.citations})))
    check("分组参数内部的 \\autoref 不被漏掉，图不再被当成「没被引用」",
          ms_n.figures and ms_n.figures[0].referenced_in,
          str([f.referenced_in for f in ms_n.figures]))
    bag_n = run_qc(ms_n, J.load("ieee-trans", ROOT), nested, None, Log(verbose=False),
                   {r.key for r in ms_n.references if r.origin == "bib"}, None, "测试")
    codes_n = codes(bag_n)
    check("嵌套 \\label（\\subfloat[] / algorithm 环境）不被误报为未定义引用",
          "LTX005" not in codes_n, str(codes_n))
    check("分组参数里的图片不再被误报为「没有图片文件」",
          not any(i.code == "FIG002" for i in bag_n.items), str(codes_n))
    check("被引用过的图不再被误报为「正文中没有引用」",
          not any(i.code == "FIG050" for i in bag_n.items), str(codes_n))


def test_preamble_carry() -> None:
    r"""导言区「补什么、不补什么」的判定。

    回归背景（两条都是**静默产出坏文件**的真 bug）：

    1. 原稿导言区里 `%\\usepackage[...]{natbib}` 这类「留档备查」的注释行极其
       常见，照抄进结果就会凭空激活一条命令。
    2. 判定「模板有没有加载这个宏包/定义这个命令」时只看模板主文件，漏掉
       `.cls`/`.sty` —— 而期刊类恰恰把 `\\usepackage[numbers,sort&compress]{natbib}`、
       `\\def\\fnm`、`\\newtheorem{theorem}` 全写在类文件里。于是原稿里的同名
       `\\usepackage` / `\\providecommand` 被重复带入，轻则冗余，重则
       `Option clash for package natbib` / `Command \\theorem already defined`
       直接编译失败。
    """
    print("\n== 导言区补入判定（注释行 / 类文件已提供）==")
    from sci_core.latex_build import _carry_preamble, _defined_name

    tpl_dir = ROOT / "tests" / "fixtures" / "springer_kit" / "sn-article-template"
    tpl_head = (tpl_dir / "sn-article.tex").read_text(encoding="utf-8").split(
        "\\begin{document}")[0]
    src_head = (
        "\\documentclass{article}\n"
        "%\\usepackage{stfloats}          % 注释掉的备查写法，不该被迁移\n"
        "\\usepackage{natbib}             % 类文件已加载 -> 不带\n"
        "\\usepackage{graphicx}           % 类文件已加载 -> 不带\n"
        "\\usepackage{siunitx}            % 模板没有 -> 要带\n"
        "\\providecommand{\\fnm}[1]{#1}   % 类文件已定义 -> 不带\n"
        "\\newcommand{\\method}{AST}      % 模板没有 -> 要带\n"
        "\\newtheorem{theorem}{Theorem}   % 类文件已定义 -> 不带\n"
        "\\def\\tsc#1{\\textsc{#1}}        % 模板没有 -> 要带\n"
        "\\graphicspath{{figures/}}\n"
    )
    cfg = J.from_template(tpl_dir / "sn-article.tex")
    out = _carry_preamble(src_head, tpl_head, cfg, "main.tex", ChangeLog(), tpl_dir=tpl_dir)
    mig = out.split("% [sci-trans] 以下导言区命令来自原稿")[1].split("\\begin{document}")[0]

    check("注释掉的 \\usepackage 不被迁移（不凭空激活命令）",
          "stfloats" not in mig, mig.strip()[:160])
    check("类文件已加载的宏包不被重复带入（避免 option clash）",
          "natbib" not in mig and "graphicx" not in mig, mig.strip()[:160])
    check("模板没加载的宏包仍被带入", "\\usepackage{siunitx}" in mig, mig.strip()[:160])
    check("类文件已定义的同名命令不被重复带入（避免 already defined）",
          "\\providecommand{\\fnm}" not in mig and "\\newtheorem{theorem}" not in mig,
          mig.strip()[:160])
    check("模板没有的自定义宏仍被带入",
          "\\newcommand{\\method}" in mig and "\\def\\tsc" in mig
          and "\\graphicspath" in mig, mig.strip()[:160])
    check("被定义命令名解析正确",
          _defined_name(r"\def\tsc#1{}") == "tsc"
          and _defined_name(r"\newcommand{\foo}[1]{}") == "foo"
          and _defined_name(r"\DeclareMathOperator*{\argmin}{arg\,min}") == "argmin"
          and _defined_name(r"\newtheorem{lemma}[theorem]{Lemma}") == "lemma"
          and _defined_name(r"\graphicspath{{figures/}}") == "",
          "")


def test_email_backfill() -> None:
    r"""主文件作者缺邮箱时，从同项目标题页按姓名回填。

    回归背景：真实投稿里「作者名单」和「通讯邮箱」经常不同源 —— 主文件写
    `\\author[aff1]{Bo Liu}`（如 elsarticle / CAS 风格），邮箱在独立
    `Title_Page.tex` 的 `\\author[1]{Bo Liu\\thanks{Corresponding author. Email: ...}}`
    里。原实现只在**主文件完全没有作者**时才读标题页，于是主文件一旦有作者
    就再也不看标题页，通讯邮箱被静默丢掉 —— 而投稿系统几乎都强制要邮箱。
    """
    print("\n== 标题页邮箱回填 ==")
    fix = ROOT / "tests" / "fixtures" / "email_backfill"
    ms = latex_parse.parse_latex(fix, Log(verbose=False))
    by_name = {a["name"]: a for a in ms.metadata.authors}
    check("主文件作者被解析到（3 位）", len(ms.metadata.authors) == 3,
          str([a["name"] for a in ms.metadata.authors]))
    check("主文件里没有的邮箱从标题页按姓名回填",
          by_name.get("Bo Liu", {}).get("email") == "csbliu@gdut.edu.cn",
          str(by_name.get("Bo Liu")))
    check("只回填有邮箱的那位，其余作者不被改动",
          by_name.get("Yunlong Sun", {}).get("email") == ""
          and by_name.get("Yanshan Xiao", {}).get("email") == "")
    check("回填来源被记录下来（可追溯）",
          ms.metadata.doc_meta.get("email_source_file") == "Title_Page.tex",
          str(ms.metadata.doc_meta.get("email_source_file")))
    # 反向：标题对不上就不该乱填（同目录常有回复信等别的文档）
    alt = OUT / "email-backfill-mismatch"
    alt.mkdir(parents=True, exist_ok=True)
    (alt / "Manuscript.tex").write_text(
        (fix / "Manuscript.tex").read_text(encoding="utf-8"),
        encoding="utf-8", newline="\n")
    (alt / "Title_Page.tex").write_text(
        (fix / "Title_Page.tex").read_text(encoding="utf-8").replace(
            "Explainability-Guided Partial Privileged Learning: A Class-Aware "
            "Weighted Similarity Approach",
            "A Completely Different Title About Something Else Entirely"),
        encoding="utf-8", newline="\n")
    ms2 = latex_parse.parse_latex(alt, Log(verbose=False))
    check("标题对不上时绝不回填（防串稿）",
          all(not a.get("email") for a in ms2.metadata.authors),
          str([(a["name"], a.get("email")) for a in ms2.metadata.authors]))


def test_cas_draft_style() -> None:
    r"""Elsevier CAS 风格初稿（`\ead` / `\cormark` / key=value 单位 / 跨行宏）。

    这四条都是**静默产出坏文件**的真 bug，而且第一条能把整篇稿子带崩：

    1. 跨行 `\newcommand*\circled[1]{\\tikz[...]{` 被逐行搬运截成半句 →
       未闭合 `{` → `scan_commands` 从这里起吞掉整个文件（实测：花括号
       1297 vs 1295、25 节全变 0 节、标题/摘要/作者全部「未识别」、
       70 个引用键被误报成「不存在」、77 个 errors）。
    2. 邮箱写在 `\author{}` **外面**（`\ead{}`）→ 8 位作者的邮箱全丢。
    3. 通讯标记 `\cormark[1]` 也在外面 → 要么全丢，要么修完 2 之后
       「人人有邮箱」导致每位作者都被标 `\author*`。
    4. `\affiliation[a]{organization={A, B}, city={C}, country={D}}` 按逗号切
       → 产出 `\orgdiv{organization={A}, \orgname{B}}` 这种垃圾，`country` 整条丢失。
    """
    print("\n== Elsevier CAS 风格初稿 ==")
    fix = ROOT / "tests" / "fixtures" / "cas_draft"
    ms = latex_parse.parse_latex(fix, Log(verbose=False))
    by_name = {a["name"]: a for a in ms.metadata.authors}

    check("CAS：\\author{} 之外的 \\ead{} 邮箱被采集到",
          by_name.get("Bo Liu", {}).get("email") == "csbliu@gdut.edu.cn"
          and by_name.get("Yanshan Xiao", {}).get("email") == "xiaoyanshan@gdut.edu.cn",
          str([(a["name"], a.get("email")) for a in ms.metadata.authors]))
    check("CAS：\\cormark 通讯标记被识别（只有 Bo Liu）",
          by_name.get("Bo Liu", {}).get("corresponding") is True
          and not by_name.get("Yanshan Xiao", {}).get("corresponding"),
          str([(a["name"], a.get("corresponding")) for a in ms.metadata.authors]))
    parts = (ms.metadata.doc_meta.get("affiliation_parts") or {}).get("a", {})
    check("CAS：key=value 单位被结构化解析（不按逗号瞎切）",
          parts.get("orgdiv") == "School of Automation"
          and parts.get("orgname") == "Guangdong University of Technology"
          and parts.get("city") == "Guangzhou"
          and parts.get("country") == "China",
          str(parts))

    res = run_pipeline(fix, "", OUT / "cas-draft", cwd=ROOT,
                       make_pdf=False, log=Log(verbose=False),
                       template=str(ROOT / "tests" / "fixtures" / "springer_kit"
                                    / "sn-article-template" / "sn-article.tex"))
    src = Path(res["journal_dir"]) / "src"
    t = (src / "new-Manuscript.tex").read_text(encoding="utf-8")
    mig = t.split("% [sci-trans] 以下导言区命令来自原稿")[1].split("\\begin{document}")[0] \
        if "% [sci-trans] 以下导言区命令来自原稿" in t else ""

    check("CAS：转换稿花括号配平（跨行 \\newcommand 不再被截断）",
          t.count("{") == t.count("}"),
          f"{{={t.count('{')} }}={t.count('}')}")
    check("CAS：跨行宏定义整条搬运（含第二行的 \\node）",
          "\\newcommand*\\circled[1]" in mig and "\\node[shape=circle" in mig,
          mig.strip()[:200])
    d = res.get("diff") or {}
    ss = d.get("sections_summary") or {}
    check("CAS：转换后正文逐字未变（章节无增删改，元信息也无实质差异）",
          res.get("diff_identical") is True
          and not ss.get("added") and not ss.get("removed")
          and not ss.get("changed_count") and not (d.get("metadata") or {}),
          f"{res.get('diff_conclusion')} | {ss} | {list((d.get('metadata') or {}).keys())}")
    check("CAS：只有通讯作者带 \\author* 星号",
          "\\author*[aff1]{\\fnm{Bo} \\sur{Liu}}" in t
          and "\\author*[aff2]" not in t
          and "\\author[aff2]{\\fnm{Yanshan} \\sur{Xiao}}" in t,
          "\n".join(l for l in t.splitlines() if l.startswith("\\author")))
    check("CAS：单位按 organization/city/country 字段落位（country 不再丢）",
          "\\orgdiv{School of Automation}" in t
          and "\\orgname{Guangdong University of Technology}" in t
          and "\\city{Guangzhou}" in t and "\\country{China}" in t,
          "\n".join(l for l in t.splitlines() if l.startswith("\\affil")))
    # ---- 结果包裁剪：只带正文真正引用到的文件 ----
    # 真实投稿目录里常混着官方模板 kit 的示例文档、说明书、缩略图、废弃图片、
    # 没被引用的 .bib。原实现**原样复制**，作者一不小心就把模板样例连同稿子
    # 一起投出去，Overleaf 里还会冒出多个候选主文件。
    pkg = {p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file()}
    check("CAS：正文引用到的图与文献库进了结果包",
          "fig1.png" in pkg and "references.bib" in pkg, str(sorted(pkg)))
    check("CAS：注释掉的 \\includegraphics 不算引用（留档旧图不进包）",
          "unused_fig.png" not in pkg, str(sorted(pkg)))
    check("CAS：原稿自带的模板 kit 垃圾不进结果包（示例/说明书/缩略图）",
          not ({"cas-sc-template.tex", "cas-dc-sample.tex",
                "doc/elsdoc-cas.tex", "thumbnails/cas-email.jpeg"} & pkg),
          str(sorted(pkg)))
    check("CAS：换成模板的文档类后，原文档类的 .cls/.sty/.bst 不再随包",
          not ({"cas-sc.cls", "cas-common.sty", "cas-model2-names.bst"} & pkg),
          str(sorted(pkg)))
    check("CAS：结果包不该再有 LTX050（无关文档根本没进来）",
          not any(i.get("code") == "LTX050" for i in res.get("issues", [])),
          str(sorted({i.get("code") for i in res.get("issues", [])})))
    check("CAS：原文档类专有命令 \\printcredits 被注释并记 CVT030",
          "% \\printcredits" in t
          and any(i.get("code") == "CVT030" for i in res.get("issues", [])),
          str(sorted({i.get("code") for i in res.get("issues", [])})))
    # 裁剪 ≠ 丢数据：原稿（含那些没进结果包的文件）必须完整躺在 original/ 里。
    orig = Path(res["submission_dir"]) / "original"
    orig_files = {p.relative_to(orig).as_posix() for p in orig.rglob("*") if p.is_file()}
    check("CAS：原稿完整备份在 submission/original/（裁剪不等于丢数据）",
          "cas-dc-sample.tex" in orig_files and "thumbnails/cas-email.jpeg" in orig_files
          and "unused_fig.png" in orig_files and "new-Manuscript.tex" in orig_files,
          f"{len(orig_files)} 个文件：{sorted(orig_files)[:8]}")
    # 单位只是换了命令体系（CAS 的 key=value → Springer 的 \orgdiv/\city），
    # 内容完全一致。差异报告若按字符串比会报「单位被改了」，把作者吓一跳。
    check("CAS：单位归一化后两种写法等价（不再误报「单位被改了」）",
          _canon_affil("organization={School of Automation, GDUT}, "
                       "city={Guangzhou}, country={China}")
          == _canon_affil("\\orgdiv{School of Automation}, \\orgname{GDUT}, "
                          "\\orgaddress{\\city{Guangzhou}, \\postcode{}, \\country{China}}"),
          _canon_affil("organization={School of Automation, GDUT}, "
                       "city={Guangzhou}, country={China}"))
    check("CAS：转换后差异报告的元信息里不再出现「单位」变更",
          "affiliations" not in ((res.get("diff") or {}).get("metadata") or {}),
          str(list(((res.get("diff") or {}).get("metadata") or {}).keys())))


def test_package_trimming() -> None:
    r"""结果包只带「编译真的会读到」的文件。

    回归背景：真实投稿目录里混着官方模板 kit 的示例文档、说明书、缩略图、
    废弃图片、没被引用的 `.bib`。原实现把它们**原样复制**进结果包 —— 作者一
    不小心就把模板样例连同稿子一起投出去，Overleaf 里还会冒出多个候选主文件。

    裁剪必须**递归**：`\documentclass{cas-sc}` → `cas-sc.cls` →
    `\RequirePackage{cas-common}` → `cas-common.sty`，只看正文会漏掉最后一层。
    """
    print("\n== 结果包按引用裁剪 ==")
    from sci_core.latex_build import _collect_needed_files, _copy_needed_files  # noqa: PLC0415

    fix = ROOT / "tests" / "fixtures" / "cas_draft"
    main_text = (fix / "new-Manuscript.tex").read_text(encoding="utf-8")
    rels = {p.relative_to(fix).as_posix() for p in _collect_needed_files(fix, [main_text])}

    check("裁剪：\\documentclass 引的 .cls 进包，并递归带出它 \\RequirePackage 的 .sty",
          "cas-sc.cls" in rels and "cas-common.sty" in rels, str(sorted(rels)))
    check("裁剪：\\bibliographystyle 引的 .bst 进包",
          "cas-model2-names.bst" in rels, str(sorted(rels)))
    check("裁剪：\\includegraphics 引的图进包；注释掉的那张不进",
          "fig1.png" in rels and "unused_fig.png" not in rels, str(sorted(rels)))
    check("裁剪：\\bibliography 引的 .bib 进包",
          "references.bib" in rels, str(sorted(rels)))
    check("裁剪：没被任何引用指向的 kit 文件一律不进包",
          not ({"cas-sc-template.tex", "cas-dc-sample.tex",
                "doc/elsdoc-cas.tex", "thumbnails/cas-email.jpeg"} & rels),
          str(sorted(rels)))

    # 文档类**选项**选出的 .bst：类文件里若用 `\if@...` 分支才 \bibliographystyle，
    # 光扫类文件认不全，得按选项名再兜一道。
    probe = OUT / "_trim_probe"
    if probe.exists():
        shutil.rmtree(probe, ignore_errors=True)
    (probe / "bst").mkdir(parents=True)
    (probe / "mystyle.cls").write_text("\\LoadClass{article}\n", encoding="utf-8")
    (probe / "bst" / "mystyle-num.bst").write_text("ENTRY{}{}{}\n", encoding="utf-8")
    (probe / "bst" / "mystyle-ay.bst").write_text("ENTRY{}{}{}\n", encoding="utf-8")
    (probe / "unused-note.txt").write_text("junk\n", encoding="utf-8")
    entry = "\\documentclass[mystyle-num]{mystyle}\n\\begin{document}x\\end{document}\n"
    probe_names = {p.name for p in _collect_needed_files(probe, [entry])}
    check("裁剪：文档类选项选出的 .bst 也认（类文件没写 \\bibliographystyle 时）",
          "mystyle-num.bst" in probe_names and "mystyle.cls" in probe_names
          and "mystyle-ay.bst" not in probe_names,
          str(sorted(probe_names)))

    # 复制时只落需要的，其余计入「被裁掉」清单
    dest = OUT / "_trim_out"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    copied, dropped = _copy_needed_files(fix, dest, _collect_needed_files(fix, [main_text]))
    check("裁剪：复制只落需要的文件，其余进「被裁掉」清单（可告知作者）",
          sorted(copied) == ["cas-common.sty", "cas-model2-names.bst", "cas-sc.cls",
                             "fig1.png", "references.bib"]
          and "unused_fig.png" in dropped and "cas-dc-sample.tex" in dropped,
          f"copied={sorted(copied)} dropped={sorted(dropped)}")


def test_missing_fields() -> None:
    r"""初稿缺字段：跳过、不写占位、能汇报。

    回归背景：以前「原稿没有摘要」会把 `\abstract{[待作者填写]}` 写进结果 ——
    差异报告于是把空摘要和原稿摘要一比，报出「摘要内容被改了」；作者看到的
    是一堆占位符，而不是一张「我缺哪些字段」的清单。要求是：

    1. 原稿没有的字段**整条略过**，不生成占位文字；
    2. 模板里的示例内容被**注释**保留（便于对照），而不是留成活命令；
    3. 迁移结束能给出「已迁移 N 项 / 跳过 M 项」并逐条说明跳过了什么；
    4. 期刊规范明确要求的字段，跳过时标 `required`。

    另一个真 bug 一并守在这里：原稿没有 `\bibliography` 时，模板自己那条
    `\bibliography{sn-bibliography}` 以前会**原样留着** —— 随包样例条目被编进
    参考文献，`sn-bibliography.bib` 也被当成"被引用"复制进结果包。
    """
    print("\n== 初稿缺字段：跳过而非占位 ==")

    # --- 单元：ChangeLog 的跳过账本 ---
    ledger = ChangeLog()
    ledger.record("元信息", "标题", after="X")
    ledger.skip("摘要", "原稿没有摘要", required=True)
    ledger.skip("关键词", "原稿没有关键词")
    ledger.skip("摘要", "原稿没有摘要", required=True)   # 同一件事记两次只算一次
    summ = ledger.summary()
    check("跳过账本：去重 + 计数 + 强制缺失清单",
          summ["changed"] == 1 and summ["skipped"] == 2
          and summ["required_missing"] == ["摘要"],
          str(summ))

    # --- 端到端：三种残缺初稿 ---
    tpl = (ROOT / "tests" / "fixtures" / "springer_kit"
           / "sn-article-template" / "sn-article.tex")
    cases = {
        "only_title": (      # 只有标题与正文：作者/单位/摘要/关键词/文献全缺
            "\\documentclass{article}\n\\begin{document}\n"
            "\\title{T}\n\\maketitle\n\\section{S}\nBody.\n\\end{document}\n"),
        "no_bib": (          # 元信息齐全，唯独没有文献命令
            "\\documentclass{article}\n\\begin{document}\n\\title{T}\n"
            "\\author{A}\n\\affil{School of X, City, China}\n"
            "\\begin{abstract}\nAbs.\n\\end{abstract}\n"
            "\\begin{keywords}\nk1, k2\n\\end{keywords}\n\\maketitle\n"
            "\\section{S}\nBody.\n\\end{document}\n"),
        "bare": (            # 连标题都没有
            "\\documentclass{article}\n\\begin{document}\n"
            "\\section{S}\nBody.\n\\end{document}\n"),
    }
    base = OUT / "missing-fields"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)

    results: dict[str, dict] = {}
    for name, text in cases.items():
        src = base / name
        src.mkdir(parents=True, exist_ok=True)
        (src / "paper.tex").write_text(text, encoding="utf-8")
        results[name] = run_pipeline(src, "", base / f"out-{name}", cwd=ROOT,
                                     make_pdf=False, log=Log(verbose=False),
                                     template=str(tpl))

    for name, res in results.items():
        jdir = Path(res["journal_dir"])
        main = Path(res["main_file"]).read_text(encoding="utf-8")
        skipped_targets = {s["target"] for s in (res.get("skipped") or [])}

        check(f"[{name}] 结果里不再出现 [待作者填写] 占位",
              "[待作者填写]" not in main,
              [ln for ln in main.split("\n") if "待作者填写" in ln][:3])
        check(f"[{name}] 迁移结果汇总已给出（改动数 + 跳过数）",
              (res.get("migration") or {}).get("changed", 0) > 0
              and (res.get("migration") or {}).get("skipped", 0) == len(skipped_targets),
              str(res.get("migration")))
        check(f"[{name}] 差异结论：元信息跳过不算「内容被改了」",
              res.get("diff_identical") is True, str(res.get("diff_conclusion")))
        check(f"[{name}] 结果包里没有模板示例文献库 sn-bibliography.bib",
              not list(jdir.rglob("sn-bibliography.bib")),
              str([p.name for p in jdir.rglob("*.bib")]))

    # 原稿**没有**摘要的那两种：模板示例摘要要被注释掉，且不能留下活命令
    for name in ("only_title", "bare"):
        main = Path(results[name]["main_file"]).read_text(encoding="utf-8")
        check(f"[{name}] 缺摘要时模板示例被注释保留（可对照），不是留成活命令",
              "% [sci-trans] 模板示例摘要已注释" in main
              and not re.search(r"(?m)^\s*\\abstract\{", main),
              [ln[:80] for ln in main.split("\n") if "abstract" in ln][:3])
    # 原稿**有**摘要的那种：照常迁移，不该被当成缺失
    no_bib_main = Path(results["no_bib"]["main_file"]).read_text(encoding="utf-8")
    check("有摘要的稿子：摘要照常迁移，不计入跳过",
          "\\abstract{Abs.}" in no_bib_main
          and "摘要" not in {s["target"] for s in results["no_bib"]["skipped"]},
          [ln[:80] for ln in no_bib_main.split("\n") if "abstract" in ln][:3])

    # 具体跳过项：只给标题的稿子，缺的字段要一个不落地列出来
    only_title = {s["target"] for s in (results["only_title"].get("skipped") or [])}
    check("缺字段清单完整（作者/单位/摘要/关键词/文献库）",
          {"作者列表", "作者单位", "摘要", "关键词", "参考文献库"} <= only_title,
          str(sorted(only_title)))
    check("只有标题的稿子：标题不算「缺失」",
          "标题" not in only_title, str(sorted(only_title)))
    check("元信息齐全、只缺文献库的稿子：只报文献库",
          {s["target"] for s in (results["no_bib"].get("skipped") or [])}
          == {"参考文献库"},
          str([s["target"] for s in (results["no_bib"].get("skipped") or [])]))
    check("连标题都没有时：标题被标为期刊强制要求的缺失项",
          "标题" in (results["bare"].get("migration") or {}).get("required_missing", []),
          str((results["bare"].get("migration") or {}).get("required_missing")))

    # 汇总要真的写进结果包（作者看的是文件，不是终端）
    log_md = (Path(results["bare"]["journal_dir"]) / "conversion-log.md").read_text(encoding="utf-8")
    check("未迁移清单写进 conversion-log.md",
          "未迁移清单" in log_md and "原稿未解析到摘要" in log_md,
          log_md[:200])

    # --- 「期刊是否强制要求」只在配置写得出来时才为真（不猜规则）---
    from sci_core.latex_build import _cfg_requires  # noqa: PLC0415
    cfg_els = J.load("elsevier-generic", ROOT)
    cfg_tpl = J.from_template(str(tpl))
    check("期刊规范写了 required_sections/keywords_min → 缺摘要/关键词标为强制",
          _cfg_requires(cfg_els, "abstract") and _cfg_requires(cfg_els, "keywords"),
          f"abstract={_cfg_requires(cfg_els, 'abstract')} "
          f"keywords={_cfg_requires(cfg_els, 'keywords')}")
    check("只有官方模板、没有期刊配置时不猜规则（不标强制）",
          not _cfg_requires(cfg_tpl, "abstract") and not _cfg_requires(cfg_tpl, "keywords"),
          f"abstract={_cfg_requires(cfg_tpl, 'abstract')} "
          f"keywords={_cfg_requires(cfg_tpl, 'keywords')}")

    # --- 正文切分护栏：畸形初稿不能静默丢章节 ---
    broken = base / "no_doc_env"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "paper.tex").write_text(
        "\\documentclass{article}\n\\title{T}\n\\section{Body}\n"
        "No \\begin{document} here at all.\n", encoding="utf-8")
    res_broken = run_pipeline(broken, "", base / "out-broken", cwd=ROOT,
                              make_pdf=False, log=Log(verbose=False),
                              template=str(tpl))
    check("畸形初稿（无 \\begin{document}）：报 CVT022 而不是静默丢章节",
          "CVT022" in {i["code"] for i in res_broken.get("issues", [])},
          str(sorted({i["code"] for i in res_broken.get("issues", [])})))


def test_rule_binding() -> None:
    """配置里的规则必须真的绑到检查上。

    回归背景：`check_structure` / `check_abstract_and_length` 曾经用扁平键名
    （`cfg.get("required_sections")`、`cfg.get("ref_max")`）去读**嵌套**的配置，
    永远取到 None —— 于是 STR010 / STR011 / STR012 / LEN002 这几项检查从未生效，
    报告却看起来"检查过了"。这里逐条验证规则生效。
    """
    print("\n== 期刊规则绑定（规则写了就必须生效）==")
    ms = latex_parse.parse_latex(LATEX_FIX)
    ms.references = refs_mod.load_references(ms, LATEX_FIX)
    bib_keys = {r.key for r in ms.references if r.origin == "bib"}

    def bag_with(mutate) -> set[str]:
        cfg = J.load("elsevier-generic", ROOT)
        mutate(cfg.raw)
        b = run_qc(ms, cfg, LATEX_FIX, None, Log(verbose=False), bib_keys, None, "测试")
        return codes(b)

    base = bag_with(lambda raw: None)
    check("基线：STR010/STR011/STR012/LEN002 默认不触发（规则未设）",
          not ({"STR010", "STR011", "STR012", "LEN002"} & base),
          str({"STR010", "STR011", "STR012", "LEN002"} & base))

    def set_heading(raw):
        raw["body"] = dict(raw.get("body") or {}, heading_levels_max=1)

    check("body.heading_levels_max 生效 → STR010",
          "STR010" in bag_with(set_heading))

    def set_sections(raw):
        raw["article_types"] = [{"id": "ra", "name": "RA",
                                 "required_sections": ["introduction", "results",
                                                       "no-such-section"]}]

    check("article_types.required_sections 生效 → STR011",
          "STR011" in bag_with(set_sections))

    def set_numbering(raw):
        raw["body"] = dict(raw.get("body") or {}, section_numbering=False)

    check("body.section_numbering 生效 → STR012",
          "STR012" in bag_with(set_numbering))

    def set_refmax(raw):
        raw["references"] = dict(raw.get("references") or {}, max_refs=3)

    check("references.max_refs 生效 → LEN002",
          "LEN002" in bag_with(set_refmax))


def test_issue_code_hygiene() -> None:
    """问题码必须"一码一义"：同一个码不能既是错误又是提示、也不能有两种含义。

    否则报告里同一个 code 会指向两件事，用户既没法回归对比也没法抑制。
    """
    print("\n== 问题码规范（一码一义）==")
    import collections
    import re as _re

    seen: dict[str, set] = collections.defaultdict(set)
    for p in sorted((ROOT / "scripts" / "sci_core").glob("*.py")):
        src = p.read_text(encoding="utf-8")
        for m in _re.finditer(
            r'bag\.(error|warn|info)\(\s*\n?\s*"([A-Z]{3}\d{3})",\s*(CAT_\w+),\s*\n?\s*(f?"[^"]*")',
            src,
        ):
            seen[m.group(2)].add((m.group(1), m.group(3), m.group(4)[:70]))
    dupes = {c: sorted(v) for c, v in seen.items() if len(v) > 1}
    check("同一问题码只有一个级别与含义", not dupes, str(dupes))
    check("问题码覆盖充分（≥70 个）", len(seen) >= 70, str(len(seen)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留中间产物")
    args = ap.parse_args()

    if OUT.exists() and not args.keep:
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    for fn in (test_latex_parse, test_latex_qc, test_latex_convert_roundtrip,
               test_docx_parse, test_docx_convert, test_pipeline,
               test_template_mode, test_template_archive, test_main_tex_pick,
               test_frontmatter_regression, test_springer_template,
               test_preamble_carry, test_email_backfill, test_cas_draft_style,
               test_package_trimming, test_missing_fields,
               test_rule_binding, test_issue_code_hygiene):
        try:
            fn()
        except Exception as exc:
            import traceback

            FAIL.append(f"{fn.__name__} 抛异常")
            print(f"  [FAIL] {fn.__name__} 抛异常：{type(exc).__name__}: {exc}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  ✗ {f}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
