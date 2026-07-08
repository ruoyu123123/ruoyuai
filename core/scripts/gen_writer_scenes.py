#!/usr/bin/env python3
"""
gen_writer_scenes.py — 逐场景顺序生成（scene-sequential freestyle · 2026-07-08 修正轮）

为什么存在（真机 A/B 实证 · 勿回退一把梭假设）：
  v27「一把梭自然涌现 12-25k CJK」被真机证伪——gemini-3.1-pro 全渠道在 ~107k writer
  prompt 下自发 finish=stop 于 2-3.5k CJK：模型把 6 个 storyboard 场景全覆盖、但每场景
  压缩成 ~500 字梗概体；裸 prompt 也只到 2.6-7.7k。gen_writer 只在 finish=length 时续写，
  自然 stop 的短稿没有任何恢复路径。根因修复 = 把「一次写完整 cluster」改成
  「逐场景一次一调用、把每个场景写透」。

架构（storyboard ≥2 场景时生效 · <2 场景 / 缺失 → 回退原一把梭路径）：
  · 每个场景一次独立 gen-model 调用：system 不变（作者档第一权威）；
    user = 完整 manifest 语境 + 当前场景卡 + 「把本场景写透写完整」指令。
  · 首场景全量注入 cluster_brief；后续场景把已消费的场景卡替换成「前情梗概行 +
    已写正文末尾锚」（防 prompt 线性膨胀），未写场景只留一行预告（防提前写 / 总结）。
  · 场景稿按 storyboard 顺序拼接成 cluster_draft（in_medias_res 的倒叙顺序已由
    outline 排定在 storyboard 里，这里绝不重排——北极星④ splitter 同款纪律）。
  · CHANGES 块只在全部场景写完后单独一次调用产出（复用 changes_only 续写机制）。
  · splitter / 审计零改动：消费的仍是单一 cluster_draft.txt；scene_receipts_scanner
    天然受益（逐场景写 → 场景覆盖率高）零改动。

🔴 红线（与 expand 的本质区别 · expand / 字数兜底已定调清除勿复活）：
  · 本模块没有任何「不够长再补」逻辑——每次调用都是结构化的「写一个场景」，
    场景内自然 stop 即完结（不续写不注水）；字数仍自然涌现，健康带只存在于遥测 / 检测端。
  · prompt 全链路不出现任何数字字数目标（回归锁 tests/test_gen_writer_scene_sequential.py）。
  · best-of-N 在 scene-sequential 下收敛为「首场景 N 选 1」（开篇定调最关键 ·
    SFS+AV 择优），后续场景单发跟随；blind_revise 强制 off（trace 记 mode）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio  # noqa: E402 · CJK 计数权威口径
import cluster_lookup  # noqa: E402 · cluster_id 归一化
from log_util import get_logger  # noqa: E402

logger = get_logger(__name__)

# 已写正文末尾锚长度（代码层常量 · 只决定注入多少「已写内容」当衔接锚 · 不是任何字数目标）
TAIL_ANCHOR_CHARS = 2000
# 前情梗概行截断长度（防单行梗概本身膨胀）
SYNOPSIS_MAX_CHARS = 100
# 逐场景模式的最小场景数（<2 场景没有「逐场景」可言 → 回退一把梭）
MIN_SCENES_FOR_SEQUENTIAL = 2
# 单场景「模型真空正文」的有界重试次数（模型偶发写不出东西 → 重发·不注水）。
SCENE_MAX_RETRIES = 4
# 「渠道 stub」的有界重试次数（更高·因 stub 是廉价快速的渠道级故障非模型问题）。
# 2026-07-08 真机实证：superapi.buzz 约 50% 概率注入 ~98 字英文横幅
#「This version of Antigravity is no longer supported...」替代真正补全 → strip 后正文为空。
# 这是上游渠道故障（与 prompt 无关·反元评论指令无效），值得更高重试预算穿透闪断；
# 与「模型真的写不出」区分——后者重试意义有限（用 SCENE_MAX_RETRIES）。
STUB_MAX_RETRIES = 9
# 渠道 stub 识别：短（< 该字数）且几乎全 ASCII（中文正文场景绝不会这样）。
_STUB_MAX_CHARS = 200
_STUB_MARKERS = ("Antigravity", "no longer supported", "Please upgrade",
                 "quota", "not supported")


def is_channel_stub(reply: str) -> bool:
    """判定回复是否为渠道级 stub（横幅/占位）而非中文正文：短 + 高 ASCII 占比，
    或命中已知渠道 stub 标志词。中文小说正文绝不会短且全英文 → 安全不误伤。"""
    s = (reply or "").strip()
    if not s:
        return False  # 纯空走「模型真空」路径
    if any(mk in s for mk in _STUB_MARKERS):
        return True
    if len(s) <= _STUB_MAX_CHARS:
        ascii_ct = sum(1 for c in s if ord(c) < 128)
        return ascii_ct / max(len(s), 1) > 0.8
    return False


def use_scene_sequential(scene_cards) -> bool:
    """storyboard ≥2 个场景卡 → 走逐场景顺序生成；否则回退一把梭（老行为保留给该形态）。"""
    return isinstance(scene_cards, list) and len(scene_cards) >= MIN_SCENES_FOR_SEQUENTIAL


def load_scene_cards(project_root, cluster_id) -> list:
    """从 事件簇.json 取当前 cluster 的 scene_storyboard 场景卡列表。

    经 gen_writer._sanitize_cluster_brief_foreshadowing 明暗线过滤（单一真理源 ·
    与 build_prompt 注入的 brief 同口径——场景卡里的 dialogue_objectives.what_unsaid
    未到期暗线同样被剥）。任何读取失败 / 无 storyboard → 返回 []（回退一把梭 · 不崩）。
    """
    import gen_writer as gw  # 懒 import 防循环（gen_writer 顶层 import 本模块）
    ec_path = Path(project_root) / '_数据库' / '事件簇.json'
    if not ec_path.exists():
        return []
    try:
        ec = json.loads(ec_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return []
    target = cluster_lookup.normalize_cluster_id(cluster_id)
    for c in ec.get('clusters', []):
        if not isinstance(c, dict):
            continue
        if cluster_lookup.normalize_cluster_id(c.get('cluster_id')) == target:
            safe = gw._sanitize_cluster_brief_foreshadowing(c, cluster_id)
            sb = safe.get('scene_storyboard')
            if isinstance(sb, list):
                return [s for s in sb if isinstance(s, dict)]
            return []
    return []


def _synopsis_of(card: dict) -> str:
    """从场景卡确定性抽一行梗概（summary → scene_goal → dramatic_question → title →
    scene_title → key_beats[0]）。零 LLM · 供前情梗概行 / 后续场景预告行。"""
    for key in ("summary", "scene_goal", "dramatic_question", "title", "scene_title"):
        v = card.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:SYNOPSIS_MAX_CHARS]
    kb = card.get("key_beats")
    if isinstance(kb, list):
        for b in kb:
            if isinstance(b, str) and b.strip():
                return b.strip()[:SYNOPSIS_MAX_CHARS]
    return "（该场景卡无梗概字段）"


def synopsis_line(scene_idx: int, card: dict) -> str:
    """前情梗概行：已写完场景的一行摘要（替代其全量场景卡 · 膨胀控制）。"""
    return f"场景 {scene_idx + 1}：{_synopsis_of(card)}"


def compact_brief_for_scene(safe_brief: dict, scene_view: dict) -> dict:
    """后续场景（idx≥1）注入 brief 前压缩 scene_storyboard（防 prompt 线性膨胀）：

      · 已消费场景卡 → 一行梗概（正文语境由「已写正文末尾锚」承载 · 勿复述）；
      · 当前场景 → 占位指路（全量卡在 prompt 末尾「当前场景卡」段 · 生成点近邻）；
      · 未写场景 → 一行预告（让模型知道故事还有后文 · 防提前收束，但不给全量卡防提前写）。

    首场景（idx=0）不走本函数——全量 brief 原样注入。返回浅拷贝不改原 brief。
    """
    if not isinstance(safe_brief, dict):
        return safe_brief
    sb = safe_brief.get("scene_storyboard")
    if not isinstance(sb, list):
        return safe_brief
    idx = int(scene_view.get("idx", 0))
    compact = []
    for k, card in enumerate(sb):
        if not isinstance(card, dict):
            compact.append(card)
            continue
        if k < idx:
            compact.append({"scene_index": k, "status": "已写完（勿复述）",
                            "synopsis": _synopsis_of(card)})
        elif k == idx:
            compact.append({"scene_index": k,
                            "status": "当前场景（全量场景卡见 prompt 末尾「当前场景卡」段）"})
        else:
            compact.append({"scene_index": k, "status": "后续场景（本次不写·由后续调用写）",
                            "synopsis": _synopsis_of(card)})
    out = dict(safe_brief)
    out["scene_storyboard"] = compact
    return out


def scene_gen_point_tail(scene_view: dict) -> str:
    """逐场景模式的生成点尾部（替代一把梭 gen_point_tail · 贴生成点 RoPE 高位）。

    含：前情梗概（idx≥1）+ 已写正文末尾锚（idx≥1）+ 当前场景卡全量 + 逐场景硬指令。
    🔴 纪律：本段（及全逐场景链路）不出现任何数字字数目标；本次调用不产 CHANGES。
    """
    idx = int(scene_view.get("idx", 0))
    total = int(scene_view.get("total", 1))
    card = scene_view.get("scene_card") or {}
    consumed = scene_view.get("consumed_lines") or []
    prev_tail = (scene_view.get("prev_tail") or "").strip()
    meta_retry = bool(scene_view.get("meta_retry"))  # 上次只回英文元评论 → 本次追加纠正

    parts = ["\n\n---\n\n# 逐场景生成模式（本次调用只写一个场景）\n\n"]
    parts.append(
        f"本 cluster 共 {total} 个场景，本次调用写第 {idx + 1} 个。"
        "场景顺序 = scene_storyboard 顺序（倒叙等叙事顺序已由大纲排定，勿自行重排）。\n")
    if consumed:
        parts.append("\n## 前情梗概（已写完的场景 · 只作衔接参考 · 勿复述）\n\n"
                     + "\n".join(f"- {line}" for line in consumed) + "\n")
    if prev_tail:
        parts.append("\n## 已写正文末尾（衔接锚 · 从这里的最后一个字自然接下去）\n\n"
                     + prev_tail + "\n")
    parts.append("\n## 当前场景卡（本次调用唯一要写的场景）\n\n```json\n"
                 + json.dumps(card, ensure_ascii=False, indent=2) + "\n```\n")
    directives = [
        "- 只写「当前场景卡」这一个场景的正文：把本场景写透写完整——动作、对话、环境、"
        "内心、冲突推进逐一到位，写到本场景自然完结为止。不预设篇幅：内容密度决定长短，"
        "该展开就展开、该收就收，切忌把场景压缩成梗概体。",
        "- 不要写后续场景的内容；不要在结尾总结本场景；不要预告 / 引出下一场景"
        "（与下一场景的衔接由后续调用完成）。",
        "- 本次调用【不要】输出 CHANGES JSON 块（CHANGES 在全部场景写完后单独产出）。",
        "- 正文仍是纯中文连续叙事：不写章节标记、不写元话语；作者风格档（上方风格 skill）"
        "仍是第一权威，7 项硬铁律 + 元 anti-slop 防御全部生效。",
    ]
    if idx > 0:
        directives.insert(1, "- 从「已写正文末尾」的最后一个字直接衔接续写：不要重复已写内容、"
                             "不要重新开头、不要复述前情。")
    parts.append("\n【本次调用的硬指令】\n" + "\n".join(directives))
    # 🔴 生成点末尾反元评论硬约束（真机实证：pro-preview 对续写场景偶发只回一段英文自评
    # 「The narrative chunk is written... / All quantitative requirements...」而不写正文）：
    parts.append(
        "\n\n🔴【输出格式绝对要求】你的回复**必须以中文小说正文的第一个字开头**，"
        "整段回复只能是中文叙事正文本身。严禁任何英文句子、严禁任何「The narrative/quantitative」"
        "式的英文自评或合规确认、严禁任何评估/总结/元说明/JSON——写完场景正文即停，不要在正文后"
        "追加任何说明。")
    if meta_retry:
        parts.append(
            "\n\n⚠️【重试纠正】上一次调用你没有写正文、只回了一段英文说明，本次作废重来："
            "直接从当前场景的中文正文第一个字写起。")
    parts.append("\n\n现在开始写当前场景的正文。")
    return "".join(parts)


def extract_changes_block(changes_reply: str) -> str:
    """从 changes_only 调用回复里取 CHANGES JSON，归一成 ```json ...``` 围栏块。

    模型可能：① 按指令输出围栏块（取最后一个）；② 裸输出 JSON（可解析才收）。
    两者皆无 → 返回 ""（与一把梭「模型没产 CHANGES → changes={}」既有容错同口径，
    save_output normalize_changes 兜底成骨架 · 不阻断草稿落盘）。
    """
    m = list(re.finditer(r'```json\s*\n(.*?)\n```', changes_reply or "", re.DOTALL))
    if m:
        return "```json\n" + m[-1].group(1).strip() + "\n```"
    raw = (changes_reply or "").strip()
    if raw:
        try:
            json.loads(raw)
            return "```json\n" + raw + "\n```"
        except json.JSONDecodeError:
            pass
    return ""


def scene_sequential_pipeline(loader, project_root, cluster_id: int, ch_start: int,
                              scene_cards: list, n: int,
                              base_system: str = None, base_user: str = None) -> tuple:
    """逐场景顺序生成主流程。

    返回 (reply_full, used_profile, best_of_n_trace, scene_trace)：
      · reply_full = 各场景正文按 storyboard 顺序拼接 + 尾部单次 CHANGES 块
        （形态与一把梭 reply 一致 · 下游 split_text_and_changes / save_output 零改动）；
      · best_of_n_trace：首场景 N 选 1 的择优痕迹（scope=first_scene_only · blind_revise
        强制 off）；BEST_OF_N=1 时为单发说明；
      · scene_trace：per-scene 遥测（scene_idx / cjk / finish / prompt_chars）——
        S8/S9 遥测对最终拼接稿照常由 save_output 计算，不在这里重复。

    失败纪律：任一场景解析后正文为空 → 响亮 RuntimeError（不注水不跳过）；
    gen-model 全链挂 → GenModelExhaustedError 向上传（主入口统一 exit 3）。
    """
    import gen_writer as gw  # 懒 import 防循环（gen_writer 顶层 import 本模块）
    total = len(scene_cards)
    per_scene: list = []
    scene_bodies: list = []
    consumed_lines: list = []
    bon_trace = None
    used_profile = None

    for i, card in enumerate(scene_cards):
        prev_tail = ""
        if scene_bodies:
            prev_tail = "\n\n".join(scene_bodies)[-TAIL_ANCHOR_CHARS:]
        # 有界重试（不注水不降健康带）：两类失败分开计预算——
        #   · 渠道 stub（横幅/短英文占位·上游故障非模型问题）→ STUB_MAX_RETRIES（高·穿透闪断）；
        #   · 模型真空正文（写不出·极短或纯空）→ SCENE_MAX_RETRIES（低·重试意义有限）。
        # 任一预算用尽仍无正文 → 响亮 RuntimeError。重试时 prompt 追加反元评论纠正提示。
        body = ""
        finish = None
        attempt = 0
        stub_fails = 0
        empty_fails = 0
        while True:
            view = {"idx": i, "total": total, "scene_card": card,
                    "consumed_lines": list(consumed_lines), "prev_tail": prev_tail,
                    "meta_retry": attempt > 0}
            system, user, _seed = gw.build_prompt(project_root, cluster_id, ch_start,
                                                  scene_view=view)
            if i == 0 and n >= 2 and attempt == 0:
                # 首场景首次尝试 N 选 1：开篇定调最关键 · SFS+AV 择优；blind_revise 强制 off
                logger.info(f"\n[scene-sequential] 场景 1/{total} · 首场景 best-of-{n} 择优"
                            f"（开篇定调 · blind_revise 强制 off）")
                reply, used_profile, bon_trace = gw.best_of_n_pipeline(
                    loader, system, user, Path(project_root), n, creative=True,
                    force_blind_revise_off=True)
                finish = None  # 择优层抽象掉单次 finish（诚实 None · 不伪装）
                bon_trace = dict(bon_trace or {})
                bon_trace["scope"] = "first_scene_only"
                bon_trace["mode"] = "scene_sequential_first_scene_n_pick_1"
            else:
                _tag = "单发生成" if attempt == 0 else f"重试 {attempt}"
                logger.info(f"\n[scene-sequential] 场景 {i + 1}/{total} · {_tag}")
                reply, used_profile, finish = gw.call_gen_model(
                    loader, system, user, creative=True, return_finish=True)
            body, _spurious = gw.split_text_and_changes(reply)  # 剥模型违令误产的 CHANGES 块
            body = (body or "").strip()
            # 接受条件：正文非空【且】原始回复不是渠道 stub（防 stub 未被现有 strip 清空时误收当正文）。
            if body and not is_channel_stub(reply):
                break
            attempt += 1
            if is_channel_stub(reply):
                stub_fails += 1
                logger.warning(f"[scene-sequential] 场景 {i + 1}/{total} 第 {attempt} 次收到"
                               f"渠道 stub（横幅/占位·上游故障非正文·"
                               f"stub {stub_fails}/{STUB_MAX_RETRIES}）→ 重发穿透闪断")
            else:
                empty_fails += 1
                logger.warning(f"[scene-sequential] 场景 {i + 1}/{total} 第 {attempt} 次"
                               f"模型真空正文（empty {empty_fails}/{SCENE_MAX_RETRIES}）→ 重发")
            if stub_fails > STUB_MAX_RETRIES or empty_fails > SCENE_MAX_RETRIES:
                _kind = ("渠道持续返回 stub（横幅/占位）——上游渠道故障，非若渝AI 代码问题，"
                         "换可用 gen-model 渠道" if stub_fails > STUB_MAX_RETRIES
                         else "模型持续写不出正文——检查 prompt / 换 profile")
                raise RuntimeError(
                    f"[FATAL scene-sequential] 场景 {i + 1}/{total} 重试用尽仍无正文"
                    f"（stub {stub_fails} · empty {empty_fails}）：{_kind}")
        scene_bodies.append(body)
        consumed_lines.append(synopsis_line(i, card))
        per_scene.append({"scene_idx": i, "cjk": cio.count_cjk(body), "finish": finish,
                          "prompt_chars": len(system) + len(user),
                          "best_of_n_applied": bool(i == 0 and n >= 2)})
        logger.info(f"[scene-sequential] 场景 {i + 1}/{total} 完成：{per_scene[-1]['cjk']} CJK"
                    f"（finish={finish} · prompt {per_scene[-1]['prompt_chars']} chars）")

    full_body = "\n\n".join(scene_bodies)

    # CHANGES：全部场景写完后单独一次调用（复用 changes_only 续写机制 · 只产 JSON 不再写正文）
    if base_system is None or base_user is None:
        base_system, base_user, _seed = gw.build_prompt(project_root, cluster_id, ch_start)
    logger.info(f"\n[scene-sequential] {total} 场景全部完成（拼接 {cio.count_cjk(full_body)} "
                f"CJK）· 尾部单独一次调用产出 CHANGES JSON")
    changes_reply, _profile, _fin = gw.call_gen_model(
        loader, base_system, base_user, creative=True,
        prior_assistant=full_body, cont_reason="changes_only", return_finish=True)
    changes_block = extract_changes_block(changes_reply)
    if not changes_block:
        logger.warning("[scene-sequential] ⚠️ changes_only 调用未产出可解析 JSON——changes "
                       "落盘走 normalize 空骨架（与一把梭『模型没产 CHANGES』同口径）")
    reply_full = full_body + ("\n\n" + changes_block if changes_block else "")

    scene_trace = {
        "mode": "scene_sequential",
        "scenes_total": total,
        "per_scene": per_scene,
        "blind_revise": "forced_off_scene_sequential",
        "first_scene_best_of_n": bool(n >= 2),
        "changes_call": {"made": True, "parsed": bool(changes_block)},
    }
    if bon_trace is None:
        bon_trace = {"best_of_n": 1,
                     "note": "scene-sequential 单发（BEST_OF_N=1 · 首场景未择优）"}
    return reply_full, used_profile, bon_trace, scene_trace
