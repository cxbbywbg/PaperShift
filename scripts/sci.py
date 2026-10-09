#!/usr/bin/env python3
"""SCI Paper Reformatter —— 命令行入口。

常用：

    python sci.py doctor
    python sci.py journals list
    python sci.py inspect  paper/main.tex
    python sci.py run      paper/main.tex --journal ieee-trans -o out
    python sci.py run      paper/manuscript.docx --journal elsevier-generic -o out
    python sci.py qc       paper/manuscript.docx --journal ieee-trans
    python sci.py diff     paper/main.tex out/submission/journals/ieee-trans/src

所有子命令在出错时返回非 0 退出码，方便脚本串联。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from sci_core import __version__  # noqa: E402
from sci_core import docx_parse, journal as J, latex_parse, refs as refs_mod  # noqa: E402
from sci_core.issues import LEVEL_LABEL, IssueBag  # noqa: E402
from sci_core.qc import run_qc, summarize  # noqa: E402
from sci_core.util import Log, check_deps, dump_json, probe_toolchain, rel, write_text  # noqa: E402


def _log(args) -> Log:
    return Log(verbose=not getattr(args, "quiet", False))


def _resolve_cfg(args, fmt: str, log: Log):
    """按 --journal / --template 解析期刊配置。

    * 只给 --journal        → 用期刊配置（默认模式）
    * 只给 --template       → 用最小配置，只做结构移植，不做期刊规范判定
    * 两个都给              → 期刊配置提供规则，--template 覆盖模板路径
    """
    tpl = getattr(args, "template", None)
    jid = getattr(args, "journal", None)
    if jid and tpl:
        return J.with_template(J.load(jid, args.cwd, log), tpl, fmt)
    if jid:
        return J.load(jid, args.cwd, log)
    if tpl:
        return J.from_template(tpl, fmt)
    raise ValueError("必须提供 --journal 或 --template 之一")


# --------------------------------------------------------------------------


def cmd_doctor(args) -> int:
    print(f"sci-paper-reformatter {__version__}")
    print(f"python: {sys.version.split()[0]}  ({sys.executable})")
    print()
    deps = check_deps()
    print("依赖：")
    print(f"  必需包缺失: {deps['missing_required'] or '无'}")
    print(f"  可选包缺失: {deps['missing_optional'] or '无'}")
    tc = probe_toolchain()
    print()
    print("工具链：")
    for k, v in tc["latex"].items():
        print(f"  {k:10s} {v or '(未检测到)'}")
    for k, v in tc["office"].items():
        print(f"  {k:10s} {v or '(未检测到)'}")
    print()
    print("能力矩阵：")
    print(f"  LaTeX 真实编译 + PDF 预览 : {'可用' if tc['latex_any'] else '不可用（将降级为静态检查）'}")
    print(f"  Word → PDF 预览           : {'可用' if tc['docx_to_pdf'] else '不可用（将跳过 PDF 预览）'}")
    print(f"  LaTeX 解析 / 转换         : 可用")
    print(f"  Word 解析 / 转换          : 可用")
    print()
    js = J.list_journals(args.cwd)
    print(f"期刊配置库（{len(js)} 个）：")
    for j in js:
        print(f"  {j['id']:22s} {j['status']:20s} {j['name']}")
    if not tc["latex_any"]:
        print()
        print("提示：安装 TeX Live 或 MiKTeX 后，`run` 会自动启用真实编译与 PDF 预览。")
    return 0 if deps["ok"] else 2


def cmd_inspect(args) -> int:
    log = _log(args)
    from sci_core.pipeline import parse_input, resolve_input

    work = Path(args.work or ".sci-trans-work")
    fmt, target, _ = resolve_input(args.input, work, log)
    ms = parse_input(fmt, target, log, None)
    if fmt == "latex":
        ms.references = refs_mod.load_references(ms, Path(ms.main_file).parent, log)
    data = ms.to_dict()
    if args.output:
        dump_json(data, args.output)
        print(f"已写出模型：{args.output}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_journals(args) -> int:
    if args.action == "list":
        js = J.list_journals(args.cwd)
        if args.json:
            print(json.dumps(js, ensure_ascii=False, indent=2))
            return 0
        print(f"{'id':24s} {'状态':22s} {'栏':>2s} {'引用':10s} 名称")
        for j in js:
            print(f"{j['id']:24s} {j['status']:22s} {j['columns']:>2} {j['ref_style']:10s} {j['name']}")
        return 0
    if args.action == "show":
        cfg = J.load(args.journal_id, args.cwd)
        if args.json:
            print(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(cfg.to_yaml())
        return 0
    if args.action == "validate":
        ids = [args.journal_id] if args.journal_id else [j["id"] for j in J.list_journals(args.cwd)]
        bad = 0
        for jid in ids:
            cfg = J.load(jid, args.cwd)
            probs = J.validate(cfg)
            errs = [p for p in probs if p["level"] == "error"]
            bad += len(errs)
            print(f"=== {jid}（{cfg.name}）")
            for p in probs:
                print(f"   [{p['level']:7s}] {p['field']}: {p['message']}")
            if not probs:
                print("   无问题")
        return 1 if bad else 0
    if args.action == "new":
        p = J.scaffold(args.journal_id, args.name or "", args.cwd, args.output)
        print(f"已生成配置骨架：{p}")
        print("请逐条核对官方 Guide for Authors，补齐 rule_sources，再把 status 改为 verified。")
        return 0
    if args.action == "path":
        print(f"内置: {J.builtin_dir()}")
        print(f"用户级: {J.user_dir()}")
        print(f"项目级: {J.project_dir(args.cwd)}")
        return 0
    return 1


def cmd_qc(args) -> int:
    log = _log(args)
    from sci_core.pipeline import parse_input, resolve_input

    cfg = J.load(args.journal, args.cwd, log)
    work = Path(args.work or ".sci-trans-work")
    fmt, target, _ = resolve_input(args.input, work, log)
    media = (work / "_media") if fmt == "docx" else None
    ms = parse_input(fmt, target, log, media)
    root = Path(ms.main_file).parent if fmt == "latex" else Path(ms.source_path)
    ms.references = refs_mod.load_references(ms, root, log)
    bib_keys = {r.key for r in ms.references if r.origin in ("bib", "ris")}

    bag = run_qc(ms, cfg, root, None, log, bib_keys, None, stage=args.stage)
    summary = summarize(bag)
    from sci_core.report import render_qc_report

    md = render_qc_report(ms, cfg, bag, args.stage, {}, root)
    if args.output:
        write_text(args.output, md)
        print(f"已写出报告：{args.output}")
    elif args.markdown:
        print(md)
    else:
        print(f"检查结论：{summary}")
        for i in bag.sorted():
            loc = i.location.label()
            print(f"  [{LEVEL_LABEL[i.level]}] {i.code} {i.message}" + (f"  <{loc}>" if loc else ""))
    if args.json:
        dump_json({"summary": summary, "issues": bag.to_list()}, args.json)
        print(f"已写出 JSON：{args.json}")
    return 0 if summary["errors"] == 0 else 1


def cmd_diff(args) -> int:
    log = _log(args)
    from sci_core.diff import diff_manuscripts, render_diff_md
    from sci_core.pipeline import parse_input, resolve_input

    work = Path(args.work or ".sci-trans-work")
    out = []
    for p in (args.before, args.after):
        fmt, target, _ = resolve_input(p, work, log)
        ms = parse_input(fmt, target, log, None)
        if fmt == "latex":
            ms.references = refs_mod.load_references(ms, Path(ms.main_file).parent, log)
        out.append(ms)
    d = diff_manuscripts(out[0], out[1], "转换前", "转换后")
    md = render_diff_md(d)
    if args.output:
        write_text(args.output, md)
        print(f"已写出差异报告：{args.output}")
    else:
        print(md)
    return 0


def cmd_convert(args) -> int:
    """只做转换，不做质检/报告/结果包。"""
    log = _log(args)
    from sci_core import docx_build, latex_build
    from sci_core.changes import ChangeLog
    from sci_core.issues import IssueBag
    from sci_core.pipeline import parse_input, resolve_input

    work = Path(args.work or ".sci-trans-work")
    fmt, target, _ = resolve_input(args.input, work, log)
    cfg = _resolve_cfg(args, fmt, log)
    media = (Path(args.output or "out") / "figures") if fmt == "docx" else None
    ms = parse_input(fmt, target, log, media)
    ms.references = refs_mod.load_references(
        ms, Path(ms.main_file).parent if fmt == "latex" else Path(ms.source_path), log
    )
    ch, bag = ChangeLog(), IssueBag()
    if fmt == "latex":
        res = latex_build.convert_latex(ms, cfg, args.output, ch, bag, log)
    else:
        res = docx_build.convert_docx(ms, cfg, args.output, ch, bag, log)
    print(f"转换完成：{res['main_file']}")
    print(f"改动 {len(ch)} 处，其中需作者确认 {len(ch.needs_author())} 处")
    if args.verbose_changes:
        for c in ch.items:
            print("  -", c.line_md().replace("\n", "\n    "))
    return 0


def cmd_run(args) -> int:
    log = _log(args)
    from sci_core.pipeline import run_pipeline

    res = run_pipeline(
        args.input, args.journal or "", args.output, args.cwd,
        make_pdf=not args.no_pdf, log=log,
        template=getattr(args, "template", None),
    )
    print()
    print("=" * 68)
    print(f"转投结果包：{res['submission_dir']}")
    print(f"目标期刊  ：{res.get('journal_id') or args.journal or '（仅官方模板）'}")
    print(f"转换后主文件：{res['main_file']}")
    print(f"质检（转换前）：{res['qc_before']}")
    print(f"质检（转换后）：{res['qc_after']}")
    print(f"改动        ：{res['changes']} 处（需作者确认 {res['needs_author']} 处）")
    print(f"内容差异    ：{res.get('diff_conclusion') or ('无（仅格式变化）' if res['diff_identical'] else '有，见 diff.md')}")
    pdf = res.get("pdf") or {}
    if pdf.get("preview"):
        print(f"PDF 预览    ：{pdf['preview']}")
    else:
        print(f"PDF 预览    ：未生成（{pdf.get('reason', '工具链不可用')}）")
    print(f"报告        ：{res['reports']['report']}")
    print(f"              {res['reports']['diff']}")
    print(f"              {res['reports']['log']}")
    print("=" * 68)
    return 0 if res["qc_after"]["errors"] == 0 else 1


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sci.py",
        description="SCI Paper Reformatter —— SCI 论文跨期刊转投的格式转换与投稿适配工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--version", action="version", version=f"sci-paper-reformatter {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--cwd", default=None, help="工作目录（用于查找项目级期刊配置）")
        sp.add_argument("--work", default=None, help="临时工作目录")
        sp.add_argument("--quiet", action="store_true", help="不输出进度信息")

    d = sub.add_parser("doctor", help="自检：依赖、工具链、期刊配置库")
    add_common(d)
    d.set_defaults(func=cmd_doctor)

    i = sub.add_parser("inspect", help="解析论文并输出统一内容模型（JSON）")
    i.add_argument("input")
    i.add_argument("-o", "--output", help="输出 JSON 文件")
    add_common(i)
    i.set_defaults(func=cmd_inspect)

    j = sub.add_parser("journals", help="期刊配置库管理")
    j.add_argument("action", choices=["list", "show", "validate", "new", "path"])
    j.add_argument("journal_id", nargs="?", help="期刊 id")
    j.add_argument("--name", help="新建配置时的期刊名称")
    j.add_argument("-o", "--output", help="新建配置的输出路径")
    j.add_argument("--json", action="store_true")
    add_common(j)
    j.set_defaults(func=cmd_journals)

    c = sub.add_parser("convert", help="只做格式转换（不含质检与结果包）")
    c.add_argument("input")
    c.add_argument("--journal", help="期刊配置 id（与 --template 至少给一个）")
    c.add_argument("--template", help="官方模板文件：LaTeX 用 .tex 主文件，Word 用 .docx")
    c.add_argument("-o", "--output", required=True)
    c.add_argument("-v", "--verbose-changes", action="store_true")
    add_common(c)
    c.set_defaults(func=cmd_convert)

    q = sub.add_parser("qc", help="对稿件做质量检查")
    q.add_argument("input")
    q.add_argument("--journal", required=True)
    q.add_argument("-o", "--output", help="写出 Markdown 报告")
    q.add_argument("--json", help="写出 JSON 结果")
    q.add_argument("--markdown", action="store_true", help="直接打印 Markdown")
    q.add_argument("--stage", default="检查", help="报告标题里的阶段名")
    add_common(q)
    q.set_defaults(func=cmd_qc)

    f = sub.add_parser("diff", help="对比两份稿件的内容差异")
    f.add_argument("before")
    f.add_argument("after")
    f.add_argument("-o", "--output", help="写出 Markdown 报告")
    add_common(f)
    f.set_defaults(func=cmd_diff)

    r = sub.add_parser("run", help="完整流程：解析→转换→编译→质检→差异→结果包")
    r.add_argument("input")
    r.add_argument("--journal", help="期刊配置 id（与 --template 至少给一个）")
    r.add_argument("--template", help="官方模板文件：LaTeX 用 .tex 主文件，Word 用 .docx")
    r.add_argument("-o", "--output", default="submission", help="结果包根目录")
    r.add_argument("--no-pdf", action="store_true", help="跳过 PDF 预览")
    add_common(r)
    r.set_defaults(func=cmd_run)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        import traceback

        print(f"未预期的错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        if not getattr(args, "quiet", False):
            traceback.print_exc()
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
