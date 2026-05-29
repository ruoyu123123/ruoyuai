"""v23.3 dash_density_scan - 检测「——」破折号过用（结构层 anti-slop）

教训：cluster_003+ writer 把 「——」 当 voice 安全捷径，ch14 密度 122/千字 = 每 8 字一个 ——。
健康标准（ch1-3 诡秘风克制期）：≤ 12/千字 / 单段 ≤ 1 / 极端段（≥3）= 0。

用法：
    python dash_density_scan.py <项目路径> [--ch <N>]
"""
import sys, os, json
from pathlib import Path


def scan_chapter(text: str) -> dict:
    cjk = sum(1 for c in text if '一' <= c <= '鿿')
    dash = text.count('——')
    density = round(dash / max(cjk, 1) * 1000, 2)
    paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    multi = [p for p in paras if p.count('——') >= 2]
    extreme = [p for p in paras if p.count('——') >= 3]
    # 极端段最长样本
    extreme_samples = [(p.count('——'), p[:200]) for p in extreme[:3]]
    issues = []
    if density > 12:
        issues.append({
            "code": "STYLE_破折号过量",
            "gate_level": "advisory",
            "level": "warning" if density < 25 else "error",
            "msg": f"——密度 {density}/千字 (健康线 12，{round(density/12, 1)}x 超标)"
        })
    if extreme:
        issues.append({
            "code": "STYLE_破折号极端段",
            "gate_level": "advisory",
            "level": "warning",
            "msg": f"{len(extreme)} 段单段 ≥3 个 —— (AI 套路硬指标 = 0)"
        })
    return {
        "cjk": cjk,
        "dash_count": dash,
        "density_per_1k": density,
        "multi_dash_paragraphs": len(multi),
        "extreme_paragraphs": len(extreme),
        "extreme_samples": extreme_samples,
        "issues": issues,
        "healthy": density <= 12 and len(extreme) == 0,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    proj = Path(sys.argv[1]).resolve()
    target_ch = None
    if '--ch' in sys.argv:
        target_ch = int(sys.argv[sys.argv.index('--ch') + 1])
    chap_dir = proj / "章节"
    out_dir = proj / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for d in sorted(chap_dir.iterdir()):
        if not d.is_dir() or 'cluster' in d.name or 'pre_opening' in d.name:
            continue
        m = [int(s) for s in d.name if s.isdigit()]
        if not m:
            continue
        ch_num = int(d.name.replace('第', '').replace('章', '').lstrip('0') or '0')
        if target_ch is not None and ch_num != target_ch:
            continue
        for f in d.glob("*.txt"):
            if 'changes' in f.name or 'pre_opening' in f.name:
                continue
            text = f.read_text(encoding='utf-8')
            results[f'ch{ch_num:03d}'] = scan_chapter(text)
            break
    # 汇总报告
    from datetime import datetime
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    rpt = {
        "scan_type": "dash_density",
        "scan_ts": ts,
        "healthy_threshold_per_1k": 12,
        "extreme_threshold_per_paragraph": 3,
        "chapters_scanned": len(results),
        "chapters_healthy": sum(1 for r in results.values() if r['healthy']),
        "chapters_warning": sum(1 for r in results.values() if not r['healthy'] and r['density_per_1k'] < 25),
        "chapters_error": sum(1 for r in results.values() if r['density_per_1k'] >= 25),
        "per_chapter": results,
    }
    out = out_dir / f"dash_density_{ts}.json"
    out.write_text(json.dumps(rpt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[dash_density_scan] {len(results)} 章 / 健康 {rpt['chapters_healthy']} / warning {rpt['chapters_warning']} / error {rpt['chapters_error']}")
    print(f"  报告: {out}")
    # 控制台 top 5 worst
    worst = sorted(results.items(), key=lambda kv: -kv[1]['density_per_1k'])[:5]
    print(f"  worst 5:")
    for k, r in worst:
        print(f"    {k}: density={r['density_per_1k']}/千字 / 极端段 {r['extreme_paragraphs']} / dash {r['dash_count']}")
    return rpt['chapters_error']


if __name__ == '__main__':
    # 2026-05-29 修：旧写法 `main() if main() else 0` 把 main() 跑两遍 → 现在只跑一次
    rc = main()
    sys.exit(rc if rc else 0)
