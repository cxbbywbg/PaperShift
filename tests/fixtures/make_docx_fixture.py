"""生成 Word 夹具：一份典型的「工程类期刊投稿」稿件。

刻意包含这些情况，供质检链路验证：
  * 单栏、A4、常规页边距（与目标期刊的双栏要求不符）
  * 图片分辨率不足（表 2 引用的图是 260x160）
  * 一张图没有被正文引用（Figure 4）
  * 参考文献是**普通文本**（不是 Word 结构化文献域）→ 需人工确认
  * 公式用 OMML 对象插入
  * 缺少 Data Availability 声明
  * 正文引用编号 [3] 在文末文献表里不存在
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "docx_demo"
FIG_DIR = OUT_DIR / "figures"

OMML_SAMPLE = (
    '<m:oMathPara xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<m:oMath>"
    "<m:r><m:t>E(k)=</m:t></m:r>"
    "<m:f><m:num><m:r><m:t>\u03a3\u03c3</m:t></m:r><m:sSub><m:e><m:r><m:t/></m:r></m:e>"
    "<m:sub><m:r><m:t>i</m:t></m:r></m:sub></m:sSub></m:num>"
    "<m:den><m:r><m:t>\u03a3\u03c3</m:t></m:r></m:den></m:f>"
    "</m:oMath></m:oMathPara>"
)


def make_fig(path: Path, size: tuple[int, int], label: str) -> None:
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([3, 3, size[0] - 4, size[1] - 4], outline=(30, 70, 140), width=2)
    d.text((10, 10), f"{label} {size[0]}x{size[1]}", fill=(30, 70, 140))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, dpi=(300, 300))


def add_para(doc, text, style=None, align=None, size=None, bold=False, italic=False):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    run.bold = bold
    run.italic = italic
    if size:
        run.font.size = Pt(size)
    if align is not None:
        p.alignment = align
    return p


def add_caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.font.size = Pt(9)
    run.italic = True
    return p


def add_omml(doc):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    from lxml import etree

    p._p.append(etree.fromstring(OMML_SAMPLE))
    return p


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_fig(FIG_DIR / "pipeline.png", (1800, 900), "pipeline")
    make_fig(FIG_DIR / "sensitivity.png", (1600, 1100), "sensitivity")
    make_fig(FIG_DIR / "energy.png", (260, 160), "low-res energy")
    make_fig(FIG_DIR / "unreferenced.png", (1400, 900), "not cited")

    doc = Document()

    # 页面设置：A4 单栏，2.5cm 页边距
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.left_margin = Cm(2.5)
    sec.right_margin = Cm(2.5)
    sec.top_margin = Cm(2.5)
    sec.bottom_margin = Cm(2.5)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing = 1.5

    add_para(
        doc,
        "Adaptive Spectral Truncation for Low-Power Edge Inference of Convolutional Networks",
        align=WD_ALIGN_PARAGRAPH.CENTER, size=16, bold=True,
    )
    add_para(doc, "Wei Zhang, Li Chen, Marcus Hale", align=WD_ALIGN_PARAGRAPH.CENTER, size=12)
    add_para(
        doc,
        "School of Automation, Harbin Institute of Technology, Harbin 150001, China",
        align=WD_ALIGN_PARAGRAPH.CENTER, size=10,
    )

    doc.add_heading("Abstract", level=1)
    add_para(
        doc,
        "Deploying convolutional neural networks on battery-powered edge devices is limited "
        "by the energy cost of dense matrix multiplication. This paper introduces Adaptive "
        "Spectral Truncation (AST), which estimates the spectral decay of each layer's weight "
        "matrix at calibration time and truncates the trailing singular subspace. On CIFAR-10 "
        "and a 100-class ImageNet subset, AST reduces multiply-accumulate operations by 41.7% "
        "while losing 0.6 percentage points of top-1 accuracy. AST is complementary to "
        "quantization and can be stacked with 8-bit post-training quantization for an "
        "additional 1.9x energy reduction.",
    )
    add_para(doc, "Keywords: edge inference, model compression, spectral methods, energy efficiency")

    doc.add_heading("1 Introduction", level=1)
    add_para(
        doc,
        "Convolutional neural networks have become the default architecture for perception on "
        "embedded platforms [1, 2]. The energy budget of a battery-powered node is dominated by "
        "the multiply-accumulate operations of dense convolutions [3]. Prior work reduces this "
        "cost through pruning, quantization [4], and knowledge distillation.",
    )
    doc.add_heading("1.1 Contributions", level=2)
    add_para(
        doc,
        "This paper makes three contributions. First, we propose AST, a calibration-time "
        "spectral truncation scheme that requires no retraining. Second, we derive a closed-form "
        "expression for the optimal truncation rank from the noise floor of the calibration set. "
        "Third, we demonstrate a 41.7% reduction in multiply-accumulate operations.",
    )

    doc.add_heading("2 Method", level=1)
    doc.add_heading("2.1 Spectral decay model", level=2)
    add_para(
        doc,
        "Let W be the flattened kernel of a convolutional layer and let the singular values be "
        "ordered in decreasing magnitude. We model the decay as a power law with an additive "
        "noise floor, given by",
    )
    add_omml(doc)
    add_caption(doc, "Equation (1)")
    add_para(
        doc,
        "where eta is the standard deviation of the calibration residual. Fitting this model by "
        "least squares yields the three parameters.",
    )

    p = doc.add_paragraph()
    p.add_run().add_picture(str(FIG_DIR / "pipeline.png"), width=Cm(14))
    add_caption(doc, "Figure 1. Overview of AST. Calibration data is passed through the frozen "
                     "network and per-layer spectra are fitted.")

    doc.add_heading("2.2 Truncation rank", level=2)
    add_para(
        doc,
        "The energy retained by keeping the first k components defines the truncation rank, as "
        "shown in Figure 1 and Figure 2. The multiplier lambda trades accuracy against computation.",
    )
    p = doc.add_paragraph()
    p.add_run().add_picture(str(FIG_DIR / "sensitivity.png"), width=Cm(12))
    add_caption(doc, "Figure 2. Sensitivity of top-1 accuracy to the multiplier lambda.")

    doc.add_heading("2.3 Complexity", level=2)
    add_para(doc, "The additional calibration cost per layer is summarized in Table 1.")
    add_caption(doc, "Table 1. Per-layer calibration cost for the ResNet-18 backbone.")
    t1 = doc.add_table(rows=6, cols=4)
    t1.style = "Table Grid"
    data1 = [
        ["Stage", "Params (M)", "SVD (ms)", "Peak RAM (MB)"],
        ["Stem", "0.009", "4.1", "3.2"],
        ["Stage 1", "0.147", "22.8", "11.5"],
        ["Stage 2", "0.525", "88.3", "40.1"],
        ["Stage 3", "2.100", "301.7", "158.4"],
        ["Stage 4", "8.400", "1187.2", "612.9"],
    ]
    for r, row in enumerate(data1):
        for c, val in enumerate(row):
            cell = t1.cell(r, c)
            cell.text = val
            if r == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True

    doc.add_heading("3 Results", level=1)
    add_para(
        doc,
        "Table 2 reports accuracy and operation counts. AST reduces multiply-accumulate "
        "operations by 41.7% on ResNet-18 while losing 0.6 percentage points of top-1 accuracy. "
        "Stacking with 8-bit post-training quantization yields a further reduction, as shown in "
        "Figure 3.",
    )
    add_caption(doc, "Table 2. Top-1 accuracy and multiply-accumulate operations on CIFAR-10.")
    t2 = doc.add_table(rows=5, cols=4)
    t2.style = "Table Grid"
    data2 = [
        ["Method", "Backbone", "Top-1 (%)", "MAC (G)"],
        ["Dense baseline", "ResNet-18", "94.8", "0.557"],
        ["Magnitude pruning", "ResNet-18", "94.1", "0.371"],
        ["AST (ours)", "ResNet-18", "94.2", "0.325"],
        ["AST + 8-bit PTQ", "ResNet-18", "93.8", "0.325"],
    ]
    for r, row in enumerate(data2):
        for c, val in enumerate(row):
            cell = t2.cell(r, c)
            cell.text = val
            if r == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True

    p = doc.add_paragraph()
    p.add_run().add_picture(str(FIG_DIR / "energy.png"), width=Cm(9))
    add_caption(doc, "Figure 3. Energy per inference as a function of the accuracy budget.")

    p = doc.add_paragraph()
    p.add_run().add_picture(str(FIG_DIR / "unreferenced.png"), width=Cm(12))
    add_caption(doc, "Figure 4. Ablation over the truncation schedule.")

    doc.add_heading("4 Conclusion", level=1)
    add_para(
        doc,
        "We presented AST, a calibration-time spectral truncation scheme for low-power edge "
        "inference. It reduces multiply-accumulate operations by 41.7% on ResNet-18 with a 0.6 "
        "percentage point accuracy loss, requires no retraining, and composes with post-training "
        "quantization.",
    )

    doc.add_heading("Funding", level=1)
    add_para(
        doc,
        "This work was supported by the National Key R&D Program of China under Grant 2023YFB4702800.",
    )
    doc.add_heading("Declaration of Competing Interest", level=1)
    add_para(
        doc,
        "The authors declare that they have no known competing financial interests or personal "
        "relationships that could have appeared to influence the work reported in this paper.",
    )

    doc.add_heading("References", level=1)
    refs = [
        "[1] LeCun Y, Bengio Y, Hinton G. Deep learning. Nature. 2015;521(7553):436-444.",
        "[2] He K, Zhang X, Ren S, Sun J. Deep residual learning for image recognition. In: "
        "Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition. 2016. "
        "p. 770-778.",
        "[4] Jacob B, Kligys S, Chen B, et al. Quantization and training of neural networks for "
        "efficient integer-arithmetic-only inference. In: CVPR. 2018. p. 2704-2713.",
        "[5] Han S, Pool J, Tran J, Dally W. Learning both weights and connections for efficient "
        "neural networks. In: Advances in Neural Information Processing Systems. 2015. p. 1135-1143.",
    ]
    for r in refs:
        add_para(doc, r)

    target = OUT_DIR / "manuscript.docx"
    doc.save(str(target))
    print(f"  已生成 {target}")
    print(f"  图片：{[p.name for p in sorted(FIG_DIR.glob('*.png'))]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
