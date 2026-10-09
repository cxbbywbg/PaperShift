# 统一论文内容模型

整个流水线的契约是：**先解析成模型，再基于模型做检查、转换、比较**。所以 `inspect` 输出的 JSON、`result.json` 里的结构、差异对比的输入，都是同一个模型。

设计上有两条硬约束：

1. **承载内容与关联关系。** 每个图/表/公式/引用都持有稳定标识符，并记录"它在哪一节"（`section`）和"谁引用了它"（`referenced_in`）这两类关系——质检报告要能定位到图表、差异报告要能判断内容有没有丢，全靠这两条关系。
2. **不强行抹平 Word 与 LaTeX 的差异。** 无法无损统一的元素（复杂公式、Word 域代码、LaTeX 宏）保留原始表示在 `raw` / `native` 字段里，由各自的适配器在输出阶段处理。

Schema 版本：`1.0`（`schema_version` 字段）。

## 顶层结构

```
Manuscript
├── source_format          "latex" | "docx"
├── source_path            输入路径
├── main_file              主文件路径
├── project_files          项目包含的全部文件
├── metadata               元信息
├── sections[]             正文章节（不含投稿声明）
├── figures[]              图
├── tables[]               表
├── equations[]            公式
├── citations[]            正文引用点
├── references[]           文末文献条目
├── declarations[]         投稿声明
├── crossrefs[]            交叉引用记录
├── latex{...}             LaTeX 特有字段
├── docx{...}              Word 特有字段
├── warnings[]             解析诊断
└── stats{}                统计
```

## 标识符约定

| 对象 | id 形式 | 说明 |
|---|---|---|
| 章节 | `sec:1` / `sec:1.2` / `sec:methods` | 优先用 `\label`（去掉 `sec:` 前缀），其次编号，最后行号 |
| 图 | `fig:1` | 按出现顺序 |
| 表 | `tab:1` | 按出现顺序 |
| 公式 | `eq:1` | 按出现顺序；`align` 内多式会拆成多个 |
| 文献 | 键名（如 `zhang2016accelerating`） | LaTeX 用 bib 键；Word 文本用 `ref1`… |
| 交叉引用 | `xref:<target>:<section>` | 记录在哪一节引用了什么 |
| 内容块 | `blk:<段落序号>` | Word 侧用段落序号 |

`Manuscript.label_index()` 会建一张 `label / 自动编号 → 对象 id` 的表，用于解析 `\ref{fig:xxx}`、`Figure 3` 这类引用。

## `metadata`

| 字段 | 说明 |
|---|---|
| `title` | 标题 |
| `authors` | `[{name, affil, email, orcid}]` |
| `affiliations` | 单位文本列表（**取的是单位名称本身，不是编号**） |
| `abstract` | 摘要纯文本 |
| `abstract_structured` | `{栏目名: 文本}`，结构化摘要才有 |
| `keywords` | 关键词列表 |
| `highlights` | 亮点（Elsevier 常用） |
| `corresponding` | 通讯作者 |
| `running_title` | 页眉短标题 |
| `doc_meta` | 解析出来的格式细节，如 `style_info.normal_size_pt`、`bib_root` |

## `Section`

| 字段 | 说明 |
|---|---|
| `id` / `level` / `heading` / `number` | 标识与标题 |
| `parent` | 父章节 id |
| `labels` | 该章节的 `\label` 列表 |
| `blocks[]` | 章节内容块 |
| `span` | 源文件位置 |
| `starred` | 是否 `\section*`（不编号） |
| `words` / `chars` | 属性，词数与字符数 |

`Block` 含 `kind`（`paragraph` / `heading` / `list` / `quote` / `verbatim` / `display_math` / `raw`）、`text`、`style`、`span`、`native`。

> **投稿声明不算正文章节。** `\section*{Funding}`、`\section*{Data Availability Statement}` 这类标题会被识别为**声明**并只进 `declarations[]`，不进 `sections[]`。这样做的原因有两个：一是章节结构检查不该把声明当成正文层级；二是转换时自动补入的声明占位不会在差异报告里被误报成"新增章节"。

## `Figure` / `Table` / `Equation`

三者共有：

| 字段 | 说明 |
|---|---|
| `id` / `number` / `label` | 标识 |
| `section` | 所属章节 id（**稳定 id，不是临时行号**） |
| `referenced_in` | 引用它的章节 id 列表；空列表 = 正文没引用 |
| `span` | 源文件位置 |
| `native` | 原始表示（OMML XML、LaTeX 片段等） |

`Figure` 另有 `caption`、`files`、`subfigures`、`env`、`placement`、`width`、`caption_in_source`。

`Table` 另有 `caption`、`colspec`、`n_cols`、`n_rows`、`footnotes`、`raw`。

`Equation` 另有 `source`（LaTeX 源码或 OMML 文本）、`env`。

## `Citation` / `Reference`

`Citation` 是**正文里的引用点**：`key`、`command`（`cite` / `parencite` / `docx_bracket`…）、`section`、`span`、`resolved`。

`Reference` 是**文末文献条目**，关键在 `origin` 与 `confidence`：

| `origin` | 来源 | `confidence` |
|---|---|---|
| `bib` | `.bib` 文件 | `confirmed` |
| `latex_manual` | `thebibliography` 手写 `\bibitem` | `confirmed` |
| `docx_field` | Word 结构化文献字段（EndNote/Zotero 域） | `confirmed` |
| `docx_text` | Word 里手打的普通文本列表 | **`needs_review`** |

字段：`key`、`type`、`fields{}`、`raw`、`used`、`span`；属性 `title`、`year`。

> 这是需求里明确要求区分的："已可靠识别"（`bib` / `docx_field`）和"需人工确认"（`docx_text`）。报告里分别以 `REF031` / `REF030` 提示。

## `Declaration`

`kind`（`funding` / `conflict_of_interest` / `data_availability` / `ethics` / `author_contributions` / `acknowledgment`）、`present`、`text`、`section`、`span`。

`present=False` 表示原稿没有这项声明。

## `crossrefs[]`

`{id, command, target, section, span}`。`target` 是解析后的对象 id（解析不到时保留原始标签名），`section` 是引用发生的位置。`\ref`、`Figure 3` 这类都会被记录。

## `latex{...}` / `docx{...}`

**LaTeX 特有**：`documentclass`、`class_options`、`packages[{name, options}]`、`bib_resources`、`bib_style`、`bib_tool`、`custom_macros`。

**Word 特有**：`page_setup`（`width_cm` / `height_cm` / `orientation` / `margin_*_cm` / `columns`）、`styles_used`、`has_tracked_changes`、`has_comments`、`has_fields`。

## `stats{}`

`sections`、`top_sections`、`figures`、`tables`、`equations`、`citations`、`unique_citation_keys`、`crossrefs`、`references`、`declarations`、`body_words`、`body_chars`、`abstract_words`、`keywords`。

## 怎么用

```bash
# 看解析结果（排查"为什么识别不到某章节/某张图"）
python scripts/sci.py inspect paper/main.tex > model.json

# 只看关心的部分
python -c "import json;d=json.load(open('model.json'));print(d['stats']);print([f['id'] for f in d['figures'] if not f['referenced_in']])"
```

排查顺序建议：

1. 看 `stats` —— 数量对不对。
2. 看 `sections[].heading` —— 章节是否都被识别（尤其 Word 稿件的标题样式）。
3. 看 `figures[].section` / `referenced_in` —— 归属和引用关系是否正确。若 `section` 是空或异常，多半是标题样式没识别出来。
4. 看 `references[].origin` —— 是 `bib`/`docx_field` 还是 `docx_text`。
5. 看 `warnings[]` —— 解析期的诊断信息。
