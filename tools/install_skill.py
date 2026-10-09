#!/usr/bin/env python3
"""把本仓库组装成可分发的 Skill 包，并可选安装到用户级技能目录。

工作区里除了技能本体（`scripts/` `assets/` `SKILL.md` `references/`）还有
`tests/`、`.test_out/`、`.e2e*/` 这类开发产物，不能一股脑拷进技能目录。
所以这里用**显式白名单**组装，而不是"排除法"——将来新增开发目录也不会被误打包。

用法：
    python tools/install_skill.py                 # 只组装到 dist/
    python tools/install_skill.py --install       # 组装并安装到用户级技能目录
    python tools/install_skill.py --install --force
    python tools/install_skill.py --zip           # 额外打出 zip
"""
from __future__ import annotations

import argparse
import shutil
import sys
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

USER_SKILLS_DIR = Path.home() / ".workbuddy-ai" / "skills"


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
        shutil.rmtree(pkg)
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


def install(pkg: Path, force: bool) -> Path:
    dst = USER_SKILLS_DIR / SKILL_NAME
    if dst.exists():
        if not force:
            raise SystemExit(
                f"目标已存在：{dst}\n加 --force 覆盖（会先删除旧目录）。"
            )
        _remove_tree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pkg, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=str(ROOT / "dist"), help="组装输出目录")
    ap.add_argument("--install", action="store_true", help="安装到用户级技能目录")
    ap.add_argument("--force", action="store_true", help="覆盖已安装的同名技能")
    ap.add_argument("--zip", action="store_true", help="额外打出 zip 包")
    args = ap.parse_args()

    dist = Path(args.dist)
    stats = build(dist)
    print(f"已组装：{stats['pkg']}")
    print(f"  文件 {stats['files']} 个，共 {stats['bytes'] / 1024:.1f} KB")

    if args.zip:
        z = make_zip(stats["pkg"], dist)
        print(f"已打包：{z}")

    if args.install:
        dst = install(stats["pkg"], args.force)
        print(f"已安装：{dst}")
        print("提示：技能会在下一次会话中生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
