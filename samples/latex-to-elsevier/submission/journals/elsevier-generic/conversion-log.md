# 转换记录

## 一、转换参数（可复现）

| 项目 | 值 |
|---|---|
| 转换时间 | 2026-10-09 15:04:48 |
| 源文件 | `D:\WorkBuddy\Sci-Trans\tests\fixtures\latex_demo` |
| 源格式 | latex |
| 期刊配置 id | `elsevier-generic` |
| 期刊配置版本 | 2026-10-09 |
| 配置核对状态 | 待确认（未逐条核对官方指南） |
| 配置来源文件 | `D:\WorkBuddy\Sci-Trans\assets\journals\elsevier-generic.yaml` |
| 配置指纹(sha256) | `93ebedfe73259956…` |
| 输入指纹(sha256) | `37c17df7b84184d8…` |
| sci-paper-reformatter | `1.0.0` |
| python | `3.13.14` |
| platform | `Windows-11-10.0.26300-SP0` |
| python-docx | `1.2.0` |
| PyYAML | `6.0.3` |
| tex | `False` |
| docx_to_pdf | `False` |
| latex_engine | `` |

## 二、改动统计

| 类别 | 条数 |
|---|---|
| 元信息 | 8 |
| 导言区 | 10 |
| 章节结构 | 2 |
| 投稿声明 | 2 |

## 三、改动明细

### 导言区（10）

- **导言区** · `\usepackage{siunitx}`
  - 变更前：`(模板未加载)`
  - 变更后：`\usepackage{siunitx}`
  - 原因：原稿正文依赖该宏包，目标模板未加载；已补入以保证结果可编译
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `\usepackage{hyperref}`
  - 变更前：`(模板未加载)`
  - 变更后：`\usepackage{hyperref}`
  - 原因：原稿正文依赖该宏包，目标模板未加载；已补入以保证结果可编译
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `\usepackage{natbib}`
  - 变更前：`(模板未加载)`
  - 变更后：`\usepackage[numbers,sort&compress]{natbib}`
  - 原因：原稿正文依赖该宏包，目标模板未加载；已补入以保证结果可编译
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `\usepackage{caption}`
  - 变更前：`(模板未加载)`
  - 变更后：`\usepackage{caption}`
  - 原因：原稿正文依赖该宏包，目标模板未加载；已补入以保证结果可编译
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `\usepackage{subcaption}`
  - 变更前：`(模板未加载)`
  - 变更后：`\usepackage{subcaption}`
  - 原因：原稿正文依赖该宏包，目标模板未加载；已补入以保证结果可编译
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `\usepackage{lineno}`
  - 变更前：`(模板未加载)`
  - 变更后：`\usepackage{lineno}`
  - 原因：原稿正文依赖该宏包，目标模板未加载；已补入以保证结果可编译
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `原稿自定义宏/设置`
  - 变更前：``
  - 变更后：`\newcommand{\method}{Adaptive Spectral Truncation (AST)}`
  - 原因：原稿正文依赖该定义，目标模板中没有；已带入导言区（位置在 \begin{document} 之前）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `原稿自定义宏/设置`
  - 变更前：``
  - 变更后：`\DeclareMathOperator*{\argmin}{arg\,min}`
  - 原因：原稿正文依赖该定义，目标模板中没有；已带入导言区（位置在 \begin{document} 之前）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `原稿自定义宏/设置`
  - 变更前：``
  - 变更后：`\graphicspath{{figures/}}`
  - 原因：原稿正文依赖该定义，目标模板中没有；已带入导言区（位置在 \begin{document} 之前）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.base · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **导言区** · `期刊配置要求的宏包`
  - 变更前：`lineno`
  - 变更后：`(未强制添加)`
  - 原因：模板模式以官方模板的导言区为准；模板已声明的宏包不重复添加，以免选项冲突。若模板缺少某必需宏包，请手动补入。
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.packages_add · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`

### 元信息（8）

- **元信息** · `标题`
  - 变更前：``
  - 变更后：`Adaptive Spectral Truncation for Low-Power Edge Inference of Convolutional Networks`
  - 原因：套用目标模板的 \title 命令（标题文字取自原稿，未改写）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.metadata.title · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **元信息** · `单位 1`
  - 变更前：``
  - 变更后：`School of Automation, Harbin Institute of Technology, Harbin 150001, China`
  - 原因：按目标模板的 \affiliation 命令重新生成（原命令保留在注释中）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.metadata · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **元信息** · `单位 2`
  - 变更前：``
  - 变更后：`Peng Cheng Laboratory, Shenzhen 518055, China`
  - 原因：按目标模板的 \affiliation 命令重新生成（原命令保留在注释中）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.metadata · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **元信息** · `单位 3`
  - 变更前：``
  - 变更后：`Department of Electrical Engineering, University of Melbourne, Parkville, VIC 3010, Australia`
  - 原因：按目标模板的 \affiliation 命令重新生成（原命令保留在注释中）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.metadata · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **元信息** · `作者列表`
  - 变更前：``
  - 变更后：`3 位作者`
  - 原因：按目标模板的 \author 命令重新生成（原命令保留在注释中）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.metadata · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - ⚠️ 需作者确认
  - 位置：`main.tex`
- **元信息** · `关键词`
  - 变更前：``
  - 变更后：`edge inference, model compression, spectral methods, convolutional neural networks, energy efficiency`
  - 原因：按目标模板的关键词命令重新生成
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.metadata · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **元信息** · `作者/单位命令区`
  - 变更前：`\author[aff1]{Template Author} \ead{template.author@example.com} \affiliation[aff1]{organization={Template Ins`
  - 变更后：`\affiliation[aff1]{School of Automation, Harbin Institute of Technology, Harbin 150001, China} \affiliation[af`
  - 原因：目标模板使用自己的作者/单位命令体系；已按模板重建，示例内容完整保留在注释中
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.metadata · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - ⚠️ 需作者确认
  - 位置：`main.tex`
- **元信息** · `摘要`
  - 变更前：``
  - 变更后：`Background: Deploying convolutional neural networks on battery-powered edge devices is limited by the energy c`
  - 原因：套用目标模板的摘要环境（摘要文字取自原稿，未改写）
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.metadata.abstract_env · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`

### 章节结构（2）

- **章节结构** · `文献区`
  - 变更前：`模板示例文献`
  - 变更后：`保留模板的文献命令，指向原稿文献库`
  - 原因：目标模板的文献命令（\bibliographystyle/\bibliography）被保留
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.template · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`
- **章节结构** · `文档骨架`
  - 变更前：`main.tex`
  - 变更后：`elsarticle-num.tex`
  - 原因：以期刊官方 LaTeX 模板为骨架，原稿内容移植进去；模板的 documentclass 与宏包体系保持不变
  - 依据：Elsevier 通用研究论文（Research Article） · output.latex.template · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - 位置：`main.tex`

### 投稿声明（2）

- **投稿声明** · `Funding`
  - 变更前：`(缺失)`
  - 变更后：`[待作者填写]`
  - 原因：目标期刊要求该声明，原稿中没有；已插入占位段落，内容必须由作者补全
  - 依据：Elsevier 通用研究论文（Research Article） · declarations.required · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - ⚠️ 需作者确认
  - 位置：`main.tex`
- **投稿声明** · `Data Availability Statement`
  - 变更前：`(缺失)`
  - 变更后：`[待作者填写]`
  - 原因：目标期刊要求该声明，原稿中没有；已插入占位段落，内容必须由作者补全
  - 依据：Elsevier 通用研究论文（Research Article） · declarations.required · needs_confirmation · https://www.elsevier.com/journals/information-for-authors
  - ⚠️ 需作者确认
  - 位置：`main.tex`

## 四、需要作者确认的改动

- [ ] **元信息** · `作者列表`：按目标模板的 \author 命令重新生成（原命令保留在注释中）
- [ ] **元信息** · `作者/单位命令区`：目标模板使用自己的作者/单位命令体系；已按模板重建，示例内容完整保留在注释中
- [ ] **投稿声明** · `Funding`：目标期刊要求该声明，原稿中没有；已插入占位段落，内容必须由作者补全
- [ ] **投稿声明** · `Data Availability Statement`：目标期刊要求该声明，原稿中没有；已插入占位段落，内容必须由作者补全

## 五、未做的改动（明确说明）

- 未改写论文正文段落（除非上方明细中明确列出）。
- 未删除公式、表格、图片或引用。
- 未改动任何科学数据与结论。
- 无法自动转换的元素保留原始内容，并在质检报告中列为警告/提示。
- **导言区** · `期刊配置要求的宏包`：模板模式以官方模板的导言区为准；模板已声明的宏包不重复添加，以免选项冲突。若模板缺少某必需宏包，请手动补入。
