"""生成测试夹具所需的图片资源。

刻意制造两类图片：
  * pipeline.png / sensitivity.png —— 高分辨率，能通过 DPI 检查
  * energy.png —— 低分辨率（300x180），用于触发「有效分辨率不足」警告
  * ablation.png —— **故意不生成**，用于触发「图片文件缺失」错误
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
FIG = ROOT / "latex_demo" / "figures"


def make(path: Path, size: tuple[int, int], label: str, color: tuple[int, int, int]) -> None:
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, size[0] - 5, size[1] - 5], outline=color, width=3)
    for i in range(1, 6):
        y = int(size[1] * i / 6)
        d.line([(20, y), (size[0] - 20, y)], fill=(220, 220, 220), width=1)
    d.text((24, 20), f"{label}  {size[0]}x{size[1]}", fill=color)
    img.save(path, dpi=(300, 300))
    print(f"  wrote {path.relative_to(ROOT)}  {size[0]}x{size[1]}")


def main() -> int:
    FIG.mkdir(parents=True, exist_ok=True)
    print("生成 LaTeX 夹具图片:")
    make(FIG / "pipeline.png", (2400, 1200), "AST pipeline", (20, 80, 160))
    make(FIG / "sensitivity.png", (1500, 1100), "lambda sweep", (30, 120, 90))
    # 低分辨率：按 3.5 英寸宽印刷时只有约 86 DPI，应被判定为不合格
    make(FIG / "energy.png", (300, 180), "energy", (170, 40, 40))
    print("  (ablation.png 故意缺失，用于触发缺失检查)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
