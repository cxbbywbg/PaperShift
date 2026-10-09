#!/usr/bin/env python3
"""把本仓库组装成可分发的 Skill 包，并安装到各 AI 客户端的技能目录。

工作区里除了技能本体（`scripts/` `assets/` `SKILL.md` `references/`）还有
`tests/`、`.test_out/`、`.e2e*/` 这类开发产物，不能一股脑拷进技能目录。
所以这里用**显式白名单**组装，而不是"排除法"——将来新增开发目录也不会被误打包。

本技能遵循 Agent Skills 开放标准（一个目录 + 一份 `SKILL.md`，frontmatter 只有
`name` / `description` 是必填），各客户端只是**存放位置**不同，因此同一份产物
可以原样投放到多个环境，不需要为每个客户端做格式转换。

用法：
    python tools/install_skill.py                       # 只组装到 dist/
    python tools/install_skill.py --list-targets        # 看支持哪些客户端及各自目录
    python tools/install_skill.py --install             # 装到 WorkBuddy / CodeBuddy（默认）
    python tools/install_skill.py --install --target claude
    python tools/install_skill.py --install --target claude,codex --force
    python tools/install_skill.py --install --target all
    python tools/install_skill.py --install --target cursor --scope project --project-dir .
    python tools/install_skill.py --zip                 # 额外打出 zip
"""
from __future__ import annotations

import argparse
import shutil
import sys
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "sci-paper-reformatter"

# 组装白名单：(源相对路径, 目标相对路径)
INCLUDE_FILES = [
    ("SKILL.md", "SKILL.md"),
    ("scripts/sci.py", "scripts/sci.py"),
]
INCLUDE_DIRS = [
    ("scripts/sci_core", "scripts/sci_core"),
    ("assets/journals", "assets/journals"),
    ("assets/templates", "assets/templates"),
    ("references", "references"),
]
# 不随包分发的文件
SKIP_NAMES = {"__pycache__", ".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo"}

# 各客户端的技能目录约定。
#   user_rel    —— 相对用户主目录（个人级 / 全局）
#   project_rel —— 相对项目根目录（随仓库提交，团队共享）
# 依据：各客户端官方文档（见 README「安装方式」一节的来源链接）。
TARGETS: dict[str, dict] = {
    "workbuddy": {
        "label": "WorkBuddy / CodeBuddy",
        "user_rel": ".workbuddy-ai/skills",
        "project_rel": ".workbuddy-ai/skills",
        "note": "WorkBuddy 与 CodeBuddy 共用该目录",
    },
    "claude": {
        "label": "Claude Code",
        "user_rel": ".claude/skills",
        "project_rel": ".claude/skills",
        "note": "个人级 ~/.claude/skills；项目级 <项目>/.claude/skills",
    },
    "codex": {
        "label": "OpenAI Codex CLI",
        "user_rel": ".agents/skills",
        "project_rel": ".agents/skills",
        "note": "用户级 ~/.agents/skills；仓库级 .agents/skills（向上扫描到仓库根）",
    },
    "cursor": {
        "label": "Cursor",
        "user_rel": ".cursor/skills",
        "project_rel": ".cursor/skills",
        "note": "全局 ~/.cursor/skills；项目级 <项目>/.cursor/skills",
    },
    "gemini": {
        "label": "Gemini CLI",
        "user_rel": ".gemini/skills",
        "project_rel": ".gemini/skills",
        "note": "用户级 ~/.gemini/skills；工作区 .gemini/skills；需开启 experimental.skills",
    },
}

# 别名 → 规范 id
TARGET_ALIASES = {
    "codebuddy": "workbuddy",
    "wb": "workbuddy",
    "claude-code": "claude",
    "claudecode": "claude",
    "anthropic": "claude",
    "openai-codex": "codex",
    "gemini-cli": "gemini",
    "googlegemini": "gemini",
}

DEFAULT_TARGET = "workbuddy"


def _copy_tree(src: Path, dst: Path, stats: dict) -> None:
    for p in sorted(src.rglob("*")):
        if p.is_dir():
            continue
        if any(part in SKIP_NAMES for part in p.parts):
            continue
        if p.suffix in SKIP_SUFFIXES:
            continue
        rel = p.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        stats["files"] += 1
        stats["bytes"] += target.stat().st_size


def build(dist: Path, clean: bool = True) -> dict:
    pkg = dist / SKILL_NAME
    if clean and pkg.exists():
        _remove_tree(pkg)
    pkg.mkdir(parents=True, exist_ok=True)
    stats = {"files": 0, "bytes": 0, "pkg": pkg}

    for rel_src, rel_dst in INCLUDE_FILES:
        src = ROOT / rel_src
        if not src.is_file():
            raise SystemExit(f"缺少必需文件：{src}")
        dst = pkg / rel_dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        stats["files"] += 1
        stats["bytes"] += dst.stat().st_size

    for rel_src, rel_dst in INCLUDE_DIRS:
        src = ROOT / rel_src
        if not src.is_dir():
            raise SystemExit(f"缺少必需目录：{src}")
        _copy_tree(src, pkg / rel_dst, stats)

    return stats


def make_zip(pkg: Path, dist: Path) -> Path:
    out = dist / f"{SKILL_NAME}.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                z.write(p, Path(SKILL_NAME) / p.relative_to(pkg))
    return out


def _remove_tree(path: Path) -> None:
    """删除目录树，带兜底。

    某些环境会把 `shutil.rmtree` 拦截成「移到回收站」，而回收站在无桌面
    会话下会失败。这里退化为逐个文件 unlink + 逐个目录 rmdir，效果相同
    但不需要回收站。
    """
    try:
        shutil.rmtree(path)
        return
    except Exception as exc:
        print(f"提示：常规删除失败（{type(exc).__name__}），改用逐文件删除")

    errors: list[str] = []
    for p in sorted(path.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        try:
            if p.is_dir():
                p.rmdir()
            else:
                p.unlink()
        except OSError as exc:
            errors.append(f"{p}: {exc}")
    try:
        path.rmdir()
    except OSError as exc:
        errors.append(f"{path}: {exc}")
    if errors:
        raise SystemExit("清理旧安装目录失败：\n  " + "\n  ".join(errors[:5]))


def resolve_targets(spec: str) -> list[str]:
    """把 --target 的取值（逗号分隔，可用 all）解析成规范的 id 列表。"""
    names: list[str] = []
    for raw in spec.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token == "all":
            names.extend(TARGETS)
            continue
        canonical = TARGET_ALIASES.get(token, token)
        if canonical not in TARGETS:
            raise SystemExit(
                f"未知目标：{raw}\n"
                f"可用：{', '.join(TARGETS)}（或用 all）\n"
                f"查看详情：python tools/install_skill.py --list-targets"
            )
        names.append(canonical)

    seen: set[str] = set()
    ordered: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def target_root(name: str, scope: str, project_dir: Path) -> Path:
    spec = TARGETS[name]
    rel = spec["user_rel"] if scope == "user" else spec["project_rel"]
    base = Path.home() if scope == "user" else project_dir
    return base / Path(rel)


def install(pkg: Path, dest: Path, force: bool) -> Path:
    dst = dest / SKILL_NAME
    if dst.exists():
        if not force:
            raise SystemExit(
                f"目标已存在：{dst}\n加 --force 覆盖（会先删除旧目录）。"
            )
        _remove_tree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pkg, dst)
    return dst


def _disp_width(text: str) -> int:
    """按终端显示宽度算字符串长度（中日韩全角字符占 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_width(text))


def print_targets() -> None:
    print(f"技能名：{SKILL_NAME}")
    print(f"当前主目录：{Path.home()}")
    print()
    cols = ["目标", "客户端", "个人级（相对 ~）", "项目级（相对项目根）"]
    rows = [
        [name, spec["label"], spec["user_rel"], spec["project_rel"]]
        for name, spec in TARGETS.items()
    ]
    widths = [
        max(_disp_width(cols[i]), *(_disp_width(r[i]) for r in rows))
        for i in range(len(cols))
    ]
    print("  ".join(_pad(c, w) for c, w in zip(cols, widths)).rstrip())
    print("-" * (sum(widths) + 2 * (len(cols) - 1)))
    for r in rows:
        print("  ".join(_pad(c, w) for c, w in zip(r, widths)).rstrip())
    print()
    for name, spec in TARGETS.items():
        if spec["note"]:
            print(f"  {name}: {spec['note']}")
    print()
    print("别名：" + "，".join(f"{a} → {b}" for a, b in TARGET_ALIASES.items()))
    print("提示：同一份 SKILL.md 通用，安装只是把目录放到各客户端认识的位置。")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="组装并安装 SCI Paper Reformatter 到各 AI 客户端的技能目录",
    )
    ap.add_argument("--dist", default=str(ROOT / "dist"), help="组装输出目录")
    ap.add_argument("--install", action="store_true", help="安装到技能目录")
    ap.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"安装目标，逗号分隔，可用 all（默认 {DEFAULT_TARGET}）",
    )
    ap.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="user=个人级（相对用户主目录，默认）；project=项目级（相对 --project-dir）",
    )
    ap.add_argument(
        "--project-dir",
        default=".",
        help="--scope project 时的项目根目录（默认当前目录）",
    )
    ap.add_argument("--force", action="store_true", help="覆盖已安装的同名技能")
    ap.add_argument("--zip", action="store_true", help="额外打出 zip 包")
    ap.add_argument(
        "--list-targets", action="store_true", help="列出支持的目标与目录约定后退出"
    )
    args = ap.parse_args()

    if args.list_targets:
        print_targets()
        return 0

    dist = Path(args.dist)
    stats = build(dist)
    print(f"已组装：{stats['pkg']}")
    print(f"  文件 {stats['files']} 个，共 {stats['bytes'] / 1024:.1f} KB")

    if args.zip:
        z = make_zip(stats["pkg"], dist)
        print(f"已打包：{z}")

    if args.install:
        names = resolve_targets(args.target)
        project_dir = Path(args.project_dir).resolve()
        if args.scope == "project":
            print(f"安装范围：项目级（{project_dir}）")
        done: set[str] = set()
        for name in names:
            dest = target_root(name, args.scope, project_dir)
            key = str(dest.resolve()).lower()
            if key in done:  # 不同目标可能指向同一目录，避免重复安装
                print(f"已跳过 {name}（与前面目标同目录：{dest}）")
                continue
            done.add(key)
            installed = install(stats["pkg"], dest, args.force)
            print(f"已安装 [{name}] {TARGETS[name]['label']}：{installed}")
        print("提示：技能会在下一次会话中生效（部分客户端需重启）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
