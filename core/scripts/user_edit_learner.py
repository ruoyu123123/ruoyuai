"""user_edit_learner.py — 从用户手改正文的 git diff 推断偏好

G2 调研发现: git 每节点自动 commit, diff 物理存在,
但无任何脚本读 commit diff 反推偏好。

机制 (确定性统计 · 零 LLM):
1. 读最近 N 个 commit 的 diff (章节文件)
2. 统计: 用户反复删的词/句式 → user_banned_words
         用户反复加的词/句式 → user_preferred_phrases
         用户改段落长度的趋势 → 段落偏好
3. 写回 写作经验.json.preferences (带来源+confidence)

纪律: advisory · 不干涉模型判断 · 用户可 /db 否决

接入点: cluster-save-state 前的预检,或手动 /db 触发
exit 0: 不阻断

用法:
  python core/scripts/user_edit_learner.py <project_root> [--last-n 10]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(p: Path, d: dict) -> None:
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _git_diff_stat(project_root: Path, last_n: int = 10) -> dict:
    """读最近 N 个 commit 的 diff, 提取删/加行。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "log",
             f"-{last_n}", "--diff-filter=M", "-p", "--",
             "章节/*/第*章.txt", "章节/*/cluster_*_draft.txt"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return {"deleted_lines": [], "added_lines": []}
    except Exception:
        return {"deleted_lines": [], "added_lines": []}

    deleted = []
    added = []
    for line in result.stdout.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            text = line[1:].strip()
            if len(text) > 3:
                deleted.append(text)
        elif line.startswith("+") and not line.startswith("+++"):
            text = line[1:].strip()
            if len(text) > 3:
                added.append(text)
    return {"deleted_lines": deleted, "added_lines": added}


def _extract_patterns(lines: list[str], min_freq: int = 2) -> dict:
    """从行列表提取高频 2-gram/3-gram 模式。"""
    bigrams: Counter = Counter()
    trigrams: Counter = Counter()

    for line in lines:
        # 按标点切片段
        segments = re.split(r"[，。！？；：、\n]", line)
        for seg in segments:
            seg = seg.strip()
            if len(seg) < 4:
                continue
            chars = list(seg)
            for i in range(len(chars) - 1):
                bigrams[chars[i] + chars[i + 1]] += 1
            for i in range(len(chars) - 2):
                trigrams[chars[i] + chars[i + 1] + chars[i + 2]] += 1

    return {
        "bigrams": {k: v for k, v in bigrams.most_common(20) if v >= min_freq},
        "trigrams": {k: v for k, v in trigrams.most_common(20) if v >= min_freq},
    }


def _extract_banned_words(deleted_lines: list[str], min_freq: int = 3) -> list[str]:
    """用户反复删的词 → banned 候选。"""
    # 关注 AI 腔调守卫禁用词被用户手动删的情况
    word_counter: Counter = Counter()
    for line in deleted_lines:
        # 简单: 2-4 字的词频统计
        for length in (2, 3, 4):
            for i in range(len(line) - length + 1):
                word = line[i : i + length]
                if not re.match(r"^[一-鿿]+$", word):
                    continue
                word_counter[word] += 1
    return [w for w, c in word_counter.most_common(30) if c >= min_freq]


def learn_from_edits(
    project_root: Path,
    last_n: int = 10,
    min_freq: int = 3,
) -> dict:
    """从 git diff 学习用户编辑偏好。

    Returns:
        {"banned_candidates": N, "preferred_candidates": N, "written": bool}
    """
    diff = _git_diff_stat(project_root, last_n=last_n)
    deleted = diff["deleted_lines"]
    added = diff["added_lines"]

    if not deleted and not added:
        return {"banned_candidates": 0, "preferred_candidates": 0, "written": False}

    banned = _extract_banned_words(deleted, min_freq=min_freq)
    deleted_patterns = _extract_patterns(deleted, min_freq=2)
    added_patterns = _extract_patterns(added, min_freq=2)

    # 写回写作经验.json
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = _load(exp_path)
    exp.setdefault("preferences", {})
    prefs = exp["preferences"]

    if banned:
        existing = set(prefs.get("user_banned_words", []))
        existing.update(banned)
        prefs["user_banned_words"] = sorted(existing)

    if deleted_patterns.get("trigrams"):
        prefs["user_deleted_patterns"] = deleted_patterns["trigrams"]
    if added_patterns.get("trigrams"):
        prefs["user_preferred_patterns"] = added_patterns["trigrams"]

    prefs["_edit_learner_last_n"] = last_n
    prefs["_edit_learner_samples"] = len(deleted) + len(added)

    _save(exp_path, exp)

    return {
        "banned_candidates": len(banned),
        "preferred_candidates": len(added_patterns.get("trigrams", {})),
        "written": True,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="从 git diff 学用户编辑偏好 (确定性·零 LLM)")
    ap.add_argument("project_root")
    ap.add_argument("--last-n", type=int, default=10, help="读最近 N 个 commit")
    ap.add_argument("--min-freq", type=int, default=3, help="最低出现频次才记录")
    args = ap.parse_args()

    r = learn_from_edits(Path(args.project_root), last_n=args.last_n, min_freq=args.min_freq)
    print(f"[user_edit_learner] banned={r['banned_candidates']} preferred={r['preferred_candidates']} written={r['written']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
