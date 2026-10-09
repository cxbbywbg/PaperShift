# 质量检查报告（转换前（基线））

## 一、本次检查的基本信息

| 项目 | 内容 |
|---|---|
| 稿件 | `D:\WorkBuddy\Sci-Trans\tests\fixtures\latex_demo` |
| 源格式 | latex |
| 目标期刊配置 | 仅按官方模板转换（elsarticle-num.tex）（`template-only`） |
| 配置核对状态 | **待确认（未逐条核对官方指南）** |
| 官方指南 | （未记录） |
| 规范获取日期 | 2026-10-09 |
| 检查时间 | 2026-10-09 15:04:46 |

## 二、结论

**❌ 存在必须处理的错误**

- 🔴 错误：5
- 🟡 警告：5
- 🔵 提示（需作者确认）：10

> 错误项必须处理完毕后再投稿。

## 三、检查项与结果

| 检查类别 | 检查内容 | 问题数 |
|---|---|---|
| 文件完整性 | 必要文件是否存在、资源路径是否正确 | 1 |
| 章节结构 | 必要章节是否缺失、标题层级是否异常、图表公式是否被正文引用 | 1 |
| 版式 | 页面尺寸、栏布局、页边距、字体与段落规范 | ✅ 通过 |
| 摘要与篇幅 | 摘要字数、结构化摘要栏目、关键词数量、正文长度、文献条数上限 | ✅ 通过 |
| 图片 | 有效分辨率、格式、印刷宽度、图号与图注、重复与未引用图片 | 2 |
| 表格 | 编号、表题、正文引用、列数与溢出风险、跨页、表注 | 3 |
| 公式 | 编号、引用、编译错误、溢出风险、Word 公式对象可编辑性 | 1 |
| 参考文献 | 引用键存在性、未引用条目、重复条目、字段完整性、文内与文末一致性 | 10 |
| LaTeX 编译 | 括号与环境配对、未定义引用、缺失资源、编译错误与 Overfull | 1 |
| Word 文档 | 修订记录、批注、脚注尾注、超链接、文献字段类型 | ✅ 通过 |
| 投稿声明 | 基金、利益冲突、数据可用性、伦理、作者贡献等声明是否齐备 | ✅ 通过 |
| 内容与元信息 | 标题、作者、单位、摘要、关键词等元信息是否可识别 | 1 |

## 四、问题明细

### 错误（5）

**参考文献**

- `REF040` **编号制引用中，正文引用的键在文末文献表里不存在：frankle2019lottery**
  - 位置：`refs`
- `REF001` **引用了不存在的文献键：frankle2019lottery**
  - 位置：`sections/results.tex:9 §sec:3.1 cit:frankle2019lottery`
  - 建议：在 .bib 中补上该条目，或修正 \cite 的键名。
**图片**

- `FIG001` **找不到图片文件：figures/ablation.png**
  - 位置：`sections/results.tex:43 §sec:3.2 fig:4（第 4 图）`
  - 建议：确认图片是否随稿件一并提交，或路径是否正确。
  - 规范依据：仅按官方模板转换（elsarticle-num.tex） · figures.formats · needs_confirmation
**文件完整性**

- `LTX020` **图片文件缺失：figures/ablation.png**
  - 位置：`sections/results.tex:43 §sec:3.2 fig:4`
  - 建议：确认文件是否漏打包，或路径与 \graphicspath 是否一致。
**章节结构**

- `LTX005` **\ref{sec:missing} 指向不存在的标签**
  - 位置：`sections/discussion.tex:12 §sec:discussion sec:missing`
  - 建议：检查该 label 是否拼写错误，或对应对象是否被删除。

### 警告（5）

**公式**

- `EQ040` **编号公式 2（eq:retained）未被正文引用**
  - 位置：`sections/method.tex:23 §sec:2.2 eq:2`
**参考文献**

- `REF010` **文献条目未被正文引用：bengio2013estimating**
  - 位置：`D:\WorkBuddy\Sci-Trans\tests\fixtures\latex_demo\refs.bib ref:bengio2013estimating`
  - 证据：Estimating or Propagating Gradients Through Stochastic Neurons for Conditional Computation
  - 建议：未引用的条目不会出现在参考文献表中，确认是否漏引或可删除。
- `REF020` **文献 zhang2016accelerating（inproceedings）缺少必填字段：booktitle**
  - 位置：`D:\WorkBuddy\Sci-Trans\tests\fixtures\latex_demo\refs.bib ref:zhang2016accelerating`
  - 建议：目标期刊的参考文献样式通常要求这些字段完整。
**图片**

- `FIG050` **第 3 图（fig:energy）在正文中没有任何引用**
  - 位置：`sections/results.tex:36 §sec:3.2 fig:3（第 3 图）`
  - 建议：期刊普遍要求每个图在正文中被显式提及（如 “as shown in Fig. 2”）。
**表格**

- `TAB020` **第 2 表有 6 列**
  - 位置：`sections/results.tex:18 §sec:3.2 tab:2（第 2 表）`
  - 证据：6 行 × 6 列
  - 建议：列数较多时建议：拆表、缩小字号、改为跨栏排版，或在 LaTeX 中使用 \small / \resizebox（须符合期刊规定）。
  - 规范依据：仅按官方模板转换（elsarticle-num.tex） · tables.max_width_cm · needs_confirmation

### 提示（10）

**LaTeX 编译**

- `LTX040` **检测到 2 个自定义宏，换模板时可能不兼容**
  - 位置：`main.tex`
  - 证据：\newcommand{\method}{Adaptive Spectral T；\DeclareMathOperator*{\argmin}{arg\,min}
  - 建议：若目标模板未定义这些宏，需要在导言区保留定义或改为标准命令。
**内容与元信息**

- `CFG001` **期刊配置「仅按官方模板转换（elsarticle-num.tex）」尚未逐条核对官方指南，本次转换的规范依据需要人工复核**
  - 位置：`template-only`
  - 建议：请打开官方 Guide for Authors 核对配置，核对完成后把配置里的 status 改为 verified。
  - 规范依据：仅按官方模板转换（elsarticle-num.tex） · status · needs_confirmation
**参考文献**

- `REF021` **文献 han2015learning 缺少 DOI**
  - 位置：`ref:han2015learning`
  - 建议：越来越多的期刊要求参考文献提供 DOI。
- `REF021` **文献 hinton2015distilling 缺少 DOI**
  - 位置：`ref:hinton2015distilling`
  - 建议：越来越多的期刊要求参考文献提供 DOI。
- `REF021` **文献 denton2014exploiting 缺少 DOI**
  - 位置：`ref:denton2014exploiting`
  - 建议：越来越多的期刊要求参考文献提供 DOI。
- `REF021` **文献 krizhevsky2009learning 缺少 DOI**
  - 位置：`ref:krizhevsky2009learning`
  - 建议：越来越多的期刊要求参考文献提供 DOI。
- `REF021` **文献 tan2019efficientnet 缺少 DOI**
  - 位置：`ref:tan2019efficientnet`
  - 建议：越来越多的期刊要求参考文献提供 DOI。
- `REF021` **文献 bengio2013estimating 缺少 DOI**
  - 位置：`ref:bengio2013estimating`
  - 建议：越来越多的期刊要求参考文献提供 DOI。
**表格**

- `TAB023` **第 1 表含 1 条表注，请核对统计符号说明是否完整**
  - 位置：`sections/method.tex:55 §sec:2.3 tab:1（第 1 表）`
- `TAB023` **第 2 表含 1 条表注，请核对统计符号说明是否完整**
  - 位置：`sections/results.tex:18 §sec:3.2 tab:2（第 2 表）`

## 五、需要人工确认的清单

下面这些项无法由脚本判定，必须由作者核对：

- [ ] `LTX040` 检测到 2 个自定义宏，换模板时可能不兼容（`main.tex`）
- [ ] `CFG001` 期刊配置「仅按官方模板转换（elsarticle-num.tex）」尚未逐条核对官方指南，本次转换的规范依据需要人工复核（`template-only`）
- [ ] `REF021` 文献 han2015learning 缺少 DOI（`ref:han2015learning`）
- [ ] `REF021` 文献 hinton2015distilling 缺少 DOI（`ref:hinton2015distilling`）
- [ ] `REF021` 文献 denton2014exploiting 缺少 DOI（`ref:denton2014exploiting`）
- [ ] `REF021` 文献 krizhevsky2009learning 缺少 DOI（`ref:krizhevsky2009learning`）
- [ ] `REF021` 文献 tan2019efficientnet 缺少 DOI（`ref:tan2019efficientnet`）
- [ ] `REF021` 文献 bengio2013estimating 缺少 DOI（`ref:bengio2013estimating`）

## 六、本次判定用到的期刊规范来源

本次没有产生带来源的判定（或期刊配置未记录 rule_sources）。

## 七、工具链

- sci-paper-reformatter: `1.0.0`
- python: `3.13.14`
- platform: `Windows-11-10.0.26300-SP0`
- python-docx: `1.2.0`
- PyYAML: `6.0.3`
- tex: `False`
- docx_to_pdf: `False`
- latex_engine: ``

> 未检测到 TeX 发行版：LaTeX 项目**未做真实编译**，只完成了静态检查。
> 若需要 PDF 预览与真实的编译错误/警告，请安装 TeX Live 或 MiKTeX 后重跑。

> 未检测到 LibreOffice/pandoc：**未生成 Word 的 PDF 预览**。
