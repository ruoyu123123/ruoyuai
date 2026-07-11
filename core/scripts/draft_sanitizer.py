# -*- coding: utf-8 -*-
"""gen-model 草稿的确定性清洗器。

处理两类机械格式问题：

1. **成对符号腰斩**：引号“”/方括号【】/书名号《》/圆括号（）/单引号‘’ 被句末标点 +
   换行劈开，右半边掉到下一段（如 `【说明：…实体化身。\\n\\n】`、`“…抢救！\\n\\n”他`）。
   统一合并被换行拆开的成对符号。

2. **整块逐字复制**：检测并移除连续出现的重复正文块。

纯确定性文本处理（不调模型）、幂等、零外部依赖。
供 gen_writer 草稿落地前调用（draft 后处理），亦可 CLI 独立修复。
"""
import re
import sys
import json

# 成对符号（左, 右）
PAIRS = [('“', '”'), ('【', '】'), ('《', '》'), ('（', '）'), ('‘', '’')]


def _imbalance(s):
    """本段所有成对符号「左多于右」的累计欠缺数（>0 = 有未闭合左符号）。"""
    return sum(max(0, s.count(l) - s.count(r)) for l, r in PAIRS)


def fix_pair_balance(text):
    """合并被换行劈开的成对符号。

    扫描段落：若某段有未闭合左符号（左>右），向后逐段合并（去掉中间换行）直到配平。
    覆盖两种腰斩形态：① 左符号开头无闭合（`“…` / `【…`）② 句末标点后右符号被切到下段。
    返回 (新文本, 合并段数)。
    """
    paras = text.split('\n\n')
    out = []
    i = 0
    merged = 0
    while i < len(paras):
        p = paras[i]
        j = i
        # 安全上限：单次最多并 20 段，防异常文本死循环
        guard = 0
        while _imbalance(p) > 0 and j + 1 < len(paras) and guard < 20:
            j += 1
            guard += 1
            p = p.rstrip() + paras[j].lstrip()
            merged += 1
        out.append(p)
        i = j + 1
    return '\n\n'.join(out), merged


def dedup_blocks(text, min_run=8):
    """删除整块逐字复制（freestyle expand 事故）。

    检测是否存在 a<b 使连续段 texts[b..b+L) 与 texts[a..a+L) 逐字相同且 L≥min_run、
    且两块不重叠（a+L≤b），删除第二份 [b..b+L)。min_run=8 保护合法的零星重复
    （如系统面板提示），只剿整块复制。返回 (新文本, 删除段数)。
    """
    paras = text.split('\n\n')
    n = len(paras)
    keep = [True] * n
    removed = 0
    b = 0
    while b < n:
        if not keep[b] or not paras[b].strip():
            b += 1
            continue
        best = None
        for a in range(0, b):
            if not keep[a] or paras[a] != paras[b] or not paras[a].strip():
                continue
            # 从 a/b 起连续比对（要求 a 块不越界进入 b 块：a+L <= b）
            L = 0
            while (b + L < n and a + L < b and keep[a + L]
                   and paras[a + L] == paras[b + L]):
                L += 1
            if L >= min_run:
                best = (a, L)
                break
        if best:
            a, L = best
            for k in range(b, b + L):
                keep[k] = False
            removed += L
            b += L
        else:
            b += 1
    new = [paras[i] for i in range(n) if keep[i]]
    return '\n\n'.join(new), removed


def split_system_panels(text):
    """纯连续系统面板段 → 每个面板独立成段（用户偏好 2026-06-07）。

    系统提示【】当 UI 弹窗，连续多条挤一行（如 `【A】【B】【C】`）应「按框换行」、
    一框一段，更像系统提示也更易读。只拆「纯面板段」——整段去掉所有【...】后无正文残留；
    正文里嵌的【】（如 `屏幕弹出那句：【欢迎接手…】`）保持不动，否则会把句子劈断。
    返回 (新文本, 拆分出的新段数)。
    """
    paras = text.split('\n\n')
    out = []
    splits = 0
    for p in paras:
        s = p.strip()
        panels = re.findall(r'【[^】]*】', s)
        if len(panels) >= 2 and re.sub(r'【[^】]*】', '', s).strip() == '':
            # 纯连续面板段 → 每框独立成段
            out.append('\n\n'.join(panels))
            splits += len(panels) - 1
        else:
            out.append(p)
    return '\n\n'.join(out), splits


def sanitize(text):
    """① 去整块复制 ② 修成对符号腰斩 ③ 纯连续系统面板段按框换行。返回 (新文本, 报告 dict)。"""
    t1, dup = dedup_blocks(text)
    t2, pair = fix_pair_balance(t1)
    t3, panel = split_system_panels(t2)
    return t3, {"dedup_segments_removed": dup, "pair_merges": pair, "panel_splits": panel}


def main():
    import argparse
    ap = argparse.ArgumentParser(description='gen-model 草稿确定性清洗（成对符号腰斩 + 整块去重）')
    ap.add_argument('file', help='草稿 txt 路径')
    ap.add_argument('--fix', action='store_true', help='就地修复（带 .sanitize_bak.txt 备份）；缺省只检测报告')
    args = ap.parse_args()
    text = open(args.file, encoding='utf-8').read()
    new, rep = sanitize(text)
    rep['changed'] = (new != text)
    if args.fix and rep['changed']:
        open(args.file + '.sanitize_bak.txt', 'w', encoding='utf-8').write(text)
        open(args.file, 'w', encoding='utf-8').write(new)
        rep['fixed'] = True
    else:
        rep['fixed'] = False
    print(json.dumps(rep, ensure_ascii=False))


if __name__ == '__main__':
    main()
