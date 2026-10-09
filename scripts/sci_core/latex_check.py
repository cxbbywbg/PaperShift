"""LaTeX 检查：静态检查 + 真实编译（有 TeX 发行版时）。

本机可能没有 TeX 发行版，所以检查分两层：

* **静态检查**（永远可用）—— 括号与环境配对、未定义引用、缺失图片、
  重复 label、孤儿图表、caption 缺失、参考文献文件缺失。
* **真实编译**（检测到 pdflatex/xelatex 时）—— 跑 2~3 遍 + bibtex/biber，
  解析 .log 得到错误、未定义引用、Overfull 盒子等。

编译不可用时，明确报告「能力降级」，而不是假装通过。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import model as M
from .issues import (
    CAT_FILE,
    CAT_LATEX,
    CAT_REFERENCE,
    CAT_STRUCTURE,
    IssueBag,
    Location,
)
from .latex_parse import (
    GRAPHIC_EXTS,
    LatexParser,
    flatten_project,
    scan_commands,
    strip_comments,
)
from .util import Log, find_tool, rel, run_cmd

ENGINE_ORDER = ["latexmk", "pdflatex", "xelatex", "lualatex"]


# --------------------------------------------------------------------------
# 资源解析
# --------------------------------------------------------------------------


def resolve_graphic(name: str, root: Path, graphics_paths: list[str], from_file: str = "") -> Path | None:
    """按 \\graphicspath 与常见扩展名解析 \\includegraphics 的路径。"""
    raw = name.strip().replace("\\", "/")
    if not raw:
        return None
    bases: list[Path] = []
    if from_file:
        bases.append((root / from_file).parent)
    for gp in graphics_paths:
        bases.append(root / gp.strip().strip("/"))
    bases.append(root)
    exts = [""] + GRAPHIC_EXTS
    for b in bases:
        for ext in exts:
            cand = b / (raw + ext) if ext else b / raw
            if cand.is_file():
                return cand
    return None


# --------------------------------------------------------------------------
# 静态检查
# --------------------------------------------------------------------------


def static_check(root: str | Path, ms: M.Manuscript, known_bib_keys: set[str] | None = None,
                 bag: IssueBag | None = None, log: Log | None = None) -> IssueBag:
    root = Path(root)
    if bag is None:
        bag = IssueBag()
    log = log or Log(verbose=False)
    known_bib_keys = known_bib_keys or set()

    main = Path(ms.main_file)
    flat = flatten_project(main, log)
    body = strip_comments(flat.text)
    graphics_paths = ms.metadata.doc_meta.get("graphicspath", []) or []

    def loc(file: str, line: int, section: str = "", obj: str = "") -> Location:
        return Location(file=file, line=line, section=section, object=obj)

    def span_loc(span: M.Span | None, section: str = "", obj: str = "") -> Location:
        if not span:
            return Location(section=section, object=obj)
        return Location(file=span.file, line=span.line, section=section, object=obj)

    # ---- 1. 花括号配平 ----
    stripped = re.sub(r"\\[{}]", "", body)
    opens = stripped.count("{")
    closes = stripped.count("}")
    if opens != closes:
        bag.error(
            "LTX001", CAT_LATEX,
            f"花括号不配平：{opens} 个 '{{' vs {closes} 个 '}}'",
            location=Location(file=rel(main, root)),
            suggestion="检查是否有未闭合的宏参数或漏写的 '}'。",
        )

    # ---- 2. \\begin / \\end 配对 ----
    parser = LatexParser(log)
    envs = parser._scan_environments(body)
    begin_names = re.findall(r"\\begin\s*\{([^}]*)\}", body)
    end_names = re.findall(r"\\end\s*\{([^}]*)\}", body)
    from collections import Counter

    cb, ce = Counter(begin_names), Counter(end_names)
    for name in sorted(set(cb) | set(ce)):
        if cb[name] != ce[name]:
            bag.error(
                "LTX002", CAT_LATEX,
                f"环境 {name} 的 \\begin 与 \\end 数量不一致（begin={cb[name]}, end={ce[name]}）",
                location=Location(file=rel(main, root)),
                suggestion="补齐缺失的 \\begin 或 \\end。",
            )

    # ---- 3. documentclass ----
    if not ms.documentclass:
        bag.error(
            "LTX003", CAT_LATEX, "未找到 \\documentclass",
            location=Location(file=rel(main, root)),
            suggestion="确认传入的是主 .tex 文件，或补上 \\documentclass。",
        )

    # ---- 4. 重复 label ----
    label_owner: dict[str, list[str]] = {}
    for obj in (*ms.figures, *ms.tables, *ms.equations):
        if obj.label:
            label_owner.setdefault(obj.label, []).append(obj.id)
    for s in ms.sections:
        for lb in s.labels:
            label_owner.setdefault(lb, []).append(s.id)
    for lb, owners in sorted(label_owner.items()):
        if len(owners) > 1:
            bag.error(
                "LTX004", CAT_LATEX, f"重复的 \\label{{{lb}}}，被 {len(owners)} 个对象共用",
                location=Location(object=", ".join(owners)),
                suggestion="每个 label 必须唯一，否则 \\ref 会指向错误的对象。",
            )

    # ---- 5. 未定义的 \\ref / \\eqref 目标 ----
    known = set(label_owner)
    known |= {s.number for s in ms.sections if s.number}
    known |= {f"fig:{f.number}" for f in ms.figures}
    known |= {f"tab:{t.number}" for t in ms.tables}
    known |= {f"eq:{e.number}" for e in ms.equations if e.number}
    for x in ms.crossrefs:
        t = x.get("target", "")
        if t and t not in known:
            sp = x.get("span") or {}
            bag.error(
                "LTX005", CAT_STRUCTURE,
                f"\\{x.get('command')}{{{t}}} 指向不存在的标签",
                location=Location(
                    file=sp.get("file", ""), line=sp.get("line", 0),
                    section=x.get("section", ""), object=t,
                ),
                suggestion="检查该 label 是否拼写错误，或对应对象是否被删除。",
            )

    # ---- 6. 图表公式的「是否被引用 / 是否有题注」由 assets.py 统一检查，
    #         因为 Word 与 LaTeX 两条链路需要同一套判据。这里不再重复。 ----

    # ---- 7. 缺失的图片文件 ----
    for f in ms.figures:
        for g in f.files:
            if resolve_graphic(g, root, graphics_paths, f.span.file if f.span else ""):
                continue
            bag.error(
                "LTX020", CAT_FILE,
                f"图片文件缺失：{g}",
                location=span_loc(f.span, f.section or "", f.id),
                suggestion="确认文件是否漏打包，或路径与 \\graphicspath 是否一致。",
            )
    # 未被任何 figure 引用的图片文件
    used = set()
    for f in ms.figures:
        for g in f.files:
            p = resolve_graphic(g, root, graphics_paths, f.span.file if f.span else "")
            if p:
                used.add(p.resolve())
    for ext in (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".tif", ".tiff"):
        for p in sorted(root.rglob(f"*{ext}")):
            try:
                parts = p.relative_to(root).parts
            except ValueError:
                parts = p.parts
            if any(part.startswith(".") for part in parts):
                continue
            if p.resolve() not in used:
                bag.info(
                    "LTX021", CAT_FILE,
                    f"图片文件未被任何 figure 引用：{rel(p, root)}",
                    location=Location(file=rel(p, root)),
                )

    # ---- 8. 引用键存在性由 refs.py 统一检查（Word/LaTeX 共用同一判据），此处不重复 ----

    # ---- 9. 行内数学 $ 配平 ----
    dollar_lines = []
    for idx, line in enumerate(body.split("\n"), 1):
        clean = re.sub(r"\\\$", "", line)
        if clean.count("$") % 2 == 1:
            dollar_lines.append((idx, line.strip()[:80]))
    if dollar_lines:
        f, ln = flat.locate(dollar_lines[0][0])
        bag.error(
            "LTX030", CAT_LATEX,
            f"疑似未闭合的行内公式 '$'，共 {len(dollar_lines)} 处",
            location=Location(file=rel(f, root) if f else "", line=ln),
            evidence="；".join(f"{rel(flat.locate(i)[0], root)}:{flat.locate(i)[1]} {t}" for i, t in dollar_lines[:3]),
            suggestion="行内公式必须成对使用 $...$，跨行公式请改用 equation 环境。",
        )

    # ---- 10. 参考文献文件 ----
    # 注意：文献库文件是否缺失由 qc.check_file_integrity 统一以 REF002 上报，
    # 这里不再重复报一遍（同一条问题两个码会让报告看起来问题翻倍）。
    if ms.citations and not ms.bib_resources and not ms.references:
        bag.warn(
            "REF003", CAT_REFERENCE,
            f"正文有 {len(ms.citations)} 处引用，但没有找到 \\bibliography 或 \\bibitem",
            location=Location(file=rel(main, root)),
        )

    # ---- 11. 章节结构 ----
    if ms.sections and not any(s.heading.lower().startswith("introduction") for s in ms.sections):
        bag.info(
            "STR003", CAT_STRUCTURE, "未找到标题为 Introduction/引言 的一级章节",
            location=Location(file=rel(main, root)),
        )
    levels = [s.level for s in ms.sections]
    for i in range(1, len(levels)):
        if levels[i] - levels[i - 1] > 1:
            bag.warn(
                "STR002", CAT_STRUCTURE,
                f"标题层级跳跃：{ms.sections[i-1].heading}（L{levels[i-1]}）→ {ms.sections[i].heading}（L{levels[i]}）",
                location=span_loc(ms.sections[i].span, ms.sections[i].id),
                suggestion="多数期刊要求章节层级连续，不要跳级。",
            )

    # ---- 12. 自定义宏提示 ----
    if ms.custom_macros:
        bag.info(
            "LTX040", CAT_LATEX,
            f"检测到 {len(ms.custom_macros)} 个自定义宏，换模板时可能不兼容",
            location=Location(file=rel(main, root)),
            evidence="；".join(m[:40] for m in ms.custom_macros[:4]),
            suggestion="若目标模板未定义这些宏，需要在导言区保留定义或改为标准命令。",
        )

    return bag


# --------------------------------------------------------------------------
# 真实编译
# --------------------------------------------------------------------------

_ERR_RE = re.compile(r"^!\s*(.+)$", re.M)
_UNDEF_REF_RE = re.compile(r"LaTeX Warning: Reference `([^']+)' on page (\S+) undefined")
_UNDEF_CITE_RE = re.compile(r"(?:LaTeX|Package \w+) Warning: Citation `([^']+)'[^u]*undefined")
_OVERFULL_RE = re.compile(r"(Overfull|Underfull) \\([hv])box \(([^)]*)\)(?: in paragraph at lines (\d+)--(\d+))?")
_MISSING_FILE_RE = re.compile(r"! LaTeX Error: File `([^']+)' not found")
_PKG_WARN_RE = re.compile(r"^(?:LaTeX|Package \w+|Class \w+) Warning: (.+)$", re.M)


def pick_engine() -> str | None:
    for e in ENGINE_ORDER:
        if find_tool(e):
            return e
    return None


def compile_project(
    main_file: str | Path,
    out_dir: str | Path,
    engine: str | None = None,
    runs: int = 2,
    use_bib: bool = True,
    timeout: int = 240,
    log: Log | None = None,
) -> dict:
    """在输出目录里编译 LaTeX 项目（会把整个项目目录复制过去）。

    返回 dict：ok / engine / passes / pdf / errors / warnings / overfull / log_tail
    """
    log = log or Log(verbose=False)
    main = Path(main_file).resolve()
    src_root = main.parent
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    engine = engine or pick_engine()
    if not engine:
        return {
            "ok": False,
            "available": False,
            "engine": None,
            "reason": "未检测到 TeX 发行版（pdflatex / xelatex / lualatex / latexmk）",
            "errors": [], "warnings": [], "overfull": [], "pdf": None, "log_tail": "",
        }

    # 复制项目（保留目录结构），避免污染原稿
    build = out / "_build"
    if build.exists():
        shutil.rmtree(build, ignore_errors=True)
    shutil.copytree(src_root, build)
    target = build / main.name

    cmd_base = [engine]
    if engine == "latexmk":
        cmd = [engine, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", target.name]
    else:
        cmd = [engine, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", target.name]

    passes = []
    for i in range(max(1, runs)):
        r = run_cmd(cmd, cwd=build, timeout=timeout)
        passes.append(r)
        if i == 0 and use_bib:
            tex_stem = target.stem
            for tool in ("biber", "bibtex"):
                exe = find_tool(tool)
                if not exe:
                    continue
                if tool == "biber":
                    r2 = run_cmd([exe, tex_stem], cwd=build, timeout=timeout)
                else:
                    r2 = run_cmd([exe, tex_stem], cwd=build, timeout=timeout)
                if r2["returncode"] not in (0, 2):
                    continue
                break

    log_file = build / f"{target.stem}.log"
    log_text = ""
    if log_file.is_file():
        try:
            log_text = log_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            log_text = ""
    pdf = build / f"{target.stem}.pdf"

    errors = [m.group(1).strip() for m in _ERR_RE.finditer(log_text)]
    undef_refs = _UNDEF_REF_RE.findall(log_text)
    undef_cites = _UNDEF_CITE_RE.findall(log_text)
    overfull = _OVERFULL_RE.findall(log_text)
    missing_files = _MISSING_FILE_RE.findall(log_text)

    # 把 PDF 拷到输出根，方便预览
    pdf_out = None
    if pdf.is_file():
        pdf_out = out / pdf.name
        shutil.copy2(pdf, pdf_out)

    return {
        "ok": pdf.is_file() and not errors,
        "available": True,
        "engine": engine,
        "passes": len(passes),
        "returncodes": [p["returncode"] for p in passes],
        "pdf": str(pdf_out) if pdf_out else None,
        "errors": errors,
        "undefined_refs": sorted({x[0] for x in undef_refs}),
        "undefined_citations": sorted(set(undef_cites)),
        "missing_files": sorted(set(missing_files)),
        "overfull": [{"type": o[0], "box": o[1], "amount": o[2], "lines": f"{o[3]}-{o[4]}" if o[3] else ""} for o in overfull],
        "log_tail": "\n".join(log_text.strip().split("\n")[-40:]),
        "build_dir": str(build),
    }


def report_compile(result: dict, bag: IssueBag, root: str | Path, main_rel: str) -> None:
    """把编译结果转成 Issue。"""
    if not result.get("available"):
        bag.info(
            "LTX100", CAT_LATEX,
            f"未执行编译：{result.get('reason')}",
            location=Location(file=main_rel),
            suggestion=(
                "静态检查已完成。若需要 PDF 预览与真实的编译错误/警告，"
                "请安装 TeX 发行版（TeX Live 或 MiKTeX）后重跑。"
            ),
        )
        return
    for e in result.get("errors", []):
        bag.error("LTX101", CAT_LATEX, f"编译错误：{e}", location=Location(file=main_rel))
    for f in result.get("missing_files", []):
        bag.error("LTX102", CAT_LATEX, f"编译期文件缺失：{f}", location=Location(file=main_rel))
    for r in result.get("undefined_refs", []):
        bag.error(
            "LTX103", CAT_LATEX, f"未定义的交叉引用：{r}",
            location=Location(file=main_rel),
            suggestion="编译日志提示该 \\ref 目标不存在。",
        )
    for c in result.get("undefined_citations", []):
        bag.error(
            "LTX104", CAT_REFERENCE, f"未定义的引用：{c}",
            location=Location(file=main_rel),
            suggestion="文献库中没有该键，或需要再跑一遍 bibtex/biber。",
        )
    for o in result.get("overfull", []):
        amount = o["amount"]
        if o["type"] == "Overfull":
            bag.warn(
                "LTX105", CAT_LATEX,
                f"内容溢出页面（{o['box']}box {amount}）",
                location=Location(file=main_rel),
                evidence=o.get("lines", ""),
                suggestion="常见于过宽的表格或无法断行的公式，投稿前应消除 Overfull。",
            )
    if result.get("ok"):
        bag.info(
            "LTX110", CAT_LATEX,
            f"编译成功（{result['engine']}，{result['passes']} 遍），已生成 PDF 预览",
            location=Location(file=main_rel),
        )
