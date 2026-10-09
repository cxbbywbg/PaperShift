# 典型工作流

六个最常见的场景，从输入到交付。命令都在 skill 根目录下执行（`python scripts/sci.py …`）。

---

## 场景 A：手上有官网下载的官方模板，要把自己的稿件装进去

这是**最贴近真实投稿**的场景：作者从期刊官网下了一个模板包（Elsevier 的 `elsarticle` kit、Springer 的 `svjour3` kit、某个期刊自己的 `xxx-template.tex`），手里还有一份自己的稿件。

**输入**：官方模板主文件 + 原稿（LaTeX 项目或 `.docx`）。

```bash
# LaTeX：模板是 .tex 主文件（模板目录里的 .cls/.bst 会自动一起进结果包）
python scripts/sci.py run paper/main.tex \
    --template ~/Downloads/elsarticle-template-num.tex -o out

# Word：模板是 .docx
python scripts/sci.py run paper/manuscript.docx \
    --template ~/Downloads/journal-template.docx -o out

# 如果同时有期刊配置（配置出规则，模板出结构）
python scripts/sci.py run paper/main.tex \
    --journal elsevier-generic \
    --template ~/Downloads/elsarticle-template-num.tex -o out
```

### 更常见的情况：原稿和模板都是压缩包

Overleaf 导出的原稿是 `.zip`，期刊官网下载的模板 kit 也是 `.zip`。**两个都不需要手工解压**，直接指过去：

```bash
python scripts/sci.py run ~/Downloads/my-paper.zip \
    --template ~/Downloads/elsarticle.zip -o out

# 包内有多份模板时，指定用哪一份
python scripts/sci.py run ~/Downloads/my-paper.zip \
    --template '~/Downloads/elsarticle.zip::elsarticle-template-num.tex' -o out
```

处理规则：

- 自动解包并挑出包内主文件（`.tex` / `.docx`），**结果与手工解压后指向主文件逐字节一致**。
- 包内**主文件同目录**的 `.cls` / `.bst` / `.sty` 随结果包提供，保证可独立编译；图片与 `.bib` 只在模板主文件真的引用它们时才带。
- 包内 `readme`、作者须知、`doc/`、`thumbnails/`、`__MACOSX` 等示例资料不复制，避免污染结果包（也避免作者把样例 PDF 一起投给期刊）。
- **包内同族版式默认各出一份**：文件名剥掉版式标记（`sc`/`dc`、`1col`/`2col`、`num`/`harv`…）后**同名同目录**的算一族，每份各产出一个结果包（`journals/template-only-sc/`、`...-dc/`）。Elsevier CAS 的单栏双栏、elsarticle 的数字制著者年制都是这一形态，一次跑完可直接对比。包内有多族时挑「最像模板本体」的那族（`cas-?-template` 优先于 `cas-?-sample`）；同族变体最多展开 4 份，只对 LaTeX 生效。
- 包内**只有一份**主文件 → 直接用。包内有多份候选但**不构成同族** → 报 `CVT013` 并列出候选，**必须人工过目**。
- 用 `kit.zip::包内路径` 指定 → 只出指定那一份，不展开变体。
- 主文件识别用「命名分档 + 体积」：`template`/`main`/`manuscript` 加分，`sample`/`example`/`doc`/`manual` 减分。**不能只看体积**——官方 kit 里最大的合法文档常常是说明书（实测 CAS 包 18.5 KB 的 `elsdoc-cas.tex` vs 4.9 KB 的真模板）。
- 压缩包解不开、或包内根本没有主文件 → 报 `CVT012` 并写明原因，自动回退定点手术模式。

**会做什么**

- **保留模板的导言区**：`\documentclass`、类选项、宏包体系原样不动——这正是期刊要求的版式。
- **移植原稿内容**：标题、作者、单位、摘要、关键词、研究亮点、正文、声明、文献命令按模板的命令体系放进去。
- 模板里的示例内容（示例标题、示例作者、示例摘要、lorem ipsum 正文）整段清掉，**并以注释保留**便于回溯。
- **示例文献库会被替换**，不是往后追加：模板的 `\bibliography{cas-refs}` 指向随包样例文献，直接追加原稿的 `\bibliography{references}` 会让两条同时生效（参考文献凭空变多），而且追加位置可能落到 `\end{document}` 之后等于没写。注释里的示例文献样式也不会被意外激活。
- 原稿依赖而模板没有的 `\newcommand` / `\DeclareMathOperator` / `\usepackage` 补进导言区，否则正文会编译失败；逐条记入转换记录。三条约束：①**被注释掉的 `%\usepackage{...}` 不算依赖**，不迁移；②判定「模板有没有」时**同时读模板主文件与同目录 `.cls`/`.sty`** —— 期刊类常把 `\usepackage[numbers,sort&compress]{natbib}`、`\def\fnm`、`\newtheorem{theorem}` 写在类文件里，只看主文件会重复带入，触发 `Option clash for package natbib` / `Command \theorem already defined`；③自定义宏按花括号配平**整条**搬运，跨行 `\newcommand` 被截成半句会留下未闭合的 `{`，把整个文件的后续解析带崩（症状：章节数变 0、引用键全部「不存在」）。
- **正文里的原文档类专有命令会被注释保留**（如 CAS 的 `\printcredits`，定义在 `cas-common.sty`，Springer `sn-jnl.cls` 没有）。判据：目标模板 + 其 `.cls`/`.sty` 确实没定义该命令，且它**独占一行**；行内出现的不动（注释半行比编译错误更难查）。记 `CVT030`。
- 模板自带的 `\bibliographystyle` 优先保留（它对应随附的 `.bst`）；与期刊配置不一致时提示人工确认，不静默覆盖。
- **作者信息在独立标题页时**：主文件没有 `\author` 才会去找同目录另一个 `.tex`，且只认 `\title` 与主文件逐字相同的那一个；补入后在转换记录里写明取自哪个文件。
- **主文件已有作者但缺通讯邮箱时**，仍会去标题页**按姓名**配对回填邮箱（只补邮箱，不新增作者、不改名字/单位，标题对不上不碰），来源记入 `email_source_file`。投稿系统几乎都强制要通讯邮箱，静默丢掉是最容易踩的坑。
- **Elsevier 的 `\ead{}` / `\cormark` 在 `\author{}` 外面**，要单独采集；`\cormark`/`\corref` 决定谁带 `\author*`。`\affiliation[a]{organization={...}, city={...}, country={...}}` 是 key=value 写法，走结构化解析而非按逗号切。
- **`LTX050`：结果包里若还有自己带 `\documentclass`、正文又没引用的独立文档**，会报警。从官网 kit 起步的稿子最容易中招（包里躺着官方模板、说明书、另一版式样例）。
- **模板要求 research highlights 而原稿没有**：列入「需作者确认」，不留空 `\item` 让作者自己发现。

**交付时要说清**

- `diff.md` 的期望结论是"未检测到内容差异"或"正文内容未变；仅补入投稿声明占位"。若出现"检测到对象丢失"，**立即核对**，这是严重问题。
- `conversion-log.md` 的"未做的改动"一节会说明模板模式下**没有**做什么（例如不重复添加期刊配置要求的宏包）。
- 若模板里找不到 `\begin{document}`（比如指到了 `.cls` 或说明文件），会报 `CVT012` 并**降级回定点手术**，不会静默失败——这时要回头确认 `--template` 指对了文件。
- 模板包里有同族版式时会出**多份结果包**，交付时要说清各自对应哪种版式（如 `template-only-sc` 单栏 / `template-only-dc` 双栏），并提醒作者按目标期刊要求选一版投。
- 若报告里出现 `CVT013`，说明包里有**多份**候选主文件但**不构成同族**、系统替你选了一份。**别跳过这条**：核对报告里写明的"已选用"是不是你要的那份，不对就用 `kit.zip::包内路径` 重新指定。
- 若本机没有 TeX，报告里是 `LTX100`（未执行编译）；模板模式改动了导言区结构，**建议在装了 TeX 的环境编译一次**。

---

## 场景 B：LaTeX 项目改投 IEEE Transactions

**输入**：一个多文件 LaTeX 项目（`main.tex` + `sections/*.tex` + `refs.bib` + `figures/`）。

```bash
# 1) 自检：确认本机有没有 TeX 发行版
python scripts/sci.py doctor

# 2) 解析一遍，确认结构识别正确（尤其章节数、图表数、文献数）
python scripts/sci.py inspect paper/main.tex -o model.json
python -c "import json;print(json.load(open('model.json'))['stats'])"

# 3) 跑完整流程
python scripts/sci.py run paper/main.tex --journal ieee-trans -o out

# 4) 读报告
#    out/submission/journals/ieee-trans/report.md
#    out/submission/journals/ieee-trans/diff.md
#    out/submission/journals/ieee-trans/conversion-log.md
```

**会做什么**

- 导言区：切换 `\documentclass`，补充 `cite/amsmath/graphicx`，把与 IEEEtran 冲突的宏包（`natbib`/`caption`/`subcaption`/`hyperref`）**注释保留**而非删除。
- 元信息：按 IEEE 模板重写 `\title` / `\author{...\thanks{...}}` / `\begin{IEEEkeywords}`；**原命令整段注释保留**在文件里备查。
- 参考文献：`\bibliographystyle` 切成 `IEEEtran`。
- 缺声明：插入 `\section*{Funding}` + `[待作者填写]` 占位，排在参考文献命令**之前**，并记为"需作者确认"。
- 正文：**逐字不动**。

**交付时要说清**

- `report.md` 里若出现 `CFG001`，说明 `ieee-trans` 配置是按常见要求整理的通用模板，**不是某本具体 IEEE 期刊的官方逐条规范**，需要用户核对目标刊的 Information for Authors。
- `diff.md` 的结论通常是"正文内容未变；仅补入投稿声明占位"——这正是想要的结果。
- 若 `doctor` 显示 TeX 不可用，报告里会有 `LTX100`：已做静态检查，**未做真实编译**。建议在装了 TeX 的环境复核一次。

---

## 场景 C：Word 稿件改投 Elsevier

**输入**：`manuscript.docx`。

```bash
python scripts/sci.py doctor
python scripts/sci.py run paper/manuscript.docx --journal elsevier-generic -o out
```

**会做什么**（安全模式，默认）

- 页面尺寸 / 页边距 / 栏数 → 按规范改写 OOXML（`w:sectPr`、`w:cols`）。
- 正文字体字号 / 行距 → 改样式表（`docDefaults`、`Normal`）。
- 题注标签：`Figure 3` → `Fig. 3`。
- 表格线型 → 三线表。
- 补齐缺失的强制声明段落。
- 正文文字、图片、公式对象、引用**不动**。

**注意**

- 若文档有修订记录，会报 `DOC001`（🔴）。**必须先在 Word 里接受或拒绝全部修订**再投稿，否则审稿人看得到痕迹。
- 若文档有批注，报 `DOC002`。
- 参考文献若是手打的普通文本（`docx_text`），报 `REF030`，需要逐条人工核对；若是 EndNote/Zotero 域（`docx_field`），报 `REF031`，可靠度高得多。

---

## 场景 D：投稿前只做质检（不改文件）

```bash
# 报告打印到终端
python scripts/sci.py qc paper/manuscript.docx --journal ieee-trans --markdown

# 或写成文件
python scripts/sci.py qc paper/main.tex --journal ieee-trans -o qc.md --json qc.json
```

适合"我只想知道有没有问题，先别动我的稿子"。

`--stage` 可以给报告换个标题（如 `--stage "投稿前自查"`），`--json` 输出结构化结果便于脚本消费。

---

## 场景 E：确认转换真的没改内容

```bash
python scripts/sci.py diff paper/main.tex out/submission/journals/ieee-trans/src -o diff.md
```

比较的是**模型级差异**，不是文件字节：把两份稿件都解析成同一个 Manuscript 再逐维度比，因此"改了排版但没改内容"能明确区分出来。

报告里会分维度列出：元信息 / 章节结构与正文文本 / 图 / 表 / 公式 / 引用 / 文献 / 交叉引用 / 投稿声明 / 统计。

结论有三种：

| 结论 | 含义 |
|---|---|
| 未检测到内容差异（仅版式/格式发生变化） | 最理想 |
| 正文内容未变；转换过程补入了投稿声明占位 | 正常，补 `[待作者填写]` 即可 |
| 检测到内容差异，详见下文 | **必须逐条核对**，尤其看有没有"丢失"项 |

看到任何 `**丢失**` 标记（图/表/公式/文献）都是严重问题，应立即排查。

---

## 场景 F：目标期刊没有内置配置

```bash
python scripts/sci.py journals new my-target-journal --name "My Target Journal"
python scripts/sci.py journals validate my-target-journal
python scripts/sci.py journals show my-target-journal
```

1. 打开该刊的 Author Guidelines，按 `references/journal-config.md` 填字段。
2. 每条规则补 `rule_sources`（URL + 核对状态）。**核不准的宁可留空或保守取值**，不要编造。
3. 先用 `convert` 跑一遍看转换稿（检查括号配平、元信息生成），再跑完整 `run`。

配置放哪：

- 只想自己用 → 用户级 `~/.workbuddy-ai/sci-paper-reformatter/journals/`
- 想跟项目一起走 → 项目级 `<项目>/.sci-trans/journals/`
- 两者同名会覆盖内置配置

用 `python scripts/sci.py journals path` 看实际目录。

---

## 常见问答

**Q：能直接把 LaTeX 转成 Word 吗？**
不做。两条流水线各管各的：LaTeX 稿件输出 LaTeX 项目，Word 稿件输出 Word 文件。需求要求的是"按目标期刊规范转换"，不是格式互转；强行互转必然丢公式和交叉引用。若用户确实要格式互转，明确说明这是独立工具的事。

**Q：会不会改我的正文？**
不会。正文文字逐字保留，转换只发生在导言区、元信息、文献命令、章节编号、图表题注标签这些格式位置。改了什么全部进 `conversion-log.md`，被替换的原命令以注释留在文件里。

**Q：报告说"检查通过"是不是就万事大吉？**
不是。要看**检查项与结果表**：哪些类别真的跑了、哪些因工具链缺失被跳过。比如 `LTX100` 表示没做真实编译；`FIG060` 表示完全没解析到图片、图片检查被跳过——这两者都是"没检查"，不是"通过"。

**Q：图片 DPI 报告说不够，但我在 Photoshop 里看是 300 dpi。**
系统算的是**有效分辨率 = 像素宽度 ÷ 印刷宽度**，不是文件里的 DPI 标签。一张 300 dpi 的图如果被放大到 15 cm 宽，有效分辨率可能只有 150 dpi。看报告"证据"里给的像素尺寸、印刷宽度及其来源，再决定是重新导出还是缩小显示宽度。

**Q：`CFG001` 一直出现，是不是有 bug？**
不是。它表示所用期刊配置未经逐条核对。内置的三个配置都是通用模板，出现它是预期行为。要消除，得逐条核对官方指南并把 `status` 改成 `verified`。
