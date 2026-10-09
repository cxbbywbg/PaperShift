"""流水线编排：一次运行产出完整转投结果包。

目录结构（对应需求文档 7.1）：

```
submission/
├── original/                 # 不可覆盖的原始论文
├── journals/
│   └── <journal_id>/
│       ├── src/              # 转换后的源文件（LaTeX 项目 / .docx）
│       ├── figures/          # Word 稿提取出的图片（便于做质量检查与独立投稿）
│       ├── preview.pdf       # PDF 预览（工具链可用时）
│       ├── report.md         # 质量检查报告
│       ├── diff.md           # 差异报告
│       └── conversion-log.md # 转换记录
└── manifest.json             # 运行清单（可复现）
```

同一篇论文可以反复对不同期刊运行，manifest 会累积记录，
原始稿 `original/` 永远不被覆盖。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from . import __version__, model as M
from . import docx_build, docx_parse, latex_build, latex_parse, refs as refs_mod
from .changes import ChangeLog
from .diff import conclusion_text, diff_manuscripts, render_diff_md
from .issues import IssueBag
from .journal import (
    JournalConfig,
    effective_rules,
    find_template_variants,
    from_template,
    load as load_journal,
    validate as validate_journal,
    with_template,
    with_variant,
)
from .latex_check import compile_project, pick_engine
from .qc import run_qc, summarize
from .report import (
    render_conversion_log,
    render_package_index,
    render_qc_report,
    tool_versions,
)
from .util import Log, dump_json, find_tool, probe_toolchain, run_cmd, sha256_file, sha256_tree, write_text


# --------------------------------------------------------------------------
# 输入识别与解析
# --------------------------------------------------------------------------


def resolve_input(path: str | Path, work_dir: Path, log: Log) -> tuple[str, Path, Path | None]:
    """返回 (格式, 实际项目根目录/文件, 解压目录或 None)。

    支持：目录（LaTeX 项目）、.zip（LaTeX 项目包或 docx 包）、.tex、.docx。
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"输入不存在：{p}")

    if p.is_dir():
        return M.FMT_LATEX, p, None

    suffix = p.suffix.lower()
    if suffix == ".zip":
        extract = work_dir / "_extracted"
        if extract.exists():
            shutil.rmtree(extract, ignore_errors=True)
        extract.mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(str(p), str(extract))
        log.step(f"已解压 {p.name} → {extract}")
        # 判断包内是 LaTeX 项目还是 Word
        texs = [x for x in extract.rglob("*.tex")]
        docxs = [x for x in extract.rglob("*.docx") if not x.name.startswith("~$")]
        if texs:
            return M.FMT_LATEX, extract, extract
        if docxs:
            return M.FMT_DOCX, docxs[0], extract
        raise ValueError(f"压缩包 {p.name} 中既没有 .tex 也没有 .docx")

    if suffix in (".tex", ".latex"):
        return M.FMT_LATEX, p.parent, None
    if suffix in (".docx", ".doc"):
        return M.FMT_DOCX, p, None
    raise ValueError(f"不支持的输入格式：{suffix}")


def parse_input(fmt: str, target: Path, log: Log, media_dir: Path | None = None) -> M.Manuscript:
    if fmt == M.FMT_LATEX:
        return latex_parse.parse_latex(target, log)
    return docx_parse.parse_docx(target, log, media_dir=media_dir)


# --------------------------------------------------------------------------
# PDF 预览
# --------------------------------------------------------------------------


def make_pdf_preview(fmt: str, main_file: Path, out_dir: Path, log: Log) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt == M.FMT_LATEX:
        if not pick_engine():
            return {
                "available": False,
                "reason": "未检测到 TeX 发行版（pdflatex / xelatex / lualatex / latexmk）",
                "pdf": None,
            }
        res = compile_project(main_file, out_dir, log=log)
        return {
            "available": True,
            "pdf": res.get("pdf"),
            "engine": res.get("engine"),
            "ok": res.get("ok"),
            "errors": res.get("errors", []),
            "undefined_refs": res.get("undefined_refs", []),
            "undefined_citations": res.get("undefined_citations", []),
            "overfull": res.get("overfull", []),
            "log_tail": res.get("log_tail", ""),
            "build_dir": res.get("build_dir"),
        }

    # Word → PDF
    soffice = find_tool("soffice") or find_tool("libreoffice")
    if soffice:
        r = run_cmd(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", str(out_dir), str(main_file)],
            timeout=300,
        )
        pdf = out_dir / (main_file.stem + ".pdf")
        return {
            "available": True,
            "pdf": str(pdf) if pdf.is_file() else None,
            "engine": Path(soffice).name,
            "ok": pdf.is_file(),
            "stdout": r.get("stdout", "")[-500:],
            "stderr": r.get("stderr", "")[-500:],
        }
    pandoc = find_tool("pandoc")
    if pandoc:
        pdf = out_dir / (main_file.stem + ".pdf")
        r = run_cmd([pandoc, str(main_file), "-o", str(pdf)], timeout=300)
        return {
            "available": True, "pdf": str(pdf) if pdf.is_file() else None,
            "engine": "pandoc", "ok": pdf.is_file(), "stderr": r.get("stderr", "")[-500:],
        }
    return {
        "available": False,
        "reason": "未检测到 LibreOffice(soffice) 或 pandoc，无法把 Word 转成 PDF 预览",
        "pdf": None,
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def _prepare_config(
    journal_id: str,
    template: str | None,
    fmt: str,
    cwd: Path,
    log: Log,
) -> tuple[JournalConfig, list]:
    """构造本次运行的期刊配置并校验。

    抽出来是为了多变体共用：模板包展开成多份版式时，每份都从同一个基配置
    派生（`with_variant`），不必重复加载。
    """
    if journal_id:
        cfg = load_journal(journal_id, cwd, log)
        log.step(f"期刊配置：{cfg.name}（状态：{cfg.status}）")
    elif template:
        cfg = from_template(template)
        log.step("未提供期刊配置：只按官方模板做结构移植，不做期刊规范判定")
    else:
        raise ValueError("必须提供 --journal 或 --template 之一")

    # --template 覆盖：只覆盖与本次输入格式对应的那一侧
    if template:
        cfg = with_template(cfg, template, fmt)
        log.step(f"模板覆盖（--template）：{template}")
    return cfg, validate_journal(cfg)


def run_pipeline(
    input_path: str | Path,
    journal_id: str = "",
    out_root: str | Path = "submission",
    cwd: str | Path | None = None,
    make_pdf: bool = True,
    log: Log | None = None,
    template: str | None = None,
) -> dict:
    """完整流程入口。

    模板压缩包里若含**同族版式**（Elsevier 的 cas-sc / cas-dc 单栏双栏、
    elsarticle 的 num / harv 引用样式），默认**每份各产出一份结果包**，
    目录名带版式后缀（`journals/template-only-sc/`、`...-dc/`）；只有一份
    时行为与单模板完全一致。
    """
    log = log or Log(verbose=True)
    cwd = Path(cwd) if cwd else Path.cwd()
    out_root = Path(out_root).resolve()

    # ---- 1. 输入与解析（多变体共享同一份解压结果）----
    work = out_root / "_work"
    work.mkdir(parents=True, exist_ok=True)
    fmt, target, _extract = resolve_input(input_path, work, log)
    log.step(f"输入格式：{fmt}，目标：{target}")

    # ---- 2. 期刊配置 ----
    cfg, cfg_problems = _prepare_config(journal_id, template, fmt, cwd, log)

    # ---- 3. 模板变体展开 ----
    targets: list[tuple[JournalConfig, str]] = [(cfg, "")]
    if template and fmt == M.FMT_LATEX:
        variants = find_template_variants(template, cfg, fmt, log)
        if len(variants) > 1:
            targets = [
                (with_variant(cfg, v.variant, v.path), v.variant) for v in variants
            ]

    # ---- 4. 逐个目标产出结果包 ----
    results = [
        _run_one_target(cfg_v, cfg_problems, fmt, target, out_root, make_pdf, log, label)
        for cfg_v, label in targets
    ]
    if len(results) == 1:
        return results[0]
    return _merge_variant_results(results, out_root)


def _merge_variant_results(results: list[dict], out_root: Path) -> dict:
    """把多份变体结果合成一个返回值：公共字段沿用第一份，另附全部变体。"""
    merged = dict(results[0])
    merged["multi"] = True
    merged["variants"] = results
    merged["variant_count"] = len(results)
    merged["out_root"] = str(out_root)
    return merged


def _run_one_target(
    cfg: JournalConfig,
    cfg_problems: list,
    fmt: str,
    target: Path,
    out_root: Path,
    make_pdf: bool,
    log: Log,
    variant: str = "",
) -> dict:
    """对一个目标（期刊配置，或模板包里的某个版式变体）跑完整流程。"""
    t0 = datetime.now()
    submission = out_root / "submission"
    work = out_root / "_work"

    jdir = submission / "journals" / cfg.id
    jdir.mkdir(parents=True, exist_ok=True)
    media_dir = jdir / "figures" if fmt == M.FMT_DOCX else None

    ms_before = parse_input(fmt, target, log, media_dir)
    log.step(
        f"原稿解析完成：{ms_before.stats()['sections']} 节 / "
        f"{ms_before.stats()['figures']} 图 / {ms_before.stats()['tables']} 表 / "
        f"{ms_before.stats()['equations']} 公式"
    )
    ms_before.references = refs_mod.load_references(ms_before, ms_before.main_file and Path(ms_before.main_file).parent or target, log)
    bib_keys = {r.key for r in ms_before.references if r.origin in ("bib", "ris")}

    # ---- 2. 保存不可覆盖的原稿 ----
    original_dir = submission / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    if fmt == M.FMT_LATEX:
        src_root = Path(ms_before.main_file).parent
        for p in sorted(src_root.rglob("*")):
            if not p.is_file():
                continue
            # 只按**相对路径**判断该不该跳过：项目本身可能就躺在
            # `out/_work/_extracted/` 里，拿绝对路径比对会把整份原稿都跳过，
            # 于是 original/ 一直是空的（而它本该是"不可覆盖的原始论文"）。
            relp = p.relative_to(src_root)
            if any(part in (".git", "__pycache__", "_work", "submission")
                   for part in relp.parts):
                continue
            tgt = original_dir / relp
            tgt.parent.mkdir(parents=True, exist_ok=True)
            if not tgt.exists():
                shutil.copy2(p, tgt)
        input_desc = f"LaTeX 项目（主文件 {Path(ms_before.main_file).name}）"
    else:
        tgt = original_dir / Path(ms_before.source_path).name
        if not tgt.exists():
            shutil.copy2(ms_before.source_path, tgt)
        input_desc = f"Word 文档（{Path(ms_before.source_path).name}）"

    # ---- 3. 基线质检（转换前）----
    bag_before = run_qc(ms_before, cfg, Path(ms_before.main_file).parent if fmt == M.FMT_LATEX else target,
                        None, log, bib_keys, None, stage="转换前")
    log.step(f"转换前质检：{summarize(bag_before)}")

    # ---- 4. 转换 ----
    changes = ChangeLog()
    conv_bag = IssueBag()
    if fmt == M.FMT_LATEX:
        src_out = jdir / "src"
        if src_out.exists():
            shutil.rmtree(src_out, ignore_errors=True)
        conv = latex_build.convert_latex(ms_before, cfg, src_out, changes, conv_bag, log)
        out_main = Path(conv["main_file"])
        out_root_dir = src_out
    else:
        conv = docx_build.convert_docx(ms_before, cfg, jdir, changes, conv_bag, log)
        out_main = Path(conv["main_file"])
        out_root_dir = jdir
    log.step(f"转换完成：{len(changes)} 处改动 → {out_main}")

    # ---- 5. 重新解析转换稿 ----
    # Word 稿需要把内嵌图片提取到临时目录才能做分辨率检查；这份提取物不放进结果包
    after_media = (work / "_media_formatted") if fmt == M.FMT_DOCX else None
    if after_media and after_media.exists():
        shutil.rmtree(after_media, ignore_errors=True)
    ms_after = parse_input(fmt, out_main if fmt == M.FMT_DOCX else out_root_dir, log, after_media)
    ms_after.references = refs_mod.load_references(
        ms_after, Path(ms_after.main_file).parent, log
    )

    # ---- 6. PDF 预览 + 编译检查 ----
    pdf_info: dict = {"available": False, "reason": "已跳过"}
    compile_result = None
    if make_pdf:
        # 编译/转换的中间产物放临时工作目录，不进结果包——否则工具链不可用时
        # 结果包里会留下一个空的 preview/ 目录，跟 README 里写的 preview.pdf 对不上。
        scratch = work / "_preview"
        pdf_info = make_pdf_preview(fmt, out_main, scratch, log)
        if fmt == M.FMT_LATEX and pdf_info.get("available"):
            compile_result = {
                "available": True,
                "engine": pdf_info.get("engine"),
                "passes": 2,
                "pdf": pdf_info.get("pdf"),
                "errors": pdf_info.get("errors", []),
                "undefined_refs": pdf_info.get("undefined_refs", []),
                "undefined_citations": pdf_info.get("undefined_citations", []),
                "overfull": pdf_info.get("overfull", []),
                "log_tail": pdf_info.get("log_tail", ""),
                "ok": pdf_info.get("ok"),
            }
        elif fmt == M.FMT_LATEX:
            compile_result = {"available": False, "reason": pdf_info.get("reason", ""), "errors": [], "overfull": [], "log_tail": ""}
        if pdf_info.get("pdf"):
            src_pdf = Path(pdf_info["pdf"])
            final_pdf = jdir / "preview.pdf"
            try:
                shutil.copy2(src_pdf, final_pdf)
                pdf_info["preview"] = str(final_pdf)
            except Exception as exc:
                log.warn(f"复制 PDF 预览失败：{exc}")

    # ---- 7. 转换后质检 ----
    qc_root = out_root_dir
    bag_after = run_qc(ms_after, cfg, qc_root, None, log, bib_keys, compile_result, stage="转换后")
    bag_after.extend(conv_bag.items)
    log.step(f"转换后质检：{summarize(bag_after)}")

    # ---- 8. 差异 ----
    d = diff_manuscripts(ms_before, ms_after, "原稿", f"转换稿({cfg.id})")

    # ---- 9. 写报告 ----
    cfg_hash = sha256_file(cfg.path) if cfg.path and Path(cfg.path).is_file() else ""
    try:
        input_hash = sha256_tree(Path(ms_before.main_file).parent) if fmt == M.FMT_LATEX else sha256_file(ms_before.source_path)
    except Exception:
        input_hash = ""
    tools = tool_versions()
    if compile_result and compile_result.get("engine"):
        tools["latex_engine_used"] = compile_result["engine"]

    extra = {"config_hash": cfg_hash, "input_hash": input_hash, "tools": tools,
             "journal_hint": f"配置问题 {len(cfg_problems)} 项" if cfg_problems else ""}

    report_md = render_qc_report(ms_after, cfg, bag_after, "转换后", extra, qc_root)
    baseline_md = render_qc_report(ms_before, cfg, bag_before, "转换前（基线）", {}, None)
    diff_md = render_diff_md(d)
    log_md = render_conversion_log(changes, cfg, ms_before, extra)

    write_text(jdir / "report.md", report_md)
    write_text(jdir / "report-baseline.md", baseline_md)
    write_text(jdir / "diff.md", diff_md)
    write_text(jdir / "conversion-log.md", log_md)

    # 结构化结果（供程序消费）
    result_json = {
        "journal": cfg.id,
        "format": fmt,
        "variant": variant,
        "input": input_desc,
        "main_file": str(out_main),
        "changes": changes.to_list(),
        "qc_before": summarize(bag_before),
        "qc_after": summarize(bag_after),
        "issues": bag_after.to_list(),
        "diff": {k: v for k, v in d.items() if k != "sections"} | {
            "sections_summary": {
                "added": d["sections"]["added"],
                "removed": d["sections"]["removed"],
                "changed_count": len(d["sections"]["changed"]),
                "unchanged_count": d["sections"]["unchanged_count"],
            },
            "identical": d["identical"],
        },
        "pdf": {k: v for k, v in pdf_info.items() if k != "log_tail"},
        "manifest": {
            "generated_at": t0.isoformat(timespec="seconds"),
            "duration_sec": round((datetime.now() - t0).total_seconds(), 1),
        },
    }
    dump_json(result_json, jdir / "result.json")

    # 结果包索引
    files = {
        "src/": "转换后的论文源文件",
        "figures/": "从 Word 稿提取的图片（供质量检查与独立投稿）" if fmt == M.FMT_DOCX else "（LaTeX 项目图片在 src/ 内）",
        "preview.pdf": "PDF 预览" if pdf_info.get("preview") else "（未能生成：工具链不可用）",
        "report.md": "质量检查报告（转换后）",
        "report-baseline.md": "质量检查报告（转换前基线）",
        "diff.md": "差异报告",
        "conversion-log.md": "转换记录",
        "result.json": "结构化结果（供程序消费）",
    }
    write_text(jdir / "README.md", render_package_index(cfg, ms_before, files, bag_before, bag_after, changes, d))

    # ---- 10. manifest.json（累积） ----
    manifest_path = submission / "manifest.json"
    manifest = {"schema": 1, "tool": "sci-paper-reformatter", "version": __version__, "entries": []}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.setdefault("entries", [])
        except Exception:
            pass
    entry = {
        "journal": cfg.id,
        "journal_name": cfg.name,
        "journal_config": cfg.path,
        "journal_config_hash": cfg_hash,
        "journal_status": cfg.status,
        "input": str(Path(ms_before.source_path).resolve()),
        "input_format": fmt,
        "input_hash": input_hash,
        "output_dir": str(jdir),
        "main_file": str(out_main),
        "generated_at": t0.isoformat(timespec="seconds"),
        "tool_versions": tools,
        "conversion_params": {
            "docx_base": cfg.docx().get("base", "safe"),
            "latex_documentclass": cfg.latex().get("documentclass", ""),
            "ref_style": cfg.ref_style,
        },
        "qc_after": summarize(bag_after),
        "changes": len(changes),
        "diff_identical": d["identical"],
        "diff_conclusion": conclusion_text(d),
        "pdf_preview": pdf_info.get("preview"),
    }
    manifest["entries"] = [e for e in manifest["entries"] if not (e.get("journal") == cfg.id and e.get("input") == entry["input"])]
    manifest["entries"].append(entry)
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    dump_json(manifest, manifest_path)

    log.step(f"结果包已生成：{jdir}")
    return {
        "submission_dir": str(submission),
        "journal_dir": str(jdir),
        "journal_id": cfg.id,
        "journal_name": cfg.name,
        "template_used": (cfg.latex().get("template") if fmt == M.FMT_LATEX else cfg.docx().get("template")) or "",
        "main_file": str(out_main),
        "manifest": str(manifest_path),
        "qc_before": summarize(bag_before),
        "qc_after": summarize(bag_after),
        "changes": len(changes),
        "needs_author": len(changes.needs_author()),
        "diff_identical": d["identical"],
        "diff_conclusion": conclusion_text(d),
        "pdf": pdf_info,
        "reports": {
            "report": str(jdir / "report.md"),
            "baseline": str(jdir / "report-baseline.md"),
            "diff": str(jdir / "diff.md"),
            "log": str(jdir / "conversion-log.md"),
            "result": str(jdir / "result.json"),
        },
        "issues": bag_after.to_list(),
        "variant": variant,
    }
