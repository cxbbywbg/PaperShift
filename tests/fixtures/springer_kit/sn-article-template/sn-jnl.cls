%% sn-jnl.cls 的最小替身（测试夹具用，不是官方类文件）。
%%
%% 两件事：
%%   1. 让「模板辅助文件随包复制」这条规则有东西可测；
%%   2. **复刻真实 sn-jnl.cls 里「类文件自己加载宏包 / 定义命令」的行为** ——
%%      官方类文件在 .cls 里 `\usepackage[numbers,sort&compress]{natbib}`、
%%      `\def\fnm`、`\def\sur`、`\newcommand{\orgdiv}`。只看模板主文件会漏掉
%%      这些，于是原稿里同名的 `\usepackage`/`\providecommand` 被重复带入，
%%      轻则冗余、重则 "Option clash for package natbib" / "already defined"。
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{sn-jnl}[2024/12/13 test stub]
\LoadClass{article}

%% 类文件内部加载的宏包（真实 sn-jnl.cls 亦如此）
\RequirePackage{graphicx}
\usepackage[numbers,sort&compress]{natbib}
\usepackage{amssymb}

%% 类文件内部定义的命令（真实 sn-jnl.cls 亦如此）
\def\fnm#1{\leavevmode\hbox{#1}}
\def\sur#1{\unskip~\nobreak\leavevmode\hbox{#1}}
\newcommand{\orgdiv}[1]{#1}
\newcommand{\orgname}[1]{#1}
\newcommand{\orgaddress}[1]{#1}
\newcommand{\street}[1]{#1}
\newcommand{\city}[1]{#1}
\newcommand{\postcode}[1]{#1}
\newcommand{\state}[1]{#1}
\newcommand{\country}[1]{#1}

%% 类文件内部定义的定理环境（真实 sn-jnl.cls 亦如此）
\newtheorem{theorem}{Theorem}
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{definition}{Definition}

%% 类文件内部选定文献样式（真实 sn-jnl.cls 用 \if@Mathphys@numrefstyle 等分支，
%% 每个分支各 \bibliographystyle 一次）。这意味着 `bst/` 里的 .bst 虽然正文
%% 一个字都没提，也是**编译必需**文件。
\bibliographystyle{sn-mathphys-num}
