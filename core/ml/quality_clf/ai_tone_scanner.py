# 🔴 2026-06-29 NN训练:质量AI腔判别
"""ai_tone_scanner.py — 把训练好的「human vs AI腔」分类器包成顾问 scanner（advisory）

【定位·北极星⑤】
  本 scanner 输出的是「AI 腔疑似度」**advisory 信号**，绝不 hard_gate：
    · 作者风格档是第一权威 —— 某作者本就工整/句长，NN 打高分也只是提示。
    · code = `AI_TONE_NN`，**永不进** audit_hub.HARD_GATE_CODES（与 SEMANTIC_* 同性质）。
    · 写作 agent 有理由可豁免（理由 < 300 字·具体到本 cluster 场景）。

【输出契约】对齐 semantic_slop_scanner → audit_hub._parse_scanner_json：
  顶层 key `ai_tone_nn` 是 check block，带 warning(str|None)/severity/gate_level/fix_hint。
  另带 score(0-1 P(ai) chunk 均值) + per_chunk 明细供定位。

【⚠️ 未训练前不可用】model.pt 不存在 → 直接 FATAL（北极星：不降级·不静默旁路）。
  训练后把本文件软链/复制进 core/scripts/ 并按 INTEGRATION.md 注册进 audit_hub + registry。

【用法】
  python ai_tone_scanner.py <项目路径> <章节号> --model-dir <runs/xxx>
  python ai_tone_scanner.py --file draft.txt --model-dir <runs/xxx>   # 独立模式
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from features import FEATURE_ORDER  # noqa: E402

CJK_RE = re.compile(r"[一-鿿]")
PARA_SPLIT = re.compile(r"\n\s*\n")
DEFAULT_THRESHOLD = 0.65  # P(ai) 均值 ≥ 阈值 → 出 advisory warning


def cjk_len(s):
    return sum(1 for ch in s if CJK_RE.match(ch))


def chunk_text(text, target=480, min_chars=200):
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks, buf, blen = [], [], 0
    for p in paras:
        buf.append(p)
        blen += cjk_len(p)
        if blen >= target:
            chunks.append("\n\n".join(buf))
            buf, blen = [], 0
    if buf and (blen >= min_chars or not chunks):
        chunks.append("\n\n".join(buf))
    return chunks or [text]


def load_chapter_body(project_root: Path, ch: int) -> str | None:
    """复用系统 chapter_io 读正文（与 semantic_slop 同口径）。"""
    sys.path.insert(0, str(Path(project_root).resolve()))
    try:
        sys.path.insert(0, str(_HERE.parents[1] / "scripts"))  # core/scripts
        import chapter_io as cio  # type: ignore
        body = cio.read_body(project_root, ch)
    except Exception:
        return None
    return "\n".join(l for l in body.split("\n")
                     if not re.match(r"^第\d+章", l.strip())).lstrip()


def predict_scores(texts, model_dir: Path):
    """加载模型 → 返回每段 P(ai)。torch/transformers 延迟导入。"""
    import torch
    from transformers import AutoTokenizer
    from features import extract_features
    sys.path.insert(0, str(_HERE))
    from train import build_model  # 复用模型定义

    cfg = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    sc = json.loads((model_dir / "feat_scaler.json").read_text(encoding="utf-8"))["scaler"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(model_dir)
    model = build_model(cfg["base"], cfg["use_feats"], cfg.get("quality_head", False),
                        cfg.get("lora", False)).to(device)
    model.load_state_dict(torch.load(model_dir / "model.pt", map_location=device))
    model.eval()

    scores = []
    with torch.no_grad():
        for t in texts:
            enc = tok(t, truncation=True, max_length=cfg["max_len"],
                      return_tensors="pt").to(device)
            f = extract_features(t)
            fv = torch.tensor([[(float(f.get(k, 0.0)) - sc[k]["mean"]) / sc[k]["std"]
                                for k in FEATURE_ORDER]], dtype=torch.float).to(device)
            logits, _ = model(enc["input_ids"], enc["attention_mask"], fv)
            scores.append(float(torch.softmax(logits, -1)[0, 1]))
    return scores


def build_report(text, model_dir, threshold):
    chunks = chunk_text(text)
    scores = predict_scores(chunks, model_dir)
    mean_score = sum(scores) / len(scores) if scores else 0.0
    hot = [{"chunk_idx": i, "score": round(s, 3),
            "preview": chunks[i][:50].replace("\n", " ")}
           for i, s in enumerate(scores) if s >= threshold]
    warn = (f"⚠️ AI 腔疑似度均值 {mean_score:.2f}（{len(hot)}/{len(chunks)} 段超阈 "
            f"{threshold}）—— NN 判别提示，非判决" if mean_score >= threshold else None)
    return {
        "schema_version": "1.0",
        "scanner": "ai_tone_scanner",
        "ai_tone_nn": {
            "score": round(mean_score, 4),
            "chunks": len(chunks),
            "hot_chunks": hot[:8],
            "threshold": threshold,
            "severity": "warning",
            "gate_level": "advisory",   # 北极星⑤·永不 hard_gate
            "fix_hint": "NN 判别本段偏 AI 腔：对照作者风格档核句长爆发度/套话密度/"
                        "情绪是否落地成动作。作者档允许的写法可豁免。",
            "warning": warn,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?", help="项目路径（章节模式）")
    ap.add_argument("chapter", nargs="?", type=int, help="章节号")
    ap.add_argument("--file", help="独立模式：直接扫一个 txt")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = ap.parse_args()

    model_dir = Path(args.model_dir)
    if not (model_dir / "model.pt").exists():
        print(f"[FATAL] 模型不存在：{model_dir}/model.pt —— 先训练（train.py）。"
              f"本 scanner 不降级、不空跑。", file=sys.stderr)
        sys.exit(2)

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.project and args.chapter is not None:
        text = load_chapter_body(Path(args.project), args.chapter)
        if text is None:
            print(f"[FATAL] 找不到第{args.chapter}章正文", file=sys.stderr)
            sys.exit(2)
    else:
        print("[FATAL] 需 <项目 章节号> 或 --file", file=sys.stderr)
        sys.exit(2)

    report = build_report(text, model_dir, args.threshold)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["ai_tone_nn"]["warning"]:
        print(report["ai_tone_nn"]["warning"], file=sys.stderr)
        sys.exit(1)  # advisory 命中（与 semantic_slop 同：1=有 advisory）
    sys.exit(0)


if __name__ == "__main__":
    main()
