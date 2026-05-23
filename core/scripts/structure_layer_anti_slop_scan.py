"""v23.5 结构层 anti-slop 统一扫描器 - N 维清单

汇总所有结构层 anti-slop 检测到一个报告，防 [[feedback_meta_anti_slop_structure_blindspot_recurrence]] 第 4 次重犯。

当前覆盖维度（持续扩展）：
- D1 字数 (CJK)
- D2 破折号「——」密度
- D3 代词「他/她」节奏
- D4 段落结构（段均长 / 单句成段率 / 极短段）
- D5 句首单调性（连续段首主语相同）
- D6 顿号三段式
- D7 拟声词密度
- D8 对话标签密度（说/道/说道）

用法：
    python structure_layer_anti_slop_scan.py <项目路径> [--ch <N> | --all]
"""
import sys, os, re, json
from pathlib import Path
from datetime import datetime


def scan_d1_word_count(text: str) -> dict:
    cjk = sum(1 for c in text if '一' <= c <= '鿿')
    return {"dimension": "字数", "cjk": cjk}


def scan_d2_dash(text: str) -> dict:
    dash = text.count('——')
    cjk = max(scan_d1_word_count(text)["cjk"], 1)
    density = round(dash / cjk * 1000, 2)
    paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    extreme = sum(1 for p in paras if p.count('——') >= 3)
    issues = []
    if density > 12:
        issues.append(f"密度 {density}/千字 (健康 ≤ 12)")
    if extreme > 0:
        issues.append(f"{extreme} 极端段 (健康 = 0)")
    return {"dimension": "破折号", "density_per_1k": density, "extreme_paragraphs": extreme, "healthy": not issues, "issues": issues}


def scan_d3_pronoun(text: str, anchor_chars: list = None) -> dict:
    anchor_chars = anchor_chars or ['陈默']
    he_subject = len(re.findall(r'(?:^|[。\n])\s*他(?=[^的们她])', text))
    name_count = sum(text.count(n) for n in anchor_chars)
    ratio = round(he_subject / max(name_count, 1), 2)
    paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    max_he_run = 0
    for p in paras:
        sents = re.split(r'[。！？]', p)
        run = 0
        cur = 0
        for s in sents:
            if re.match(r'^\s*他(?=[^的们她])', s.strip()):
                cur += 1
                run = max(run, cur)
            else:
                cur = 0
        max_he_run = max(max_he_run, run)
    issues = []
    if ratio > 1.5:
        issues.append(f"「他」做主语 {ratio}x「{anchor_chars[0]}」 (健康 ≤ 1.5x)")
    if max_he_run >= 3:
        issues.append(f"单段连续「他」开头 {max_he_run} 句 (健康 ≤ 2)")
    return {"dimension": "代词节奏", "he_to_name_ratio": ratio, "max_consecutive_he_run": max_he_run, "healthy": not issues, "issues": issues}


def scan_d4_paragraph(text: str) -> dict:
    paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paras:
        return {"dimension": "段落结构", "healthy": True, "issues": []}
    avg_len = sum(len(p) for p in paras) / len(paras)
    short_pct = round(sum(1 for p in paras if len(p) < 15) / len(paras) * 100, 1)
    single_sent_pct = round(sum(1 for p in paras if len(re.findall(r'[。！？]', p)) <= 1) / len(paras) * 100, 1)
    issues = []
    # v23.5.1 阈值校准（用户决策 2026-05-17）：诡秘风短句节奏感是 voice 特征 → 放宽
    # 极端边界（≥70% 极短段 / 段均长 < 12）仍判定为不健康 - 这是真正 carrying voice
    if avg_len < 12 or avg_len > 60:
        issues.append(f"段均长 {avg_len:.1f} (健康 12-60，voice waiver 已宽容)")
    if short_pct > 70:
        issues.append(f"极短段 {short_pct}% (健康 ≤ 70%，voice waiver 已宽容)")
    if single_sent_pct > 90:
        issues.append(f"单句成段率 {single_sent_pct}% (健康 ≤ 90%，voice waiver 已宽容)")
    return {"dimension": "段落结构", "avg_paragraph_len": round(avg_len, 1), "short_para_pct": short_pct, "single_sentence_para_pct": single_sent_pct, "healthy": not issues, "issues": issues, "voice_waiver": "诡秘风短句节奏（用户决策）"}


def scan_d5_para_start_monotone(text: str) -> dict:
    paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    if len(paras) < 5:
        return {"dimension": "段首单调", "healthy": True, "issues": []}
    starts = [p[:2] for p in paras]
    max_run = 0
    cur_run = 1
    cur_token = starts[0] if starts else ''
    for s in starts[1:]:
        if s == cur_token:
            cur_run += 1
            max_run = max(max_run, cur_run)
        else:
            cur_token = s
            cur_run = 1
    issues = []
    if max_run >= 4:
        issues.append(f"连续段首相同 {max_run} 段 (健康 ≤ 3)")
    return {"dimension": "段首单调", "max_consecutive_same_start": max_run, "healthy": not issues, "issues": issues}


def scan_d6_dunhao_triple(text: str) -> dict:
    triples = len(re.findall(r'[^、\n]{1,8}、[^、\n]{1,8}、[^、\n]{1,8}', text))
    cjk = max(scan_d1_word_count(text)["cjk"], 1)
    per_1k = round(triples / cjk * 1000, 2)
    issues = []
    if per_1k > 3:
        issues.append(f"顿号三段式 {per_1k}/千字 (健康 ≤ 3)")
    return {"dimension": "顿号三段式", "per_1k": per_1k, "healthy": not issues, "issues": issues}


def scan_d7_onomatopoeia(text: str) -> dict:
    # 单字拟声常用：砰 咚 嗖 嘟 哗 嘀 啪 嘎 啦 喵 嗒 滴
    onos = re.findall(r'[砰咚嗖嘟哗嘀啪嘎啦喵嗒滴]——|[砰咚嗖嘟哗嘀啪嘎啦喵嗒滴][砰咚嗖嘟哗嘀啪嘎啦喵嗒滴]', text)
    cjk = max(scan_d1_word_count(text)["cjk"], 1)
    per_1k = round(len(onos) / cjk * 1000, 2)
    issues = []
    if per_1k > 5:
        issues.append(f"拟声 {per_1k}/千字 (健康 ≤ 5)")
    return {"dimension": "拟声词", "per_1k": per_1k, "count": len(onos), "healthy": not issues, "issues": issues}


def scan_d8_dialogue_tag(text: str) -> dict:
    tags = len(re.findall(r'[他她][说道](?:[，。：])', text))
    cjk = max(scan_d1_word_count(text)["cjk"], 1)
    per_1k = round(tags / cjk * 1000, 2)
    issues = []
    if per_1k > 8:
        issues.append(f"对话标签「他说/他道」{per_1k}/千字 (健康 ≤ 8)")
    return {"dimension": "对话标签", "per_1k": per_1k, "count": tags, "healthy": not issues, "issues": issues}


def scan_d10_named_dialogue_tag(text: str) -> dict:
    """v23.7 D10: 具名对话标签冗余（双人对话不必每句标说话人）
    用户反馈 2026-05-17：「这种就没必要一直强调是谁说的了，如果剧情中只有两个人对话」
    - 具名标签「X说，/X道，」密度 ≤ 8/千字
    - 连续 ≥3 段均为「具名+说/道+引号」格式（无动作节拍/无合并）= 双人对话套路
    """
    cjk = max(scan_d1_word_count(text)["cjk"], 1)
    named = re.findall(r'[一-龥]{2,4}[说道][，。：]', text)
    density = round(len(named) / cjk * 1000, 2)
    # 连续「具名说」段：每段以 2-4 字人名+说/道 开头，且该段含引号对话
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    max_run = 0
    cur = 0
    for ln in lines:
        if re.match(r'^[一-龥]{2,4}[说道][，。：].*[「『"]', ln):
            cur += 1
            max_run = max(max_run, cur)
        else:
            cur = 0
    issues = []
    if density > 8:
        issues.append(f"具名对话标签「X说，」{density}/千字 (健康 ≤ 8)")
    if max_run >= 3:
        issues.append(f"连续「具名说」对话 {max_run} 段无动作节拍 (健康 ≤ 2，双人对话省标签)")
    return {"dimension": "具名对话标签冗余", "density_per_1k": density, "named_tag_count": len(named),
            "max_consecutive_named_say": max_run, "healthy": not issues, "issues": issues}


def scan_d9_real_world_time(text: str) -> dict:
    """v23.6 D9: 真实世界时间禁令（用户禁令 2026-05-17）
    任何 20XX 年 / 20XX.MM.DD / 20XX-MM-DD / 20XX/MM/DD 出现 → 违规
    双轨制：官方文书用「异常历 X 年」/ 个人叙事用月日+星期（去年份）
    """
    violations = []
    patterns = [
        (r'(?<![\d])(20[12]\d)\s*年', '年份「YYYY 年」'),
        (r'(20[12]\d)[./\-](\d{1,2})[./\-](\d{1,2})', '日期「YYYY-MM-DD」'),
        (r'(20[12]\d)/(\d{1,2})/(\d{1,2})', '日期「YYYY/MM/DD」'),
        (r'GD-(20[12]\d)-', '编号「GD-YYYY-」'),
        (r'(20[12]\d)-(20[12]\d)', '年段「YYYY-YYYY」'),
    ]
    for pat, desc in patterns:
        ms = list(re.finditer(pat, text))
        if ms:
            violations.append({"pattern": desc, "count": len(ms), "samples": [m.group(0) for m in ms[:3]]})
    issues = []
    if violations:
        total = sum(v["count"] for v in violations)
        issues.append(f"真实世界时间出现 {total} 次（必须 = 0，用异常历或去年份）")
    return {"dimension": "真实世界时间禁令", "violation_count": sum(v["count"] for v in violations), "violations": violations, "healthy": not issues, "issues": issues}


def scan_chapter(text: str, anchor_chars: list = None) -> dict:
    """对单章跑全 N 维"""
    dims = {
        "D1_word_count": scan_d1_word_count(text),
        "D2_dash": scan_d2_dash(text),
        "D3_pronoun": scan_d3_pronoun(text, anchor_chars),
        "D4_paragraph": scan_d4_paragraph(text),
        "D5_para_start": scan_d5_para_start_monotone(text),
        "D6_dunhao_triple": scan_d6_dunhao_triple(text),
        "D7_onomatopoeia": scan_d7_onomatopoeia(text),
        "D8_dialogue_tag": scan_d8_dialogue_tag(text),
        "D9_real_world_time": scan_d9_real_world_time(text),
        "D10_named_dialogue_tag": scan_d10_named_dialogue_tag(text),
    }
    healthy = sum(1 for d in dims.values() if d.get("healthy", True))
    unhealthy = [k for k, d in dims.items() if not d.get("healthy", True)]
    return {"per_dimension": dims, "healthy_dims": healthy, "unhealthy_dims": unhealthy, "total_dims": len(dims)}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    proj = Path(sys.argv[1]).resolve()
    target_ch = None
    all_mode = '--all' in sys.argv
    if '--ch' in sys.argv:
        target_ch = int(sys.argv[sys.argv.index('--ch') + 1])
    chap_dir = proj / "章节"
    out_dir = proj / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 读 anchor_chars
    anchor_chars = ['陈默']
    try:
        prefs = json.loads((proj / "_数据库" / "用户偏好.json").read_text(encoding="utf-8"))
        ns = prefs.get("narrative_style", {})
        if ns.get("pov_anchor_character"):
            anchor_chars = [ns["pov_anchor_character"]]
    except Exception:
        pass
    results = {}
    for d in sorted(chap_dir.iterdir()):
        if not d.is_dir() or 'cluster' in d.name or 'pre_opening' in d.name:
            continue
        digits = ''.join(c for c in d.name if c.isdigit())
        if not digits:
            continue
        ch_num = int(digits)
        if target_ch is not None and ch_num != target_ch:
            continue
        for f in d.glob("*.txt"):
            if 'changes' in f.name or 'pre_opening' in f.name:
                continue
            text = f.read_text(encoding='utf-8')
            results[f'ch{ch_num:03d}'] = scan_chapter(text, anchor_chars)
            break
    # 跨章趋势汇总
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    summary_dims = {}
    for dkey in ["D2_dash", "D3_pronoun", "D4_paragraph", "D5_para_start", "D6_dunhao_triple", "D7_onomatopoeia", "D8_dialogue_tag", "D9_real_world_time", "D10_named_dialogue_tag"]:
        unhealthy_chs = [k for k, r in results.items() if dkey in r["per_dimension"] and not r["per_dimension"][dkey].get("healthy", True)]
        summary_dims[dkey] = {
            "unhealthy_chapters": unhealthy_chs,
            "unhealthy_count": len(unhealthy_chs),
            "trend_worst_5": sorted([(k, r["per_dimension"][dkey]) for k, r in results.items() if dkey in r["per_dimension"]],
                                     key=lambda kv: -sum(len(i) for i in kv[1].get("issues") or []))[:5]
        }
    rpt = {
        "scan_type": "structure_layer_anti_slop_v23_5",
        "scan_ts": ts,
        "ref_doc": "core/claude-home/ANTI_SLOP_STRUCTURE_LAYER.md",
        "anchor_chars": anchor_chars,
        "chapters_scanned": len(results),
        "summary_per_dimension": summary_dims,
        "per_chapter": results,
    }
    out = out_dir / f"structure_layer_{ts}.json"
    out.write_text(json.dumps(rpt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[structure_layer_anti_slop_scan v23.5] {len(results)} 章 / {len(summary_dims)} 维")
    for dkey, info in summary_dims.items():
        cnt = info["unhealthy_count"]
        flag = "❌" if cnt >= 5 else ("⚠️" if cnt >= 1 else "✅")
        print(f"  {flag} {dkey}: {cnt} 章不健康")
    print(f"  报告: {out}")


if __name__ == '__main__':
    main()
