%% cas-sc.cls 的最小替身（测试夹具用，不是官方类文件）。
%%
%% 要点：类文件自己 `\RequirePackage{cas-common}` —— 于是「结果包该带哪些文件」
%% 必须**递归跟到类文件里**，只看正文会漏掉 cas-common.sty。
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{cas-sc}[2024/12/13 test stub]
\LoadClass{article}
\RequirePackage{cas-common}
\RequirePackage{graphicx}
\endinput
