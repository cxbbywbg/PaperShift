---
name: sci-paper-reformatter
description: "SCI/SCIE 论文跨期刊转投的格式转换与投稿适配。当用户要把已写好的论文从一本期刊转到另一本期刊投稿（改投/转投/换刊），需要按目标期刊官方规范调整 LaTeX 项目或 Word 稿件、处理图片与参考文献格式、并在投稿前做质量自查时使用。覆盖：期刊规范获取与结构化、跨期刊格式转换、图片/表格/公式检查、参考文献一致性、投稿前质检报告、转换前后差异报告。LaTeX 与 Word 为两条原生流水线，支持 IEEE / Elsevier / Springer 类期刊配置，也可按模板新增期刊。"
agent_created: true
---

# SCI Paper Reformatter

把一篇**已经写好**的论文，按目标期刊的规范转换成可直接投稿的稿件；交付前给出能定位到文件/章节/图表的质量检查报告和差异报告。**LaTeX 与 Word 是两条平行的原生流水线**，各自用本格式的工具链处理，不把一种格式强转成另一种。

## 何时使用

- 论文被拒或主动改投，需要按另一本期刊的格式重排
- 投稿前自查：图片分辨率、文献完整性、强制声明、篇幅与摘要超限
- 需要一份"改了什么 / 没改什么"的差异报告交给合作者复核
- 手上是 LaTeX 项目，但目标期刊只收 Word；或反过来

**不适用**：从零撰写论文、语言润色、审稿意见回复、查重、投稿系统代操作。

## 核心原则（不可违背）

1. **不改写科学内容。** 正文文字逐字保留。转换只发生在导言区、元信息、文献命令、章节编号、图表题注标签这类**格式位置**。
2. **不猜规则。** 每条格式判定都必须能追溯到期刊配置里的某一条，并带上来源 URL 与核对状态。配置没写的不做判断，也不编造要求。
3. **改不动就保留原文并警告。** 无法转换的内容原样保留，同时报一条问题说明原因，绝不静默丢弃或删改。
4. **每次改动留痕。** 所有实质性修改进转换记录，含改动前、改动后、原因、依据；被替换的原命令以注释形式留在文件里备查。
5. **能力降级要明说。** 机器上没有 TeX 发行版 / LibreOffice / pandoc 时，跳过编译与 PDF 预览，并在报告里写清"未执行 + 原因"，**不假装通过**。
6. **缺作者信息就留占位，不代笔。** 缺声明、缺 DOI 这类必须由作者提供的内容，插入 `[待作者填写]` 占位并标记为需作者确认。

## 快速开始

```bash
# 1) 环境自检：依赖、TeX/LibreOffice 工具链、可用期刊配置
python scripts/sci.py doctor

# 2) 看有哪些期刊配置
python scripts/sci.py journals list

# 3) 一键跑完整流程（解析 → 转换 → 编译 → 质检 → 差异 → 结果包）
python scripts/sci.py run paper/main.tex --journal ieee-trans -o out
python scripts/sci.py run paper/manuscript.docx --journal elsevier-generic -o out

# 4) 手上有官网下载的官方模板时，直接指过去（最贴近真实投稿场景）
python scripts/sci.py run paper/main.tex --template ~/Downloads/elsarticle-template-num.tex -o out
python scripts/sci.py run paper/manuscript.docx --template ~/Downloads/journal-template.docx -o out

# 5) 原稿和模板都是压缩包（Overleaf 导出 + 官网 kit）——不用手工解压
python scripts/sci.py run ~/Downloads/my-paper.zip --template ~/Downloads/elsarticle.zip -o out
# 包内有多份模板时指定用哪一份
python scripts/sci.py run ~/Downloads/my-paper.zip \
    --template '~/Downloads/elsarticle.zip::elsarticle-template-num.tex' -o out
```

`run` 默认**尽可能全自动**：不逐项等确认，跑完直接给出报告，把需要作者决定的事情集中列在"需人工确认"清单里。

## 两种转换模式

| 模式 | 触发方式 | 做法 | 适用 |
|---|---|---|---|
| **定点手术**（默认） | 只给 `--journal` | 在原稿副本上只改已知结构位置（导言区、元信息命令、文献命令、章节编号、题注标签），正文逐字不动 | 目标期刊的文档类与原稿同构（如 IEEEtran ↔ 其他单文件类） |
| **模板模式** | `--template <文件或压缩包>`，或配置里写 `output.latex.base: template` | 以官方模板为骨架：**保留模板的 `\documentclass` 与宏包体系**，把原稿的标题/作者/单位/摘要/关键词/正文/声明/文献命令移植进去；结果包只带编译真正用到的 `.cls`/`.bst` | 手上有官网下载的模板；或目标文档类有强制结构（如 Elsevier `elsarticle` 要求元信息必须包在 `\begin{frontmatter}…\end{frontmatter}` 里） |

`--template` 接受三种形态，**都不需要事先手工解压**：

| 形态 | 写法 | 说明 |
|---|---|---|
| 单文件 | `--template path/to/main.tex` | LaTeX 用 `.tex` 主文件，Word 用 `.docx` |
| 压缩包 | `--template ~/Downloads/elsarticle.zip` | 自动解包、挑出包内主文件；包内 `.cls`/`.bst` 随结果包提供 |
| 包内指定 | `--template 'kit.zip::elsarticle-template-num.tex'` | 包内有多份模板时，明确指定用哪一份 |

模板模式的关键行为：

- **模板的版式权威，原稿的内容权威。** 模板导言区原样保留；原稿正文逐字移植。
- **原稿依赖的宏与宏包会一起带过去 —— 但模板已有的不重复带。** 模板没声明的 `\newcommand`、`\DeclareMathOperator`、`\usepackage` 会被补进导言区，否则正文里的自定义命令会直接编译失败；这些补入逐条记入转换记录。判定「模板有没有」时**同时读模板主文件与它的 `.cls`/`.sty`** —— 期刊类常把 `natbib`、`\fnm`、`\theorem` 写在类文件里，只看主文件会重复带入，触发 `Option clash` / `already defined`。被注释掉的 `%\usepackage{...}` 不算依赖，不会被激活；自定义宏按花括号配平**整条**搬运，跨行定义不会被截成半句。
- **正文里的原文档类专有命令会被注释保留**（如 Elsevier CAS 的 `\printcredits`，`sn-jnl.cls` 没有这个命令）。只在目标模板确实没定义、且它独占一行时才动手，并记 `CVT030`。
- **模板里的示例内容整段清掉并注释保留**（示例作者、示例摘要、示例正文），可回溯。
- **摘要/标题/关键词用 LaTeX 原文**，`\method{}`、`41.7\%`、`$1.9\times$` 不会被抹平。
- **模板自带的文献样式优先**，与期刊配置不一致时提示人工确认，不静默覆盖。
- 配置里的 `packages_add` 在模板模式下**不强制添加**（避免 option clash），转换记录里会明确写"未做的改动"。
- 结构定位失败（模板里找不到 `\begin{document}` 等）时**降级回定点手术并报 `CVT012`**，不静默失败。
- **模板压缩包自动解包**：`--template kit.zip` 等价于「手工解压后指向包内主文件」，结果包内容逐字节一致。包内 `readme`、作者须知、`__MACOSX` 垃圾不复制。
- **包内同族版式默认各出一份**：Elsevier CAS 的单栏 `cas-sc` / 双栏 `cas-dc`、elsarticle 的 `num` / `harv` 是同一份模板的不同版式——结构一致，只有文档类或引用样式不同。工具按「文件名剥掉版式标记后同名同目录」识别成族，**每份各产出一个结果包**（`journals/template-only-sc/`、`...-dc/`），一次就能对比排版。用 `kit.zip::包内路径` 指定则只出指定那份。
- **包内多份候选但不同族时报 `CVT013`** 并列出候选，**这一条必须人工过目**；不对就用 `::` 指定。
- **模板主文件按「命名分档 + 体积」挑**：`template`/`main`/`manuscript` 加分，`sample`/`example`/`doc`/`manual` 减分——官方 kit 里体积最大的常常是说明书（实测 CAS 包最大的合法文档是 18.5 KB 的 `elsdoc-cas.tex`，真模板只有 4.9 KB），只看体积会选错。**原稿侧相反**：作者的正文一定是包里最大的 `.tex`（实测是第二名的 8 倍），按体积挑、并排除被 `\input` 引用的文件。
- **模板辅助文件按需复制**：结果包只装**编译真的会读到**的文件 —— 被正文（含 `\input` 进来的子文件、类文件）引用到的图片、`.bib`、`.cls`/`.sty`/`.bst`。官方 kit 里的 `sample.pdf`、`doc/`、`thumbnails/`、另一版式样例，以及原稿包里混进来的旧稿副本（`Response_to_Reviewers.tex`、`Title_Page.tex`）、废弃图片、没被引用的 `.bib`，**一律不进结果包**。判定是递归的（`.cls` 里 `\RequirePackage` 的 `.sty` 也要带）；注释掉的引用不算引用；文献样式若由文档类选项选出（Springer 的 `sn-mathphys-num`）按选项名也认。原稿**完整**副本留在 `submission/original/`，转换记录里逐条列出「本次未带：…」——剔除 ≠ 丢数据。
- **元信息写法差异都认**：`\title[短]{长}`（取长标题）、命令式 `\abstract{...}`（Springer sn-jnl）与环境式 `\begin{abstract}`、`\keywords{a,b}` 与 `\begin{keyword}...\sep...`。
- **作者信息在独立标题页时**：主文件没有 `\author` 才会去找同目录另一个 `.tex`，且**只认 `\title` 与主文件逐字相同的那一个**（同目录常有回复审稿人意见书等别的文档，不能见 `\author` 就用）。补入后写进转换记录。**主文件已有作者但缺通讯邮箱时**，仍会去标题页**按姓名**回填邮箱（只补邮箱，不新增作者、不改名字/单位），来源记入 `email_source_file` —— 投稿系统几乎都强制要通讯邮箱。
- **Elsevier 的 `\ead{}` / `\cormark` 写在 `\author{}` 外面**：邮箱与通讯标记都要单独采集。`\cormark`/`\corref` 决定换模板后谁带 `\author*` 星号；没有这类标记时才退化成「有邮箱即通讯作者」。`\affiliation[a]{organization={...}, city={...}, country={...}}` 是 key=value 写法，走结构化解析（不能按逗号切，`organization` 的值自身含逗号）。
- **模板要求 research highlights 而原稿没有**：明确列进「需作者确认」，不留一堆空 `\item` 让作者自己发现。

`--journal` 与 `--template` 的分工：**期刊配置提供"规则"（图片 DPI、声明要求、篇幅上限…），模板提供"结构"（文档类、命令体系、版式骨架）**。两个都给 = 规则 + 结构都齐；只给 `--template` = 只做结构移植，不做期刊规范判定（结果包目录名为 `template-only`）。

## 标准工作流

1. **环境自检** — `doctor`。若显示"LaTeX 真实编译不可用"，说明本机没装 TeX 发行版；此时仍可完成解析、转换、静态检查，但拿不到 PDF 预览与真实编译错误。如实告知用户，别承诺 PDF。
2. **确认目标期刊** — `journals list` 看内置配置。内置的都是 `needs_confirmation`（按常见要求整理的通用模板，不是某本期刊的官方逐条规范）。若用户要投具体期刊，走 `journals new` 新建配置，并把官方指南里能核实的条目填进去。
3. **跑 `run`** — 产出完整结果包。
4. **读报告** — 先看 `report.md` 的结论与"需人工确认清单"，再看 `diff.md` 确认正文没被动过。
5. **收尾** — 提醒用户补 `[待作者填写]` 的声明、处理错误级问题；确认期刊配置的核对状态。

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
| `convert <input> --template <文件>` | 只做模板移植（LaTeX 用 `.tex` 主文件，Word 用 `.docx`；也可直接给 `.zip` 模板包） |
| `qc <input> --journal <id>` | 只做质量检查 |
| `diff <before> <after>` | 对比两份稿件的内容差异（模型级比较，不是字节比较） |
| `run <input> --journal <id>` | 完整流程 + 结果包 |
| `run <input> --template <文件或.zip>` | 完整流程，以官方模板为骨架（`--journal` 可省略） |

`<input>` 可以是目录（LaTeX 项目）、`.tex`、`.docx` 或 `.zip`。
`--template` 可以是 `.tex` / `.docx` 主文件，也可以是官网下载的 `.zip` 模板包；
包内有多份模板时用 `kit.zip::包内路径` 指定。
`--journal` 与 `--template` 至少给一个；两个都给时，配置出规则、模板出结构。

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

## 期刊配置

内置 `ieee-trans` / `elsevier-generic` / `springer-generic` 三个，**均为 `needs_confirmation`**——它们是按常见投稿要求整理的通用模板，不是任何一本期刊的官方逐条规范。使用前必须提示用户核对目标期刊的最新 Author Guidelines。

`elsevier-generic` 走**模板模式**，套用内置的 `assets/templates/elsarticle-num.tex` 结构骨架。**该骨架不是 Elsevier 官方模板原件**，只是按 `elsarticle.cls` 的必需结构（`\begin{frontmatter}…\end{frontmatter}`）写的最小可用骨架；投正式期刊时请用 `--template` 指向官网下载的模板主文件。

新增期刊用 `journals new <id>`，再按 `references/journal-config.md` 填写。配置里每条规则都要写 `rule_sources`（来源 URL + 核对状态），没核实的写 `needs_confirmation`，**不要为了"看起来完整"而编造规则**。

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
| `CVT` | 转换与模板 | | |

每个码只对应一个含义和一个级别（错误 / 警告 / 提示）。完整码表与处置建议见 `references/quality-checks.md`。

## 参考文档

| 文件 | 何时读 |
|---|---|
| `references/journal-config.md` | 新建/修改期刊配置、填写 `rule_sources`、理解字段含义 |
| `references/quality-checks.md` | 报告里出现不认识的问题码、需要给用户解释或给出修复建议 |
| `references/content-model.md` | 需要消费 `result.json` / `inspect` 输出，或排查解析归属问题 |
| `references/workflow.md` | 典型场景端到端操作：LaTeX 改投 IEEE、**用官网模板套稿**、Word 改投 Elsevier、只做质检、双格式互查 |

## 已知边界

- **不做**：正文改写、语言润色、图片内容重绘、从零生成图表。
- **不做**：把 LaTeX 转成 Word 或反向转换（两条流水线各管各的；用户确实需要时明确说明这是独立工具的事）。
- **模板模式不做内容级判断**：它把内容搬进模板骨架并记录每一次替换，但不会替你判断"这段内容放在模板的哪个位置更合适"；模板里没有对应锚点的内容会按标题→作者→摘要→关键词的顺序补在元信息区末尾，并在转换记录里说明。
- **PDF 预览**依赖 TeX 发行版（LaTeX）或 LibreOffice/pandoc（Word）；缺失时降级为静态检查并说明。
- 图片"有效分辨率"由**像素尺寸 ÷ 印刷宽度**算出，并区分线条图/照片，**DPI 数值本身不作为唯一判据**。
- Word 的参考文献若来自结构化文献字段可较可靠转换；若是普通文本，只能解析后标记为"需人工确认"。
- 内置期刊配置未经逐条核对，报告中会出现 `CFG001` 提示，属预期行为。
