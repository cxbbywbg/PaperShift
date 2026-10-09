# 期刊配置参考

期刊配置是一份 YAML，把"目标期刊要求什么"从代码里彻底剥离出来。**换期刊不改代码**，只加一份配置。

## 查找顺序

同名配置按以下顺序命中，先找到的生效：

1. 内置 `<skill>/assets/journals/*.yaml`
2. 用户级 `~/.workbuddy-ai/sci-paper-reformatter/journals/*.yaml`
3. 项目级 `<cwd>/.sci-trans/journals/*.yaml`

用 `python scripts/sci.py journals path` 查看这三个目录的实际位置。用户级可用环境变量 `SCI_TRANS_JOURNALS` 覆盖。

> 想改内置配置又不想动 skill 目录时，把同名文件放到用户级目录即可整体覆盖。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 配置标识，命令行 `--journal` 用它。建议 `刊名缩写-类型`，如 `ieee-trans` |
| `name` | ✅ | 人类可读名称 |
| `publisher` | | 出版方 |
| `issn` | | 有就填，便于日后核对 |
| `guide_url` | ✅ | 官方投稿指南 URL |
| `retrieved` | | 规则获取日期 `YYYY-MM-DD` |
| `status` | ✅ | `verified` / `partial` / `needs_confirmation` |
| `notes` | | 给使用者看的说明。**不确定的地方要写在这里** |
| `source_documents` | | 本地保存的官方 PDF/网页快照路径列表 |

### `status` 的含义（很重要）

| 值 | 含义 | 系统行为 |
|---|---|---|
| `verified` | 已逐条对照官方指南核对 | 不报 `CFG001` |
| `partial` | 部分条目已核对 | 报 `CFG001` 提示 |
| `needs_confirmation` | 通用模板 / 未经核对 | 报 `CFG001` 提示 |

**不要把没核实的配置标成 `verified`。** 这个字段决定了报告会不会提醒用户"规范依据需要人工复核"。

## `article_types`

不同文章类型要求不同（Regular Paper / Letter / Review）。每条含 `id`、`name`、`required_sections`（用 `abstract`/`introduction`/`conclusion` 这类通用名，不要写刊内专有名）。

## `output`

### `output.formats`

产出哪些交付物，可选 `docx` / `latex` / `pdf_preview`。

### `output.docx`

| 字段 | 说明 |
|---|---|
| `base` | `safe`（默认，套规范但保留原文档关系）或 `template`（从官方模板骨架移植，会记录降级项） |
| `template` | `template` 模式下的模板文件路径 |
| `caption_label_figure` / `caption_label_table` | 题注标签，如 `Fig.` / `TABLE` |

### `output.latex`

| 字段 | 说明 |
|---|---|
| `base` | `surgery`（默认，在原稿副本上定点手术）或 `template`（以官方模板为骨架移植内容） |
| `template` | `template` 模式下的官方模板主 `.tex` 文件路径。相对路径依次按「期刊配置所在目录 → 当前工作目录 → 技能根目录」解析，所以内置模板可以写 `assets/templates/xxx.tex` |
| `documentclass` | 目标文档类，如 `IEEEtran`。**`template` 模式下该字段不生效**——文档类以模板为准 |
| `class_options` | 文档类选项，如 `[journal]`。同上，`template` 模式下以模板为准 |
| `packages_add` | 需要补充的宏包。**`template` 模式下不强制添加**（避免与模板已有选项冲突），转换记录里会写明"未做的改动" |
| `packages_remove` | **冲突宏包**。注意：处理方式是**注释保留**而不是删除，原命令留在文件里备查 |
| `section_style` | `numbered` / `unnumbered` |
| `metadata.title` | 标题模板，`%value` 占位 |
| `metadata.author_line` | 作者行模板，占位符：`%name`、`%affil`、`%affil_key` |
| `metadata.affiliation` | 单位行模板，占位符：`%key`、`%value` |
| `metadata.abstract_env` | 摘要环境名，如 `abstract` |
| `metadata.keywords_command` | 关键词命令模板，`%value` 占位 |
| `metadata.keyword_sep` | 关键词之间的分隔符。Elsevier `elsarticle` 用 `" \\sep "`；不写时若模板里出现 `\sep` 会自动沿用，否则用 `, ` |
| `bibliography.tool` | `bibtex` / `biber` |
| `bibliography.style` | `\bibliographystyle` 的值。`template` 模式下模板自带的样式优先，不一致时提示人工确认 |
| `declarations.<kind>` | 某类声明的专用环境模板（`%value` 占位）。不写则统一用 `\section*{…}` + `[待作者填写]` |

**`template` 模式下 metadata 可以整段省略。** 系统会从模板自带的示例元信息里推断命令写法
（模板写了 `\author[aff1]{…}` 就按带标签的形式生成）；配置里显式写了 `metadata` 则以配置为准。

**模板占位符**（只在 `metadata.*` 里生效）：

| 占位符 | 含义 |
|---|---|
| `%value` | 该字段的完整文本 |
| `%name` | 作者姓名 |
| `%affil` | 该作者对应的单位文本（可能多个单位，用 ` and ` 连接） |
| `%affil_key` | 单位的标签，如 `aff1`、`aff1,aff2` |
| `%key` | 单位标签（用于 `affiliation` 模板） |

> ⚠️ 写 `author_line` 时注意花括号配平。写成 `"\\author{%name\\thanks{%affil}}"`（两个右括号），
> 多一个就会让转换稿花括号不配平（`LTX001`）。改完配置后跑一次
> `python scripts/sci.py convert` 并检查输出文件的括号数。

## `layout`

| 字段 | 说明 |
|---|---|
| `columns` | 栏数，`1` 或 `2` |
| `page` | `A4` / `letter` |
| `margin_cm` | `{top, bottom, left, right}`，单位 cm |
| `body_font` | `{latin, eastasia, size_pt}` |
| `heading_font` | 同上 |
| `line_spacing` | 行距倍数 |
| `paragraph_space_after_pt` | 段后间距（磅） |

## `abstract` / `body`

| 字段 | 说明 |
|---|---|
| `abstract.max_words` | 摘要词数上限 |
| `abstract.structured` | 是否要求结构化摘要 |
| `abstract.required_labels` | 结构化摘要必须有哪几个栏目 |
| `abstract.keywords_min` / `keywords_max` | 关键词数量区间 |
| `body.max_words` | 正文词数上限 |
| `body.heading_levels_max` | 标题最大层级 |
| `body.section_numbering` | 章节是否编号 |
| `body.note` | 备注（如"保留原编号体系"） |

## `figures` / `tables`

| 字段 | 说明 |
|---|---|
| `figures.formats` | 允许的图片格式列表 |
| `figures.min_dpi_photo` | 照片类最低有效分辨率 |
| `figures.min_dpi_line` | 线条图最低有效分辨率 |
| `figures.max_width_cm` | 图片最大宽度 |
| `figures.caption_position` | `above` / `below` |
| `figures.caption_label` | 题注标签，如 `Fig.` |
| `figures.subfigure_label` | 子图标注形式，如 `(a)` |
| `tables.*` | 同上；另有 `style`（如"三线表"）、`footnotes_required` |

**关于 DPI**：系统算的是**有效分辨率 = 像素宽度 ÷ 印刷宽度(英寸)**，并依据颜色复杂度区分线条图与照片，然后与对应阈值比较。证据里会同时给出像素尺寸、印刷宽度及其来源（Word 显示宽度 / `\includegraphics[width=]` / 版心宽度估算）和颜色数。**不要把 DPI 数值当作唯一判据。**

## `equations` / `references` / `declarations`

| 字段 | 说明 |
|---|---|
| `equations.numbering` | `continuous` / `by_section` |
| `equations.editable_required` | 是否要求公式为可编辑对象（Word 的 OMML） |
| `references.style` | `numbered`（编号制）/ `author-year`（作者—年份制） |
| `references.style_name` | 样式名，如 `IEEE`、`APA` |
| `references.max_refs` | 文献条数上限 |
| `references.doi_required` | 是否强制要求 DOI |
| `references.bibtex_style` / `biblatex_style` | 供 LaTeX 侧使用 |
| `declarations.required` | **强制**声明，缺失时报 `DEC001` 并插入 `[待作者填写]` 占位 |
| `declarations.optional` | 建议声明，缺失时报 `DEC002` 提示 |

声明种类的可用值：`funding`、`conflict_of_interest`、`data_availability`、`ethics`、`author_contributions`。

## `rule_sources`（可追溯性的关键）

把配置路径映射到来源。写法是**点号路径 → `{url, note, verified}`**：

```yaml
rule_sources:
  layout.columns:
    url: "https://…/authoring-tools-and-templates/"
    note: "正文为双栏排版"
    verified: needs_confirmation
  figures.min_dpi_line:
    url: "https://…/graphics-guidelines"
    note: "线条图要求 ≥ 600 dpi"
    verified: verified
```

- 键用配置里的**点号路径**（`figures.max_width_cm`）。查不到精确路径时会**逐级回退到父路径**（`figures.max_width_cm` → `figures`），所以可以只写一条覆盖整个分组。
- 报告里每条问题都会带上对应的 `journal / field / verified / source_url`。
- 缺 `rule_sources` 的条目会在 `journals validate` 里被列出来。

## 新增期刊

```bash
python scripts/sci.py journals new my-journal
python scripts/sci.py journals validate my-journal
python scripts/sci.py journals show my-journal
```

1. 打开官方 Author Guidelines，把能核实的条目逐条填进去。
2. 每条都补 `rule_sources`，核对过的写 `verified: verified`，没核实的写 `needs_confirmation`。
3. **核不准的条目宁可留空或保守取值**，并在 `notes` 里写明——不要编造。
4. `validate` 通过后，用一篇真实稿件跑 `convert`，检查转换稿的括号配平与元信息生成结果。
