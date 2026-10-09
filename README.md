# SCI Paper Reformatter

把一篇**已经写好**的论文，按目标期刊的规范转换成可直接投稿的稿件，并给出能定位到文件 / 章节 / 图表的**质检报告**与**差异报告**。

LaTeX 与 Word 是**两条平行的原生流水线**：各自用本格式的工具链处理，不把一种格式强转成另一种。

- **正文逐字不改** —— 转换只发生在导言区、元信息、文献命令、章节编号、图表题注标签这类格式位置
- **不猜规则** —— 每条格式判定都能追溯到期刊配置里的某一条，并带来源 URL 与核对状态
- **改不动就保留原文 + 警告** —— 绝不静默丢弃；被替换的原命令以注释留在文件里
- **能力降级要明说** —— 没装 TeX / LibreOffice 时明确写"未执行 + 原因"，不假装通过

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [环境要求与安装](#环境要求与安装)
- [快速开始](#快速开始)
- [两种转换模式](#两种转换模式)
- [命令速查](#命令速查)
- [结果包结构](#结果包结构)
- [典型场景](#典型场景)
- [期刊配置](#期刊配置)
- [质检问题码](#质检问题码)
- [开发与测试](#开发与测试)
- [已知边界](#已知边界)

---

## 它解决什么问题

- 论文被拒或主动改投，需要按另一本期刊的格式重排
- 投稿前自查：图片有效分辨率、文献完整性、强制声明、篇幅与摘要超限
- 需要一份"改了什么 / 没改什么"的差异报告交给合作者复核
- 手上有官网下载的期刊模板，要把自己的稿件装进去

**不适用**：从零撰写论文、语言润色、审稿意见回复、查重、投稿系统代操作。

---

## 环境要求与安装

### 1. 环境要求

| 项 | 要求 |
|---|---|
| Python | 3.10 及以上（本项目在 3.13 上开发与测试） |
| 必需 pip 包 | `python-docx`、`PyYAML`、`lxml` |
| 可选 pip 包 | `Pillow`（图片像素尺寸探测）、`bibtexparser`（`.bib` 解析），缺失时自动降级 |
| 可选外部工具 | TeX 发行版（TeX Live / MiKTeX）、LibreOffice 或 pandoc —— **只影响 PDF 预览** |

装依赖：

```bash
pip install -r requirements.txt
```

`Pillow` 与 `bibtexparser` 在 `requirements.txt` 里是注释状态，需要时去掉注释即可。

### 2. 安装方式

**方式一：作为 WorkBuddy / CodeBuddy 技能安装**（推荐）

```bash
python tools/install_skill.py --install          # 组装并装到 ~/.workbuddy-ai/skills/
python tools/install_skill.py --install --force  # 覆盖已安装的同名技能
python tools/install_skill.py --zip              # 额外打出可分发的 zip
```

`install_skill.py` 用**显式白名单**组装（只收 `SKILL.md`、`scripts/`、`assets/`、`references/`），`tests/`、`samples/`、`dist/` 这类开发产物不会被误打包。装好后在对话里说"帮我把这篇论文改投 XX 期刊"即可触发。

**方式二：当普通命令行工具用**

不需要安装，在仓库根目录直接跑：

```bash
python scripts/sci.py --help
```

---

## 快速开始

```bash
# 1) 环境自检：依赖、TeX/LibreOffice 工具链、可用期刊配置
python scripts/sci.py doctor

# 2) 看有哪些内置期刊配置
python scripts/sci.py journals list

# 3) 一键跑完整流程（解析 → 转换 → 编译 → 质检 → 差异 → 结果包）
python scripts/sci.py run paper/main.tex        --journal ieee-trans       -o out
python scripts/sci.py run paper/manuscript.docx --journal elsevier-generic -o out

# 4) 手上有官网下载的官方模板时，直接指过去（最贴近真实投稿场景）
python scripts/sci.py run paper/main.tex        --template ~/Downloads/elsarticle-template-num.tex -o out
python scripts/sci.py run paper/manuscript.docx --template ~/Downloads/journal-template.docx     -o out
```

`run` 默认**尽可能全自动**：不逐项等确认，跑完直接出报告，把需要作者决定的事情集中列在"需人工确认"清单里。

跑完先看这两个文件：

1. `out/submission/journals/<期刊id>/report.md` —— 结论 + 需人工确认清单
2. `out/submission/journals/<期刊id>/diff.md` —— 确认正文没被动过

`<input>` 可以是目录（LaTeX 项目）、`.tex`、`.docx` 或 `.zip`。
`--journal` 与 `--template` **至少给一个**。

---

## 两种转换模式

| 模式 | 触发方式 | 做法 | 适用 |
|---|---|---|---|
| **定点手术**（默认） | 只给 `--journal` | 在原稿副本上只改已知结构位置（导言区、元信息命令、文献命令、章节编号、题注标签），正文逐字不动 | 目标期刊的文档类与原稿同构 |
| **模板模式** | `--template <文件>`，或配置里写 `output.latex.base: template` | 以官方模板为骨架：**保留模板的 `\documentclass` 与宏包体系**，把原稿的标题/作者/单位/摘要/关键词/正文/声明/文献命令移植进去；模板自带的 `.cls`/`.bst` 一并复制进结果包 | 手上有官网下载的模板；或目标文档类有强制结构（如 Elsevier `elsarticle` 要求元信息必须包在 `\begin{frontmatter}…\end{frontmatter}` 里） |

**分工：期刊配置提供"规则"（图片 DPI、声明要求、篇幅上限…），模板提供"结构"（文档类、命令体系、版式骨架）。**

- 两个都给 = 规则 + 结构都齐
- 只给 `--template` = 只做结构移植，不做期刊规范判定（结果包目录名为 `template-only`）

模板模式的关键行为：

- **模板的版式权威，原稿的内容权威。** 模板导言区原样保留；原稿正文逐字移植。
- **原稿依赖的宏与宏包会一起带过去。** 模板没声明的 `\newcommand` / `\DeclareMathOperator` / `\usepackage` 会被补进导言区，否则正文里的自定义命令直接编译失败；逐条记入转换记录。
- **模板里的示例内容整段清掉并注释保留**，可回溯。
- **摘要 / 标题 / 关键词用 LaTeX 原文**，`\method{}`、`41.7\%`、`$1.9\times$` 不会被抹平。
- **模板自带的文献样式优先**，与期刊配置不一致时提示人工确认，不静默覆盖。
- 结构定位失败（模板里找不到 `\begin{document}` 等）时**降级回定点手术并报 `CVT012`**，不静默失败。

---

## 命令速查

| 命令 | 用途 |
|---|---|
| `doctor` | 自检依赖与工具链，输出能力矩阵（哪些能真做、哪些降级） |
| `inspect <input>` | 只解析，输出统一内容模型 JSON（排查"为什么识别不到某章节"时用） |
| `journals list` / `show <id>` | 列出 / 查看某期刊的生效规则 |
| `journals validate <id>` | 校验配置：必填项、状态合法性、规则来源是否齐全 |
| `journals new <id>` | 生成新期刊配置骨架 |
| `journals path` | 打印三个配置查找目录（内置 / 用户级 / 项目级） |
| `convert <input> --journal <id>` | 只做格式转换，不做质检、不建结果包 |
| `convert <input> --template <文件>` | 只做模板移植（LaTeX 用 `.tex` 主文件，Word 用 `.docx`） |
| `qc <input> --journal <id>` | 只做质量检查 |
| `diff <before> <after>` | 对比两份稿件的内容差异（模型级比较，不是字节比较） |
| `run <input> --journal <id>` | 完整流程 + 结果包 |
| `run <input> --template <文件>` | 完整流程，以官方模板为骨架（`--journal` 可省略） |

全局选项：`--cwd`（查找项目级期刊配置的起点）、`--work`（临时工作目录）、`--quiet`（不输出进度）。
`qc` 另有 `-o`（写 Markdown）、`--json`（结构化结果）、`--markdown`（直接打印）、`--stage`（报告标题里的阶段名）。
`run` 另有 `--no-pdf`（跳过 PDF 预览）。

---

## 结果包结构

```
out/submission/
├── original/                      # 原稿副本，不可覆盖，留证
├── journals/<期刊id>/
│   ├── src/                       # 转换后的源文件（LaTeX 项目 / 或 docx）
│   ├── manuscript_formatted.docx  # Word 流水线的转换结果
│   ├── figures/                   # 抽取出的图片
│   ├── preview.pdf                # PDF 预览（工具链可用时）
│   ├── report.md                  # 质量检查报告（转换后）
│   ├── report-baseline.md         # 质量检查报告（转换前基线）
│   ├── diff.md                    # 差异报告
│   ├── conversion-log.md          # 转换记录（含"未做的改动"）
│   ├── result.json                # 结构化结果，便于脚本消费
│   └── README.md                  # 本包索引
└── manifest.json                  # 累积清单：输入哈希、配置哈希、工具版本
```

其中 `manuscript_formatted.docx` 只在 Word 流水线出现；`figures/` 只在 Word 流水线出现（LaTeX 项目的图片留在 `src/` 内）；`preview.pdf` 只在工具链可用时生成。

`diff.md` 的结论有三种：

| 结论 | 含义 |
|---|---|
| 未检测到内容差异（仅版式/格式发生变化） | 最理想 |
| 正文内容未变；转换过程补入了投稿声明占位 | 正常，补 `[待作者填写]` 即可 |
| 检测到内容差异，详见下文 | **必须逐条核对**，尤其看有没有 `**丢失**` 标记 |

---

## 典型场景

`samples/` 目录里放了三种模式的实跑样例，可直接对照输出：

| 目录 | 场景 |
|---|---|
| `samples/latex-to-elsevier/` | LaTeX 稿件 → Elsevier（走模板模式） |
| `samples/latex-template/` | LaTeX 稿件 + 外部官方模板 |
| `samples/docx-template/` | Word 稿件 + 外部官方模板 |

**A. 用官网下载的官方模板套自己的稿件**

```bash
python scripts/sci.py run paper/main.tex --template ~/Downloads/elsarticle-template-num.tex -o out
```

模板目录里的 `.cls` / `.bst` / `.sty` 会自动复制进结果包，保证结果包**可独立编译**。
`--template` 必须指向**主文件**（LaTeX 的 `.tex`、Word 的 `.docx`）；指错了会报 `CVT012` 并降级回定点手术。

**B. LaTeX 项目改投 IEEE Transactions**

```bash
python scripts/sci.py doctor                                  # 先看有没有 TeX
python scripts/sci.py inspect paper/main.tex -o model.json    # 确认结构识别正确
python scripts/sci.py run paper/main.tex --journal ieee-trans -o out
```

**C. 投稿前只做质检（不改文件）**

```bash
python scripts/sci.py qc paper/manuscript.docx --journal ieee-trans --markdown
python scripts/sci.py qc paper/main.tex --journal ieee-trans -o qc.md --json qc.json
```

**D. 确认转换真的没改内容**

```bash
python scripts/sci.py diff paper/main.tex out/submission/journals/ieee-trans/src -o diff.md
```

比较的是**模型级差异**：两份稿件都解析成同一个 Manuscript 再逐维度比，因此"改了排版但没改内容"能明确区分出来。

更多端到端说明见 [`references/workflow.md`](references/workflow.md)。

---

## 期刊配置

内置 `ieee-trans` / `elsevier-generic` / `springer-generic` 三个，**均为 `needs_confirmation`** —— 它们是按常见投稿要求整理的通用模板，**不是任何一本期刊的官方逐条规范**。使用前请核对目标期刊最新的 Author Guidelines。

`elsevier-generic` 走**模板模式**，套用内置的 `assets/templates/elsarticle-num.tex` 结构骨架。该骨架**不是 Elsevier 官方模板原件**，只是按 `elsarticle.cls` 必需结构写的最小可用骨架；投正式期刊时请用 `--template` 指向官网下载的模板主文件。

新增期刊：

```bash
python scripts/sci.py journals new my-target-journal --name "My Target Journal"
python scripts/sci.py journals validate my-target-journal
```

再按 [`references/journal-config.md`](references/journal-config.md) 填字段，每条规则补 `rule_sources`（URL + 核对状态）。**核不准的宁可留空或保守取值，不要为了"看起来完整"而编造规则。**

配置放哪：

- 只想自己用 → 用户级 `~/.workbuddy-ai/sci-paper-reformatter/journals/`
- 想跟项目一起走 → 项目级 `<项目>/.sci-trans/journals/`
- 两者同名会覆盖内置配置

用 `python scripts/sci.py journals path` 看实际目录。

---

## 质检问题码

报告里的每个问题都带稳定问题码（`REF001`、`FIG020`…），格式为 `类别前缀 + 三位序号`：

| 前缀 | 类别 | 前缀 | 类别 |
|---|---|---|---|
| `FIL` | 文件完整性 | `EQ` | 公式 |
| `STR` | 章节结构 | `REF` | 参考文献 |
| `LAY` | 版式 | `LTX` | LaTeX 编译 |
| `ABS` `LEN` | 摘要与篇幅 | `DOC` | Word 文档 |
| `FIG` | 图片 | `DEC` | 投稿声明 |
| `TAB` | 表格 | `MET` `CFG` | 内容与元信息 |

每个码只对应一个含义和一个级别（🔴 错误 / 🟡 警告 / 🔵 提示）。完整码表与处置建议见 [`references/quality-checks.md`](references/quality-checks.md)。

几个常被误解的：

- `CFG001` —— 所用期刊配置未经逐条核对。内置三个配置都会出现，**属预期行为**。
- `LTX100` —— 本机没有 TeX，**未做真实编译**，只做了静态检查。
- `FIG060` —— 完全没解析到图片，图片检查被**跳过**，不是"通过"。
- `DOC001` —— Word 文档有修订记录。**必须先在 Word 里接受或拒绝全部修订**再投稿。

---

## 开发与测试

```bash
python tests/run_tests.py        # 退出码 0 = 全通过
```

测试是**缺陷注入型**：夹具里刻意埋了重复题注、缺图、文献不匹配、越界引用等缺陷，验证检查项真的能报出来。另有一组 `test_rule_binding` 逐条验证"配置里设了某规则 → 对应检查真的会触发"，防止规则写了却不生效。

代码结构：

```
scripts/sci.py              CLI 入口（doctor/inspect/journals/convert/qc/diff/run）
scripts/sci_core/
  util.py        零依赖基础设施（日志、工具探测、依赖检查）
  model.py       统一论文内容模型（整个流水线的契约）
  issues.py      问题模型 + IssueBag      changes.py  改动记录 ChangeLog
  latex_parse.py / latex_build.py / latex_check.py     LaTeX 流水线
  docx_parse.py  / docx_build.py                       Word 流水线
  journal.py     期刊配置库（三级查找 + 模板解析）
  assets.py      图/表/公式检查            refs.py     参考文献
  qc.py          质检引擎                  diff.py     模型级差异对比
  report.py      报告渲染                  pipeline.py 流水线编排
assets/journals/   期刊配置（YAML）
assets/templates/  内置结构骨架
tests/             测试与夹具
tools/install_skill.py  白名单式组装 + 安装
```

---

## 已知边界

- **不做**：正文改写、语言润色、图片内容重绘、从零生成图表。
- **不做**：把 LaTeX 转成 Word 或反向转换。两条流水线各管各的；确实需要格式互转时请用独立工具。
- **模板模式不做内容级判断**：它把内容搬进模板骨架并记录每一次替换，但不会替你判断"这段内容放在模板的哪个位置更合适"。模板里没有对应锚点的内容会按标题→作者→摘要→关键词的顺序补在元信息区末尾，并在转换记录里说明。
- **PDF 预览**依赖 TeX 发行版（LaTeX）或 LibreOffice/pandoc（Word）；缺失时降级为静态检查并说明原因。
- **图片"有效分辨率"** 由 `像素尺寸 ÷ 印刷宽度` 算出，并区分线条图/照片，**DPI 数值本身不作为唯一判据**。一张标称 300 dpi 的图被放大到 15 cm 宽，有效分辨率可能只有 150 dpi。
- **Word 参考文献**若来自结构化文献字段（EndNote/Zotero 域）可较可靠转换；若是手打普通文本，只能解析后标记为"需人工确认"。
- 内置期刊配置未经逐条核对，报告中会出现 `CFG001` 提示，属预期行为。
