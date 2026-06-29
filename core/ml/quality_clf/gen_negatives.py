# 🔴 2026-06-29 NN训练:质量AI腔判别
"""gen_negatives.py — （可选·推荐）用 gen-model 量产「同题材 AI 负样本」

【为什么需要它】（调研接地·见 README §数据局限 / arXiv:2604.11796 C-ReD / 2601.04633 MAGA）
  磁盘上的 41 个 gemini 复刻样本是**珍贵的对抗硬负样本/held-out 评测种子**，但
  数量太少 + 单一生成器(gemini) + 都在模仿这 10 位作者 → 直接拿来训练，分类器学到的
  是「gemini/本管线指纹」而非泛化「AI 腔」，换 DeepSeek/豆包/Qwen 就崩。
  正解：**从人样本开头出发、跨多生成器、同题材配对生成** AI 续写/改写当训练负样本。

【topic-controlled 配对（抗捷径核心）】
  · continue 模式：拿真人章节开头 → 让模型续写（题材/人物一致·只差人感↔AI腔）。
  · rewrite 模式：拿真人段落 → 让模型用网文笔法改写（情节不变·纯比表达）。
  这样人/AI 两侧题材对齐，分类器无法靠「题材/词表」作弊（arXiv:2503.00258）。

【多生成器】
  --profiles 传多个 .env GEN__ profile 名（逗号分隔）→ 各生成器各产一批，
  文件名 `<author>__<profile>__NN.txt`，data_prep.py --ai-extra-dir 自动 ingest+按组 split。
  ⚠️ 当前 .env 多半只配了 gemini 一个 profile —— 要真泛化，请在 .env 加 DeepSeek/Qwen/
  豆包等 OpenAI 兼容 profile 后再多生成器跑（见 README）。

【运行环境】
  本脚本需 `openai` 包。ml venv 是裸的 → **用带 openai 的系统 Python 跑**：
    py -3 core/ml/quality_clf/gen_negatives.py --max-samples 60 --mode continue
  （别花太多·--max-samples 默认 60·硬上限 100）

【北极星纪律】只造负样本喂检测器训练·不碰任何小说项目产出·不写 workspace/novels。
"""
from __future__ import annotations
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "core" / "scripts"))

CJK_RE = re.compile(r"[一-鿿]")
TITLE_RE = re.compile(r"^\s*第[0-9一二三四五六七八九十百千零〇]+[章卷回节]")
INDENT_RE = re.compile(r"^[　\s]+")
HARD_CAP = 100


def cjk_len(s: str) -> int:
    return sum(1 for ch in s if CJK_RE.match(ch))


def clean(text: str) -> str:
    lines = [INDENT_RE.sub("", l) for l in text.split("\n") if not TITLE_RE.match(l)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def take_prefix(text: str, n_cjk: int) -> str:
    """取前 n_cjk 个中文字所在的完整段落前缀。"""
    out, c = [], 0
    for para in re.split(r"\n\s*\n", text):
        out.append(para)
        c += cjk_len(para)
        if c >= n_cjk:
            break
    return "\n\n".join(out)


def build_messages(mode: str, seed: str) -> list[dict]:
    if mode == "rewrite":
        sys_p = ("你是中文网络小说写手。把用户给的文字用网文笔法改写，情节、人物、"
                 "设定完全不变，只改表达。直接输出改写后的正文，不要解释。")
        user = f"改写下面这段（约 600 字）：\n\n{seed}"
    else:  # continue
        sys_p = ("你是中文网络小说写手。顺着给定开头自然续写故事，保持网文风格与节奏。"
                 "直接输出续写正文（约 700 字），不要解释、不要小标题。")
        user = f"开头：\n\n{seed}\n\n（请续写）"
    return [{"role": "system", "content": sys_p},
            {"role": "user", "content": user}]


def generate_one(client, profile, messages, max_tokens, extra_body):
    """非流式 chat.completions（OpenAI 兼容）。返回正文或 None。"""
    try:
        kw = dict(model=profile.model, messages=messages,
                  temperature=float(profile.temperature or 1.0),
                  max_tokens=max_tokens)
        if extra_body:
            kw["extra_body"] = extra_body
        resp = client.chat.completions.create(**kw)
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa
        print(f"  [gen 失败] {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--styles-dir", default=str(REPO / "workspace" / "styles"))
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "data" / "ai_generated"))
    ap.add_argument("--profiles", default="", help="逗号分隔 .env GEN__ profile 名；空=active")
    ap.add_argument("--mode", choices=["continue", "rewrite"], default="continue")
    ap.add_argument("--authors", nargs="*", default=[], help="只用这些作者(空=全部)")
    ap.add_argument("--per-author", type=int, default=8)
    ap.add_argument("--max-samples", type=int, default=60, help=f"总上限(硬顶 {HARD_CAP})")
    ap.add_argument("--seed-cjk", type=int, default=220, help="喂给模型的开头字数")
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    n_target = min(args.max_samples, HARD_CAP)
    rng = random.Random(args.seed)

    try:
        from gen_model_loader import GenModelLoader, reasoning_extra_body
        from openai import OpenAI
    except Exception as e:
        print(f"[FATAL] 需要 openai + core/scripts 可导入。用带 openai 的系统 Python 跑"
              f"（py -3 ...）。原因：{e}", file=sys.stderr)
        sys.exit(2)

    loader = GenModelLoader()
    prof_names = [p.strip() for p in args.profiles.split(",") if p.strip()]
    if prof_names:
        profiles = [loader.get_profile(n) for n in prof_names]
        if any(p is None for p in profiles):
            print(f"[FATAL] 有 profile 不存在。可用：{[x.name for x in loader.list_profiles()]}",
                  file=sys.stderr)
            sys.exit(2)
    else:
        profiles = [loader.get_active_profile()]
    print(f"[gen] 使用 profile：{[p.name for p in profiles]}", file=sys.stderr)
    if len(profiles) == 1:
        print("  ⚠️ 单生成器 → 负样本仍是单一指纹，泛化受限。建议在 .env 配多生成器后多 profile 跑。",
              file=sys.stderr)

    styles = Path(args.styles_dir)
    authors = args.authors or [d.name for d in sorted(styles.iterdir())
                               if (d / "原文").is_dir()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    produced = 0

    for prof in profiles:
        client = OpenAI(api_key=prof.api_key, base_url=prof.base_url)
        try:
            extra_body = reasoning_extra_body(prof)
        except Exception:
            extra_body = {}
        for author in authors:
            if produced >= n_target:
                break
            chapters = sorted((styles / author / "原文").glob("*.txt"))
            rng.shuffle(chapters)
            for ch in chapters[:args.per_author]:
                if produced >= n_target:
                    break
                raw = clean(ch.read_text(encoding="utf-8", errors="ignore"))
                if cjk_len(raw) < args.seed_cjk + 100:
                    continue
                seed = take_prefix(raw, args.seed_cjk) if args.mode == "continue" \
                    else take_prefix(raw, 500)
                text = generate_one(client, prof, build_messages(args.mode, seed),
                                    args.max_tokens, extra_body)
                if not text or cjk_len(text) < 200:
                    continue
                idx = produced
                fname = f"{author}__{prof.name}__{idx:03d}.txt"
                (out_dir / fname).write_text(text, encoding="utf-8")
                manifest.append({"file": fname, "author": author,
                                 "generator": prof.name, "mode": args.mode,
                                 "seed_source": str(ch.relative_to(REPO)).replace("\\", "/"),
                                 "cjk_len": cjk_len(text)})
                produced += 1
                print(f"  [{produced}/{n_target}] {fname} ({cjk_len(text)} CJK)",
                      file=sys.stderr)
                time.sleep(0.3)  # 轻微限速

    (out_dir / "manifest.json").write_text(
        json.dumps({"_marker": "🔴 2026-06-29 NN训练:质量AI腔判别",
                    "count": produced, "mode": args.mode,
                    "generators": [p.name for p in profiles],
                    "samples": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n=== 生成 {produced} 条负样本 → {out_dir} ===", file=sys.stderr)
    print(f"喂训练：python data_prep.py --ai-extra-dir {out_dir} --replicas-heldout-test",
          file=sys.stderr)


if __name__ == "__main__":
    main()
