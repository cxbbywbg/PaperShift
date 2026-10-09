# 质量检查报告（转换前（基线））

## 一、本次检查的基本信息

| 项目 | 内容 |
|---|---|
| 稿件 | `D:\WorkBuddy\Sci-Trans\tests\fixtures\docx_demo\manuscript.docx` |
| 源格式 | docx |
| 目标期刊配置 | 仅按官方模板转换（manuscript.docx）（`template-only`） |
| 配置核对状态 | **待确认（未逐条核对官方指南）** |
| 官方指南 | （未记录） |
| 规范获取日期 | 2026-10-09 |
| 检查时间 | 2026-10-09 15:04:50 |

## 二、结论

**❌ 存在必须处理的错误**

- 🔴 错误：2
- 🟡 警告：1
- 🔵 提示（需作者确认）：4

> 错误项必须处理完毕后再投稿。

## 三、检查项与结果

| 检查类别 | 检查内容 | 问题数 |
|---|---|---|
| 文件完整性 | 必要文件是否存在、资源路径是否正确 | ✅ 通过 |
| 章节结构 | 必要章节是否缺失、标题层级是否异常、图表公式是否被正文引用 | ✅ 通过 |
| 版式 | 页面尺寸、栏布局、页边距、字体与段落规范 | ✅ 通过 |
| 摘要与篇幅 | 摘要字数、结构化摘要栏目、关键词数量、正文长度、文献条数上限 | ✅ 通过 |
| 图片 | 有效分辨率、格式、印刷宽度、图号与图注、重复与未引用图片 | 1 |
| 表格 | 编号、表题、正文引用、列数与溢出风险、跨页、表注 | ✅ 通过 |
| 公式 | 编号、引用、编译错误、溢出风险、Word 公式对象可编辑性 | ✅ 通过 |
| 参考文献 | 引用键存在性、未引用条目、重复条目、字段完整性、文内与文末一致性 | 3 |
| LaTeX 编译 | 括号与环境配对、未定义引用、缺失资源、编译错误与 Overfull | ✅ 通过 |
| Word 文档 | 修订记录、批注、脚注尾注、超链接、文献字段类型 | 1 |
| 投稿声明 | 基金、利益冲突、数据可用性、伦理、作者贡献等声明是否齐备 | ✅ 通过 |
| 内容与元信息 | 标题、作者、单位、摘要、关键词等元信息是否可识别 | 2 |

## 四、问题明细

### 错误（2）

**参考文献**

- `REF001` **引用了不存在的文献键：ref3**
  - 位置：`§sec:1 cit:ref3`
  - 建议：在 .bib 中补上该条目，或修正 \cite 的键名。
- `REF040` **编号制引用中，正文引用的键在文末文献表里不存在：ref3**
  - 位置：`refs`

### 警告（1）

**图片**

- `FIG050` **第 4 图（无 label）在正文中没有任何引用**
  - 位置：`manuscript.docx:31 §sec:3 fig:4（第 4 图）`
  - 建议：期刊普遍要求每个图在正文中被显式提及（如 “as shown in Fig. 2”）。

### 提示（4）

**Word 文档**

- `DOC006` **参考文献列表由普通文本解析（4/4 条），不是 Word 结构化文献字段**
  - 位置：`D:\WorkBuddy\Sci-Trans\tests\fixtures\docx_demo\manuscript.docx`
  - 建议：普通文本的文献条目自动转换容易误判，请在报告中标为「需人工确认」的条目上逐条核对。
**内容与元信息**

- `MET003` **3 位作者未能识别到单位标记**
  - 位置：`D:\WorkBuddy\Sci-Trans\tests\fixtures\docx_demo\manuscript.docx`
  - 建议：换模板时需要作者与单位的对应关系，请人工核对。
- `CFG001` **期刊配置「仅按官方模板转换（manuscript.docx）」尚未逐条核对官方指南，本次转换的规范依据需要人工复核**
  - 位置：`template-only`
  - 建议：请打开官方 Guide for Authors 核对配置，核对完成后把配置里的 status 改为 verified。
  - 规范依据：仅按官方模板转换（manuscript.docx） · status · needs_confirmation
**参考文献**

- `REF030` **4 条参考文献是从普通文本中解析的，需要人工确认**
  - 位置：`共 4 条`
  - 证据：LeCun Y, Bengio Y, Hinton G. Deep learning. Nature. 2015;521；He K, Zhang X, Ren S, Sun J. Deep residual learning for imag；Jacob B, Kligys S, Chen B, et al. Quantization and training 
  - 建议：Word 里若文献不是结构化字段而是普通文本，自动转换可能误判。请对照原文核对这几条的作者、年份与期刊名。

## 五、需要人工确认的清单

下面这些项无法由脚本判定，必须由作者核对：

- [ ] `DOC006` 参考文献列表由普通文本解析（4/4 条），不是 Word 结构化文献字段（`D:\WorkBuddy\Sci-Trans\tests\fixtures\docx_demo\manuscript.docx`）
- [ ] `MET003` 3 位作者未能识别到单位标记（`D:\WorkBuddy\Sci-Trans\tests\fixtures\docx_demo\manuscript.docx`）
- [ ] `CFG001` 期刊配置「仅按官方模板转换（manuscript.docx）」尚未逐条核对官方指南，本次转换的规范依据需要人工复核（`template-only`）
- [ ] `REF030` 4 条参考文献是从普通文本中解析的，需要人工确认（`共 4 条`）

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
