# 🔍 安全审计报告

## 📊 执行摘要

- **审计对象**：`sci-paper-reformatter`（SCI 论文跨期刊转投格式转换 Skill）
- **审计路径**：`dist/sci-paper-reformatter/`（组装后的完整分发包，27 个文件）
- **审计方式**：纯静态文本分析，全程只读，未执行被审查内容
- **审计时间**：2026-10-09（第二轮：新增「官方模板模式」后复审）
- **发现问题总数**：0 个
  - 🔴 P0 阻断级：0 个
  - ⚠️ P1 需关注：0 个
  - 📝 信息性提醒：1 个（已在实现中处置，见下）
- **安全评分**：98 / 100

---

## 🔴 P0 阻断级风险发现

✅ 未发现 P0 风险

---

## ⚠️ P1 需关注风险发现

✅ 未发现 P1 风险

---

## 📝 信息性提醒（非风险项）

### 模板模式的目录遍历范围（本轮新增，已在实现中处置）

**位置**：`scripts/sci_core/latex_build.py` → `_copy_template_assets()`

`--template` 会把模板所在目录里的**辅助文件**复制进结果包（`.cls`/`.bst` 等），
这是让结果包可独立编译的必要动作。但官方模板常被解压在 `~/Downloads` 这类
混有大量个人文件的目录里，若无差别 `rglob` 复制，会把整个下载目录写进交付包——
既是可用性问题，也存在**把无关个人文件带进交付物**的隐私风险。

**处置（已实现）**：

1. 后缀白名单 `TEMPLATE_ASSET_SUFFIXES`：只复制 LaTeX 支持文件
   （`.cls .sty .bst .bbx .cbx .def .cfg .clo .fd .ldf .ist .dtx .ins .eps .bib`）
   与图片（`.pdf .png .jpg .jpeg .gif .tif .tiff`）；
2. 单文件大小上限 `TEMPLATE_ASSET_MAX_BYTES = 20 MB`，超限跳过并告警；
3. 不复制任何 `.tex`（避免干扰主文件识别）；
4. 复制目标路径一律由 `p.relative_to(tpl_dir)` 生成，**无法跳出模板目录**；
5. 跳过数量会打印到日志，让使用者知道"有什么没被复制"。

有对应回归测试：`模板模式：模板目录里的无关文件不被复制（避免污染/泄露）`。

✅ 除上述一项外，未发现其他信息性提醒，未发现任何硬编码凭证。

---

## 📋 详细检查结果

### 命令执行与权限检查

- **发现次数**：1 处（`subprocess.run`），命中关键词的其余 10 处均为 Markdown 代码块语言标记 ` ```bash `
- **详细列表**：
  - `scripts/sci_core/util.py:13,253,269` — `import subprocess` 与 `subprocess.run(...)`

**分析**：`run_cmd()` 使用 **argv 列表**形式调用（`subprocess.run(argv, ...)`），**未使用 `shell=True`**，不存在 shell 注入面。调用点共 4 处，参数全部是固定选项 + 文件路径：

| 位置 | 调用 | 说明 |
|---|---|---|
| `latex_check.py:319` | `[engine, "-interaction=nonstopmode", …, target.name]` | engine 取自 `pick_engine()`，仅可能是 `pdflatex`/`xelatex`/`lualatex`/`latexmk` |
| `latex_check.py:328,330` | `[exe, tex_stem]` | `exe` 来自 `find_tool("biber"/"bibtex")` |
| `pipeline.py:124` | `[soffice, "--headless", …, str(main_file)]` | `soffice` 来自 `find_tool` |
| `pipeline.py:141` | `[pandoc, str(main_file), "-o", str(pdf)]` | `pandoc` 来自 `find_tool` |

外部命令仅限 TeX 发行版 / Office / pandoc，用于编译与 PDF 预览，属于该 Skill 的核心功能。**判定：合理，非风险。**

- **权限提升**：未发现 `sudo` / `chmod` / `chown` / `setuid`（0 处）

### 文件操作与敏感路径检查

- **敏感路径访问**：未发现对 `~/.ssh`、`~/.aws`、`~/.gcloud`、`~/.kube`、`.env`、`credentials`、`private_key` 等敏感路径的读取或外送（0 处，grep 命中的 `token`/`env` 均为普通变量名与 LaTeX 环境名）

- **删除操作**：4 处 `shutil.rmtree(..., ignore_errors=True)`，**全部限定在调用方指定的输出目录/临时工作目录的子目录内**：

| 位置 | 删除目标 | 范围判定 |
|---|---|---|
| `latex_check.py:307` | `<out_dir>/_build` | 编译临时副本目录 |
| `pipeline.py:67` | `<work_dir>/_extracted` | zip 解压临时目录 |
| `pipeline.py:226` | `<journal_dir>/src` | 结果包内待重建的转换稿目录 |
| `pipeline.py:240` | `<work>/_media_formatted` | 图片提取临时目录 |

**判定：合理，非风险。** 无一处指向用户目录、家目录或系统路径。

- **写入操作**：写入目标均为用户显式指定的输出目录（`-o/--output`）、临时工作目录或用户级期刊配置目录 `~/.workbuddy-ai/sci-paper-reformatter/journals/`（仅在用户主动执行 `journals new` 时创建）。

### 网络请求检查

- **代码中的网络调用**：**0 处**。无 `requests` / `urllib` / `httpx` / `aiohttp` / `socket` / `urlopen`，无任何 HTTP 客户端依赖。
- **发现的 URL**（共 18 条，全部为 YAML 配置中的**文档元数据**，代码从不访问）：
  - `https://www.elsevier.com/journals/information-for-authors`
  - `https://journals.ieeeauthorcenter.ieee.org/create-your-ieee-journal-article/authoring-tools-and-templates/`
  - `https://www.springer.com/gp/livingreviews/latex-templates`
- 这些 URL 出现在 `guide_url` 与 `rule_sources.*.url` 字段，用途是**在质检报告里标注每条格式判定的规范来源**，供人工核对。**代码中不存在读取或请求这些 URL 的逻辑。**
- **Base64 编码检测**：未发现可疑的 Base64 编码字符串。

### 远程脚本深度分析

✅ 不适用——本 Skill **不存在自动下载或执行远程脚本的行为**（无 `curl`、无 `wget`、无二次下载、无 `eval`），因此不触发步骤 4。

### 依赖安装风险检查

- **全局安装检测**：未发现任何依赖安装命令（0 处 `pip install` / `npm install -g` / `gem install` / `cargo install` / `brew install`）
- **虚拟环境检查**：不适用（不安装依赖）
- **依赖来源检查**：不适用

**说明**：核心代码（`util.py` / `model.py` / `issues.py` / `changes.py`）刻意**零第三方依赖**，仅用 Python 标准库，以保证在裸环境下 `doctor` 也能运行。`refs.py` 对 `bibtexparser` 做了「有则用、无则回退到内置正则解析」的处理。所有 Office/OOXML 操作通过标准库 `zipfile` + `xml.etree` 直接完成，未引入 `python-docx` 等外部包。

---

## 💡 总体建议

1. **当前实现已符合 P2 安全标准**，无需修改即可使用。
2. 建议在发布说明中注明：本 Skill 会在执行 `run` 时调用本机已安装的 `pdflatex`/`soffice`/`pandoc` 等外部程序完成编译与 PDF 预览；若用户环境不希望执行外部程序，可加 `--no-pdf` 跳过。
3. `run_cmd()` 已正确使用 argv 列表形式，后续维护时**务必保持不使用 `shell=True`**。
4. 若未来需要联网抓取期刊官方指南，应新增显式的用户确认步骤，并只允许访问白名单域名。

---

## ✅ 审计结论

**风险等级**：**P2 — 安全（无投毒风险）**

**使用建议**：

- ✅ **P2 - 可以安全使用**

**判定依据**：

- 技能描述与实际行为一致：SKILL.md 声明的是"论文格式转换与质检"，代码实际做的就是解析、转换、检查、报告，无隐藏行为。
- 无远程下载与执行，无 `eval`/`exec`，无 shell 注入面。
- 不读取任何敏感文件，不外送任何数据，代码中不存在网络调用。
- 不安装依赖，不修改系统配置，不提升权限。
- 删除操作全部限定在用户指定的输出/临时目录的子目录内。
- 无硬编码凭证。

---

**📌 审计原则提醒**：本报告仅评估 Skill 本身的供应链投毒风险；不涉及代码风格、算法效率等开发质量问题。
