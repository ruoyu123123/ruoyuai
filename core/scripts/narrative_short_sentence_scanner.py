#!/usr/bin/env python3
"""
narrative_short_sentence_scanner.py — 叙述段短句堆叠检测

检测「叙述段（非对话非笔注）句号当逗号用」的 AI 模板节奏：
段长 < 80 CJK 字 且 句号 ≥3 且 逗号 ≤1 → 触发 advisory

排除：
- 对话段（含「」引号包裹的内容占段 ≥50%）
- 章末笔注段（章尾区含「样本编号」「档案标号」等通用档案标记后的所有段）
- 极短引子段（单句对话/动作描述）

输出：JSON {scanner, ch, violations:[{line,severity,evidence,period,comma,cjk}], total, density}

用法：
  python narrative_short_sentence_scanner.py <章节文件路径>
  python narrative_short_sentence_scanner.py <章节文件路径> [--project <项目路径>] [--style <作者风格档>]
    # --project/--style 可选：读作者档句长均值，短句碎切作者(均值<22)时 relax-only 放宽阈值(北极星⑤)
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

# v2 cluster 化（2026-05-29 复核）：本 scanner 的判定单元是「单个叙述段」——
# 触发阈值（段长 < 80 CJK 且 句号 ≥3 且 逗号 ≤1）只看单段内部结构，
# 与整篇文本体量（chapter 3-4k vs cluster 12-25k CJK）无关：cluster 草稿里的
# 每个段落和 chapter 里的段落是同一批物理段，逐段判定完全一致。
# violation_density 用 narrative_paras_total 归一，长文本天然不会因段多而误升。
# 故 cluster 化对本 scanner 阈值无意义 → 不引入 IS_CLUSTER_MODE 分支（删除死变量 + 误导注释）。



def _author_sentence_mean(style_path: str | None, project: str | None):
    """读作者档句长均值作第一权威(北极星⑤·relax-only)。

    对齐兄弟 scanner prose_rhythm_scanner._author_baseline：读
    _数据库/作者风格.json|作者风格_FINAL.json 的 quantitative.sentence_length.mean。
    取不到返回 None → 沿用通用兜底阈值(行为同改前)。
    """
    cands = []
    if style_path:
        cands.append(Path(style_path))
    if project:
        cands += [Path(project) / '_数据库' / '作者风格.json',
                  Path(project) / '_数据库' / '作者风格_FINAL.json']
    for c in cands:
        try:
            if c.exists():
                d = json.loads(c.read_text(encoding='utf-8'))
                sl = (d.get('quantitative') or {}).get('sentence_length') or {}
                if isinstance(sl.get('mean'), (int, float)):
                    return float(sl['mean'])
        except Exception:
            pass
    return None


def is_dialogue_para(para: str) -> bool:
    """对话段：「」引号包裹内容占段 ≥50%"""
    quote_chars = sum(1 for c in para if c in '「」“”『』"')  # 补弯引号 U+201C/U+201D
    if quote_chars < 2:
        return False
    # 提取「」内字符
    inside = re.findall(r'「([^」]*)」', para) + re.findall('“([^”]*)”', para) + re.findall(r'"([^"]*)"', para)
    inside_cjk = sum(len(re.findall(r'[一-鿿]', s)) for s in inside)
    total_cjk = len(re.findall(r'[一-鿿]', para))
    if total_cjk == 0:
        return False
    return (inside_cjk / total_cjk) >= 0.5


def detect_annotation_zone(paras: list) -> set:
    """检测章末笔注段范围(仅章尾区·防中段误命中吞整尾)

    2026-06-16 修(triage L50)：去掉绑定某本书世界观专名的 marker(ZF/观测员档案)，
    系统须对任意作者通用；并加位置守卫——笔注是章末块，只在后 40%(且至少最后 3 段)
    内找首个标记，避免正文中段子串命中(如『翻开档案标号』)静默吞掉整条章尾扫描。
    """
    annotation_markers = ['样本编号', '【档案', '档案标号']
    n = len(paras)
    if n == 0:
        return set()
    # 位置守卫:笔注是章末块，只在后 40%(且至少最后 3 段)内找首个标记
    tail_start = max(n - 3, int(n * 0.6))
    annotation_start = None
    for i in range(tail_start, n):
        if any(m in paras[i] for m in annotation_markers):
            annotation_start = i
            break
    if annotation_start is None:
        return set()
    return set(range(annotation_start, n))


def scan_chapter(text: str, author_sent_mean: float | None = None) -> dict:
    paras = text.split('\n')
    # 北极星⑤:作者档证短句碎切风格(句长均值 < 22)则放宽 period 阈值，relax-only 不收紧。
    # author_sent_mean 缺省(None)时 period_floor=3，行为与改前完全一致。
    period_floor = 3
    if author_sent_mean is not None and author_sent_mean < 22:
        period_floor = 5
    annotation_zone = detect_annotation_zone(paras)
    violations = []
    narrative_paras = 0

    for i, para in enumerate(paras):
        para = para.strip()
        if not para:
            continue
        if i in annotation_zone:
            continue
        if is_dialogue_para(para):
            continue
        # 跳过极短段（< 15 字）
        cjk = len(re.findall(r'[一-鿿]', para))
        if cjk < 15:
            continue
        narrative_paras += 1

        period = para.count('。')
        comma = para.count('，')

        # 触发条件：段长 < 80 字 且 句号 ≥period_floor 且 逗号 ≤1
        if cjk < 80 and period >= period_floor and comma <= 1:
            severity = 'major' if period >= 5 else 'minor'
            violations.append({
                'line': i + 1,
                'severity': severity,
                'evidence': para[:80],
                'period': period,
                'comma': comma,
                'cjk': cjk,
                'period_per_10cjk': round(period * 10 / cjk, 2)
            })

    return {
        'scanner': 'narrative_short_sentence_overuse',
        'narrative_paras_total': narrative_paras,
        'annotation_paras': len(annotation_zone),
        'violations_count': len(violations),
        'violation_density': round(len(violations) / max(narrative_paras, 1), 3),
        'violations': violations,
        'verdict': 'PASS' if not violations else ('FAIL_MAJOR' if any(v['severity'] == 'major' for v in violations) else 'FAIL_MINOR'),
        'gate_level': 'advisory',  # 默认 advisory（voice 工艺豁免后仍要看，不直接 hard_gate）
        '_doc': 'voice 工艺被错套到叙述段的探针；feedback_voice_craft_applies_to_boundary lesson 配套'
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python narrative_short_sentence_scanner.py <章节路径>")
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"路径不存在: {path}")
        sys.exit(2)
    # 北极星⑤:解析可选 --style/--project，读作者档句长均值作第一权威(relax-only)。
    # 未传时 asm=None → 行为同改前。
    style_path = None
    project = None
    args = sys.argv[2:]
    for k in range(len(args) - 1):
        if args[k] == '--style':
            style_path = args[k + 1]
        elif args[k] == '--project':
            project = args[k + 1]
    asm = _author_sentence_mean(style_path, project)
    text = path.read_text(encoding='utf-8')
    result = scan_chapter(text, asm)
    result['file'] = str(path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # exit code: 0 PASS, 1 violations exist
    sys.exit(0 if result['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
