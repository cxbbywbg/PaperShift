"""通用工具：路径、文本、哈希、子进程、外部工具链探测、日志。

本模块只依赖标准库，保证在没有任何第三方包时也能被导入
（用于 `sci.py doctor` 在裸环境下给出可读的诊断）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------


class Log:
    """极简日志器。所有诊断信息走 stderr，stdout 只留给结构化结果。"""

    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose
        self.warnings: list[str] = []

    def info(self, msg: str) -> None:
        if self.verbose:
            print(f"[sci] {msg}", file=sys.stderr)

    def step(self, msg: str) -> None:
        if self.verbose:
            print(f"[sci] -> {msg}", file=sys.stderr)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"[sci][warn] {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        print(f"[sci][error] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# 文本
# --------------------------------------------------------------------------

_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "big5", "cp1252", "latin-1")


def read_text(path: str | os.PathLike, encoding: str | None = None) -> str:
    """读取文本文件，自动尝试常见编码。

    LaTeX 项目经常是 GBK/Big5 混排，直接 utf-8 会抛异常，所以按优先级试。
    """
    p = Path(path)
    data = p.read_bytes()
    if encoding:
        return data.decode(encoding, errors="replace")
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def write_text(path: str | os.PathLike, text: str, encoding: str = "utf-8") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding=encoding, newline="\n")


def normalize_ws(text: str) -> str:
    """折叠所有空白，用于比较与摘要展示。"""
    return re.sub(r"\s+", " ", text or "").strip()


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def slug(text: str, maxlen: int = 60) -> str:
    """把任意标题转成安全的标识符片段。"""
    t = strip_accents(text or "").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if not t:
        t = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:8]
    return t[:maxlen]


def truncate(text: str, n: int = 160) -> str:
    t = normalize_ws(text)
    return t if len(t) <= n else t[: n - 1] + "…"


def looks_like_english(text: str, threshold: float = 0.6) -> bool:
    """粗略判断是否以拉丁字母为主（用于中文期刊/英文期刊的排版差异提示）。"""
    if not text:
        return False
    latin = sum(1 for c in text if "a" <= c.lower() <= "z")
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    total = latin + cjk
    if total == 0:
        return False
    return latin / total >= threshold


# --------------------------------------------------------------------------
# 哈希与文件
# --------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(path: str | os.PathLike, ignore: tuple[str, ...] = (".git", "__pycache__")) -> str:
    """目录内容指纹，用于「输入未变则跳过」与 manifest 复现。"""
    root = Path(path)
    if root.is_file():
        return sha256_file(root)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if any(part in ignore for part in p.parts):
            continue
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(sha256_file(p).encode("ascii"))
    return h.hexdigest()


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n / 1:.1f}{unit}"
        n /= 1024.0  # type: ignore[assignment]
    return f"{n}B"


def rel(path: str | os.PathLike, base: str | os.PathLike) -> str:
    """尽量给出相对路径，失败则回退为绝对路径。"""
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except Exception:
        return str(path).replace("\\", "/")


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def dump_json(obj, path: str | os.PathLike | None = None, indent: int = 2) -> str:
    text = json.dumps(obj, ensure_ascii=False, indent=indent, default=str)
    if path:
        write_text(path, text + "\n")
    return text


def load_json(path: str | os.PathLike):
    return json.loads(read_text(path))


# --------------------------------------------------------------------------
# 外部工具链
# --------------------------------------------------------------------------

_TOOL_CACHE: dict[str, str | None] = {}

_LATEX_KNOWN_DIRS = [
    r"C:\texlive",
    r"C:\Program Files\MiKTeX",
    r"C:\Users\Public\MiKTeX",
    r"C:\Program Files\texlive",
    r"/usr/local/texlive",
    r"/Library/TeX/texbin",
]


def find_tool(name: str, extra_dirs: list[str] | None = None) -> str | None:
    """在 PATH 与常见安装目录中查找可执行文件。"""
    if name in _TOOL_CACHE:
        return _TOOL_CACHE[name]
    found = shutil.which(name)
    if not found:
        dirs = list(extra_dirs or []) + _LATEX_KNOWN_DIRS
        exts = [".exe", ".bat", ".cmd", ""] if os.name == "nt" else [""]
        for d in dirs:
            base = Path(d)
            if not base.exists():
                continue
            for ext in exts:
                for candidate in (
                    base / f"{name}{ext}",
                    base / "bin" / f"{name}{ext}",
                ):
                    if candidate.is_file():
                        found = str(candidate)
                        break
                if found:
                    break
            if found:
                break
    _TOOL_CACHE[name] = found
    return found


def probe_toolchain() -> dict:
    """探测本机可用的工具链，供 doctor 与「能力降级」判断使用。"""
    latex = {}
    for t in ("pdflatex", "xelatex", "lualatex", "latexmk", "bibtex", "biber"):
        latex[t] = find_tool(t)
    office = {t: find_tool(t) for t in ("soffice", "libreoffice", "pandoc")}
    # LibreOffice 默认安装位置
    if not office["soffice"]:
        for cand in (
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ):
            if Path(cand).is_file():
                office["soffice"] = cand
                break
    return {
        "latex": latex,
        "latex_any": any(latex.values()),
        "office": office,
        "docx_to_pdf": bool(office["soffice"] or office["libreoffice"] or office["pandoc"]),
    }


def run_cmd(
    argv: list[str],
    cwd: str | os.PathLike | None = None,
    timeout: int = 300,
    env: dict | None = None,
) -> dict:
    """执行外部命令，永不抛异常（调用方根据 returncode 判断）。"""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace"),
            "stderr": proc.stderr.decode("utf-8", errors="replace"),
            "cmd": " ".join(argv),
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": 127, "stdout": "", "stderr": f"未找到可执行文件: {argv[0]}", "cmd": " ".join(argv)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": 124, "stdout": "", "stderr": f"执行超时({timeout}s)", "cmd": " ".join(argv)}
    except Exception as exc:  # pragma: no cover - 兜底
        return {"ok": False, "returncode": 1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "cmd": " ".join(argv)}


# --------------------------------------------------------------------------
# 依赖自检
# --------------------------------------------------------------------------

REQUIRED_PKGS = {
    "docx": "python-docx",
    "yaml": "PyYAML",
    "lxml": "lxml",
}
OPTIONAL_PKGS = {
    "PIL": "Pillow",
    "bibtexparser": "bibtexparser",
}


def check_deps() -> dict:
    import importlib.util

    missing_req, missing_opt = [], []
    for mod, pkg in REQUIRED_PKGS.items():
        if importlib.util.find_spec(mod) is None:
            missing_req.append(pkg)
    for mod, pkg in OPTIONAL_PKGS.items():
        if importlib.util.find_spec(mod) is None:
            missing_opt.append(pkg)
    return {"missing_required": missing_req, "missing_optional": missing_opt, "ok": not missing_req}
