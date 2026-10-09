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

本技能遵循 **Agent Skills 开放标准**（一个目录 + 一份 `SKILL.md`，frontmatter 只有 `name` / `description` 是必填）。各客户端只是**存放位置**不同，所以同一份产物可以原样投放到多个环境，不需要为每个客户端做格式转换。

先看支持哪些环境：

```bash
python tools/install_skill.py --list-targets
```

| 目标 | 客户端 | 个人级（相对 `~`） | 项目级（相对项目根） |
|---|---|---|---|
| `workbuddy` | WorkBuddy / CodeBuddy | `.workbuddy-ai/skills/` | `.workbuddy-ai/skills/` |
| `claude` | Claude Code | `.claude/skills/` | `.claude/skills/` |
| `codex` | OpenAI Codex CLI | `.agents/skills/` | `.agents/skills/` |
| `cursor` | Cursor | `.cursor/skills/` | `.cursor/skills/` |
| `gemini` | Gemini CLI | `.gemini/skills/` | `.gemini/skills/` |

装到单个 / 多个 / 全部环境：

```bash
python tools/install_skill.py --install                        # 默认装 workbuddy
python tools/install_skill.py --install --target claude
python tools/install_skill.py --install --target claude,codex  # 逗号分隔
python tools/install_skill.py --install --target all           # 5 个环境全装
python tools/install_skill.py --install --target all --force   # 覆盖已装的同名技能
```

目标名有别名：`codebuddy` → `workbuddy`、`claude-code` → `claude`、`openai-codex` → `codex`、`gemini-cli` → `gemini`。

**项目级安装**（随仓库提交，团队共享同一版本）：

```bash
python tools/install_skill.py --install --target claude --scope project --project-dir .
# → ./.claude/skills/sci-paper-reformatter/
```

只组装、不安装：

```bash
python tools/install_skill.py          # 组装到 dist/
python tools/install_skill.py --zip    # 组装 + 额外打出可分发的 zip
```

`install_skill.py` 用**显式白名单**组装（只收 `SKILL.md`、`scripts/`、`assets/`、`references/`），`tests/`、`samples/`、`dist/` 这类开发产物不会被误打包。装好后在对话里说"帮我把这篇论文改投 XX 期刊"即可触发；部分客户端需要重启才生效。

> 两点说明：
> - `SKILL.md` 的 frontmatter 里除标准的 `name` / `description` 外还有一个 `agent_created`，那是 WorkBuddy 自己的标记，其他客户端会忽略，不影响加载。
> - Gemini CLI 的技能目前是实验特性，需要在 `/settings` 里打开 `experimental.skills`。

上表路径的依据（各客户端官方文档）：[Codex Agent Skills](https://developers.openai.ac.cn/codex/skills)（用户级 `~/.agents/skills`、仓库级 `.agents/skills`）、[Gemini CLI Skills](https://gemini-cli.cn/docs/cli/skills/)（用户级 `~/.gemini/skills`、工作区 `.gemini/skills`）、[Agent Skills 开放标准](https://agentskills.io)（Claude Code 与 Cursor 的目录约定）。**各客户端版本迭代较快，装完先用它的 `/skills` 之类命令确认技能被发现了。**

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

# 5) 原稿和模板都是压缩包（Overleaf 导出 + 官网 kit）——不用手工解压
python scripts/sci.py run ~/Downloads/my-paper.zip --template ~/Downloads/elsarticle.zip -o out
# 包内有多份模板时，指定用哪一份
python scripts/sci.py run ~/Downloads/my-paper.zip \
    --template '~/Downloads/elsarticle.zip::elsarticle-template-num.tex' -o out
```

`run` 默认**尽可能全自动**：不逐项等确认，跑完直接出报告，把需要作者决定的事情集中列在"需人工确认"清单里。

跑完先看这两个文件：

1. `out/submission/journals/<期刊id>/report.md` —— 结论 + 需人工确认清单
2. `out/submission/journals/<期刊id>/diff.md` —— 确认正文没被动过

`<input>` 可以是目录（LaTeX 项目）、`.tex`、`.docx` 或 `.zip`。
`--template` 可以是 `.tex` / `.docx` 主文件，也可以是官网下载的 `.zip` 模板包（自动解包）。
`--journal` 与 `--template` **至少给一个**。

---

## 两种转换模式

| 模式 | 触发方式 | 做法 | 适用 |
|---|---|---|---|
| **定点手术**（默认） | 只给 `--journal` | 在原稿副本上只改已知结构位置（导言区、元信息命令、文献命令、章节编号、题注标签），正文逐字不动 | 目标期刊的文档类与原稿同构 |
| **模板模式** | `--template <文件或压缩包>`，或配置里写 `output.latex.base: template` | 以官方模板为骨架：**保留模板的 `\documentclass` 与宏包体系**，把原稿的标题/作者/单位/摘要/关键词/正文/声明/文献命令移植进去；模板自带的 `.cls`/`.bst` 一并复制进结果包 | 手上有官网下载的模板；或目标文档类有强制结构（如 Elsevier `elsarticle` 要求元信息必须包在 `\begin{frontmatter}…\end{frontmatter}` 里） |

`--template` 接受三种形态，**都不需要事先手工解压**：

| 形态 | 写法 | 说明 |
|---|---|---|
| 单文件 | `--template path/to/main.tex` | LaTeX 用 `.tex` 主文件，Word 用 `.docx` |
| 压缩包 | `--template ~/Downloads/elsarticle.zip` | 自动解包、挑出包内主文件；包内 `.cls`/`.bst` 随结果包提供 |
| 包内指定 | `--template 'kit.zip::elsarticle-template-num.tex'` | 包内有多份模板时，明确指定用哪一份（**指定后不再自动展开变体**） |

**包内有「同族版式」时默认各出一份。** Elsevier 的 CAS 包同时给了单栏（`cas-sc`）和双栏（`cas-dc`），elsarticle 给了数字制（`num`）和著者年制（`harv`）——这些是同一份模板的不同版式，结构完全一致，只有文档类或引用样式不同。工具把它们识别成一族、**每份各产出一个结果包**：

```
submission/journals/
├── template-only-sc/     ← 单栏
└── template-only-dc/     ← 双栏
```

判定规则：文件名剥掉版式标记（`sc`/`dc`、`1col`/`2col`、`num`/`harv`…）后**同名同目录**的算一族；包内有多族时挑「最像模板本体」的那族（`cas-?-template` 优先于 `cas-?-sample`）。只有一份模板时行为与单模板完全一致。

**分工：期刊配置提供"规则"（图片 DPI、声明要求、篇幅上限…），模板提供"结构"（文档类、命令体系、版式骨架）。**

- 两个都给 = 规则 + 结构都齐
- 只给 `--template` = 只做结构移植，不做期刊规范判定（结果包目录名为 `template-only`，多变体时带版式后缀）

模板模式的关键行为：

- **模板的版式权威，原稿的内容权威。** 模板导言区原样保留；原稿正文逐字移植。
- **原稿依赖的宏与宏包会一起带过去 —— 但模板已有的不重复带。** 模板没声明的 `\newcommand` / `\DeclareMathOperator` / `\usepackage` 会被补进导言区，否则正文里的自定义命令直接编译失败；逐条记入转换记录。判定「模板有没有」时**同时看模板主文件和它的 `.cls`/`.sty`**：期刊类几乎都把 `\usepackage[numbers,sort&compress]{natbib}`、`\def\fnm`、`\newtheorem{theorem}` 写在类文件里，只看主文件会把原稿里的同名命令重复带入，轻则冗余、重则 `Option clash for package natbib` / `Command \theorem already defined` 直接编译失败。**被注释掉的 `%\usepackage{...}` 不算依赖**，不会被凭空激活。
- **模板里的示例内容整段清掉并注释保留**，可回溯。
- **摘要 / 标题 / 关键词用 LaTeX 原文**，`\method{}`、`41.7\%`、`$1.9\times$` 不会被抹平。
- **模板自带的文献样式优先**，与期刊配置不一致时提示人工确认，不静默覆盖。
- 结构定位失败（模板里找不到 `\begin{document}` 等）时**降级回定点手术并报 `CVT012`**，不静默失败。
- **模板压缩包自动解包**：`--template kit.zip` 等价于「手工解压后指向包内主文件」，产出逐字节一致；包内 `readme`、作者须知、`__MACOSX` 垃圾不复制。
- **包内同族版式自动各出一份**（见上方说明）。包内有多份候选但**不构成同族**时报 `CVT013` 并列出候选，**必须人工过目**；不对就用 `kit.zip::包内路径` 指定。
- **主文件按「命名分档 + 体积」挑**：`template`/`main`/`manuscript` 加分，`sample`/`example`/`doc`/`manual` 减分。官方 kit 里按体积排第一名常常是**说明书**——实测 Elsevier CAS 包最大的合法文档是 18.5 KB 的 `elsdoc-cas.tex`，而真模板只有 4.9 KB，只看体积会选错。
- **原稿主文件按体积挑**：作者的正文一定是包里最大的 `.tex`（实测 106.9 KB，是第二名的 8 倍）。多候选时按体积降序，被 `\input` 引用的文件排除在外。

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
| `convert <input> --template <文件或.zip>` | 只做模板移植（LaTeX 用 `.tex` 主文件，Word 用 `.docx`；也可直接给官网下载的 `.zip` 模板包） |
| `qc <input> --journal <id>` | 只做质量检查 |
| `diff <before> <after>` | 对比两份稿件的内容差异（模型级比较，不是字节比较） |
| `run <input> --journal <id>` | 完整流程 + 结果包 |
| `run <input> --template <文件或.zip>` | 完整流程，以官方模板为骨架（`--journal` 可省略） |

全局选项：`--cwd`（查找项目级期刊配置的起点）、`--work`（临时工作目录）、`--quiet`（不输出进度）。
`qc` 另有 `-o`（写 Markdown）、`--json`（结构化结果）、`--markdown`（直接打印）、`--stage`（报告标题里的阶段名）。
`run` 另有 `--no-pdf`（跳过 PDF 预览）。

---

## 结果包结构

```
out/submission/
├── original/                      # 原稿**完整**副本，不可覆盖，留证
│                                  # （结果包里被裁掉的文件也在这里，随时可拿回来）
├── journals/<期刊id>/
│   ├── src/                       # 转换后的源文件：只含正文真正引用到的文件
│   ├── manuscript_formatted.docx  # Word 流水线的转换结果
│   ├── figures/                   # 抽取出的图片
│   ├── preview.pdf                # PDF 预览（工具链可用时）
│   ├── report.md                  # 质量检查报告（转换后）
│   ├── report-baseline.md         # 质量检查报告（转换前基线）
│   ├── diff.md                    # 差异报告
│   ├── conversion-log.md          # 转换记录（含"未做的改动"与"本次未带的文件"）
│   ├── result.json                # 结构化结果，便于脚本消费
│   └── README.md                  # 本包索引
└── manifest.json                  # 累积清单：输入哈希、配置哈希、工具版本
```

其中 `manuscript_formatted.docx` 只在 Word 流水线出现；`figures/` 只在 Word 流水线出现（LaTeX 项目的图片留在 `src/` 内）；`preview.pdf` 只在工具链可用时生成。

`diff.md` 的结论有五种：

| 结论 | 含义 |
|---|---|
| 未检测到内容差异（仅版式/格式发生变化） | 最理想 |
| 正文内容未变；转换过程补入了投稿声明占位 | 正常，补 `[待作者填写]` 即可 |
| 正文内容未变；元信息按目标模板的命令体系重新表示 | 正常，换模板必然换写法（`\author` → `\fnm/\sur` 等）；单位会先归一化再比，内容一致就不算变化 |
| 正文内容未变；但元信息变化较大，请对照差异报告核对 | 元信息相似度掉下来了，逐条核对标题/作者/单位/摘要/关键词 |
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
但结果包**不是**把原稿目录整份搬过去——它只装**编译真的会读到**的文件：

- 保留：被正文（含 `\input` 进来的子文件、类文件）引用到的**图片**、**`.bib`**、
  **`.cls`/`.sty`/`.bst`**，以及正文本身；
- 剔除：官方 kit 的**示例文档**（`cas-dc-sample.tex`）、**说明书**（`doc/elsdoc-cas.tex`）、
  **缩略图**（`thumbnails/`）、**废弃图片**、**没被引用的 `.bib`**、**旧稿副本**
  （`Response_to_Reviewers.tex`、`Title_Page.tex`）等一切「正文一个字都没提」的文件。

判定是**递归**的：`\documentclass{cas-sc}` → `cas-sc.cls` → `\RequirePackage{cas-common}` →
`cas-common.sty`，只看正文会漏掉最后一层。注释掉的引用不算引用（`%\includegraphics{旧图}`
不会把旧图带进来）。文献样式若由**文档类选项**选出（Springer 的 `sn-mathphys-num`），
按选项名也认。

**剔除 ≠ 丢数据**：原稿（含所有被剔除的文件）完整躺在 `submission/original/` 里，
转换记录里会逐条列出「本次未带：…」，需要哪份直接拿回来即可。质检里的
`LTX020`（图片缺失）/ `REF002`（`.bib` 缺失）是这条裁剪的安全网——真漏带了会报错。

`--template` 必须指向**主文件**（LaTeX 的 `.tex`、Word 的 `.docx`）；指错了会报 `CVT012` 并降级回定点手术。

**A2. 原稿和模板都是压缩包**（Overleaf 导出 + 官网 kit）

```bash
python scripts/sci.py run ~/Downloads/my-paper.zip --template ~/Downloads/elsarticle.zip -o out
# 包内有多份模板时指定用哪一份
python scripts/sci.py run ~/Downloads/my-paper.zip \
    --template '~/Downloads/elsarticle.zip::elsarticle-template-num.tex' -o out
```

两个包都不用手工解压，结果与「手工解压后指向主文件」**逐字节一致**。包内的 `.cls`/`.bst` 照样随结果包提供，`readme`、作者须知、`__MACOSX` 垃圾不复制；原稿包里混进来的模板 kit（示例文档、说明书、缩略图）也会被裁掉，只留正文真正引用到的文件。

**CAS 这种单栏/双栏双模板的包，默认出两份结果**（`template-only-sc/` 和 `template-only-dc/`），可以直接对比排版效果再决定投哪一版。只想出一份就用 `::` 明确指定：

```bash
python scripts/sci.py run my-paper.zip \
    --template 'els-cas-templates.zip::els-cas-templates/cas-sc-template.tex' -o out
```

若报告里出现 `CVT013`，说明包内有**多份**候选主文件但**不构成同族版式**（比如模板 + 说明书）、系统替你选了一份——**别跳过这条**，核对报告里写明的"已选用"是不是你要的那份。

**A3. 盲审稿：作者信息在单独的标题页文件里**

Springer `sn-jnl`、部分期刊模板会把 `\author` / `\affil` 拆到 `Title_Page.tex`，主文件里只剩标题与摘要。工具在**主文件没有作者信息**时会去找同目录下的另一个 `.tex`，但只在**能客观确认是同一篇稿子**时才用：该文件的 `\title` 与主文件标题逐字相同。

```bash
# 目录里同时有 Manuscript.tex 和 Title_Page.tex 时，作者/单位会被自动补入
python scripts/sci.py run ~/Downloads/my-paper.zip --template ~/Downloads/els-cas.zip -o out
# 日志里会出现：主文件未含作者信息，已从同项目的标题页 Title_Page.tex 补入 8 位作者 / 2 个单位
```

标题对不上就一律留空并列进「需作者确认」——同目录里还可能有回复审稿人意见书等别的文档，不能见 `\author` 就拿来用。

**主文件已有作者、但缺通讯邮箱时也会去标题页找。** 真实投稿里「作者名单」和「邮箱」经常不同源：主文件写 `\author[aff1]{Bo Liu}`（elsarticle / CAS 风格），邮箱却在 `Title_Page.tex` 的 `\author[1]{Bo Liu\thanks{Corresponding author. Email: ...}}` 里。这时工具**只按姓名回填邮箱**，不新增作者、不改名字、不动单位；标题对不上同样不碰。回填来源会记进转换记录（`email_source_file`）——投稿系统几乎都强制要通讯邮箱，静默丢掉是最容易踩的坑。

**A4. 元信息写法差异（不用改原稿）**

不同文档类的元信息写法差别很大，工具都认得：

| 文档类 | 标题 | 摘要 | 关键词 |
|---|---|---|---|
| Elsevier elsarticle / CAS | `\title{...}` | `\begin{abstract}...\end{abstract}` | `\begin{keyword}...\end{keyword}` + `\sep` |
| Springer sn-jnl | `\title[短]{长}` | `\abstract{...}`（**命令式**） | `\keywords{a, b, c}`（逗号分隔） |
| IEEE | `\title{...}` | `\begin{abstract}...\end{abstract}` | `\begin{IEEEkeywords}...\end{IEEEkeywords}` |

**Elsevier 的邮箱与通讯标记写在 `\author{}` 外面**，也认得：

```latex
\author[a]{Bo Liu}\cormark[1]\ead{csbliu@gdut.edu.cn}\credit{Writing - original draft}
\affiliation[a]{organization={School of Automation, Guangdong University of Technology},
                city={Guangzhou}, country={China}}
```

`\ead{}` 是邮箱、`\cormark` / `\corref` 标通讯作者（决定换模板后谁带 `\author*`），
`\affiliation` 的 `organization= / city= / country=` 是 key=value 写法、走结构化解析。
只读 `\author{}` 的参数会把这三样全丢掉。

`\title[短标题]{长标题}` 取的是**长标题**（短标题进 `\shorttitle`/`runningtitle` 的映射规则）。
目标模板带 `research highlights` 环境（Elsevier 等必填）而原稿没有时，会明确列进「需作者确认」而不是留一堆空 `\item` 让你自己发现。

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
| `CVT` | 转换与模板 | | |

每个码只对应一个含义和一个级别（🔴 错误 / 🟡 警告 / 🔵 提示）。完整码表与处置建议见 [`references/quality-checks.md`](references/quality-checks.md)。

几个常被误解的：

- `CFG001` —— 所用期刊配置未经逐条核对。内置三个配置都会出现，**属预期行为**。
- `LTX100` —— 本机没有 TeX，**未做真实编译**，只做了静态检查。
- `FIG060` —— 完全没解析到图片，图片检查被**跳过**，不是"通过"。
- `DOC001` —— Word 文档有修订记录。**必须先在 Word 里接受或拒绝全部修订**再投稿。
- `CVT013` —— 模板压缩包里有**多份**候选主文件、且它们**不构成同族版式**，系统替你选了一份。**必须人工确认选对了**；不对就用 `kit.zip::包内路径` 重新指定。（同族版式如 cas-sc / cas-dc 不报此码，而是各出一份结果包。）
- `LTX050` —— 结果包里还躺着**其他独立文档**（自己带 `\documentclass`、正文又没引用）。正常情况**不该出现**：结果包已按引用裁剪，无关文件根本不会被复制。真出现了说明有文件是**经 `\input` 被正文拉进来的**，或裁剪漏判了——按报告里的文件名核对，必要时手动剔除（Overleaf 里出现多个候选主文件，选错就编出模板样例）。
- `CVT030` —— 正文里有**原文档类专有命令**（如 Elsevier CAS 的 `\printcredits`），目标模板没有定义，已注释保留。不注释会直接编译报 `Undefined control sequence`。

---

## 开发与测试

```bash
python tests/run_tests.py        # 退出码 0 = 全通过
```

测试是**缺陷注入型**：夹具里刻意埋了重复题注、缺图、文献不匹配、越界引用等缺陷，验证检查项真的能报出来。另有一组 `test_rule_binding` 逐条验证"配置里设了某规则 → 对应检查真的会触发"，防止规则写了却不生效。
`test_frontmatter_regression` 钉住真实投稿里踩过的坑（Springer 命令式 `\abstract{}`、模板 `\title` 落在作者块区间内被吞、示例文献库没被替换反而往后追加到 `\end{document}` 之后、独立标题页的作者/单位映射）。
`test_cas_draft_style` 钉住 Elsevier CAS 风格初稿的四类坑（跨行 `\newcommand` 被截断会把整篇解析带崩、`\ead` 邮箱与 `\cormark` 通讯标记写在 `\author{}` 之外、`organization={...}` 这类 key=value 单位不能按逗号切、`\printcredits` 要注释并记 `CVT030`）。
`test_package_trimming` 钉住结果包裁剪（`.cls` 引的 `.sty` 要递归带出、注释掉的引用不算引用、kit 垃圾一律不进包、文档类选项选出的 `.bst` 也认、原稿完整备份在 `original/`）。

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
tools/install_skill.py  白名单式组装 + 多客户端安装
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
- **模板压缩包只在解析阶段解包**：解到系统临时目录 `sci-trans-templates/`（目录名带内容指纹，模板换版本自动重建；重复使用直接命中缓存）。它不写进你的下载目录，也不进结果包。
- **同族变体最多展开 4 份**：包里同名不同版式的文件理论上可以很多，全出会淹掉结果包，超过 4 份只取前 4 并给警告。变体展开目前只对 LaTeX 生效（Word 模板包几乎没有多版式主文件）。
- **作者信息来自独立标题页时**：只在「另一个 `.tex` 的 `\title` 与主文件逐字相同」时才自动补入，并在转换记录里写明取自哪个文件。对不上就留空并列进「需作者确认」，绝不猜。
- **通讯邮箱单独回填**：主文件已有作者但没人带邮箱时，仍会去标题页按**姓名**配对回填邮箱（只补邮箱，不新增作者/不改名字/不改单位）。标题对不上同样不碰。回填来源记进 `email_source_file`。
- **导言区补入的三个约束**：①被注释掉的 `%\usepackage{...}` 不算依赖，不迁移；②判定「模板是否已提供」时同时读模板主文件与同目录 `.cls`/`.sty`，类文件里已加载的宏包（如 `natbib`）、已定义的命令（如 `\fnm`、`\theorem`）都不再重复带入；③自定义宏**按花括号配平整条搬运**，跨行的 `\newcommand`（如 `\newcommand*\circled[1]{\tikz[...]{` 换行接 `\node...{#1};}}`）不会被截成半句——截断会留下未闭合的 `{`，把整个文件的后续解析全带崩。
- **通讯作者标记**：原稿有 `\cormark` / `\corref` 就按它决定谁带 `\author*` 星号；一个都没有时才退化成「有邮箱即通讯作者」，否则修好邮箱采集后会出现「人人都是通讯作者」。
- **Elsevier 的 `key=value` 单位写法**（`\affiliation[a]{organization={...}, city={...}, country={...}}`）走结构化解析，不按逗号切——`organization` 的值自身就含逗号，按逗号切会产出 `\orgdiv{organization={...}` 这种垃圾并把 `country` 整条丢掉。
- **正文里的原文档类专有命令会被注释保留**（如 CAS 的 `\printcredits`，定义在 `cas-common.sty`，`sn-jnl.cls` 没有）。只在目标模板确实没定义该命令、且它**独占一行**时才动手；行内出现的一律不动（注释掉半行比编译错误更难查），并记 `CVT030` 警告。
- **声明扫描先摘掉前言命令的参数**：CAS 的 `\credit{Funding acquisition}` 里带 "funding" 一词，不摘掉会被当成「已提供基金声明」，而 `\credit` 属前言、不会进结果包正文，于是报告误报「丢失 funding」。
- **嵌套在分组参数里的引用**：`scan_commands` 读到配平的分组会整块跳过，所以 `\textcolor{red}{\autoref{fig:x}}`、`\subfloat[题注\label{sub}]{\includegraphics{...}}` 这类写法额外走一遍不区分位置的补扫。补扫只做**新增**（不改已有结果），并过滤掉 `\ref{#1}` 这种宏体占位。
- **不认「导言区里对 `\abstract` 的重定义」**：命令式摘要只取 `\begin{document}` 之后出现的那个。
