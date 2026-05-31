#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""av_judge.py — AV-judge 解耦特质·作者验证（读者视角配对判别 · advisory · 2026-05-31）

【是什么】Author-Verification judge：喂 1 段**作者原文** + 1 段**仿写**，要求 gen-model
  **逐维度配对判别**——把 A（作者）当锚、B（仿写）当待验，逐维点名「B 在哪个维度露馅
  （走味，读起来不像作者）」。4 个解耦特质维度（authorship verification 文献的
  stylometric 拆解 · 不混在一起打一个总分）：

    1. 词汇选择（lexical choice）—— 用词层（具体名词偏好 / 动词色彩 / 书面 vs 口语）
    2. 句法（syntax）—— 句长节奏 / 长短句交错 / 单句独行 / 流水句 vs 复句
    3. 话语连接词（discourse connectives）—— 衔接套路（转/承/因果连接词偏好 + AI 套话过渡）
    4. 语用语气（pragmatic tone）—— 叙事口吻 / 距离感 / 反讽 / 留白 vs 说破

【为什么填空档】治「SFS 数值过了但读着不像」：
  · SFS（style_evaluator.py）是**统计指纹**——句长/段长/虚词分布对齐了，但隐性的「读者一眼
    认不认得出是这个作者」抓不住（统计同分布 ≠ 读者感知同作者）。
  · 黑箱 LLM-judge（judge_consensus.py）给**整体分**——分高分低不解释「哪个维度露馅」，
    无法定位回改。
  AV-judge 站中间：**读者视角** + **解耦 4 维** + **配对判别**（A 锚 B 验，比绝对打分可靠——
  Catch Me If You GAN / Are We There Yet 等实证：LLM 对单段绝对风格打分方差大，配对相对判别稳）。

【配对判别 > 绝对打分】(authorship verification 范式)
  不问「B 像不像作者（打 1-10）」，问「给定 A 是作者真迹，B 在哪几维露馅」——
  相对锚定把「这个作者基线长什么样」交给样本 A 决定，绕开 LLM 对网文隐性风格的绝对标尺漂移。

【必 shadow 上线 · 绝不 hard_gate】(北极星⑤ + 共同纪律 2)
  实证（Catch Me If You GAN / Are We There Yet On Detecting LLM Texts）：LLM-judge 对网文
  隐性风格会**失准**——创意写作域约 1/4 难例判别翻转。故：
    · 永远 advisory，code AV_TRAIT_DRIFT **绝不进 audit_hub.HARD_GATE_CODES**。
    · env AV_JUDGE_MODE 控制（默认 off）：
        off（默认）：完全跳过——不构 prompt、不调 gen-model、零回归（共同纪律 2：改判决行为默认 off）。
        shadow：构 prompt + 调 gen-model + 出 4 维 advisory，但**只记录**（report.shadow=True ·
                顶层 verdict=None · 不上报 audit_hub）→ 先与 SFS / 人评校准，再放量。
        active：超阈维度作为 advisory 待裁决项上报（仍 advisory · 仍可豁免 · 永不 hard_gate）。
  shadow 是默认上线姿态：先攒「AV-judge 判走味」vs「SFS 判过」vs「人评」三方对照样本，
  确认 AV-judge 在本作者上不误判，再考虑 active。

【复用 · 薄】(北极星⑥ 别臃肿)
  · 纯 prompt + 薄 Python，gen-model 调用复用 gen_model_loader 同款 fallback 管线
    （与 distill_replicate.call_gen_model 一致的 active→fallback 链 · 不另起调用栈）。
  · 零新依赖（stdlib + openai/​dotenv 已是 gen_model 栈既有）· 零 GPU。
  · 独立文件——**不挂 distill_replicate**（避与 L3b CoT-first / snippet_seed 等并行件冲突）。

用法：
  shadow:  AV_JUDGE_MODE=shadow python av_judge.py \\
             --author workspace/styles/蛊真人/原文/第010章.txt \\
             --replica workspace/styles/蛊真人/复刻测试/v7_round1/cluster_001_replica.txt \\
             [--out report.json]
  默认 off（无 env）→ 直接打印 skipped 报告退出 0。

测试只验确定性层（prompt 构造 + rubric + 配对结构 + off 默认）· 不实跑 gen-model（需 API）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 复用 gen_model_loader 管线（薄复用 · 不另起调用栈 · 北极星⑥）
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from gen_model_loader import (  # noqa: E402
    GenModelLoader,
    GenModelConfigError,
    GenModelExhaustedError,
    Profile,
)

# advisory 专用 issue code · ⚠️ 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤ · 共同纪律 2）
ISSUE_CODE = "AV_TRAIT_DRIFT"

# 走味判定阈值：维度判别 verdict ∈ {命中, 走味}；命中 = 读者认得出是作者，走味 = 露馅。
# 这是**读者视角的定性判别**（不是 1-10 打分），阈值即「这一维 LLM 判定走味」。
DRIFT_VERDICT = "走味"
MATCH_VERDICT = "命中"


# ════════════════════════════════════════════════════════════════
# 4 维解耦特质 rubric（authorship verification stylometric 拆解 · 单一来源）
# ════════════════════════════════════════════════════════════════
# 每维：(维度名, 读者视角说明, 配对判别问法——给定 A 是作者真迹，B 在该维是否露馅)
AV_TRAIT_DIMS = [
    (
        "词汇选择",
        "用词层：具体名词偏好、动词色彩、书面 vs 口语、专有词汇密度",
        "B 的用词读起来像不像 A 这位作者？具体名词/动词的选择是否露出非作者的痕迹（如更书面、更泛化、更 AI）？",
    ),
    (
        "句法",
        "句子层：句长节奏、长短句交错、单句独行占比、流水句 vs 复句结构",
        "B 的句子骨架像不像 A？句长节奏 / 长短交错 / 单句成段的习惯是否和作者错位？",
    ),
    (
        "话语连接词",
        "衔接层：段落/句间过渡套路、转承因果连接词偏好、是否冒出 AI 套话过渡",
        "B 在衔接上像不像 A？是否出现「与此同时/然而/值得一提的是」等作者不用的 AI 过渡，或丢了作者的衔接习惯？",
    ),
    (
        "语用语气",
        "语用层：叙事口吻、与读者的距离感、反讽/克制/留白 vs 说破的倾向",
        "B 的口吻像不像 A？叙事距离、反讽与留白、是否把情绪说破——这些语用习惯有没有走味？",
    ),
]


def _av_judge_mode() -> str:
    """读 env AV_JUDGE_MODE：off（默认）/ shadow / active。

    off（默认）：完全跳过——不构 prompt、不调 gen-model、零回归（共同纪律 2 · 改判决行为默认 off）。
    shadow：构 prompt + 调 gen-model + 出 4 维 advisory，但只记录（不上报 audit_hub · 先校准）。
    active：超阈维度作为 advisory 待裁决项上报（仍 advisory · 仍可豁免 · 永不 hard_gate）。

    空 / 非法值 → off（保守默认 · 不静默开启未经人评校准的 LLM-judge）。
    """
    m = (os.environ.get("AV_JUDGE_MODE") or "").strip().lower()
    return m if m in ("shadow", "active", "off") else "off"


# ════════════════════════════════════════════════════════════════
# Prompt 构造（确定性可测 · 配对判别 + 4 维解耦 + 读者视角）
# ════════════════════════════════════════════════════════════════

AV_JUDGE_SYSTEM_PROMPT = """你是一位资深网文读者兼文本鉴定师，专做「作者验证」（authorship verification）。

给你两段文本：**A 是某位作者的真迹**（锚 · 已确认出自该作者），**B 是一段仿写**（待验证）。
你的任务**不是**给 B 打一个总分，而是**逐维度做配对判别**——以 A 为基准，判断 B 在每个
风格维度上**像不像同一位作者写的**，并指出 B 在哪一维「露馅 / 走味」（读者一眼觉得不是 A）。

# 判别纪律（authorship verification · 配对相对判别）

1. **以 A 为锚**：A 就是「这位作者长什么样」的唯一基准——不要用你脑中泛泛的「好文笔」标尺，
   只问「B 这一维像不像 A」。
2. **读者视角**：你是读者，凭语感判「读起来是不是同一个人」，不是查统计指标。
3. **逐维度解耦**：4 个维度**分开判**，不要混成一个印象分。某维像、某维不像，如实分列。
4. **配对判别输出**：每维给 verdict —— 「命中」（B 这一维读起来像 A）或「走味」（B 露馅、像别人/像 AI）。
5. **走味必须指证**：判「走味」要点名 B 哪一处露馅、它和 A 的差别在哪（一句话，落到具体文本）。
6. **不比内容**：A 和 B 写的人物/情节/场景不同是正常的——只比**写法风格**，不比写了什么。
"""


def _trim(text: str, limit: int) -> str:
    """裁到 limit 字（配对判别只需要足量语感样本 · 防 prompt 过长占 token）。"""
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit] + f"\n……[截断于 {limit} 字]"
    return text


def build_av_judge_prompt(author_text: str, replica_text: str,
                          sample_limit: int = 3000) -> str:
    """构造 AV-judge 配对判别 prompt：A=作者真迹（锚）/ B=仿写（待验）· 4 维解耦 · 读者视角。

    确定性纯函数（不调 gen-model）——测试只验此处的配对结构 + 4 维 rubric + 输出 JSON 契约。

    结构保证（供测试 + 复盘核对，不黑箱）：
      · 明确标注「A=作者真迹（锚）」在「B=仿写（待验）」之前呈现（配对 + 锚定方向固定）。
      · 4 维（AV_TRAIT_DIMS）全列 · 每维带读者视角说明 + 配对判别问法。
      · 输出 JSON 每维要 verdict（命中/走味）+ reason（走味须指证）。
    """
    a = _trim(author_text, sample_limit)
    b = _trim(replica_text, sample_limit)

    L = [
        "# 作者验证 · 配对判别（A=作者真迹 → B=仿写 · 逐维度判 B 哪维走味）",
        "",
        "下面 **A 段是作者真迹（锚）**，**B 段是一段仿写（待验证）**。",
        "请以 A 为基准，逐维度判别 B 像不像同一位作者——这是**配对相对判别**，比给 B 打绝对分可靠。",
        "",
        "━━━━━━━━━━ A 段 · 作者真迹（锚 · 这位作者长这样）━━━━━━━━━━",
        "",
        a,
        "",
        "━━━━━━━━━━ B 段 · 仿写（待验证 · 判它哪维露馅）━━━━━━━━━━",
        "",
        b,
        "",
        "# 4 个解耦特质维度（分开判 · 不要混成总分）",
        "",
    ]
    for i, (name, desc, ask) in enumerate(AV_TRAIT_DIMS, 1):
        L += [
            f"## 维度 {i} · {name}",
            f"说明：{desc}",
            f"配对判别问法：{ask}",
            f"verdict 取值：「{MATCH_VERDICT}」（B 这一维读起来像 A）或「{DRIFT_VERDICT}」（B 露馅，像别人/像 AI）。",
            "",
        ]

    L += [
        "# 输出格式",
        "请严格按以下 JSON 输出（4 维各一项 · verdict 必须是「命中」或「走味」二选一）：",
        "```json",
        "{",
        '  "dimensions": {',
    ]
    for i, (name, _desc, _ask) in enumerate(AV_TRAIT_DIMS):
        comma = "," if i < len(AV_TRAIT_DIMS) - 1 else ""
        L.append(
            f'    "{name}": {{"verdict": "{MATCH_VERDICT}|{DRIFT_VERDICT}", '
            f'"reason": "<一句话理由 · 判走味须点名 B 哪处露馅及与 A 的差别>"}}{comma}'
        )
    L += [
        "  }",
        "}",
        "```",
        "",
        f"提醒：以 A 为锚做相对判别，只比写法不比内容；判「{DRIFT_VERDICT}」必须指证 B 的具体露馅处。",
    ]
    return "\n".join(L)


# ════════════════════════════════════════════════════════════════
# gen-model 调用（薄复用 gen_model_loader · 与 distill_replicate 同款 fallback 管线）
# ════════════════════════════════════════════════════════════════

def call_gen_model(loader: GenModelLoader, system: str, user: str,
                   default_max_tokens: int = 1500,
                   tag: str = "av_judge") -> tuple[str, Profile, float]:
    """调当前 active profile，失败按 fallback 链尝试。返回 (text, profile, elapsed)。

    与 distill_replicate.call_gen_model 同款 active→fallback 链——不另起调用栈（北极星⑥）。
    判别只需短输出（4 维 JSON），default_max_tokens 比复刻小。
    """
    from openai import OpenAI

    candidates = loader.get_callable_profiles()
    failures: list[tuple[str, str]] = []
    prefix = f"[{tag}] " if tag else ""

    for i, profile in enumerate(candidates):
        max_tokens = profile.max_tokens or default_max_tokens
        if i == 0:
            print(f"{prefix}[av_judge] 调用 active: {profile.name} ({profile.model})", file=sys.stderr)
        else:
            print(f"\n{prefix}[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url)
        full_text = ""
        t0 = time.time()
        try:
            stream = client.chat.completions.create(
                model=profile.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=profile.temperature,
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    full_text += piece
                    sys.stderr.write(piece)
                    sys.stderr.flush()
        except Exception as e:  # noqa: BLE001 — fallback 链需吞任意 provider 异常
            reason = str(e)[:200]
            print(f"\n{prefix}[FALLBACK] {profile.name} 失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue

        elapsed = time.time() - t0
        print(f"\n{prefix}[av_judge] 接收完毕 ({len(full_text)} chars, {elapsed:.1f}s) via {profile.name}",
              file=sys.stderr)
        return full_text, profile, elapsed

    raise GenModelExhaustedError(failures)


# ════════════════════════════════════════════════════════════════
# 解析 LLM 回复 → 4 维 advisory
# ════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> dict:
    """从 LLM 回复里宽容抽 JSON（去 ```json 包裹 · 取首个花括号块）。失败返回 {}。"""
    if not text:
        return {}
    t = text.strip()
    # 去 markdown 围栏
    if "```" in t:
        # 取第一个 ``` 块内容（兼容 ```json / ``` 两种）
        parts = t.split("```")
        for seg in parts:
            seg = seg.strip()
            if seg.lower().startswith("json"):
                seg = seg[4:].strip()
            if seg.startswith("{"):
                t = seg
                break
    # 截取首个 { ... } 平衡块（宽容尾部多余文本）
    start = t.find("{")
    if start < 0:
        return {}
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return {}
    return {}


def parse_av_verdicts(reply: str) -> dict:
    """把 LLM 回复解析成 4 维 verdict advisory。

    返回 {dimensions: {维度: {verdict, reason, drift: bool}}, drift_dims: [...], parse_ok: bool}。
    宽容：缺维度 → verdict=None / drift=False（不臆造走味）；非「走味」一律当未走味（保守 · 不误伤）。
    """
    data = _extract_json(reply)
    raw = data.get("dimensions", data) if isinstance(data, dict) else {}
    dims: dict[str, dict] = {}
    drift_dims: list[str] = []
    for name, _desc, _ask in AV_TRAIT_DIMS:
        entry = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(entry, dict):
            verdict = str(entry.get("verdict") or "").strip()
            reason = str(entry.get("reason") or "").strip()
        elif isinstance(entry, str):
            verdict, reason = entry.strip(), ""
        else:
            verdict, reason = "", ""
        is_drift = (DRIFT_VERDICT in verdict) if verdict else False
        dims[name] = {
            "verdict": verdict or None,
            "reason": reason or None,
            "drift": is_drift,
        }
        if is_drift:
            drift_dims.append(name)
    return {
        "dimensions": dims,
        "drift_dims": drift_dims,
        "parse_ok": bool(data),
    }


def build_report(mode: str, parsed: dict | None, author_path: str = "",
                 replica_path: str = "", profile_name: str | None = None,
                 elapsed: float | None = None, error: str | None = None) -> dict:
    """组装 advisory 报告（shadow → verdict=None 只记录；active → 走味维度上报为待裁决项）。

    永远 advisory · code AV_TRAIT_DRIFT 绝不进 HARD_GATE_CODES（北极星⑤ · 共同纪律 2）。
    """
    shadow = (mode == "shadow")
    report: dict = {
        "judge": "av_judge",
        "issue_code": ISSUE_CODE,
        "gate_level": "advisory",   # ⚠️ 永远 advisory · 永不 hard_gate
        "mode": mode,
        "shadow": shadow,
        "author_path": author_path,
        "replica_path": replica_path,
        "profile_used": profile_name,
        "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        "note": ("AV-judge 是顾问非法官 · 仅 advisory（可豁免）· 绝不 hard_gate · "
                 "默认 off · shadow 先与 SFS/人评校准（LLM-judge 对网文隐性风格会失准 · "
                 "创意写作域约 1/4 难例翻转）"),
    }
    if error:
        report["error"] = error
        report["verdict"] = None
        report["dimensions"] = {}
        report["drift_dims"] = []
        return report

    parsed = parsed or {"dimensions": {}, "drift_dims": [], "parse_ok": False}
    report["dimensions"] = parsed["dimensions"]
    report["drift_dims"] = parsed["drift_dims"]
    report["parse_ok"] = parsed.get("parse_ok", False)
    if shadow:
        # shadow：只记录 · 不出顶层 verdict（不上报 audit_hub）
        report["verdict"] = None
        report["issues"] = []
    else:
        # active：每个走味维度作为 advisory 待裁决项上报（仍 advisory · 仍可豁免）
        report["verdict"] = "drift" if parsed["drift_dims"] else "match"
        report["issues"] = [
            {
                "code": ISSUE_CODE,
                "gate_level": "advisory",
                "dimension": d,
                "reason": parsed["dimensions"][d].get("reason"),
                "message": f"AV-judge 配对判别：维度「{d}」走味（B 读起来不像作者 A）· advisory 可豁免",
            }
            for d in parsed["drift_dims"]
        ]
    return report


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="AV-judge 解耦特质·作者验证（配对判别 · 读者视角 · advisory · 默认 off）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--author", required=True, help="作者真迹文本路径（A · 锚）")
    parser.add_argument("--replica", required=True, help="仿写文本路径（B · 待验证）")
    parser.add_argument("--out", help="报告 JSON 输出路径（缺省打印到 stdout）")
    parser.add_argument("--sample-limit", type=int, default=3000,
                        help="每段送审字数上限（默认 3000 · 配对判别只需足量语感样本）")
    args = parser.parse_args()

    mode = _av_judge_mode()
    print(f"[av_judge] AV_JUDGE_MODE = {mode}"
          f"（{'完全跳过 · 零回归' if mode == 'off' else 'shadow 只记录 · 不上报' if mode == 'shadow' else 'active · 走味维度作 advisory 上报'}）",
          file=sys.stderr)

    # off（默认）：完全跳过——不构 prompt、不调 gen-model（共同纪律 2 · 零回归）
    if mode == "off":
        report = build_report("off", None, args.author, args.replica)
        report["skipped"] = True
        report["verdict"] = None  # 跳过 = 未判别 · 不报 match（off 报告不暗示真判决）
        report["issues"] = []
        _emit(report, args.out)
        return 0

    author_path, replica_path = Path(args.author), Path(args.replica)
    for p, label in ((author_path, "作者真迹"), (replica_path, "仿写")):
        if not p.exists():
            print(f"[ERROR] {label}文件不存在: {p}", file=sys.stderr)
            return 2

    author_text = _read_text(author_path)
    replica_text = _read_text(replica_path)
    user_prompt = build_av_judge_prompt(author_text, replica_text, args.sample_limit)

    loader = GenModelLoader()
    try:
        active = loader.get_active_profile()
        print(f"[av_judge] active profile = {active.name} ({active.model})", file=sys.stderr)
    except GenModelConfigError as e:
        # 配置缺失：advisory 层不阻断流水线——出 error 报告退 0（共同纪律 · 失败不中断）
        report = build_report(mode, None, str(author_path), str(replica_path),
                              error=f"gen-model 配置错误: {e}")
        _emit(report, args.out)
        return 0

    try:
        reply, profile, elapsed = call_gen_model(
            loader, AV_JUDGE_SYSTEM_PROMPT, user_prompt, tag="av_judge")
    except GenModelExhaustedError as e:
        report = build_report(mode, None, str(author_path), str(replica_path),
                              error=f"gen-model 全部失败: {str(e)[:300]}")
        _emit(report, args.out)
        return 0  # advisory 不阻断

    parsed = parse_av_verdicts(reply)
    report = build_report(mode, parsed, str(author_path), str(replica_path),
                          profile_name=profile.name, elapsed=elapsed)
    _emit(report, args.out)
    return 0


def _emit(report: dict, out: str | None) -> None:
    blob = json.dumps(report, ensure_ascii=False, indent=2)
    if out:
        op = Path(out)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(blob, encoding="utf-8")
        print(f"[av_judge] 报告写入: {op}", file=sys.stderr)
    else:
        print(blob)


if __name__ == "__main__":
    sys.exit(main())
