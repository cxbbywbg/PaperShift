"""差异对比：原稿 vs 转换稿。

做法是**把转换稿重新解析成同一个 Manuscript 模型，然后做模型级比较**——
这比比较文件字节更能说明「内容到底变了没有」，也正好复用了模块 1 的解析能力。

比较维度：
  元信息 / 章节结构与正文文本 / 图 / 表 / 公式 / 引用 / 文献 / 交叉引用 / 统计
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import model as M
from .util import normalize_ws, truncate


def _sec_key(s: M.Section) -> str:
    return s.number or s.heading.strip().lower()


# 单位信息的两种写法：
#   CAS 式：`organization={School of Automation, GDUT}, city={Guangzhou}, country={China}`
#   Springer 式：`\orgdiv{School of Automation}, \orgname{GDUT}, \city{Guangzhou}, \country{China}`
# 内容等价，只是命令体系不同。直接比字符串会把「换个写法」误报成「单位被改了」。
_AFFIL_KV_RE = re.compile(
    r"\b(?:organization|organisation|institution|department|division|"
    r"street|address|city|town|postcode|zip|state|province|country)\s*=\s*")
_AFFIL_CMD_RE = re.compile(
    r"\\(?:orgdiv|orgname|orgaddress|organization|organisation|institution|"
    r"department|division|street|address|city|town|postcode|zip|state|"
    r"province|country|affil|affiliation)\s*")


def _canon_affil(text: str) -> str:
    """把单位信息归一成「内容」，忽略命令体系与 `key=` 写法的差异。

    剥掉 `key=` 前缀与已知命令名，去掉花括号与空字段后按逗号重组。
    `\postcode{}` 这类空字段不会在结果里留下多余的 `, ,`。
    """
    t = _AFFIL_KV_RE.sub("", text or "")
    t = _AFFIL_CMD_RE.sub("", t)
    t = t.replace("{", " ").replace("}", " ")
    segs = [normalize_ws(s) for s in t.split(",")]
    return ", ".join(s for s in segs if s)


def conclusion_text(d: dict) -> str:
    """一句话结论（CLI、报告、差异报告共用同一套措辞，避免口径不一致）。"""
    if d.get("identical"):
        if d.get("metadata_rewritten"):
            return "正文内容未变；元信息按目标模板的命令体系重新表示（标题/作者/单位/摘要/关键词）"
        if d.get("metadata_changed"):
            return "正文内容未变；但元信息变化较大，请对照差异报告核对"
        return "无（仅版式/格式发生变化）"
    if d.get("declarations_only_added"):
        decl = d.get("declarations") or {}
        names = "、".join(decl.get("added") or [])
        return f"正文内容未变；仅补入投稿声明占位（{names}，需作者补全）"
    return "有，见差异报告"


def _text_of(s: M.Section) -> str:
    return "\n".join(b.text for b in s.blocks if b.text)


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def diff_manuscripts(before: M.Manuscript, after: M.Manuscript,
                     label_before: str = "原稿", label_after: str = "转换稿") -> dict:
    out: dict = {
        "label_before": label_before,
        "label_after": label_after,
        "identical": True,
        "metadata": {},
        "sections": {},
        "declarations": {},
        "figures": {},
        "tables": {},
        "equations": {},
        "citations": {},
        "references": {},
        "crossrefs": {},
        "stats": {},
        "notes": [],
    }

    # ---- 元信息 ----
    md: dict = {}
    for field, name in (
        ("title", "标题"), ("abstract", "摘要"), ("keywords", "关键词"),
    ):
        va = normalize_ws(str(getattr(before.metadata, field, "") or ""))
        vb = normalize_ws(str(getattr(after.metadata, field, "") or ""))
        if va != vb:
            md[field] = {
                "label": name,
                "before": truncate(va, 300),
                "after": truncate(vb, 300),
                "ratio": _ratio(va, vb),
            }
    names_a = [a.get("name", "") for a in before.metadata.authors]
    names_b = [a.get("name", "") for a in after.metadata.authors]
    if names_a != names_b:
        md["authors"] = {
            "label": "作者", "before": ", ".join(names_a), "after": ", ".join(names_b),
            "ratio": _ratio(", ".join(names_a), ", ".join(names_b)),
        }
    aff_a = before.metadata.affiliations
    aff_b = after.metadata.affiliations
    # 比「内容」而不是「写法」：CAS 的 key=value 与 Springer 的 \orgdiv/\city
    # 内容相同时不该报成「单位被改了」。
    canon_a = " | ".join(_canon_affil(a) for a in aff_a)
    canon_b = " | ".join(_canon_affil(a) for a in aff_b)
    if canon_a != canon_b:
        md["affiliations"] = {
            "label": "单位", "before": " | ".join(aff_a), "after": " | ".join(aff_b),
            "ratio": _ratio(canon_a, canon_b),
        }
    out["metadata"] = md

    # ---- 章节与正文 ----
    map_a = {_sec_key(s): s for s in before.sections}
    map_b = {_sec_key(s): s for s in after.sections}
    added = [k for k in map_b if k not in map_a]
    removed = [k for k in map_a if k not in map_b]
    changed = []
    unchanged = []
    for key in map_a:
        if key not in map_b:
            continue
        sa, sb = map_a[key], map_b[key]
        ta, tb = _text_of(sa), _text_of(sb)
        if ta == tb and sa.heading == sb.heading:
            unchanged.append(key)
            continue
        entry = {
            "key": key,
            "heading_before": sa.heading,
            "heading_after": sb.heading,
            "words_before": sa.words,
            "words_after": sb.words,
            "ratio": _ratio(ta, tb),
            "unified": list(difflib.unified_diff(
                ta.split("\n"), tb.split("\n"),
                fromfile=f"{label_before}/{sa.heading}",
                tofile=f"{label_after}/{sb.heading}",
                lineterm="", n=1,
            ))[:120],
        }
        changed.append(entry)
    out["sections"] = {
        "added": [{"key": k, "heading": map_b[k].heading, "words": map_b[k].words} for k in added],
        "removed": [{"key": k, "heading": map_a[k].heading, "words": map_a[k].words} for k in removed],
        "changed": changed,
        "unchanged_count": len(unchanged),
        "total_before": len(before.sections),
        "total_after": len(after.sections),
    }

    # ---- 投稿声明 ----
    # 声明与正文章节分开比较：转换器在缺声明时会插入「[待作者填写]」占位，
    # 这属于**已知的、需作者补全的改动**，不应该混进"章节新增"里吓人。
    da = {d.kind: d for d in before.declarations}
    db = {d.kind: d for d in after.declarations}
    decl_added = sorted(k for k in db if k not in da or (db[k].present and not da[k].present))
    decl_removed = sorted(k for k in da if k not in db or (da[k].present and not db[k].present))
    decl_changed = []
    for k in da:
        if k not in db or k in decl_added or k in decl_removed:
            continue
        ta, tb = normalize_ws(da[k].text or ""), normalize_ws(db[k].text or "")
        if ta != tb:
            decl_changed.append({
                "kind": k, "before": truncate(ta, 200), "after": truncate(tb, 200),
                "ratio": _ratio(ta, tb),
            })
    out["declarations"] = {
        "added": decl_added,
        "removed": decl_removed,
        "changed": decl_changed,
        "count_before": sum(1 for d in before.declarations if d.present),
        "count_after": sum(1 for d in after.declarations if d.present),
    }

    # ---- 图 / 表 / 公式 ----
    def _obj_diff(a_list, b_list, name_fields):
        da = {x.id: x for x in a_list}
        db = {x.id: x for x in b_list}
        res = {
            "added": [{"id": i, **{f: getattr(db[i], f, "") for f in name_fields}} for i in db if i not in da],
            "removed": [{"id": i, **{f: getattr(da[i], f, "") for f in name_fields}} for i in da if i not in db],
            "changed": [],
            "count_before": len(a_list),
            "count_after": len(b_list),
        }
        for i in da:
            if i not in db:
                continue
            diffs = {}
            for f in name_fields:
                va, vb = getattr(da[i], f, ""), getattr(db[i], f, "")
                if normalize_ws(str(va)) != normalize_ws(str(vb)):
                    diffs[f] = {"before": truncate(str(va), 200), "after": truncate(str(vb), 200)}
            if diffs:
                res["changed"].append({"id": i, "fields": diffs})
        return res

    out["figures"] = _obj_diff(before.figures, after.figures, ["caption", "files", "label"])
    out["tables"] = _obj_diff(before.tables, after.tables, ["caption", "label", "n_rows", "n_cols"])
    out["equations"] = _obj_diff(before.equations, after.equations, ["source", "label", "env"])

    # ---- 引用与文献 ----
    ca = sorted({c.key for c in before.citations})
    cb = sorted({c.key for c in after.citations})
    out["citations"] = {
        "added": sorted(set(cb) - set(ca)),
        "removed": sorted(set(ca) - set(cb)),
        "count_before": len(ca),
        "count_after": len(cb),
    }
    ra = sorted({r.key for r in before.references})
    rb = sorted({r.key for r in after.references})
    out["references"] = {
        "added": sorted(set(rb) - set(ra)),
        "removed": sorted(set(ra) - set(rb)),
        "count_before": len(ra),
        "count_after": len(rb),
    }
    xa = sorted({x.get("target", "") for x in before.crossrefs})
    xb = sorted({x.get("target", "") for x in after.crossrefs})
    out["crossrefs"] = {
        "added": sorted(set(xb) - set(xa)),
        "removed": sorted(set(xa) - set(xb)),
        "count_before": len(xa),
        "count_after": len(xb),
    }

    # ---- 统计 ----
    sa, sb = before.stats(), after.stats()
    out["stats"] = {
        k: {"before": sa.get(k), "after": sb.get(k)}
        for k in sorted(set(sa) | set(sb))
        if sa.get(k) != sb.get(k)
    }

    # ---- 结论 ----
    # 元信息**不算正文**：换模板必然要换命令体系（`\author` → `\fnm/\sur`、
    # `\affiliation` → `\orgdiv/\orgname/\orgaddress`），把这种「换个写法」算成
    # 「内容差异」会让作者以为正文被改了。单独归类，只在相似度掉下来时才提示。
    meta_ratios = [v.get("ratio", 0.0) for v in (md or {}).values()]
    body_changed = bool(any([
        out["sections"]["added"], out["sections"]["removed"], out["sections"]["changed"],
        out["figures"]["added"], out["figures"]["removed"], out["figures"]["changed"],
        out["tables"]["added"], out["tables"]["removed"], out["tables"]["changed"],
        out["equations"]["added"], out["equations"]["removed"], out["equations"]["changed"],
        out["citations"]["added"], out["citations"]["removed"],
        out["references"]["added"], out["references"]["removed"],
    ]))
    decl = out["declarations"]
    decl_changed_any = bool(decl["added"] or decl["removed"] or decl["changed"])
    # 「正文没动，只是补了声明占位」是一个需要单独说清楚的结论：
    # 它既不是"零差异"，也不该被当成正文被改写。
    out["body_changed"] = body_changed
    out["metadata_changed"] = bool(md)
    # 元信息差异是否只是「换了个写法」——相似度都还在高位就按表示变化处理
    out["metadata_rewritten"] = bool(meta_ratios) and all(r >= 0.9 for r in meta_ratios)
    out["declarations_only_added"] = bool(
        not body_changed and decl["added"] and not decl["removed"] and not decl["changed"]
    )
    out["identical"] = not body_changed and not decl_changed_any

    if out["figures"]["removed"] or out["tables"]["removed"] or out["equations"]["removed"]:
        out["notes"].append("检测到对象丢失，属于严重问题，请立即核对转换结果。")
    if out["references"]["removed"]:
        out["notes"].append("参考文献条目减少，请确认是否被误删。")
    if out["declarations_only_added"]:
        out["notes"].append(
            f"转换过程自动补入了 {len(decl['added'])} 条投稿声明占位"
            f"（{'、'.join(decl['added'])}），正文内容为 `[待作者填写]`，需作者补全。"
        )
    return out


# --------------------------------------------------------------------------
# Markdown 渲染
# --------------------------------------------------------------------------


def render_diff_md(d: dict, max_unified: int = 12) -> str:
    L: list[str] = []
    L.append("# 差异报告")
    L.append("")
    L.append(f"- 对比：**{d['label_before']}** → **{d['label_after']}**")
    if d.get("identical"):
        if d.get("metadata_rewritten"):
            concl = ("**正文内容未变**；元信息已按目标模板的命令体系重新表示"
                     "（标题/作者/单位/摘要/关键词）")
        elif d.get("metadata_changed"):
            concl = "**正文内容未变**；但元信息变化较大，请对照下文核对"
        else:
            concl = "**未检测到内容差异**（仅版式/格式发生变化）"
    elif d.get("declarations_only_added"):
        concl = "**正文内容未变**；转换过程补入了投稿声明占位（`[待作者填写]`，需作者补全）"
    else:
        concl = "**检测到内容差异，详见下文**"
    L.append(f"- 结论：{concl}")
    L.append("")
    for note in d.get("notes", []):
        L.append(f"> ⚠️ {note}")
    if d.get("notes"):
        L.append("")

    # 元信息
    if d["metadata"]:
        L.append("## 一、元信息变化")
        L.append("")
        L.append("| 项目 | 相似度 | 变更前 | 变更后 |")
        L.append("|---|---|---|---|")
        for k, v in d["metadata"].items():
            L.append(f"| {v['label']} | {v['ratio']:.2%} | {v['before'][:80]} | {v['after'][:80]} |")
        L.append("")

    # 章节
    s = d["sections"]
    L.append("## 二、章节结构")
    L.append("")
    L.append(f"- 章节数：{s['total_before']} → {s['total_after']}")
    L.append(f"- 内容未变的章节：{s['unchanged_count']} 个")
    if s["added"]:
        L.append(f"- 新增章节：{'、'.join(x['heading'] for x in s['added'])}")
    if s["removed"]:
        L.append(f"- 缺失章节：{'、'.join(x['heading'] for x in s['removed'])}")
    L.append("")
    if s["changed"]:
        L.append("### 正文文本变化")
        L.append("")
        L.append("| 章节 | 词数 前→后 | 文本相似度 |")
        L.append("|---|---|---|")
        for c in s["changed"]:
            L.append(f"| {c['heading_after'] or c['heading_before']} | {c['words_before']} → {c['words_after']} | {c['ratio']:.2%} |")
        L.append("")
        low = [c for c in s["changed"] if c["ratio"] < 0.98]
        if low:
            L.append("文本相似度低于 98% 的章节（需重点核对）：")
            L.append("")
            for c in low[:max_unified]:
                L.append(f"<details><summary>{c['heading_after']}（相似度 {c['ratio']:.2%}）</summary>")
                L.append("")
                L.append("```diff")
                L.extend(c["unified"][:60] or ["(无行级差异)"])
                L.append("```")
                L.append("</details>")
                L.append("")

    # 图表公式
    def _block(title: str, key: str, fields: str) -> None:
        b = d[key]
        if not (b["added"] or b["removed"] or b["changed"]):
            L.append(f"## {title}")
            L.append("")
            L.append(f"- 数量：{b['count_before']} → {b['count_after']}，**无变化**")
            L.append("")
            return
        L.append(f"## {title}")
        L.append("")
        L.append(f"- 数量：{b['count_before']} → {b['count_after']}")
        if b["added"]:
            L.append(f"- 新增：{', '.join(x['id'] for x in b['added'])}")
        if b["removed"]:
            L.append(f"- **丢失**：{', '.join(x['id'] for x in b['removed'])}")
        for c in b["changed"]:
            parts = "；".join(
                f"{f}: `{v['before']}` → `{v['after']}`" for f, v in c["fields"].items()
            )
            L.append(f"- 变化：`{c['id']}` {parts}")
        L.append("")

    _block("三、图", "figures", "caption/files/label")
    _block("四、表", "tables", "caption/label/dims")
    _block("五、公式", "equations", "source/label/env")

    # 引用与文献
    L.append("## 六、引用与参考文献")
    L.append("")
    for key, name in (("citations", "正文引用"), ("references", "参考文献条目"), ("crossrefs", "交叉引用")):
        b = d[key]
        flag = "（无变化）" if not (b["added"] or b["removed"]) else ""
        L.append(f"- **{name}**：{b['count_before']} → {b['count_after']} {flag}")
        if b["added"]:
            L.append(f"  - 新增：{', '.join(b['added'][:20])}")
        if b["removed"]:
            L.append(f"  - 丢失：{', '.join(b['removed'][:20])}")
    L.append("")

    # 投稿声明
    decl = d.get("declarations") or {}
    if decl:
        L.append("## 七、投稿声明")
        L.append("")
        L.append(f"- 已提供的声明：{decl['count_before']} → {decl['count_after']} 条")
        if decl["added"]:
            L.append(f"- **自动补入（需作者填写）**：{'、'.join(decl['added'])}")
        if decl["removed"]:
            L.append(f"- **丢失**：{'、'.join(decl['removed'])}")
        for c in decl["changed"]:
            L.append(f"- 变化：`{c['kind']}`（相似度 {c['ratio']:.2%}）"
                     f"\n  - 前：{c['before'][:150]}\n  - 后：{c['after'][:150]}")
        if not (decl["added"] or decl["removed"] or decl["changed"]):
            L.append("- 无变化")
        L.append("")

    # 统计
    if d["stats"]:
        L.append("## 八、统计变化")
        L.append("")
        L.append("| 指标 | 变更前 | 变更后 |")
        L.append("|---|---|---|")
        for k, v in d["stats"].items():
            L.append(f"| {k} | {v['before']} | {v['after']} |")
        L.append("")
    return "\n".join(L)
