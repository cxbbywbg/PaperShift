#!/usr/bin/env python3
"""端到端自检：两条链路（LaTeX / Word）各自跑通解析→转换→质检→差异→结果包。

用法：
    python tests/run_tests.py            # 跑全部
    python tests/run_tests.py --keep     # 保留中间产物

退出码 0 表示全部通过。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from sci_core import docx_parse, journal as J, latex_parse, refs as refs_mod  # noqa: E402
from sci_core.diff import diff_manuscripts, render_diff_md  # noqa: E402
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

    # ---- 包内多份候选（elsarticle kit 就是这样）：必须提醒 ----
    multi = _make_zip(zips / "multi.zip", {
        "kit/myjournal.cls": TEMPLATE_KIT / "myjournal.cls",
        "kit/elsarticle-template-num.tex": base,
        "kit/elsarticle-template-harv.tex": base.replace("myjournal-num", "myjournal-harv"),
    })
    res_m = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip-multi", cwd=ROOT,
                         make_pdf=False, log=Log(verbose=False), template=str(multi))
    check("模板压缩包：包内多份候选时提醒用户（CVT013）",
          "CVT013" in (Path(res_m["journal_dir"]) / "report.md").read_text(encoding="utf-8"))

    res_p = run_pipeline(LATEX_FIX, "", OUT / "tpl-zip-pick", cwd=ROOT, make_pdf=False,
                         log=Log(verbose=False),
                         template=f"{multi}::kit/elsarticle-template-num.tex")
    t_p = (Path(res_p["journal_dir"]) / "src" / "main.tex").read_text(encoding="utf-8")
    check("模板压缩包：zip::包内路径 可指定用哪一份模板",
          "\\bibliographystyle{myjournal-num}" in t_p
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
    J.ARCHIVE_CACHE_DIR = orig_cache


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
               test_template_mode, test_template_archive, test_rule_binding,
               test_issue_code_hygiene):
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
