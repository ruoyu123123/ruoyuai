#!/usr/bin/env python3
"""distill_character_verify.py — 角色蒸馏回灌验证闸（🔴 2026-06-27 C13）

distill-character.plan.json step 5。两类判定，泾渭分明：

  ① PROCESS-INTEGRITY（唯一 hard 项）—— 确定性契约校验（北极星⑤：契约破损非创作判断）：
     · 同栈证据：voice_pack._gen_provenance.generated_by_model/profile 存在
       （证明 style_samples/anti_samples 来自 gen-model 而非 Claude 凭印象编 / polish hack）。
     · provenance：每条 style_samples/anti_samples 带 ≥2 个不同章号
       （命令文档 P2-7 反 over-generalize 强制）。
     · banned_phrases：首次明确即可入列（不要求 ≥2 源）—— 只查结构是 list（不查内容/来源）。
     破损 + --strict → exit 2，拦在出货前；非 strict → WARN 放行。

  ② voice-fidelity（永 advisory）—— 轻量 voice 自洽 / 新对白比对（复用 distill_finalize_verify
     的「简化 estimate」哲学：词法启发轻量比对，**不跑全 SFS multi-ref**·重量级 per-character SFS
     循环会劝退 distill-character 的中途校准用途）。fidelity 分数**绝不**影响 exit code
     （北极星⑤：强制完美 voice 复制会干涉模型创作 + 冻结本应随角色成长 fluid 演化的 voice）。

用法：
  python core/scripts/distill_character_verify.py \\
    --project workspace/novels/<书名> \\
    --character 李若渝 \\
    --output workspace/novels/<书名>/对比报告/voice_verify_李若渝.json \\
    [--strict] [--skip-genmodel]

exit：0 = ok（含 fidelity 不达标·advisory 放行）/ 2 = PROCESS-INTEGRITY 契约破损（仅 --strict）。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path


# ============ PROCESS-INTEGRITY（唯一 hard 项·纯函数·可测）============

def sample_provenance_ok(sample) -> bool:
    """单条 style/anti_sample 是否带 ≥2 个不同章号 provenance。

    合法形态：{"text": "...", "from_chapters": [3, 5]}（或 source_chapters / chapters 别名）。
    纯字符串 / 缺 from_chapters / <2 不同章 → False（Claude 凭印象编、单次特色用法当弱信号）。
    """
    if not isinstance(sample, dict):
        return False
    chs = (sample.get("from_chapters") or sample.get("source_chapters")
           or sample.get("chapters"))
    if not isinstance(chs, (list, tuple)):
        return False
    distinct = {str(c).strip() for c in chs if c is not None and str(c).strip()}
    return len(distinct) >= 2


def check_process_integrity(voice_pack) -> tuple[bool, list[dict]]:
    """PROCESS-INTEGRITY 契约校验（确定性·hard）。返回 (ok, violations)。

    语义：只在 voice_pack 真有样本时校验同栈 + provenance（无样本 = 没产/没并·非破损·vacuous ok）。
    banned_phrases 只查结构（list），不查 provenance（首次明确即入·底线非正向 pattern）。
    """
    violations: list[dict] = []
    if not isinstance(voice_pack, dict):
        return False, [{"code": "VOICE_PACK_MISSING",
                        "msg": "未找到该角色的 voice_pack（人物卡.json 结构异常或角色不存在）"}]

    style = voice_pack.get("style_samples") or []
    anti = voice_pack.get("anti_samples") or []
    samples = ([("style_samples", s) for s in style if not isinstance(s, str) or s]
               + [("anti_samples", s) for s in anti if not isinstance(s, str) or s])

    if samples:
        # 同栈证据（gen-model 出品·非 Claude 编 / polish hack）
        prov = voice_pack.get("_gen_provenance") or {}
        model = (prov.get("generated_by_model") or prov.get("generated_by_profile")
                 if isinstance(prov, dict) else None)
        if not model:
            violations.append({
                "code": "SAME_STACK_PROVENANCE_MISSING",
                "msg": "voice_pack._gen_provenance 缺 generated_by_model/profile —— "
                       "style_samples/anti_samples 须来自 gen-model（gen_creative --mode voice_sample·同栈），"
                       "禁 Claude 凭印象编 / gen_fixer polish hack"})
        # 逐条 ≥2 章 provenance
        for field, s in samples:
            if not sample_provenance_ok(s):
                snippet = (s.get("text") if isinstance(s, dict) else str(s)) or ""
                violations.append({
                    "code": "INSUFFICIENT_PROVENANCE", "field": field,
                    "sample": str(snippet)[:40],
                    "msg": "样本缺 ≥2 个不同章号 from_chapters（反 over-generalize·单次出现当弱信号不入 samples）"})

    banned = voice_pack.get("banned_phrases")
    if banned is not None and not isinstance(banned, list):
        violations.append({
            "code": "BANNED_PHRASES_MALFORMED",
            "msg": f"banned_phrases 应为 list（当前 {type(banned).__name__}）"})

    return (len(violations) == 0), violations


# ============ voice-fidelity（永 advisory·轻量·纯函数·可测）============

_SENT_SPLIT_RE = re.compile(r"[。！？!?…]+")
# 通用 AI 套话泄漏哨兵（与项目反 AI 腔守卫对齐·命中只 advisory·不据此 hard-lock）
_AI_SLOP_WORDS = ["与此同时", "值得一提的是", "不仅如此", "顿时", "淡淡", "微微挑眉",
                  "嘴角勾起", "深吸一口气", "缓缓地说", "心中一凛"]


def _avg_sentence_len(texts: list[str]) -> float:
    sents = []
    for t in texts:
        for s in _SENT_SPLIT_RE.split(t or ""):
            s = s.strip()
            if s:
                sents.append(s)
    if not sents:
        return 0.0
    return round(sum(len(s) for s in sents) / len(sents), 2)


def voice_fidelity_estimate(sample_texts: list[str], history_texts: list[str],
                            voice_pack: dict) -> dict:
    """轻量 voice 保真度估算（advisory · 永不影响 exit code）。

    复用 distill_finalize_verify 简化 estimate 哲学（词法启发·非全 SFS multi-ref）：
      · 句长偏差：样本平均句长 vs 历史平均句长（|Δ| 越小越像）。
      · 口头禅命中：voice_pack.catchphrases 在样本里的命中率。
      · AI 套话泄漏：通用 AI 腔哨兵词在样本里的命中数（越少越好）。

    全 advisory：分数只写 report 供人审，绝不据此拦截（voice 随角色成长 fluid·校准忌重量级循环）。
    """
    s_len = _avg_sentence_len(sample_texts)
    h_len = _avg_sentence_len(history_texts)
    len_delta = round(abs(s_len - h_len), 2) if (s_len and h_len) else None

    catchphrases = []
    cp = voice_pack.get("catchphrases") if isinstance(voice_pack, dict) else None
    if isinstance(cp, list):
        catchphrases = [str(c) for c in cp if c]
    joined = "\n".join(sample_texts)
    cp_hits = sum(1 for c in catchphrases if c and c in joined)
    cp_rate = round(cp_hits / len(catchphrases), 2) if catchphrases else None

    slop_hits = [w for w in _AI_SLOP_WORDS if w in joined]

    return {
        "gate_level": "advisory",
        "verdict": "advisory",
        "sample_avg_sentence_len": s_len,
        "history_avg_sentence_len": h_len,
        "sentence_len_delta": len_delta,
        "catchphrase_hit_rate": cp_rate,
        "ai_slop_leaks": slop_hits,
        "note": "voice-fidelity 永 advisory（轻量词法估算·非全 SFS multi-ref）·"
                "分数不拦截出货（voice 随角色成长 fluid 演化·校准忌重量级 SFS 循环·北极星⑤）",
    }


# ============ 人物卡 / voice_pack 解析（容错多 schema）============

def find_character(doc: dict, character: str) -> dict | None:
    """从 人物卡.json 找目标角色 dict。容错 list / dict-keyed / 顶层 keyed 三种 schema。"""
    if not isinstance(doc, dict):
        return None
    chars = doc.get("characters")
    if isinstance(chars, list):
        for c in chars:
            if isinstance(c, dict) and (c.get("id") == character
                                        or c.get("name") == character):
                return c
    if isinstance(chars, dict) and isinstance(chars.get(character), dict):
        return chars[character]
    if isinstance(doc.get(character), dict):
        return doc[character]
    return None


def extract_voice_pack(character_obj: dict | None) -> dict | None:
    """从角色 dict 取 voice_pack（容错：嵌套 voice_pack 键 或 角色 dict 自身即含 samples）。"""
    if not isinstance(character_obj, dict):
        return None
    vp = character_obj.get("voice_pack")
    if isinstance(vp, dict):
        return vp
    if "style_samples" in character_obj or "anti_samples" in character_obj:
        return character_obj
    return None


def _sample_texts(voice_pack: dict) -> list[str]:
    out = []
    for field in ("style_samples", "anti_samples"):
        for s in (voice_pack.get(field) or []):
            if isinstance(s, dict) and s.get("text"):
                out.append(str(s["text"]))
            elif isinstance(s, str) and s.strip():
                out.append(s.strip())
    return out


def _history_texts(material_path: Path | None, limit: int = 40) -> list[str]:
    if material_path is None or not material_path.exists():
        return []
    try:
        mat = json.loads(material_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    quotes = (mat.get("dialogue_quotes") or mat.get("quotes") or [])
    out = []
    for q in quotes[:limit]:
        if isinstance(q, dict) and q.get("text"):
            out.append(str(q["text"]))
        elif isinstance(q, str) and q.strip():
            out.append(q.strip())
    return out


# ============ 主流程 ============

def build_report(voice_pack: dict | None, material_path: Path | None,
                 character: str) -> tuple[dict, bool]:
    """组装 verify 报告 + 返回 (report, integrity_ok)。纯函数（不调 gen-model·可测）。"""
    integrity_ok, violations = check_process_integrity(voice_pack or {})
    sample_texts = _sample_texts(voice_pack) if isinstance(voice_pack, dict) else []
    history_texts = _history_texts(material_path)
    fidelity = voice_fidelity_estimate(sample_texts, history_texts, voice_pack or {})
    report = {
        "verify_runner": "distill_character_verify.py (C13)",
        "character": character,
        "process_integrity": {
            "gate_level": "hard_gate",
            "ok": integrity_ok,
            "violations": violations,
        },
        "voice_fidelity": fidelity,
        "_note": "唯一 hard 项 = process_integrity（同栈 gen-model + ≥2 章 provenance）·"
                 "voice_fidelity 永 advisory（不据此拦截）",
    }
    return report, integrity_ok


def main():
    # stdout/stderr UTF-8（Windows 默认 GBK·report/日志含中文/emoji 直打 GBK 终端会
    # UnicodeEncodeError·与 gen_creative/distill_replicate 同款防御）
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    parser = argparse.ArgumentParser(description="角色蒸馏回灌验证闸（C13）")
    parser.add_argument("--project", required=True, type=Path,
                        help="小说项目根（含 _数据库/人物卡.json）")
    parser.add_argument("--character", required=True, help="角色 id / 名")
    parser.add_argument("--output", required=True, type=Path, help="verify 报告路径")
    parser.add_argument("--strict", action="store_true",
                        help="PROCESS-INTEGRITY 破损时 exit 2（plan step 5 闸门）")
    parser.add_argument("--skip-genmodel", action="store_true",
                        help="跳过 gen-model 新对白比对（fidelity 仅看现有样本自洽·测试/无 API 用）")
    args = parser.parse_args()

    project = args.project.resolve()
    cards_path = project / "_数据库" / "人物卡.json"
    if not cards_path.exists():
        print(f"[ERROR] 人物卡.json 不存在: {cards_path}", file=sys.stderr)
        sys.exit(2)
    try:
        doc = json.loads(cards_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[ERROR] 人物卡.json 解析失败: {e}", file=sys.stderr)
        sys.exit(2)

    character_obj = find_character(doc, args.character)
    voice_pack = extract_voice_pack(character_obj)
    material_path = project / "_数据库" / ".distill_character" / f"{args.character}_material.json"

    report, integrity_ok = build_report(voice_pack, material_path, args.character)

    # 报告永远落盘（即便 PROCESS-INTEGRITY 破损·留审计痕迹·step5 expected_outputs 也满足）
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    fid = report["voice_fidelity"]
    print(f"[char-verify] {args.character} · PROCESS-INTEGRITY ok={integrity_ok} · "
          f"句长 样本{fid.get('sample_avg_sentence_len')}/历史{fid.get('history_avg_sentence_len')} · "
          f"AI 套话泄漏 {len(fid.get('ai_slop_leaks') or [])} 处（advisory）", file=sys.stderr)
    print(f"[char-verify] 报告 → {args.output}", file=sys.stderr)

    if not integrity_ok:
        for v in report["process_integrity"]["violations"]:
            print(f"  [PROCESS-INTEGRITY · {v.get('code')}] {v.get('msg')}", file=sys.stderr)
        if args.strict:
            print("[FAIL · strict] PROCESS-INTEGRITY 契约/provenance 破损 · 出货前拦截"
                  "（修 voice_pack 同栈/provenance 后重跑）", file=sys.stderr)
            sys.exit(2)
        print("[WARN] PROCESS-INTEGRITY 破损 · 非 strict 放行 · 强烈建议修复", file=sys.stderr)

    # voice-fidelity 永 advisory：无论分数如何都 exit 0（绝不据 fidelity delta 拦截）
    print("[OK] 角色蒸馏回灌验证通过（PROCESS-INTEGRITY 过 · fidelity 仅 advisory）", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
