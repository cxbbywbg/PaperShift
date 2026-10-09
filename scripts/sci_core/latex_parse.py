r"""LaTeX 项目解析器。

策略：**先把多文件项目展平为单一文本 + 行映射，再基于位置做解析**。
这样 \input / \include 的跨文件结构不会打断解析逻辑，同时每个元素
都能反查回真实的 (文件, 行号)。

不追求实现一个完整的 TeX 引擎，只覆盖需求文档 1.2 列出的那几类结构：
主文件识别、子文件递归、documentclass/usepackage、元信息、章节结构、
figure/table/equation 环境、label/ref/cite、bib 资源、自定义宏。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import model as M
from .util import Log, normalize_ws, read_text, rel, slug

GRAPHIC_EXTS = [".pdf", ".png", ".jpg", ".jpeg", ".eps", ".tif", ".tiff", ".svg", ".ps", ".bmp"]

ENV_KINDS = {
    "figure": "figure",
    "figure*": "figure",
    "wrapfigure": "figure",
    "subfigure": "figure",
    "table": "table",
    "table*": "table",
    "sidewaystable": "table",
    "longtable": "table",
    "tabular": "tabular",
    "tabular*": "tabular",
    "tabularx": "tabular",
    "equation": "equation",
    "equation*": "equation",
    "align": "equation",
    "align*": "equation",
    "gather": "equation",
    "gather*": "equation",
    "multline": "equation",
    "multline*": "equation",
    "eqnarray": "equation",
    "eqnarray*": "equation",
    "displaymath": "equation",
    "verbatim": "verbatim",
    "lstlisting": "verbatim",
    "minted": "verbatim",
    "algorithm": "float",
    "algorithm*": "float",
    "algorithmic": "verbatim",
    "abstract": "abstract",
    "thebibliography": "bibliography",
    "acknowledgments": "declaration",
    "acknowledgements": "declaration",
}

NUMBERED_EQUATION_ENVS = {"equation", "align", "gather", "multline", "eqnarray"}

CITE_COMMANDS = {
    "cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citeyear",
    "citeyearpar", "Citep", "Citet", "parencite", "textcite", "autocite",
    "Autocite", "footcite", "smartcite", "supercite", "nocite", "fullcite",
    "cites", "parencites", "textcites", "citeA", "shortcite",
}

REF_COMMANDS = {
    "ref", "eqref", "autoref", "cref", "Cref", "cpageref", "nameref",
    "pageref", "vref", "autopageref", "labelcref",
}

SECTION_COMMANDS = {"section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}

DECLARATION_HINTS = {
    "funding": "funding",
    "financial support": "funding",
    "acknowledgment": "acknowledgment",
    "acknowledgments": "acknowledgment",
    "acknowledgements": "acknowledgment",
    "conflict of interest": "conflict_of_interest",
    "conflicts of interest": "conflict_of_interest",
    "competing interests": "conflict_of_interest",
    "declaration of competing interest": "conflict_of_interest",
    "data availability": "data_availability",
    "data availability statement": "data_availability",
    "availability of data": "data_availability",
    "ethics statement": "ethics",
    "ethical approval": "ethics",
    "author contributions": "author_contributions",
    "credit authorship contribution statement": "author_contributions",
    "supplementary material": "supplementary",
}


def is_declaration_heading(heading: str, starred: bool = True) -> bool:
    """判断某个章节标题是不是"投稿声明"，而不是正文结构。

    判定刻意保守，避免把正文里真的叫 "Funding models …" 的章节误判：

    * 标题规范化后**完全等于**某个声明提示词 → 判定为声明（编号与否都算）；
    * 标题是**星号章节**（``\\section*``，通常不参与编号）且以提示词开头、
      且词数不多于 6 → 判定为声明。

    这样 ``\\section*{Data Availability Statement}`` 会被识别，
    而 ``\\section{Funding Models for Edge Inference}`` 不会被误伤。
    """
    h = normalize_ws(heading).lower().strip().rstrip(".:： ")
    if not h:
        return False
    for hint in DECLARATION_HINTS:
        if h == hint:
            return True
        if starred and h.startswith(hint) and len(h.split()) <= 6:
            return True
    return False


# --------------------------------------------------------------------------
# 文本级工具
# --------------------------------------------------------------------------


def strip_comments(text: str) -> str:
    """去掉 % 注释，但保留 \\% 与 URL 中的 %。"""
    out = []
    for line in text.split("\n"):
        buf = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and i + 1 < len(line):
                buf.append(line[i : i + 2])
                i += 2
                continue
            if ch == "%":
                break
            buf.append(ch)
            i += 1
        out.append("".join(buf))
    return "\n".join(out)


def read_group(text: str, i: int, opener: str = "{", closer: str = "}") -> tuple[str, int]:
    """从 text[i] == opener 处读取一个配平的分组，返回 (内容, 结束后的下标)。"""
    assert text[i] == opener
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "\\" and j + 1 < len(text):
            j += 2
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return text[i + 1 : j], j + 1
        j += 1
    return text[i + 1 :], len(text)


# 不属于正文的排版命令：出现在最后一节的尾部区间时不应被当成正文文本
_NONBODY_CMDS = re.compile(
    r"\\(?:bibliographystyle|bibliography|addbibresource|printbibliography|"
    r"tableofcontents|maketitle|listoffigures|listoftables|"
    r"begin\s*\{\s*document\s*\}|end\s*\{\s*document\s*\}|clearpage|newpage|"
    r"pagestyle|thispagestyle)"
    r"(?:\s*\[[^\]]*\])?(?:\s*\{[^{}]*\})?"
)


def strip_nonbody(text: str) -> str:
    """去掉 \\bibliography / \\end{document} 等非正文命令，避免污染正文文本。"""
    return _NONBODY_CMDS.sub(" ", text)


@dataclass
class Cmd:
    name: str
    star: bool
    opt: list[str]
    args: list[str]
    start: int
    end: int
    line: int

    @property
    def arg(self) -> str:
        return self.args[0] if self.args else ""


def scan_commands(text: str) -> list[Cmd]:
    """扫描全部命令，记录其（可选参数 / 必选参数）。"""
    cmds: list[Cmd] = []
    line = 1
    i = 0
    n = len(text)
    line_starts = [0]
    for m in re.finditer("\n", text):
        line_starts.append(m.end())

    def line_of(pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    while i < n:
        c = text[i]
        if c == "%" and (i == 0 or text[i - 1] != "\\"):
            nl = text.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if c != "\\":
            i += 1
            continue
        j = i + 1
        if j >= n:
            break
        if text[j].isalpha():
            k = j
            while k < n and text[k].isalpha():
                k += 1
            name = text[j:k]
        else:
            name = text[j]
            k = j + 1
        star = k < n and text[k] == "*"
        if star:
            k += 1
        opt: list[str] = []
        args: list[str] = []
        cursor = k
        while cursor < n:
            while cursor < n and text[cursor] in " \t\r\n":
                cursor += 1
            if cursor < n and text[cursor] == "[":
                inner, cursor = read_group(text, cursor, "[", "]")
                opt.append(inner)
                continue
            if cursor < n and text[cursor] == "{":
                inner, cursor = read_group(text, cursor, "{", "}")
                args.append(inner)
                continue
            break
        cmds.append(Cmd(name=name, star=star, opt=opt, args=args, start=i, end=cursor, line=line_of(i)))
        i = max(cursor, i + 1)
    return cmds


_TEXT_PASSTHROUGH = {
    "textbf", "textit", "textrm", "textsf", "texttt", "textsc", "textsl",
    "emph", "textup", "textmd", "textnormal", "mbox", "hbox", "underline",
    "text", "mathrm", "mathbf", "mathit", "operatorname", "mbox",
}
_TEXT_DROP_NOARG = {
    "noindent", "centering", "small", "footnotesize", "scriptsize", "tiny",
    "large", "Large", "LARGE", "huge", "Huge", "normalsize", "bfseries",
    "itshape", "ttfamily", "rmfamily", "sffamily", "clearpage", "newpage",
    "par", "medskip", "smallskip", "bigskip", "maketitle", "printbibliography",
    "bibliography", "tableofcontents", "raggedright", "linebreak", "vspace",
    "hspace", "vskip", "hskip", "item", "quad", "qquad", "toprule",
    "midrule", "bottomrule", "hline", "centering", "sloppy", "fussy",
}
_LITERAL_REPLACEMENTS = [
    ("\\%", "%"), ("\\&", "&"), ("\\_", "_"), ("\\#", "#"), ("\\$", "$"),
    ("\\{", "{"), ("\\}", "}"), ("\\textbackslash", "\\"),
    ("---", "\u2014"), ("--", "\u2013"), ("``", "\u201c"), ("''", "\u201d"),
    ("~", " "),
]


def detex(text: str, keep_math: bool = True) -> str:
    """把 LaTeX 片段转成可读纯文本（用于模型中的 text 字段与字数统计）。"""
    if not text:
        return ""
    t = strip_comments(text)

    # 逐命令处理
    out: list[str] = []
    i = 0
    n = len(t)
    while i < n:
        c = t[i]
        if c == "\\":
            j = i + 1
            if j >= n:
                break
            if t[j].isalpha():
                k = j
                while k < n and t[k].isalpha():
                    k += 1
                name = t[j:k]
            else:
                name = t[j]
                k = j + 1
            star = k < n and t[k] == "*"
            if star:
                k += 1
            cursor = k
            opt = ""
            args: list[str] = []
            while cursor < n:
                while cursor < n and t[cursor] in " \t\r\n":
                    cursor += 1
                if cursor < n and t[cursor] == "[":
                    opt, cursor = read_group(t, cursor, "[", "]")
                    continue
                if cursor < n and t[cursor] == "{":
                    inner, cursor = read_group(t, cursor, "{", "}")
                    args.append(inner)
                    continue
                break
            if name in _TEXT_PASSTHROUGH and args:
                out.append(detex(args[0], keep_math))
            elif name in CITE_COMMANDS and args:
                out.append("[" + args[0].replace(",", ", ") + "]")
            elif name in REF_COMMANDS and args:
                out.append("{" + args[0] + "}")
            elif name in _TEXT_DROP_NOARG:
                if args:
                    out.append(detex(" ".join(args), keep_math))
                else:
                    out.append(" ")
            elif name == "\\":
                out.append("\n")
            else:
                if args:
                    out.append(detex(" ".join(args), keep_math))
                else:
                    out.append(" ")
            i = max(cursor, i + 1)
            continue
        if c == "$":
            # 行内/行间公式：保留或丢弃
            fence = "$$" if t.startswith("$$", i) else "$"
            close = t.find(fence, i + len(fence))
            if close < 0:
                out.append(c)
                i += 1
                continue
            if keep_math:
                out.append(t[i : close + len(fence)])
            else:
                out.append(" ")
            i = close + len(fence)
            continue
        out.append(c)
        i += 1

    s = "".join(out)
    for pat, rep in _LITERAL_REPLACEMENTS:
        s = s.replace(pat, rep)
    s = re.sub(r"\\([A-Za-z]+)", r"\1", s)  # 残留命令名
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --------------------------------------------------------------------------
# 项目展平
# --------------------------------------------------------------------------


@dataclass
class FlatTex:
    text: str
    line_map: list[tuple[str, int]] = field(default_factory=list)  # 每行 -> (文件, 行号)
    files: list[str] = field(default_factory=list)
    missing_inputs: list[tuple[str, str, int]] = field(default_factory=list)

    def locate(self, line: int) -> tuple[str, int]:
        if 1 <= line <= len(self.line_map):
            return self.line_map[line - 1]
        return ("", 0)


_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]*)\}")


def _is_hidden(path: Path, root: Path) -> bool:
    """判断 path 是否位于 root 下的隐藏目录中。

    注意必须用**相对 root 的路径**判断：否则项目本身放在
    `.tmp`、`.work` 这类目录里时，整棵树都会被误判成隐藏文件。
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts)


def find_main_tex(project_dir: str | Path) -> str | None:
    """识别主 .tex 文件：优先含 \\documentclass 的文件；多个时取引用最多者。"""
    root = Path(project_dir)
    texs = sorted(root.rglob("*.tex")) if root.is_dir() else [root]
    if root.is_file():
        return str(root)
    candidates = []
    for p in texs:
        if _is_hidden(p, root):
            continue
        try:
            text = read_text(p)
        except Exception:
            continue
        if "\\documentclass" in strip_comments(text):
            candidates.append(p)
    if not candidates:
        return None
    if len(candidates) == 1:
        return str(candidates[0])
    # 多个主文件：被 \input 引用的不算主文件，排除掉
    referenced = set()
    for p in texs:
        try:
            for m in _INPUT_RE.finditer(strip_comments(read_text(p))):
                referenced.add(m.group(1).replace("\\", "/").strip())
        except Exception:
            pass
    filtered = [p for p in candidates if p.stem not in referenced and p.name not in referenced]
    return str((filtered or candidates)[0])


def resolve_include(name: str, root: Path, current: Path) -> Path | None:
    raw = name.strip().replace("\\", "/")
    bases = [current.parent, root]
    cands = []
    for b in bases:
        cands.append(b / raw)
        if not raw.lower().endswith(".tex"):
            cands.append(b / (raw + ".tex"))
    for c in cands:
        if c.is_file():
            return c
    return None


def flatten_project(main_file: str | Path, log: Log | None = None) -> FlatTex:
    """递归展开 \\input / \\include，产出展平文本与行映射。"""
    log = log or Log(verbose=False)
    main = Path(main_file).resolve()
    root = main.parent
    flat = FlatTex(text="")
    seen: set[Path] = set()

    def walk(path: Path, depth: int) -> None:
        if depth > 25:
            log.warn(f"\\input 递归过深，已截断: {path}")
            return
        if path in seen:
            return
        seen.add(path)
        flat.files.append(str(path))
        try:
            text = read_text(path)
        except Exception as exc:
            log.warn(f"读取失败 {path}: {exc}")
            return
        for idx, line in enumerate(text.split("\n"), 1):
            clean = strip_comments(line)
            m = _INPUT_RE.search(clean) if not clean.lstrip().startswith("\\documentclass") else None
            if m and not clean.lstrip().startswith("%"):
                target = resolve_include(m.group(1), root, path)
                marker = f"% >>> begin input: {m.group(1)}"
                flat.text += marker + "\n"
                flat.line_map.append((str(path), idx))
                if target is None:
                    flat.missing_inputs.append((m.group(1), str(path), idx))
                    flat.text += f"% !!! missing: {m.group(1)}\n"
                    flat.line_map.append((str(path), idx))
                    continue
                walk(target, depth + 1)
                flat.text += f"% <<< end input: {m.group(1)}\n"
                flat.line_map.append((str(path), idx))
                continue
            flat.text += line + "\n"
            flat.line_map.append((str(path), idx))

    walk(main, 0)
    return flat


# --------------------------------------------------------------------------
# 解析器
# --------------------------------------------------------------------------


class LatexParser:
    def __init__(self, log: Log | None = None):
        self.log = log or Log(verbose=False)

    def parse(self, path: str | Path) -> M.Manuscript:
        p = Path(path).resolve()
        if p.is_dir():
            main = find_main_tex(p)
            if not main:
                raise ValueError(f"在 {p} 中找不到含 \\documentclass 的主 .tex 文件")
        else:
            main = str(p)
        root = Path(main).parent

        flat = flatten_project(main, self.log)
        ms = M.Manuscript(source_format=M.FMT_LATEX, source_path=str(p), main_file=main)
        ms.project_files = [rel(f, root) for f in flat.files]

        body = strip_comments(flat.text)
        cmds = scan_commands(body)
        envs = self._scan_environments(body)

        def loc(line: int) -> M.Span:
            f, ln = flat.locate(line)
            return M.Span(file=rel(f, root) if f else "", line=ln)

        # ---- 前言 ----
        graphics_paths: list[str] = []
        for c in cmds:
            if c.name == "documentclass":
                ms.documentclass = c.arg.strip()
                ms.class_options = [o.strip() for o in (c.opt[0].split(",") if c.opt else []) if o.strip()]
            elif c.name == "usepackage" or c.name == "RequirePackage":
                opts = [o.strip() for o in (c.opt[0].split(",") if c.opt else []) if o.strip()]
                for pkg in (c.arg or "").split(","):
                    pkg = pkg.strip()
                    if pkg:
                        ms.packages.append({"name": pkg, "options": opts})
            elif c.name == "graphicspath":
                graphics_paths.extend(re.findall(r"\{([^}]*)\}", c.arg))
            elif c.name in ("newcommand", "renewcommand", "providecommand", "def", "DeclareMathOperator"):
                ms.custom_macros.append("\\" + c.name + ("*" if c.star else "") + "".join("{" + a + "}" for a in c.args))
            elif c.name == "bibliography":
                ms.bib_resources.extend(x.strip() for x in c.arg.split(",") if x.strip())
                ms.bib_tool = "bibtex"
            elif c.name == "addbibresource":
                ms.bib_resources.append(c.arg.strip())
                ms.bib_tool = "biblatex"
            elif c.name == "bibliographystyle":
                ms.bib_style = c.arg.strip()
            elif c.name == "title":
                ms.metadata.title = normalize_ws(detex(c.arg))
                ms.metadata.title_raw = normalize_ws(c.arg)
            elif c.name in ("author", "Author"):
                ms.metadata.native.setdefault("author_raw", []).append(c.arg)
                parsed = self._parse_authors(c.arg, c.opt)
                ms.metadata.authors.extend(parsed)
                # \author{A\thanks{School of X}} 这种写法把单位内嵌在作者里，
                # 顺手补进 affiliations，否则换模板时会丢掉单位信息
                for a in parsed:
                    txt = a.get("affil_text")
                    if not txt:
                        continue
                    # \thanks{A and B} 表示作者同时属于两个单位，拆开登记
                    for part in re.split(r"\s+and\s+", txt):
                        part = part.strip(" ,;")
                        if part and part not in ms.metadata.affiliations:
                            ms.metadata.affiliations.append(part)
            elif c.name in ("affil", "affiliation", "affiliations", "institute", "address"):
                # 双参数形式 \affiliation{1}{School of ...} 取第二个参数才是单位名称；
                # elsarticle 的 \affiliation[aff1]{School of ...} 标签在可选参数里
                value = c.args[1] if len(c.args) >= 2 else c.arg
                text = normalize_ws(detex(value))
                if text:
                    ms.metadata.affiliations.append(text)
                    key = ""
                    if len(c.args) >= 2:
                        key = normalize_ws(detex(c.args[0]))
                    elif c.opt:
                        key = normalize_ws(detex(" ".join(c.opt)))
                    if key:
                        ms.metadata.doc_meta.setdefault("affiliation_map", {})[key] = text
            elif c.name in ("keywords", "keyword", "keyWords"):
                items = [normalize_ws(x) for x in re.split(r"\\sep|[,;]", c.arg) if normalize_ws(x)]
                ms.metadata.keywords_raw.extend(items)
                ms.metadata.keywords.extend(
                    k.strip() for k in re.split(r"[,;]", detex(c.arg)) if k.strip()
                )
            elif c.name in ("runningtitle", "shorttitle", "markboth"):
                ms.metadata.running_title = normalize_ws(detex(c.arg))
            elif c.name == "correspondingauthor":
                ms.metadata.corresponding = normalize_ws(detex(c.arg))
            elif c.name == "date":
                ms.metadata.doc_meta.setdefault("date", normalize_ws(detex(c.arg)))

        ms.metadata.doc_meta["graphicspath"] = list(graphics_paths)

        # ---- 环境：摘要 / 关键词 ----
        for env in envs:
            if env.name in ("keyword", "keywords", "IEEEkeywords", "keyword*", "keyWord"):
                raw = body[env.body_start : env.body_end]
                items = [normalize_ws(x) for x in re.split(r"\\sep|[,;]", raw) if normalize_ws(x)]
                ms.metadata.keywords_raw.extend(items)
                ms.metadata.keywords.extend(
                    normalize_ws(detex(x)) for x in items
                )
        for env in envs:
            if env.name == "abstract":
                raw = body[env.body_start : env.body_end]
                ms.metadata.abstract_raw = raw.strip("\n").strip()
                text = normalize_ws(detex(raw))
                ms.metadata.abstract = text
                # 结构化摘要：识别 "Background:" 这类小标题
                for m in re.finditer(
                    r"(?:^|\n)\s*\\(?:textbf|emph|textit)\{([^}]{2,40})\}\s*[:：]?", raw
                ):
                    ms.metadata.abstract_structured.setdefault(m.group(1).strip(), "")
                if not ms.metadata.abstract_structured:
                    for m in re.finditer(
                        r"(?m)^\s*([A-Z][A-Za-z \-]{2,30})\s*[:：]\s*(.+)$", detex(raw)
                    ):
                        ms.metadata.abstract_structured[m.group(1).strip()] = normalize_ws(m.group(2))
                break

        # ---- 章节与块 ----
        self._build_sections(ms, body, cmds, envs, loc)

        # ---- 图 / 表 / 公式 ----
        self._build_floats(ms, body, envs, cmds, loc, root, graphics_paths)

        # ---- 引用与交叉引用 ----
        self._build_citations(ms, cmds, loc)

        # ---- 文献 ----
        self._build_bibliography(ms, body, cmds, envs, root, loc)

        # ---- 声明 ----
        self._build_declarations(ms, body, cmds, loc)

        if flat.missing_inputs:
            for name, f, ln in flat.missing_inputs:
                ms.warnings.append(f"\\input/\\include 目标不存在: {name}（{rel(f, root)}:{ln}）")
        if not ms.documentclass:
            ms.warnings.append("未找到 \\documentclass，可能不是完整的主文件")
        if ms.bib_resources:
            for r in ms.bib_resources:
                if not self._find_bib(r, root):
                    ms.warnings.append(f"未找到文献库文件: {r}.bib")
        return ms

    # ---------------------------------------------------------------- 作者

    @staticmethod
    def _parse_authors(raw: str, opt: list[str] | None = None) -> list[dict]:
        r"""尽力从 \author{...} 里拆出作者与单位标记。

        先把单位/脚注分组「寄存」成占位符再切分作者——否则单位文本里出现的
        字面 "and" 或 "\\" 会把一个作者切成两半（\author{Li Chen\thanks{A and B}}
        是常见写法）。

        `opt` 是 \author[...]{...} 的可选参数（如 elsarticle 的 `\author[aff1]{}`），
        作者自身没有单位标记时用它兜底——否则换模板会丢掉作者与单位的对应关系。
        """
        authors: list[dict] = []
        groups: list[str] = []

        def _stash(m):
            groups.append(m.group(0))
            return f"\x00{len(groups) - 1}\x00"

        protected = re.sub(
            r"\\(?:inst|affil|affiliation|affiliations|thanks|footnotemark|footnotetext|email|corref)"
            r"\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}",
            _stash,
            raw,
        )
        chunks = re.split(r"\\and\b|\\\\|\band\b", protected)

        for ch in chunks:
            ch = re.sub(r"\x00(\d+)\x00", lambda m: groups[int(m.group(1))], ch)
            stripped = re.sub(
                r"\\(?:inst|affil|affiliation|affiliations|thanks|footnotemark|footnotetext|email|corref)"
                r"\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}",
                " ",
                ch,
            )
            stripped = re.sub(r"\\(?:inst|affil)\s*(?:\[[^\]]*\])?\s*\d*", " ", stripped)
            name = normalize_ws(detex(stripped))
            name = re.sub(r"\s*\d+(?:\s*,\s*\d+)*\s*$", "", name).strip(" ,;")
            affil = ""
            m = re.search(
                r"\\(?:inst|affil|affiliation|affiliations|thanks)\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}",
                ch,
            )
            if m:
                affil = normalize_ws(detex(m.group(1)))
            if not affil:
                mo = re.match(r"\s*\[([^\]]*)\]", ch)
                if mo:
                    affil = normalize_ws(mo.group(1))
            email = ""
            me = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", ch)
            if me:
                email = me.group(0)
            orcid = ""
            mo2 = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", ch, re.I)
            if mo2:
                orcid = mo2.group(1)
            if not name:
                continue
            entry = {"name": name, "affil": affil, "email": email, "orcid": orcid}
            # 保留单位原文：\author{A\thanks{School of X}} 这种形式下
            # affil 既是「标记」也是「单位文本」，直接记下来便于换模板时复用
            if affil and not re.fullmatch(r"[\d,\s]+", affil):
                entry["affil_text"] = affil
            authors.append(entry)
        if opt:
            opt_text = normalize_ws(detex(" ".join(opt)))
            if opt_text:
                for a in authors:
                    if not a.get("affil"):
                        a["affil"] = opt_text
        return authors

    @staticmethod
    def _find_bib(name: str, root: Path) -> Path | None:
        raw = name.strip().replace("\\", "/")
        for cand in (root / raw, root / (raw + ".bib")):
            if cand.is_file():
                return cand
        return None

    # ------------------------------------------------------------- 环境扫描

    @dataclass
    class Env:
        name: str
        kind: str
        start: int
        body_start: int
        body_end: int
        end: int
        line: int
        args: str = ""

    def _scan_environments(self, text: str) -> list["LatexParser.Env"]:
        """用栈配平 \\begin / \\end，得到每个环境的区间。"""
        begin_re = re.compile(r"\\begin\s*\{([^}]*)\}")
        end_re = re.compile(r"\\end\s*\{([^}]*)\}")
        tokens: list[tuple[int, int, str, str]] = []
        for m in begin_re.finditer(text):
            tokens.append((m.start(), m.end(), "begin", m.group(1)))
        for m in end_re.finditer(text):
            tokens.append((m.start(), m.end(), "end", m.group(1)))
        tokens.sort()
        stack: list[tuple[str, int, int]] = []
        envs: list["LatexParser.Env"] = []
        for start, end, kind, name in tokens:
            if kind == "begin":
                stack.append((name, start, end))
            else:
                # 找到最近的同名 begin
                for idx in range(len(stack) - 1, -1, -1):
                    if stack[idx][0] == name:
                        _, bs, be = stack.pop(idx)
                        line = text.count("\n", 0, bs) + 1
                        envs.append(
                            LatexParser.Env(
                                name=name,
                                kind=ENV_KINDS.get(name, "other"),
                                start=bs,
                                body_start=be,
                                body_end=start,
                                end=end,
                                line=line,
                            )
                        )
                        break
        envs.sort(key=lambda e: e.start)
        return envs

    # -------------------------------------------------------------- 章节

    def _build_sections(self, ms, body, cmds, envs, loc) -> None:
        sec_cmds = [c for c in cmds if c.name in SECTION_COMMANDS]
        boundaries: list[tuple[int, int, M.Cmd]] = []
        for c in sec_cmds:
            boundaries.append((c.start, c.end, c))

        # 投稿声明（Funding / Data Availability / Ethics …）虽然在源码里也是
        # \section*，但它是**声明**而不是正文结构：它由 _build_declarations 单独建模。
        # 若在这里再当成章节，差异报告会把"转换时自动补入的声明占位"误报成
        # "新增章节"，也会让章节层级检查（如 STR010/STR011）读到噪声。
        self._sec_ranges: list[tuple[int, M.Section]] = []

        # 内容区间：章节命令之后，到下一个章节命令之前
        for idx, (start, end, c) in enumerate(boundaries):
            nxt = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(body)
            level = SECTION_COMMANDS[c.name]
            heading = normalize_ws(detex(c.arg))
            if is_declaration_heading(heading, c.star):
                continue
            seg = body[end:nxt]
            s = M.Section(
                id=f"sec:line{c.line}",
                level=level,
                heading=heading,
                number="",
                starred=c.star,
                span=loc(c.line),
            )
            # 紧随其后的 \label
            m = re.match(r"\s*\\label\s*\{([^}]*)\}", seg)
            if m:
                s.labels.append(m.group(1))
            s.blocks = self._segment_blocks(seg, envs, start, nxt, loc, c.line)
            ms.sections.append(s)
            # 位置区间与章节对象一一对应，供图/表/公式归属使用。
            # 注意这里存的是 Section **对象**（id 稍后才会被重写成稳定 id），
            # 不能存索引——否则跳过声明章节后索引会整体错位。
            self._sec_ranges.append((start, s))

        M.renumber_sections(ms.sections)
        self._assign_section_ids(ms)

        if not ms.sections:
            ms.warnings.append("未识别到任何 \\section 结构，正文块未归入章节")

    @staticmethod
    def _assign_section_ids(ms) -> None:
        """把章节 id 从临时行号改成稳定语义 id：优先 \\label，其次编号。"""
        used: set[str] = set()
        for s in ms.sections:
            if s.labels:
                core = re.sub(r"^(sec|sect|chapter|ch)[:.]", "", s.labels[0])
                base = "sec:" + core
            elif s.number:
                base = f"sec:{s.number}"
            elif s.span and s.span.line:
                base = f"sec:line{s.span.line}"
            else:
                base = f"sec:{slug(s.heading) or 'unnamed'}"
            sid = base
            n = 2
            while sid in used:
                sid = f"{base}-{n}"
                n += 1
            used.add(sid)
            s.id = sid

    def _owner(self, pos: int) -> str | None:
        cur = None
        for start, sec in getattr(self, "_sec_ranges", []):
            if start <= pos:
                cur = sec.id
            else:
                break
        return cur


    def _segment_blocks(self, seg: str, envs, seg_start: int, seg_end: int, loc, base_line: int) -> list[M.Block]:
        """把章节正文切成段落块；图表公式环境替换成引用标记块。"""
        # 找出与本章节区间重叠的浮动环境
        holes: list[tuple[int, int, str]] = []
        for env in envs:
            if env.kind in ("figure", "table", "equation", "verbatim", "float", "bibliography"):
                if env.start >= seg_start and env.start < seg_end:
                    holes.append((env.start, env.end, env.kind))
        holes.sort()

        blocks: list[M.Block] = []
        cursor = seg_start
        counter = 0

        def emit_text(chunk: str, offset: int) -> None:
            nonlocal counter
            clean = strip_nonbody(strip_comments(chunk))
            for para in re.split(r"\n\s*\n", clean):
                text = normalize_ws(detex(para))
                if not text:
                    continue
                counter += 1
                blocks.append(
                    M.Block(
                        id=f"blk:{base_line}:{counter}",
                        kind=M.BLOCK_PARAGRAPH,
                        text=text,
                        span=loc(base_line),
                    )
                )

        for hs, he, hk in holes:
            if hs > cursor:
                emit_text(seg[cursor - seg_start : hs - seg_start], cursor)
            kind = {"figure": "figure", "table": "table", "equation": "equation"}.get(hk, "raw")
            counter += 1
            blocks.append(
                M.Block(
                    id=f"blk:{base_line}:{counter}",
                    kind=M.BLOCK_RAW,
                    text="",
                    style=f"env:{hk}",
                    span=loc(base_line),
                    native={"ref_kind": kind, "env": hk, "env_start": hs},
                )
            )
            cursor = he
        if cursor < seg_end:
            emit_text(seg[cursor - seg_start : seg_end - seg_start], cursor)

        # 合并连续空块
        return [b for b in blocks if b.kind != M.BLOCK_PARAGRAPH or b.text]

    # -------------------------------------------------------------- 浮动体

    def _build_floats(self, ms, body, envs, cmds, loc, root: Path, graphics_paths: list[str]) -> None:
        fig_no = tab_no = eq_no = 0

        # 交叉引用：label -> 引用它的位置（用于判断「谁引用了这个图」）
        ref_use: dict[str, list[int]] = {}
        for c in cmds:
            if c.name in REF_COMMANDS and c.arg:
                for key in c.arg.split(","):
                    ref_use.setdefault(key.strip(), []).append(c.start)

        def refs_of(anchors: list[str]) -> list[str]:
            out: list[str] = []
            for a in anchors:
                for pos in ref_use.get(a, []):
                    sid = self._owner(pos)
                    if sid and sid not in out:
                        out.append(sid)
            return out

        for env in envs:
            raw = body[env.start : env.end]
            if env.kind == "figure":
                fig_no += 1
                fid = f"fig:{fig_no}"
                f = M.Figure(id=fid, number=fig_no, env=env.name, span=loc(env.line), section=self._owner(env.start))
                f.native["raw"] = raw[:4000]
                for c in scan_commands(raw):
                    if c.name == "caption":
                        f.caption = normalize_ws(detex(c.arg))
                    elif c.name == "label":
                        f.label = c.arg.strip()
                    elif c.name == "includegraphics":
                        path = c.arg.strip()
                        f.files.append(path)
                        if c.opt:
                            for o in c.opt[0].split(","):
                                if o.strip().startswith("width"):
                                    f.width = o.strip()
                                elif o.strip().startswith("angle"):
                                    f.placement = o.strip()
                    elif c.name in ("subfloat", "subfigure", "subcaptionbox", "includegraphics"):
                        pass
                    elif c.name in ("subcaption", "subref"):
                        pass
                # 子图：\subfloat[..]{..} / \subfigure[..]{..} / \begin{subfigure}
                for m in re.finditer(r"\\subfloat\s*\[([^\]]*)\]\s*\{", raw):
                    f.subfigures.append({"caption": normalize_ws(detex(m.group(1)))})
                for sub in [e for e in envs if e.name in ("subfigure", "subtable") and e.start >= env.start and e.end <= env.end]:
                    sub_raw = body[sub.start : sub.end]
                    cap = ""
                    mc = re.search(r"\\caption\s*\{", sub_raw)
                    if mc:
                        cap, _ = read_group(sub_raw, mc.end() - 1)
                    f.subfigures.append({"caption": normalize_ws(detex(cap)), "env": sub.name})
                f.caption_in_source = bool(f.caption)
                f.referenced_in = refs_of(f.anchors)
                ms.figures.append(f)
            elif env.kind == "table":
                tab_no += 1
                tid = f"tab:{tab_no}"
                t = M.Table(id=tid, number=tab_no, env=env.name, span=loc(env.line), section=self._owner(env.start))
                t.raw = raw[:8000]
                for c in scan_commands(raw):
                    if c.name == "caption":
                        t.caption = normalize_ws(detex(c.arg))
                    elif c.name == "label":
                        t.label = c.arg.strip()
                    elif c.name in ("centering", "small", "footnotesize", "scriptsize"):
                        pass
                    elif c.name in ("tnote", "tablenote", "footnotetext", "footnotemark"):
                        t.footnotes.append(normalize_ws(detex(c.arg)))
                mpos = re.search(r"\\begin\s*\{(tabular\*?|tabularx|longtable)\}\s*(\{[^}]*\})?", raw)
                if mpos:
                    t.colspec = (mpos.group(2) or "").strip("{}")
                    tab_body_start = raw.find("\\end{tabular")
                    tab_raw = raw[mpos.end() : tab_body_start if tab_body_start > 0 else len(raw)]
                    t.n_rows = max(0, len(re.findall(r"\\\\", tab_raw)) )
                    t.n_cols = self._count_cols(t.colspec, tab_raw)
                t.referenced_in = refs_of(t.anchors)
                ms.tables.append(t)
            elif env.kind == "equation":
                raw_body = body[env.body_start : env.body_end]
                labels = [m.group(1) for m in re.finditer(r"\\label\s*\{([^}]*)\}", raw_body)]
                numbered = env.name in NUMBERED_EQUATION_ENVS
                count = max(1, len(labels))
                rows = len(re.findall(r"\\\\", raw_body))
                if env.name in ("align", "align*", "gather", "gather*", "eqnarray", "eqnarray*") and rows > 0:
                    count = max(count, rows if numbered else 1)
                for k in range(count):
                    eq_no += 1
                    eid = f"eq:{eq_no}"
                    eq = M.Equation(
                        id=eid,
                        number=eq_no if numbered else 0,
                        label=labels[k] if k < len(labels) else None,
                        env=env.name,
                        source=normalize_ws(raw_body),
                        section=self._owner(env.start),
                        span=loc(env.line),
                    )
                    eq.native["raw"] = raw_body[:4000]
                    eq.native["numbered"] = numbered
                    eq.referenced_in = refs_of(eq.anchors)
                    ms.equations.append(eq)

    @staticmethod
    def _count_cols(colspec: str, tab_raw: str) -> int:
        spec = re.sub(r"@\{[^}]*\}|>\{[^}]*\}|<\{[^}]*\}|\|[^a-zA-Z]", "", colspec or "")
        n = len(re.findall(r"[lcrpmbX]", spec))
        if n:
            return n
        first_row = tab_raw.split("\\\\")[0] if tab_raw else ""
        if first_row:
            return max(1, len(first_row.split("&")))
        return 0

    # -------------------------------------------------------------- 引用

    def _build_citations(self, ms, cmds, loc) -> None:
        seen: set[tuple[str, int]] = set()
        for c in cmds:
            if c.name in CITE_COMMANDS and c.args:
                for key in re.split(r",", c.args[0]):
                    key = key.strip()
                    if not key:
                        continue
                    k = (key, c.line)
                    if k in seen:
                        continue
                    seen.add(k)
                    ms.citations.append(
                        M.Citation(key=key, command=c.name, section=self._owner(c.start), span=loc(c.line))
                    )
        # 交叉引用清单：供质检判断「未定义的 \ref 目标」与「孤儿图表」
        for c in cmds:
            if c.name in REF_COMMANDS and c.arg:
                for target in c.arg.split(","):
                    target = target.strip()
                    if not target:
                        continue
                    ms.crossrefs.append(
                        {
                            "id": f"xref:{c.line}:{target}",
                            "command": c.name,
                            "target": target,
                            "section": self._owner(c.start),
                            "span": loc(c.line).to_dict(),
                        }
                    )


    # -------------------------------------------------------------- 文献

    def _build_bibliography(self, ms, body, cmds, envs, root: Path, loc) -> None:
        for env in envs:
            if env.name == "thebibliography":
                raw = body[env.body_start : env.body_end]
                for m in re.finditer(r"\\bibitem(?:\[[^\]]*\])?\s*\{([^}]*)\}", raw):
                    key = m.group(1).strip()
                    rest = raw[m.end() :]
                    nxt = re.search(r"\\bibitem", rest)
                    entry = rest[: nxt.start()] if nxt else rest
                    ms.references.append(
                        M.Reference(
                            key=key,
                            origin="latex_manual",
                            fields={"raw": normalize_ws(detex(entry))},
                            raw=entry.strip(),
                            confidence="confirmed",
                            span=loc(env.line),
                        )
                    )
        # .bib 文件在 refs.py 里解析（需要 bibtexparser），这里只登记路径
        ms.metadata.doc_meta["bib_root"] = str(root)

    # -------------------------------------------------------------- 声明

    def _build_declarations(self, ms, body, cmds, loc) -> None:
        found: set[str] = set()
        for c in cmds:
            if c.name in SECTION_COMMANDS or c.name in ("paragraph",):
                title = normalize_ws(detex(c.arg)).lower().rstrip(".:")
                for hint, kind in DECLARATION_HINTS.items():
                    if hint in title and kind not in found:
                        found.add(kind)
                        ms.declarations.append(
                            M.Declaration(kind=kind, present=True, text=normalize_ws(detex(c.arg)), span=loc(c.line))
                        )
            if c.name in (
                "funding", "dataavailability", "conflictsofinterest", "competinginterests",
                "authorcontributions", "ethicsstatement", "acknowledgments",
            ):
                kind = {
                    "funding": "funding",
                    "dataavailability": "data_availability",
                    "conflictsofinterest": "conflict_of_interest",
                    "competinginterests": "conflict_of_interest",
                    "authorcontributions": "author_contributions",
                    "ethicsstatement": "ethics",
                    "acknowledgments": "acknowledgment",
                }[c.name]
                if kind not in found:
                    found.add(kind)
                    ms.declarations.append(
                        M.Declaration(kind=kind, present=True, text=normalize_ws(detex(c.arg)), span=loc(c.line))
                    )
        # 全文关键词扫描（兜底）
        low = detex(body).lower()
        for hint, kind in DECLARATION_HINTS.items():
            if kind in found:
                continue
            if hint in low:
                found.add(kind)
                ms.declarations.append(M.Declaration(kind=kind, present=True, text="", span=M.Span()))
        for kind in ("funding", "conflict_of_interest", "data_availability"):
            if kind not in found:
                ms.declarations.append(M.Declaration(kind=kind, present=False))


# --------------------------------------------------------------------------
# 便捷入口
# --------------------------------------------------------------------------


def parse_latex(path: str | Path, log: Log | None = None) -> M.Manuscript:
    return LatexParser(log).parse(path)


def find_bib_files(ms: M.Manuscript, root: str | Path) -> list[str]:
    """返回实际存在的 .bib 文件绝对路径。"""
    rootp = Path(root)
    out = []
    for name in ms.bib_resources:
        raw = name.strip().replace("\\", "/")
        for cand in (rootp / raw, rootp / (raw + ".bib")):
            if cand.is_file():
                out.append(str(cand))
    return out
