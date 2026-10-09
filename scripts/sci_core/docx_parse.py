"""Word (.docx) 原生解析器。

刻意**不做**「先把 Word 转成别的格式再处理」：直接读 OOXML，
保留段落样式、表格、图片关系、OMML 公式、脚注、页眉页脚与页面设置。

需求文档 1.2 要求提取的 Word 侧内容：
标题样式、脚注、页眉页脚、题注、公式对象；并检查「可能丢失的内容」，
所以这里额外探测修订记录(w:ins/w:del)、批注与域代码。
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Emu

from . import model as M
from .util import Log, normalize_ws, rel

NS_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_V = "{urn:schemas-microsoft-com:vml}"
NS_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"

EMU_PER_CM = 360000.0

CAPTION_RE_STRICT = re.compile(
    r"^\s*(?:(Figure|Fig\.?|FIGURE|Table|TABLE|图|表)\s*([0-9]+(?:\.[0-9]+)?[a-z]?)\s*([.:：])\s*)(.*)$"
)
# 无分隔符的题注形如 "Figure 1 Overview of the method"，只在样式或位置证据足够时才采用，
# 否则会把 "Table 2 reports accuracy ..." 这类正文句子误判成题注。
CAPTION_RE_LOOSE = re.compile(
    r"^\s*(?:(Figure|Fig\.?|FIGURE|Table|TABLE|图|表)\s*([0-9]+(?:\.[0-9]+)?[a-z]?)\s*)(.*)$"
)
NUMBERED_REF_RE = re.compile(r"^\s*[\[\u3010]?(\d{1,3})[\]\u3011.]\s*(.+)$")
KEYWORDS_RE = re.compile(r"^\s*(?:Keywords|Key words|关键词)\s*[:：]\s*(.+)$", re.I)
ABSTRACT_HEADING_RE = re.compile(r"^\s*(?:Abstract|ABSTRACT|摘要)\s*[:：]?\s*$")
REF_HEADING_RE = re.compile(r"^\s*(?:References|REFERENCES|Bibliography|参考文献|參見文獻)\s*[:：]?\s*$")

HEADING_STYLE_RE = re.compile(r"(?:heading|标题|標題)\s*([1-9])", re.I)
CAPTION_STYLES = {"caption", "题注", "題註", "figurecaption", "tablecaption"}
ABSTRACT_STYLES = {"abstract", "摘要"}
TITLE_STYLES = {"title", "标题", "標題"}
AUTHOR_STYLES = {"author", "作者", "subtitle"}

DECLARATION_KEYWORDS = {
    "funding": ["funding", "financial support", "资助", "基金", "acknowledg"],
    "conflict_of_interest": ["conflict of interest", "competing interest", "利益冲突", "declaration of competing"],
    "data_availability": ["data availability", "availability of data", "数据可用性", "数据可用"],
    "ethics": ["ethics statement", "ethical approval", "伦理"],
    "author_contributions": ["author contributions", "credit authorship", "作者贡献"],
}

_LEADING_NUM_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*|[IVXLC]+)\s*[.)、]?\s+")


def is_declaration_heading(text: str) -> bool:
    """判断一个 Word 标题是不是「投稿声明」，而不是正文结构。

    Word 稿件常把声明也套上标题样式、甚至带上编号（"5 Funding"），
    所以先剥掉前导编号，再沿用与 LaTeX 侧一致的**保守**策略：

    * 规范化后**完全等于**关键词 → 声明；
    * 以关键词开头且词数 ≤ 6 → 声明。

    这样 ``5 Funding`` / ``Declaration of Competing Interest`` 会被识别，
    而 ``5 Funding Models for Edge Inference`` 不会被误伤。
    """
    core = _LEADING_NUM_RE.sub("", normalize_ws(text)).strip().lower().rstrip(".:： ")
    if not core:
        return False
    for kws in DECLARATION_KEYWORDS.values():
        for kw in kws:
            if core == kw:
                return True
            if core.startswith(kw) and len(core.split()) <= 6:
                return True
    return False


def _cm(value) -> float:
    try:
        return round(Emu(int(value)).cm, 2)
    except Exception:
        return 0.0


def _pt(value) -> float:
    try:
        return round(float(value.pt), 1)
    except Exception:
        return 0.0


def _style_name(style) -> str:
    try:
        return style.name or ""
    except Exception:
        return ""


class DocxParser:
    def __init__(self, log: Log | None = None):
        self.log = log or Log(verbose=False)
        self.media: dict[str, bytes] = {}  # partname -> bytes
        self.media_files: dict[str, str] = {}  # partname -> 落盘后的绝对路径
        self._text_scan: list[tuple[str, "M.Section | None"]] = []  # (正文文本, 所属章节)
        self._media_dir: Path | None = None

    # ------------------------------------------------------------------
    def parse(self, path: str | Path, media_dir: str | Path | None = None) -> M.Manuscript:
        p = Path(path).resolve()
        if media_dir:
            self._media_dir = Path(media_dir)
            self._media_dir.mkdir(parents=True, exist_ok=True)
        doc = Document(str(p))
        ms = M.Manuscript(source_format=M.FMT_DOCX, source_path=str(p), main_file=str(p))

        self._page_setup(doc, ms)
        self._doc_flags(doc, ms)
        self._styles(doc, ms)
        self._core_props(doc, ms)

        body = doc.element.body
        para_index = 0
        figure_no = table_no = 0
        current_section: M.Section | None = None
        pending_caption: tuple[str, str, str] | None = None  # (kind, caption, number)
        last_fig_index: int | None = None
        in_references = False
        in_abstract = False
        ref_buffer: list[str] = []
        abstract_buffer: list[str] = []
        found_decl: set[str] = set()
        # 临时章节 id → Section 对象。章节 id 会在解析末尾被重写成稳定 id
        # （优先用标题编号），所以前面记录到图/表/公式上的 id 必须能反查回来。
        sec_by_temp: dict[str, M.Section] = {}

        for child in body.iterchildren():
            tag = child.tag
            if tag == qn("w:p"):
                para_index += 1
                para = self._wrap_paragraph(doc, child)
                text = normalize_ws(para.text)
                style = _style_name(para.style)
                low_style = style.lower()

                # --- 修订 / 批注 / 域 ---
                if child.findall(f".//{NS_W}ins") or child.findall(f".//{NS_W}del"):
                    ms.has_tracked_changes = True

                # --- 图片 ---
                images = self._paragraph_images(doc, child)
                if images:
                    figure_no += 1
                    fid = f"fig:{figure_no}"
                    fig = M.Figure(
                        id=fid, number=figure_no,
                        caption="", env="docx_inline",
                        section=current_section.id if current_section else None,
                        span=M.Span(file=p.name, line=para_index),
                    )
                    fig.files = [self._save_media(doc, rid) for rid, _w in images]
                    widths = [w for _r, w in images if w]
                    if widths:
                        fig.native["display_width_cm"] = max(widths)
                    extracted = [self.media_files.get(f) for f in fig.files]
                    extracted = [x for x in extracted if x]
                    if extracted:
                        fig.native["media_path"] = extracted
                    ms.figures.append(fig)
                    last_fig_index = len(ms.figures) - 1
                    if pending_caption and pending_caption[0] == "figure":
                        fig.caption = pending_caption[1]
                        fig.caption_in_source = True
                        fig.native["caption_number"] = pending_caption[2]
                        pending_caption = None
                    continue

                # --- 公式 ---
                math_nodes = child.findall(f".//{NS_M}oMath")
                if math_nodes and text == "":
                    for node in math_nodes:
                        eid = f"eq:{len(ms.equations) + 1}"
                        eq = M.Equation(
                            id=eid,
                            number=0,
                            label=None,
                            env="omml",
                            source=self._omml_text(node),
                            section=current_section.id if current_section else None,
                            span=M.Span(file=p.name, line=para_index),
                        )
                        eq.native["omml_xml"] = self._xml_snippet(node)
                        ms.equations.append(eq)
                    continue

                if not text:
                    continue

                # --- 标题 ---
                level = self._heading_level(style)
                guessed = M.guess_heading_number(text)
                if level is None and guessed and self._looks_like_heading(text, style):
                    level, number, title = guessed

                if level is not None:
                    # 声明类小标题：它是**声明**不是正文结构，由 ms.declarations 单独建模。
                    # 若不在这里截住，声明标题会被 renumber 成 "5 Funding" 这样的正文章节，
                    # 转换时自动补入的声明占位也会在差异报告里被误报成「新增章节」。
                    if is_declaration_heading(text):
                        low_decl = text.lower()
                        for kind, kws in DECLARATION_KEYWORDS.items():
                            if any(k in low_decl for k in kws) and kind not in found_decl:
                                found_decl.add(kind)
                                ms.declarations.append(
                                    M.Declaration(kind=kind, present=True, text=text,
                                                  span=M.Span(file=p.name, line=para_index))
                                )
                        # 后续声明正文段落不属于任何正文章节
                        current_section = None
                        continue
                    if REF_HEADING_RE.match(text):
                        in_references = True
                        current_section = None
                        continue
                    in_references = False
                    in_abstract = bool(ABSTRACT_HEADING_RE.match(text))
                    if in_abstract:
                        continue
                    sec = M.Section(
                        id=f"sec:line{para_index}", level=level, heading=text,
                        number=(guessed[1] if guessed else ""),
                        span=M.Span(file=p.name, line=para_index),
                    )
                    ms.sections.append(sec)
                    sec_by_temp[sec.id] = sec
                    current_section = sec
                    continue

                # --- 参考文献区 ---
                if in_references:
                    ref_buffer.append(text)
                    continue

                # --- 关键词（必须排在摘要之前，否则 "Keywords:" 行会被并入摘要） ---
                mk = KEYWORDS_RE.match(text)
                if mk:
                    ms.metadata.keywords.extend(
                        k.strip() for k in re.split(r"[,;，；]", mk.group(1)) if k.strip()
                    )
                    continue

                # --- 摘要 ---
                if in_abstract or low_style in ABSTRACT_STYLES:
                    abstract_buffer.append(text)
                    continue

                # --- 题注 ---
                is_caption_style = low_style in CAPTION_STYLES
                mc = CAPTION_RE_STRICT.match(text)
                if not mc and is_caption_style:
                    mc = CAPTION_RE_LOOSE.match(text)
                if not mc and last_fig_index is not None and len(text) <= 160:
                    # 紧跟在图片段落之后、且图还没有题注 → 无分隔符也认定为图注
                    fig = ms.figures[last_fig_index]
                    if not fig.caption:
                        fig.caption = text
                        fig.caption_in_source = True
                        last_fig_index = None
                        continue
                if mc:
                    kind_raw = mc.group(1).lower()
                    num = mc.group(2)
                    cap_text = normalize_ws(mc.group(4))
                    if kind_raw.startswith(("fig", "图")):
                        if last_fig_index is not None and not ms.figures[last_fig_index].caption:
                            fig = ms.figures[last_fig_index]
                            fig.caption = cap_text
                            fig.caption_in_source = True
                            fig.native["caption_number"] = num
                            last_fig_index = None
                            continue
                        # 图注在图之前出现（少见）→ 记下来等图片段落消费
                        pending_caption = ("figure", cap_text, num)
                        continue
                    pending_caption = ("table", cap_text, num)
                    continue

                # --- 正文块 ---
                block = M.Block(
                    id=f"blk:{para_index}",
                    kind=M.BLOCK_PARAGRAPH,
                    text=text,
                    style=style,
                    span=M.Span(file=p.name, line=para_index),
                )
                if current_section is not None:
                    current_section.blocks.append(block)
                self._text_scan.append((text, current_section))
                last_fig_index = None

            elif tag == qn("w:tbl"):
                table_no += 1
                tbl = self._wrap_table(doc, child)
                tid = f"tab:{table_no}"
                t = M.Table(
                    id=tid, number=table_no, env="docx_table",
                    section=current_section.id if current_section else None,
                    span=M.Span(file=p.name, line=para_index),
                )
                try:
                    t.n_rows = len(tbl.rows)
                    t.n_cols = max((len(r.cells) for r in tbl.rows), default=0)
                    t.raw = self._table_text(tbl)
                except Exception:
                    pass
                if pending_caption and pending_caption[0] == "table":
                    t.caption = pending_caption[1]
                    t.native["caption_number"] = pending_caption[2]
                    pending_caption = None
                ms.tables.append(t)
                last_fig_index = None

        # ---- 元信息 ----
        self._title_authors(doc, ms)
        if abstract_buffer:
            ms.metadata.abstract = normalize_ws(" ".join(abstract_buffer))
            for m in re.finditer(r"(?m)^\s*([A-Z][A-Za-z \-]{2,30})\s*[:：]\s*(.+)$", ms.metadata.abstract):
                ms.metadata.abstract_structured[m.group(1).strip()] = normalize_ws(m.group(2))

        M.renumber_sections(ms.sections)
        self._assign_section_ids(ms)
        # 图/表/公式在构造时只能拿到 sec:lineNN 这种**临时**章节 id，
        # 稳定 id 要等章节收集完统一分配，所以这里必须回填一次。
        self._remap_section_refs(ms, sec_by_temp)
        self._scan_intext_refs(ms)

        # ---- 参考文献 ----
        self._parse_references(doc, ms, ref_buffer, para_index)
        self._detect_declarations_text(ms, found_decl, doc, para_index)
        self._extract_notes(doc, ms)
        self._headers_footers(doc, ms)
        self._scan_relations(doc, ms)

        if ms.has_tracked_changes:
            ms.warnings.append("文档包含修订记录（w:ins/w:del），转换后可能丢失修订痕迹")
        if ms.has_comments:
            ms.warnings.append("文档包含批注，转换后批注不会保留")
        if not ms.sections:
            ms.warnings.append("未识别到标题样式或编号标题，章节结构为空")
        return ms

    # ---------------------------------------------------------------- 设置

    def _page_setup(self, doc, ms: M.Manuscript) -> None:
        try:
            s = doc.sections[0]
        except Exception:
            return
        ms.page_setup = {
            "width_cm": _cm(s.page_width),
            "height_cm": _cm(s.page_height),
            "orientation": str(s.orientation).split()[0] if s.orientation is not None else "",
            "margin_top_cm": _cm(s.top_margin),
            "margin_bottom_cm": _cm(s.bottom_margin),
            "margin_left_cm": _cm(s.left_margin),
            "margin_right_cm": _cm(s.right_margin),
            "header_cm": _cm(s.header_distance),
            "footer_cm": _cm(s.footer_distance),
            "sections": len(doc.sections),
            "columns": self._columns(doc),
        }

    @staticmethod
    def _columns(doc) -> int:
        try:
            sectPr = doc.element.body.find(qn("w:sectPr"))
            if sectPr is None:
                return 1
            cols = sectPr.find(qn("w:cols"))
            if cols is None:
                return 1
            return int(cols.get(qn("w:num")) or 1)
        except Exception:
            return 1

    def _doc_flags(self, doc, ms: M.Manuscript) -> None:
        try:
            with zipfile.ZipFile(ms.source_path) as z:
                names = set(z.namelist())
            ms.has_comments = any(n.startswith("word/comments") for n in names)
            if any(n.startswith("word/footnotes") for n in names):
                ms.metadata.doc_meta["has_footnotes"] = True
            ms.metadata.doc_meta["has_endnotes"] = any(n.startswith("word/endnotes") for n in names)
            ms.metadata.doc_meta["media_files"] = sorted(
                n for n in names if n.startswith("word/media/")
            )
        except Exception as exc:
            self.log.warn(f"读取 docx 包结构失败: {exc}")
        try:
            ms.has_fields = bool(
                doc.element.body.findall(f".//{NS_W}fldSimple")
                or doc.element.body.findall(f".//{NS_W}instrText")
            )
        except Exception:
            pass

    def _styles(self, doc, ms: M.Manuscript) -> None:
        from collections import Counter

        used: Counter = Counter()
        for p in doc.paragraphs:
            used[_style_name(p.style) or "(默认)"] += 1
        ms.styles_used = dict(used.most_common())

        info: dict = {}
        try:
            styles_el = doc.styles.element
            docd = styles_el.find(qn("w:docDefaults"))
            if docd is not None:
                rpr = docd.find(f"{NS_W}rPrDefault/{NS_W}rPr")
                if rpr is not None:
                    rf = rpr.find(qn("w:rFonts"))
                    if rf is not None:
                        info["default_font"] = rf.get(qn("w:ascii")) or rf.get(qn("w:eastAsia"))
                        info["default_font_eastasia"] = rf.get(qn("w:eastAsia"))
                    sz = rpr.find(qn("w:sz"))
                    if sz is not None:
                        info["default_size_pt"] = round(int(sz.get(qn("w:val"))) / 2, 1)
                    spacing = rpr.find(qn("w:spacing"))
                    if spacing is not None and spacing.get(qn("w:line")):
                        info["default_line_spacing"] = round(int(spacing.get(qn("w:line"))) / 240, 2)
            # Normal 样式的字体与段落
            for st in styles_el.findall(qn("w:style")):
                if st.get(qn("w:styleId")) == "Normal":
                    rpr = st.find(qn("w:rPr"))
                    if rpr is not None:
                        rf = rpr.find(qn("w:rFonts"))
                        if rf is not None and rf.get(qn("w:ascii")):
                            info.setdefault("normal_font", rf.get(qn("w:ascii")))
                        sz = rpr.find(qn("w:sz"))
                        if sz is not None:
                            info.setdefault("normal_size_pt", round(int(sz.get(qn("w:val"))) / 2, 1))
                    ppr = st.find(qn("w:pPr"))
                    if ppr is not None:
                        sp = ppr.find(qn("w:spacing"))
                        if sp is not None:
                            if sp.get(qn("w:line")):
                                info["normal_line_spacing"] = round(int(sp.get(qn("w:line"))) / 240, 2)
                            if sp.get(qn("w:after")):
                                info["normal_space_after_pt"] = round(int(sp.get(qn("w:after"))) / 20, 1)
        except Exception as exc:
            self.log.warn(f"读取样式失败: {exc}")
        ms.metadata.doc_meta["style_info"] = info

    def _core_props(self, doc, ms: M.Manuscript) -> None:
        try:
            cp = doc.core_properties
            ms.metadata.doc_meta["core"] = {
                "author": cp.author or "",
                "last_modified_by": cp.last_modified_by or "",
                "created": str(cp.created) if cp.created else "",
                "modified": str(cp.modified) if cp.modified else "",
                "revision": cp.revision,
            }
            if cp.title:
                ms.metadata.doc_meta.setdefault("core_title", cp.title)
        except Exception:
            pass

    # ------------------------------------------------------------ 段落辅助

    @staticmethod
    def _wrap_paragraph(doc, element):
        from docx.text.paragraph import Paragraph

        return Paragraph(element, doc)

    @staticmethod
    def _wrap_table(doc, element):
        from docx.table import Table

        return Table(element, doc)

    @staticmethod
    def _heading_level(style: str) -> int | None:
        m = HEADING_STYLE_RE.search(style or "")
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _looks_like_heading(text: str, style: str) -> bool:
        if not text or len(text) > 120:
            return False
        if text.endswith((".", "。", ",", "，")):
            return False
        low = style.lower()
        if low in CAPTION_STYLES or low in ABSTRACT_STYLES:
            return False
        return True

    def _paragraph_images(self, doc, element) -> list[tuple[str, float]]:
        """返回该段落的 (图片关系 id, 显示宽度 cm) 列表。

        显示宽度来自 DrawingML 的 wp:extent，是判断「有效 DPI」的必要输入——
        单看图片像素数无法判断印刷质量（需求文档 4.1 明确提到这一点）。
        """
        out: list[tuple[str, float]] = []
        for extent in element.iter(f"{NS_WP}extent"):
            cx = extent.get("cx")
            w_cm = round(int(cx) / EMU_PER_CM, 2) if cx else 0.0
            # 同一 drawing 内的 blip
            parent = extent.getparent()
            rids = [
                b.get(f"{NS_R}embed") or b.get(f"{NS_R}link")
                for b in parent.iter(f"{NS_A}blip")
            ]
            rids = [r for r in rids if r]
            if rids:
                out.append((rids[0], w_cm))
                for extra in rids[1:]:
                    out.append((extra, w_cm))
        seen = {r for r, _ in out}
        for blip in element.iter(f"{NS_A}blip"):
            rid = blip.get(f"{NS_R}embed") or blip.get(f"{NS_R}link")
            if rid and rid not in seen:
                out.append((rid, 0.0))
                seen.add(rid)
        for img in element.iter(f"{NS_V}imagedata"):
            rid = img.get(f"{NS_R}id")
            if rid and rid not in seen:
                out.append((rid, 0.0))
                seen.add(rid)
        return out

    def _save_media(self, doc, rid: str) -> str:
        """把图片二进制读进内存；若指定了 media_dir 则同时落盘，便于做分辨率检查。"""
        try:
            part = doc.part.related_parts[rid]
        except Exception:
            return f"(missing-part:{rid})"
        name = str(part.partname).lstrip("/")
        try:
            blob = part.blob
        except Exception:
            return name
        self.media[name] = blob
        if self._media_dir is not None and name not in self.media_files:
            stem = Path(name).stem
            suffix = Path(name).suffix or ".bin"
            target = self._media_dir / f"{stem}{suffix}"
            n = 2
            while target.exists():
                target = self._media_dir / f"{stem}_{n}{suffix}"
                n += 1
            try:
                target.write_bytes(blob)
                self.media_files[name] = str(target)
            except Exception as exc:
                self.log.warn(f"图片落盘失败 {name}: {exc}")
        return name

    @staticmethod
    def _omml_text(node) -> str:
        return normalize_ws("".join(t.text or "" for t in node.iter(f"{NS_M}t")))

    @staticmethod
    def _xml_snippet(node, limit: int = 2000) -> str:
        from lxml import etree

        try:
            return etree.tostring(node, encoding="unicode")[:limit]
        except Exception:
            return ""

    @staticmethod
    def _table_text(tbl) -> str:
        rows = []
        for r in tbl.rows:
            rows.append(" | ".join(normalize_ws(c.text) for c in r.cells))
        return "\n".join(rows)

    # ------------------------------------------------------------ 元信息

    def _title_authors(self, doc, ms: M.Manuscript) -> None:
        """识别标题/作者/单位。

        优先看样式（Title / Author）；样式缺失时（很常见）退化为
        「第一个标题之前的头几段」这个版面启发式。
        """
        try:
            paras = [normalize_ws(p.text) for p in doc.paragraphs]
            styles = [_style_name(p.style).lower() for p in doc.paragraphs]
        except Exception:
            return

        # 1) 样式优先
        for text, style in zip(paras, styles):
            if not text:
                continue
            if style in TITLE_STYLES and not ms.metadata.title:
                ms.metadata.title = text

        # 2) 版面启发式：第一个标题段落之前的前几段
        if not ms.metadata.title:
            limit = len(paras)
            for i, (text, style) in enumerate(zip(paras, styles)):
                if not text:
                    continue
                if self._heading_level(style) is not None:
                    limit = i
                    break
                if M.guess_heading_number(text) and self._looks_like_heading(text, style) and i > 0:
                    limit = i
                    break
            head = [(i, paras[i]) for i in range(limit) if paras[i]]
            if head:
                ms.metadata.title = head[0][1]
                for _i, text in head[1:5]:
                    if "@" in text and len(text) < 120:
                        continue  # 邮箱行不当作作者
                    if self._looks_like_affiliation(text):
                        if text not in ms.metadata.affiliations:
                            ms.metadata.affiliations.append(text)
                        continue
                    if len(text) <= 200 and not ms.metadata.authors:
                        for a in re.split(r"[,;，；]|\s+and\s+", text):
                            a = a.strip().rstrip("0123456789*†‡, ")
                            if a and len(a) < 60:
                                ms.metadata.authors.append(
                                    {"name": a, "affil": "", "email": "", "orcid": ""}
                                )

        # 3) 补邮箱与 ORCID
        joined = " ".join(paras[:40])
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", joined)
        if email and ms.metadata.authors:
            ms.metadata.authors[0]["email"] = email.group(0)
            ms.metadata.corresponding = ms.metadata.authors[0]["name"]
        for m in re.finditer(r"([\w\u4e00-\u9fff.\- ]{2,40})\s*\(?orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", joined, re.I):
            name = m.group(1).strip(" ,;")
            for a in ms.metadata.authors:
                if name and name.split()[-1] in a["name"]:
                    a["orcid"] = m.group(2)

    @staticmethod
    def _looks_like_affiliation(text: str) -> bool:
        if len(text) > 240:
            return False
        kws = (
            "university", "institute", "institution", "school", "department",
            "college", "academy", "laboratory", "centre", "center", "faculty",
            "大学", "学院", "研究所", "实验室", "系", "中心",
        )
        low = text.lower()
        if any(k in low for k in kws):
            return True
        # 含邮编 + 国家/城市 的地址行
        return bool(re.search(r"\b\d{5,6}\b", text)) and len(text) < 160

    @staticmethod
    def _remap_section_refs(ms: M.Manuscript, sec_by_temp: dict[str, M.Section]) -> None:
        """把图/表/公式上记录的**临时**章节 id 换成重写后的稳定 id。

        这些对象在构造时只能拿到 ``sec:lineNN`` 这种占位 id（真正的稳定 id 要等
        所有章节收集完、按标题编号统一分配）。若不回填，质检报告里就会出现
        ``§sec:line12`` 这种对不上任何章节的定位——需求要求问题必须能定位到
        文件/章节/图表，这个回填就是保证那一半。
        """
        if not sec_by_temp:
            return
        for obj in list(ms.figures) + list(ms.tables) + list(ms.equations):
            sid = getattr(obj, "section", None)
            if sid and sid in sec_by_temp:
                obj.section = sec_by_temp[sid].id

    @staticmethod
    def _assign_section_ids(ms) -> None:
        from .util import slug

        used: set[str] = set()
        for s in ms.sections:
            base = f"sec:{s.number}" if s.number else f"sec:{slug(s.heading) or 'unnamed'}"
            sid, n = base, 2
            while sid in used:
                sid = f"{base}-{n}"
                n += 1
            used.add(sid)
            s.id = sid

    # ------------------------------------------------------------ 参考文献

    def _parse_references(self, doc, ms: M.Manuscript, buffer: list[str], para_index: int) -> None:
        """从文末参考文献区解析条目。

        区分两种来源：
          * `docx_field` —— 段落里有 Word 文献域（ADDIN EN.CITE），字段可结构化提取
          * `docx_text`  —— 只是普通文本，需要人工确认
        """
        if not buffer:
            return
        # 先看是否有结构化域
        field_entries: list[M.Reference] = []
        try:
            for child in doc.element.body.iterchildren(qn("w:p")):
                instr = "".join(
                    t.text or "" for t in child.iter(f"{NS_W}instrText")
                )
                if "EN.CITE" not in instr and "EN.REFLIST" not in instr:
                    continue
                if "EN.CITE" not in instr:
                    continue
                fields = {}
                for m in re.finditer(r"<([A-Za-z]+)>([^<]*)</\1>", instr):
                    fields[m.group(1).lower()] = m.group(2).strip()
                if not fields:
                    continue
                key = (fields.get("id") or fields.get("doi") or fields.get("title", ""))[:60]
                key = re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_") or f"field{len(field_entries)+1}"
                field_entries.append(
                    M.Reference(
                        key=key,
                        origin="docx_field",
                        type="article",
                        fields={
                            "author": fields.get("author", ""),
                            "title": fields.get("title", ""),
                            "year": fields.get("year", ""),
                            "journal": fields.get("journalname", fields.get("secondaryauthor", "")),
                            "volume": fields.get("volume", ""),
                            "pages": fields.get("pages", ""),
                            "doi": fields.get("doi", ""),
                        },
                        confidence="confirmed",
                    )
                )
        except Exception:
            pass
        if field_entries:
            ms.references.extend(field_entries)
            return

        # 普通文本：合并被换行拆开的条目
        merged: list[str] = []
        for line in buffer:
            if NUMBERED_REF_RE.match(line) or not merged:
                merged.append(line)
            else:
                merged[-1] = merged[-1] + " " + line
        for i, entry in enumerate(merged, 1):
            m = NUMBERED_REF_RE.match(entry)
            num = m.group(1) if m else str(i)
            text = normalize_ws(m.group(2) if m else entry)
            year = ""
            my = re.search(r"\b(1[89]\d{2}|20\d{2})\b", text)
            if my:
                year = my.group(1)
            doi = ""
            md = re.search(r"10\.\d{4,9}/[^\s,;)]+", text)
            if md:
                doi = md.group(0).rstrip(".")
            ms.references.append(
                M.Reference(
                    key=f"ref{num}",
                    origin="docx_text",
                    type="article",
                    fields={"year": year, "doi": doi, "raw": text},
                    raw=text,
                    confidence="needs_review",
                    span=M.Span(file=Path(ms.source_path).name, line=para_index),
                )
            )

    # ------------------------------------------------------------ 正文引用扫描

    def _scan_intext_refs(self, ms: M.Manuscript) -> None:
        """扫描正文里的编号引用 [1,2] 与图表提及 Figure 3 / Table 2。

        Word 侧没有 \\cite / \\ref 这样的显式命令，只能从文本里推断。
        这里把推断结果同时写进 citations / crossrefs / referenced_in，
        使 Word 与 LaTeX 两条链路在质检阶段能用同一套判据。
        """
        cite_re = re.compile(r"\[\s*(\d{1,3}(?:\s*[,\u2013\-\u2014]\s*\d{1,3})*)\s*\]")
        xref_re = re.compile(
            r"\b(Figure|Fig\.|FIGURE|Table|TABLE|Eq\.|Equation)\s*(\d{1,3})", re.I
        )
        xref_cn = re.compile(r"(图|表|式)\s*(\d{1,3})")

        obj_by_anchor = ms.label_index()
        seen_cite: set[tuple[str, str]] = set()

        for text, sec in self._text_scan:
            sec_id = sec.id if sec is not None else ""
            for m in cite_re.finditer(text):
                body = m.group(1)
                nums: list[int] = []
                for part in re.split(r"[,\u2013\-\u2014]", body):
                    part = part.strip()
                    if part.isdigit():
                        nums.append(int(part))
                # 支持 [3-5] 这类区间
                if "\u2013" in body or "-" in body or "\u2014" in body:
                    bounds = [int(x) for x in re.split(r"[^\d]+", body) if x]
                    if len(bounds) == 2 and bounds[0] < bounds[1] <= bounds[0] + 30:
                        nums = list(range(bounds[0], bounds[1] + 1))
                for n in nums:
                    key = f"ref{n}"
                    if (key, sec_id or "") in seen_cite:
                        continue
                    seen_cite.add((key, sec_id or ""))
                    ms.citations.append(
                        M.Citation(key=key, command="docx_bracket", section=sec_id)
                    )
                    ms.crossrefs.append(
                        {
                            "id": f"xref:cite:{key}:{sec_id}",
                            "command": "docx_bracket",
                            "target": key,
                            "section": sec_id or "",
                            "span": {},
                        }
                    )
            for rx in (xref_re, xref_cn):
                for m in rx.finditer(text):
                    kind_raw, num = m.group(1).lower(), m.group(2)
                    if kind_raw.startswith(("fig", "图")):
                        anchor = f"fig:{num}"
                    elif kind_raw.startswith(("tab", "表")):
                        anchor = f"tab:{num}"
                    else:
                        anchor = f"eq:{num}"
                    target = obj_by_anchor.get(anchor, anchor)
                    ms.crossrefs.append(
                        {
                            "id": f"xref:{anchor}:{sec_id}",
                            "command": "docx_text",
                            "target": anchor,
                            "section": sec_id or "",
                            "span": {},
                        }
                    )
                    for coll in (ms.figures, ms.tables, ms.equations):
                        for obj in coll:
                            if obj.id == target and sec_id and sec_id not in obj.referenced_in:
                                obj.referenced_in.append(sec_id)

    # ------------------------------------------------------------ 其他

    def _detect_declarations_text(self, ms: M.Manuscript, found: set[str], doc, para_index: int) -> None:
        low_all = " ".join(normalize_ws(p.text) for p in doc.paragraphs).lower()
        for kind, kws in DECLARATION_KEYWORDS.items():
            if kind in found:
                continue
            if any(k in low_all for k in kws):
                found.add(kind)
                ms.declarations.append(
                    M.Declaration(kind=kind, present=True, text="", span=M.Span(file=Path(ms.source_path).name))
                )
        for kind in ("funding", "conflict_of_interest", "data_availability"):
            if kind not in found:
                ms.declarations.append(M.Declaration(kind=kind, present=False))

    def _extract_notes(self, doc, ms: M.Manuscript) -> None:
        """脚注/尾注正文（用于判断内容是否会在转换中丢失）。"""
        notes: list[str] = []
        try:
            with zipfile.ZipFile(ms.source_path) as z:
                for name in ("word/footnotes.xml", "word/endnotes.xml"):
                    if name not in z.namelist():
                        continue
                    from lxml import etree

                    root = etree.fromstring(z.read(name))
                    for t in root.iter(f"{NS_W}t"):
                        if t.text:
                            notes.append(t.text)
        except Exception:
            pass
        text = normalize_ws(" ".join(notes))
        if text:
            ms.metadata.doc_meta["notes_text"] = text[:2000]
            ms.metadata.doc_meta["notes_count"] = len(notes)

    def _headers_footers(self, doc, ms: M.Manuscript) -> None:
        try:
            out = {}
            for i, s in enumerate(doc.sections):
                out[f"section{i}"] = {
                    "header": normalize_ws(" ".join(p.text for p in s.header.paragraphs)),
                    "footer": normalize_ws(" ".join(p.text for p in s.footer.paragraphs)),
                    "different_first_page": bool(s.different_first_page_header_footer),
                }
            ms.metadata.doc_meta["headers_footers"] = out
        except Exception:
            pass

    def _scan_relations(self, doc, ms: M.Manuscript) -> None:
        """统计超链接等关系，判断转换是否会丢内容。"""
        try:
            rels = doc.part.rels
            hyper = sum(1 for r in rels.values() if "hyperlink" in r.reltype)
            ms.metadata.doc_meta["hyperlink_count"] = hyper
        except Exception:
            pass


# --------------------------------------------------------------------------


def parse_docx(path: str | Path, log: Log | None = None, media_dir: str | Path | None = None) -> M.Manuscript:
    return DocxParser(log).parse(path, media_dir=media_dir)


def extract_media(path: str | Path, out_dir: str | Path) -> list[str]:
    """把 docx 内嵌图片导出为独立文件（用于图片质量检查与独立投稿文件）。"""
    p = Path(path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with zipfile.ZipFile(p) as z:
        for name in z.namelist():
            if not name.startswith("word/media/"):
                continue
            data = z.read(name)
            target = out / Path(name).name
            target.write_bytes(data)
            written.append(str(target))
    return written


def sniff_format(path: str | Path) -> str:
    """根据扩展名/内容判断输入格式，供 CLI 自动分派。"""
    p = Path(path)
    if p.is_dir():
        return M.FMT_LATEX
    suffix = p.suffix.lower()
    if suffix in (".docx", ".doc"):
        return M.FMT_DOCX
    if suffix in (".tex", ".latex"):
        return M.FMT_LATEX
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
            if any(n.startswith("word/") for n in names):
                return M.FMT_DOCX
            if any(n.endswith(".tex") for n in names):
                return M.FMT_LATEX
        except Exception:
            pass
    return ""
