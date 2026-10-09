"""图片、表格、公式检查。

需求文档第五节专门把这个模块单列，因为「跨期刊转投时最容易出问题」。
三条最关键的原则在这里落地：

1. **DPI 数值不能单独决定图片质量** —— 必须结合最终印刷尺寸、像素尺寸，
   以及图片属于照片还是线条图。所以这里算的是「有效 DPI」：
   像素宽度 ÷ 印刷宽度（英寸），并且用颜色复杂度把图分成线条图/照片，
   分别套用不同的下限。
2. **不擅自把公式转成图片** —— 只报告，不改。
3. 所有结论都要能定位到具体的图/表/公式，并附上期刊规则来源。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import model as M
from .issues import (
    CAT_EQUATION,
    CAT_FIGURE,
    CAT_TABLE,
    IssueBag,
    Location,
)
from .journal import JournalConfig
from .latex_check import resolve_graphic
from .util import Log, normalize_ws, rel

RASTER = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}
VECTOR = {".pdf", ".eps", ".svg", ".ps"}
CM_PER_INCH = 2.54


# --------------------------------------------------------------------------
# 图片
# --------------------------------------------------------------------------


def _image_info(path: Path) -> dict:
    """读取像素尺寸、格式与「颜色复杂度」，用于判断线条图还是照片。"""
    info: dict = {"path": str(path), "exists": path.is_file()}
    if not info["exists"]:
        return info
    info["bytes"] = path.stat().st_size
    info["ext"] = path.suffix.lower()
    info["sha1"] = hashlib.sha1(path.read_bytes()).hexdigest()
    if path.suffix.lower() in VECTOR:
        info["vector"] = True
        return info
    try:
        from PIL import Image

        with Image.open(path) as im:
            info["px_w"], info["px_h"] = im.size
            info["mode"] = im.mode
            info["dpi_tag"] = im.info.get("dpi")
            info["format"] = im.format or ""
            # 颜色复杂度：缩小后统计唯一颜色占比，粗略区分线条图/照片
            small = im.convert("RGB").resize((64, 64))
            colors = small.getcolors(maxcolors=64 * 64) or []
            uniq = len(colors)
            info["unique_colors"] = uniq
            info["is_lineart"] = uniq <= 200
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _linewidth_cm(cfg: JournalConfig, ms: M.Manuscript) -> float:
    """估算目标版式下的正文栏宽（cm），作为 \\linewidth 的近似。"""
    explicit = cfg.get("figures.max_width_cm")
    if explicit:
        return float(explicit)
    margins = cfg.get("layout.margin_cm") or {}
    page = (cfg.get("layout.page") or "A4").upper()
    page_w = {"A4": 21.0, "LETTER": 21.59, "A5": 14.8}.get(page, 21.0)
    left = float(margins.get("left", 2.5))
    right = float(margins.get("right", 2.5))
    usable = max(4.0, page_w - left - right)
    cols = cfg.columns or 1
    if cols > 1:
        return usable / cols - 0.4  # 减去栏间距
    return usable


def _estimate_print_width_cm(fig: M.Figure, cfg: JournalConfig, ms: M.Manuscript) -> tuple[float, str]:
    """估算图片在目标版式下的印刷宽度，返回 (cm, 依据说明)。"""
    if fig.native.get("display_width_cm"):
        return float(fig.native["display_width_cm"]), "Word 内嵌显示尺寸"
    width = (fig.width or "").strip()
    lw = _linewidth_cm(cfg, ms)
    if width:
        m = re.search(r"([\d.]+)\s*\\(?:linewidth|textwidth|columnwidth|hsize)", width)
        if m:
            return round(float(m.group(1)) * lw, 2), f"\\includegraphics[width={m.group(1)}\\linewidth] × 栏宽 {lw:.1f}cm"
        m = re.search(r"([\d.]+)\s*(cm|mm|in|pt)", width)
        if m:
            val, unit = float(m.group(1)), m.group(2)
            factor = {"cm": 1.0, "mm": 0.1, "in": 2.54, "pt": 2.54 / 72}.get(unit, 1.0)
            return round(val * factor, 2), f"\\includegraphics[width={width}]"
    return round(lw, 2), f"未指定宽度，按正文栏宽 {lw:.1f}cm 估算"


def check_figures(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag, root: str | Path, log: Log | None = None) -> None:
    root = Path(root)
    log = log or Log(verbose=False)
    graphics_paths = ms.metadata.doc_meta.get("graphicspath", []) or []
    min_photo = cfg.get("figures.min_dpi_photo")
    min_line = cfg.get("figures.min_dpi_line")
    allowed = [str(x).lower().lstrip(".") for x in (cfg.get("figures.formats") or [])]
    max_w = cfg.get("figures.max_width_cm")

    by_hash: dict[str, list[str]] = {}
    resolved_any = False

    for fig in ms.figures:
        loc = Location(
            file=(fig.span.file if fig.span else ""),
            line=(fig.span.line if fig.span else 0),
            section=fig.section or "",
            object=f"{fig.id}（第 {fig.number} 图）",
        )
        # 解析实际文件
        paths: list[Path] = []
        for p in fig.native.get("media_path", []) or []:
            if p and Path(p).is_file():
                paths.append(Path(p))
        if not paths:
            for name in fig.files:
                if name.startswith("word/media/"):
                    continue
                cand = resolve_graphic(name, root, graphics_paths, fig.span.file if fig.span else "")
                if cand:
                    paths.append(cand)
        if not paths and fig.files:
            bag.error(
                "FIG001", CAT_FIGURE,
                f"找不到图片文件：{', '.join(fig.files)}",
                location=loc,
                rule_source=cfg.rule_source("figures.formats"),
                suggestion="确认图片是否随稿件一并提交，或路径是否正确。",
            )
            continue
        if not paths:
            bag.warn(
                "FIG002", CAT_FIGURE,
                f"第 {fig.number} 图没有检测到图片文件（可能是矢量图或外部引用）",
                location=loc,
            )
            continue

        resolved_any = True
        for path in paths:
            info = _image_info(path)
            if info.get("sha1"):
                by_hash.setdefault(info["sha1"], []).append(f"{fig.id}")
            if info.get("error"):
                bag.warn(
                    "FIG003", CAT_FIGURE,
                    f"无法读取图片 {path.name}：{info['error']}",
                    location=loc,
                )
                continue

            # 格式
            ext = info.get("ext", "").lstrip(".")
            if allowed and ext and ext not in allowed:
                bag.warn(
                    "FIG010", CAT_FIGURE,
                    f"图片格式 .{ext} 不在目标期刊允许的格式内（{', '.join(allowed)}）",
                    location=loc,
                    evidence=path.name,
                    rule_source=cfg.rule_source("figures.formats"),
                    suggestion=f"建议转换为 {allowed[0]} 后再投稿。",
                )

            if info.get("vector"):
                continue  # 矢量图不受 DPI 限制

            px_w = info.get("px_w")
            if not px_w:
                continue
            print_w, basis = _estimate_print_width_cm(fig, cfg, ms)
            if max_w and print_w > float(max_w) + 0.05:
                bag.warn(
                    "FIG011", CAT_FIGURE,
                    f"图片宽度 {print_w}cm 超出目标期刊上限 {max_w}cm",
                    location=loc,
                    evidence=f"{path.name}；{basis}",
                    rule_source=cfg.rule_source("figures.max_width_cm"),
                    suggestion="缩小图片宽度或改用跨栏排版（需按期刊规定）。",
                )
            eff_dpi = round(px_w / (print_w / CM_PER_INCH)) if print_w else 0
            is_line = bool(info.get("is_lineart"))
            threshold = min_line if is_line else min_photo
            if threshold and eff_dpi and eff_dpi < int(threshold):
                bag.warn(
                    "FIG020", CAT_FIGURE,
                    f"有效分辨率不足：约 {eff_dpi} dpi（目标要求 "
                    f"{'线条图' if is_line else '照片类'} ≥ {threshold} dpi）",
                    location=loc,
                    evidence=(
                        f"{path.name}：{px_w}×{info.get('px_h')} px，"
                        f"印刷宽度约 {print_w}cm（{basis}），"
                        f"颜色数≈{info.get('unique_colors')} → 判定为{'线条图' if is_line else '照片'}"
                    ),
                    rule_source=cfg.rule_source(
                        "figures.min_dpi_line" if is_line else "figures.min_dpi_photo"
                    ),
                    suggestion=(
                        "注意 DPI 数值不能单独决定质量：需要按最终印刷尺寸重新导出，"
                        "线条图建议重新生成矢量版本或提高导出分辨率。"
                    ),
                )
            tag = info.get("dpi_tag")
            if tag and tag[0] and tag[0] < 72:
                bag.info(
                    "FIG021", CAT_FIGURE,
                    f"图片内嵌 DPI 标记偏低（{tag[0]}），部分期刊系统会据此拒绝",
                    location=loc, evidence=path.name,
                )

        if not fig.caption:
            bag.warn(
                "FIG030", CAT_FIGURE, f"第 {fig.number} 图没有题注",
                location=loc,
                rule_source=cfg.rule_source("figures.caption_position"),
            )
        if not fig.referenced_in:
            bag.warn(
                "FIG050", CAT_FIGURE,
                f"第 {fig.number} 图（{fig.label or '无 label'}）在正文中没有任何引用",
                location=loc,
                suggestion="期刊普遍要求每个图在正文中被显式提及（如 “as shown in Fig. 2”）。",
            )
        if fig.subfigures and not any(s.get("caption") for s in fig.subfigures):
            bag.info(
                "FIG031", CAT_FIGURE,
                f"第 {fig.number} 图包含 {len(fig.subfigures)} 个子图，但子图缺少标注",
                location=loc,
                suggestion="多数期刊要求子图标注 (a)(b)(c) 并在题注中逐一说明。",
            )

    # 重复图片
    for sha, users in by_hash.items():
        uniq = sorted(set(users))
        if len(uniq) > 1:
            bag.warn(
                "FIG040", CAT_FIGURE,
                f"同一张图片被 {len(uniq)} 个图对象重复使用：{', '.join(uniq)}",
                location=Location(object=", ".join(uniq)),
                suggestion="确认是否应为不同的子图，避免重复提交。",
            )

    if ms.figures and not resolved_any:
        bag.info(
            "FIG060", CAT_FIGURE,
            "未能在本地解析到任何图片文件，图片质量检查已跳过",
            location=Location(file=ms.source_path),
        )


# --------------------------------------------------------------------------
# 表格
# --------------------------------------------------------------------------


def check_tables(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag) -> None:
    max_w = cfg.get("tables.max_width_cm")
    for t in ms.tables:
        loc = Location(
            file=(t.span.file if t.span else ""),
            line=(t.span.line if t.span else 0),
            section=t.section or "",
            object=f"{t.id}（第 {t.number} 表）",
        )
        if not t.caption:
            bag.error(
                "TAB001", CAT_TABLE, f"第 {t.number} 表缺少表题",
                location=loc,
                rule_source=cfg.rule_source("tables.caption_position"),
            )
        if not t.referenced_in:
            bag.warn(
                "TAB010", CAT_TABLE, f"第 {t.number} 表在正文中没有任何引用",
                location=loc,
                suggestion="期刊普遍要求每个表格在正文中被显式提及。",
            )
        if t.n_cols >= 6:
            est = round(t.n_cols * 1.5 + 2.0, 1)
            hint = ""
            if max_w and est > float(max_w):
                hint = f"，估算最小宽度约 {est}cm，可能超出 {max_w}cm 版心"
            bag.warn(
                "TAB020", CAT_TABLE,
                f"第 {t.number} 表有 {t.n_cols} 列{hint}",
                location=loc,
                evidence=f"{t.n_rows} 行 × {t.n_cols} 列",
                rule_source=cfg.rule_source("tables.max_width_cm"),
                suggestion=(
                    "列数较多时建议：拆表、缩小字号、改为跨栏排版，"
                    "或在 LaTeX 中使用 \\small / \\resizebox（须符合期刊规定）。"
                ),
            )
        if t.n_rows >= 30:
            bag.info(
                "TAB021", CAT_TABLE,
                f"第 {t.number} 表有 {t.n_rows} 行，很可能会跨页",
                location=loc,
                suggestion="跨页表格需重复表头，并按期刊要求处理续表标注。",
            )
        if t.n_rows and t.n_cols and t.n_rows * t.n_cols > 400:
            bag.info(
                "TAB022", CAT_TABLE,
                f"第 {t.number} 表较大（{t.n_rows}×{t.n_cols}），建议评估是否移入补充材料",
                location=loc,
            )
        if t.footnotes:
            bag.info(
                "TAB023", CAT_TABLE,
                f"第 {t.number} 表含 {len(t.footnotes)} 条表注，请核对统计符号说明是否完整",
                location=loc,
            )
        if cfg.get("tables.style") and "docx_table" not in (t.env or ""):
            bag.info(
                "TAB030", CAT_TABLE,
                f"第 {t.number} 表需符合「{cfg.get('tables.style')}」要求",
                location=loc,
                rule_source=cfg.rule_source("tables.style"),
            )


# --------------------------------------------------------------------------
# 公式
# --------------------------------------------------------------------------


def check_equations(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag) -> None:
    numbering = cfg.get("equation_numbering", "continuous")
    for e in ms.equations:
        loc = Location(
            file=(e.span.file if e.span else ""),
            line=(e.span.line if e.span else 0),
            section=e.section or "",
            object=f"{e.id}",
        )
        if e.env == "omml":
            if cfg.get("equation_editable_required"):
                bag.info(
                    "EQ001", CAT_EQUATION,
                    "公式以 Word 公式对象（OMML）存在，需确认转换后仍然是可编辑公式而不是图片",
                    location=loc,
                    suggestion="跨格式转换（Word→LaTeX）时 OMML 无法无损映射，必须人工核对。",
                )
            if not e.source.strip():
                bag.warn(
                    "EQ010", CAT_EQUATION,
                    "检测到公式对象，但未能提取出可见文本，可能是图片式公式或特殊结构",
                    location=loc,
                )
            continue
        src = e.source or ""
        if len(src) > 220 and "\\\\" not in src and "\\begin{split}" not in src and "\\notag" not in src:
            bag.warn(
                "EQ020", CAT_EQUATION,
                "公式源码较长且没有换行，排版后可能溢出页面",
                location=loc,
                evidence=normalize_ws(src)[:120],
                suggestion="改用 align/split 环境断行，或使用 \\resizebox（须符合期刊规定）。",
            )
        if numbering == "none" and e.number:
            bag.info(
                "EQ030", CAT_EQUATION,
                "目标期刊要求公式不编号，但原稿公式带编号",
                location=loc,
                rule_source=cfg.rule_source("equations.numbering"),
            )
        if e.number and not e.referenced_in:
            bag.warn(
                "EQ040", CAT_EQUATION,
                f"编号公式 {e.number}（{e.label or '无 label'}）未被正文引用",
                location=loc,
            )
        if e.number and not e.label:
            bag.warn(
                "EQ041", CAT_EQUATION,
                f"编号公式（{e.env}）没有标签，无法被交叉引用",
                location=loc,
                suggestion="补上 \\label{eq:...}，否则投稿后无法引用该式。",
            )


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def check_assets(ms: M.Manuscript, cfg: JournalConfig, bag: IssueBag, root: str | Path, log: Log | None = None) -> None:
    check_figures(ms, cfg, bag, root, log)
    check_tables(ms, cfg, bag)
    check_equations(ms, cfg, bag)
