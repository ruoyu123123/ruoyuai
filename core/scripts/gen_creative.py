#!/usr/bin/env python3
"""gen_creative.py — outline 侧创作产物确定性验收器（零 LLM 调用）

创作笔触由 Claude agent 亲笔完成，本脚本只做机器验收与单元调度：

  --mode brainstorm --verify      验收 novel-outline-planner MODE=brainstorm 亲笔写的灵感卡
                                  （恰 N 卡 / 必备键齐 / logline≤50 字 / core_mechanism≤100 字 /
                                   volume_skeleton 5-6 卷 / source_refs≥1 且逐条 URL 真实存在于调研缓存）
  --mode distill_reflect --verify 验收 novel-skill-author MODE=draft 亲笔写的 skill_v{N}.md
                                  （五必备小节齐 + ≥200 字·配合 /distill-style）
  --mode volume_arc               卷级大纲单元验收 + 确定性合并落库（实现在 gen_creative_volume_arc.py·
                                  scene_jobs 范式：单元 WAL 缺失/破损 → 写 _数据库/.wal/volume_arc_jobs.json
                                  并 exit 2=pending → 主代理 spawn novel-outline-planner
                                  MODE=volume_arc_unit 亲笔补件 → 重跑续跑验收·配合 /outline）

用法示例：

  # 灵感卡验收（agent 落盘 inspiration_cards.json 后跑）
  python core/scripts/gen_creative.py --mode brainstorm --verify \\
    --cards <_数据库/.wal/inspiration_cards.json> --count 3 \\
    --research <_数据库/.research_cache/inspiration_synthesis.json>

exit 语义：0=验收通过 / 1=输入或硬错误 / 2=验收不过或单元 pending（主代理补 agent 产物后重跑）。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import atomic_json  # noqa: E402


# ============ 共享：JSON 解析 ============
def _parse_json_loose(reply: str, fallback: dict) -> dict:
    """从文本中找 JSON 块。支持：纯 JSON / ```json ... ``` 包裹 / 末尾 JSON"""
    # 1. 尝试 ```json ... ``` 包裹
    m = re.search(r'```json\s*\n(.*?)\n```', reply, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 2. 尝试纯 JSON（首字符 { 或 [）
    stripped = reply.strip()
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    # 3. 找第一个 { 到最后一个 } 之间
    first = stripped.find('{')
    last = stripped.rfind('}')
    if first >= 0 and last > first:
        try:
            return json.loads(stripped[first:last + 1])
        except json.JSONDecodeError:
            pass
    return fallback


# ============ MODE: brainstorm --verify（灵感卡确定性验收）============
# 与 novel-outline-planner MODE=brainstorm 合约（.claude/agents/novel-outline-planner.md）
# 一一对应：agent 亲笔写卡，本门把合约承诺升级成机器门。
BRAINSTORM_REQUIRED_CARD_KEYS = (
    "card_id", "title", "logline", "core_mechanism",
    "volume_skeleton", "selling_point", "risk", "source_refs",
)
BRAINSTORM_LOGLINE_MAX = 50
BRAINSTORM_MECHANISM_MAX = 100
BRAINSTORM_VOLUME_SKELETON_MIN = 5
BRAINSTORM_VOLUME_SKELETON_MAX = 6


def verify_brainstorm_cards(cards_doc: object, *, count: int,
                            research_text: str) -> list[str]:
    """灵感卡结构验收（纯函数·返回违规清单·空=通过）。

    规则：恰 count 卡 / 必备键齐且非空 / card_id 不重复 / logline≤50 字 /
    core_mechanism≤100 字 / volume_skeleton 5-6 卷（元素为 object）/
    source_refs≥1 且每条 URL 必须逐字存在于调研缓存原文（防凭记忆编造来源）。
    """
    errors: list[str] = []
    if not isinstance(cards_doc, dict) or cards_doc.get("_parse_failed"):
        return ["灵感卡文件不是合法 JSON object"]
    topic = cards_doc.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        errors.append("顶层 topic 缺失或为空")
    cards = cards_doc.get("cards")
    if not isinstance(cards, list):
        return errors + ["顶层 cards 缺失或不是 array"]
    if len(cards) != count:
        errors.append(f"卡数必须恰 {count} 张，实得 {len(cards)}")
    seen_ids: set[str] = set()
    for i, card in enumerate(cards):
        label = f"cards[{i}]"
        if not isinstance(card, dict):
            errors.append(f"{label} 必须是 object")
            continue
        missing = [k for k in BRAINSTORM_REQUIRED_CARD_KEYS if k not in card]
        if missing:
            errors.append(f"{label} 缺必备键: {missing}")
        card_id = card.get("card_id")
        if not isinstance(card_id, str) or not card_id.strip():
            errors.append(f"{label}.card_id 不能为空")
        elif card_id in seen_ids:
            errors.append(f"{label}.card_id 重复: {card_id}")
        else:
            seen_ids.add(card_id)
        for key in ("title", "logline", "core_mechanism", "selling_point", "risk"):
            v = card.get(key)
            if key in card and (not isinstance(v, str) or not v.strip()):
                errors.append(f"{label}.{key} 必须是非空字符串")
        logline = card.get("logline")
        if isinstance(logline, str) and len(logline.strip()) > BRAINSTORM_LOGLINE_MAX:
            errors.append(f"{label}.logline 超长: {len(logline.strip())} > {BRAINSTORM_LOGLINE_MAX} 字")
        mech = card.get("core_mechanism")
        if isinstance(mech, str) and len(mech.strip()) > BRAINSTORM_MECHANISM_MAX:
            errors.append(f"{label}.core_mechanism 超长: {len(mech.strip())} > {BRAINSTORM_MECHANISM_MAX} 字")
        skeleton = card.get("volume_skeleton")
        if "volume_skeleton" in card:
            if not isinstance(skeleton, list) or not all(isinstance(v, dict) for v in skeleton):
                errors.append(f"{label}.volume_skeleton 必须是 object array")
            elif not (BRAINSTORM_VOLUME_SKELETON_MIN <= len(skeleton)
                      <= BRAINSTORM_VOLUME_SKELETON_MAX):
                errors.append(f"{label}.volume_skeleton 必须 "
                              f"{BRAINSTORM_VOLUME_SKELETON_MIN}-{BRAINSTORM_VOLUME_SKELETON_MAX} 卷，"
                              f"实得 {len(skeleton)}")
        refs = card.get("source_refs")
        if "source_refs" in card:
            if not isinstance(refs, list) or not refs \
                    or any(not isinstance(r, str) or not r.strip() for r in refs):
                errors.append(f"{label}.source_refs 必须是 ≥1 条的非空字符串 array")
            else:
                for r in refs:
                    if r.strip() not in research_text:
                        errors.append(f"{label}.source_refs URL 不存在于调研缓存"
                                      f"（禁止凭记忆编造来源）: {r.strip()}")
    return errors


def _verify_brainstorm(args) -> int:
    if not args.cards or not args.research:
        print("[FATAL] --mode brainstorm --verify 需要 --cards <灵感卡路径> 与 "
              "--research <调研缓存路径>（source_refs 落地校验）", file=sys.stderr)
        return 1
    research_path = Path(args.research)
    if not research_path.exists():
        print(f"[FATAL] 调研缓存不存在: {research_path}"
              f"（step2 novel-researcher 必产·重 spawn planner 修不了此项）", file=sys.stderr)
        return 1
    cards_path = Path(args.cards)
    if not cards_path.exists():
        print(f"[FATAL] 灵感卡未落盘: {cards_path}\n"
              f"主代理须 spawn novel-outline-planner MODE=brainstorm 亲笔写卡后重跑本验收",
              file=sys.stderr)
        return 2
    research_text = research_path.read_text(encoding="utf-8")
    doc = _parse_json_loose(cards_path.read_text(encoding="utf-8"),
                            fallback={"_parse_failed": True})
    errors = verify_brainstorm_cards(doc, count=args.count, research_text=research_text)
    if errors:
        for e in errors:
            print(f"[FATAL] brainstorm 验收不过: {e}", file=sys.stderr)
        print(f"[FATAL] 共 {len(errors)} 项违规 → exit 2·主代理重 spawn "
              f"novel-outline-planner MODE=brainstorm 重写后重跑", file=sys.stderr)
        return 2
    # 验收通过 → 盖确定性验收章 + 归一为严格 JSON 落盘（下游 pause 读干净产物）
    meta = doc.setdefault("_meta", {})
    if isinstance(meta, dict):
        meta.update({
            "mode": "brainstorm",
            "authored_by": "novel-outline-planner",
            "verified_by": "gen_creative.brainstorm.verify",
            "verified_at": datetime.now().isoformat(timespec="seconds"),
            "research": str(research_path),
            "card_count": args.count,
        })
    atomic_json.atomic_write_json(cards_path, doc)
    print(f"[OK] brainstorm 验收通过: {args.count} 卡 · 必备键/长度/卷骨架/调研来源全绿 → {cards_path}")
    return 0


# ============ MODE: distill_reflect --verify（skill 小节确定性验收）============
# 与 novel-skill-author MODE=draft 合约（.claude/agents/novel-skill-author.md）一一对应。
REQUIRED_SKILL_SECTIONS = ("## 句式与节奏", "## 段落与标点", "## 对话工艺",
                           "## 描写与情绪", "## 反模式")
SKILL_MIN_CHARS = 200


def verify_distill_skill_text(text: str) -> list[str]:
    """skill markdown 结构验收（纯函数·返回违规清单·空=通过）：五必备小节齐 + ≥200 字。"""
    errors: list[str] = []
    body = (text or "").strip()
    if len(body) < SKILL_MIN_CHARS:
        errors.append(f"skill 过短: {len(body)} < {SKILL_MIN_CHARS} 字")
    missing = [s for s in REQUIRED_SKILL_SECTIONS if s not in body]
    if missing:
        errors.append(f"缺必备小节: {missing}")
    return errors


def _verify_distill_skill(args) -> int:
    if not args.skill:
        print("[FATAL] --mode distill_reflect --verify 需要 --skill <skill_v{N}.md 路径>",
              file=sys.stderr)
        return 1
    skill_path = Path(args.skill)
    if not skill_path.exists():
        print(f"[FATAL] skill 未落盘: {skill_path}\n"
              f"主代理须 spawn novel-skill-author MODE=draft 亲笔撰写后重跑本验收",
              file=sys.stderr)
        return 2
    errors = verify_distill_skill_text(skill_path.read_text(encoding="utf-8"))
    if errors:
        for e in errors:
            print(f"[FATAL] distill_reflect 验收不过: {e}", file=sys.stderr)
        print("[FATAL] exit 2·主代理重 spawn novel-skill-author MODE=draft 重写后重跑",
              file=sys.stderr)
        return 2
    print(f"[OK] distill_reflect 验收通过: 五必备小节齐 + ≥{SKILL_MIN_CHARS} 字 → {skill_path}")
    return 0


# ============ 主入口 ============
def main():
    # stdout/stderr UTF-8（Windows 默认 GBK·中文诊断直打 GBK 终端会 UnicodeEncodeError）
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    parser = argparse.ArgumentParser(
        description='outline 侧创作产物确定性验收器（创作由 Claude agent 亲笔·本脚本零 LLM）'
    )
    parser.add_argument('--mode', required=True,
                        choices=['brainstorm', 'volume_arc', 'distill_reflect'])
    parser.add_argument('--verify', action='store_true',
                        help='[brainstorm/distill_reflect] 确定性验收 agent 亲笔产物（两模式必传）')

    # brainstorm --verify 参数
    parser.add_argument('--cards', help='[brainstorm] 待验收灵感卡 JSON 路径')
    parser.add_argument('--count', type=int, default=3, help='[brainstorm] 期望卡数（默认 3）')
    parser.add_argument('--research', help='[brainstorm] 调研缓存路径（source_refs 落地校验）；'
                                           '[volume_arc] 调研背景路径（写入 jobs 清单）')

    # distill_reflect --verify 参数
    parser.add_argument('--skill', help='[distill_reflect] 待验收 skill_v{N}.md 路径')

    # volume_arc 参数（单元验收 + 合并落库·实现在 gen_creative_volume_arc.py）
    parser.add_argument('--project', help='[volume_arc] 项目根路径')
    parser.add_argument('--selected-card', help='[volume_arc] 选中灵感卡 JSON 路径')
    parser.add_argument('--cluster-count', type=int, help='[volume_arc] 每卷故事块数（软提示）')
    parser.add_argument('--framework', help='[volume_arc] 叙事框架')
    parser.add_argument('--rhythm', help='[volume_arc] 节奏档')
    parser.add_argument('--style-ref', help='[volume_arc] 风格 skill md 路径（写入 jobs 清单）')
    parser.add_argument('--emit-to-db', action='store_true',
                        help='[volume_arc] 拆产出落 大势卡.json + 事件簇.json')

    args = parser.parse_args()

    if args.mode == 'brainstorm':
        if not args.verify:
            print("[FATAL] 灵感卡由 novel-outline-planner MODE=brainstorm 亲笔写作；"
                  "--mode brainstorm 只支持 --verify 确定性验收", file=sys.stderr)
            sys.exit(1)
        sys.exit(_verify_brainstorm(args))

    if args.mode == 'distill_reflect':
        if not args.verify:
            print("[FATAL] skill 由 novel-skill-author MODE=draft 亲笔撰写；"
                  "--mode distill_reflect 只支持 --verify 确定性验收", file=sys.stderr)
            sys.exit(1)
        sys.exit(_verify_distill_skill(args))

    # volume_arc：单元验收 + jobs pending + 确定性合并落库
    if args.verify:
        print("[FATAL] --mode volume_arc 自带单元验收（jobs pending 范式），不接受 --verify",
              file=sys.stderr)
        sys.exit(1)
    from gen_creative_volume_arc import _run_volume_arc
    sys.exit(_run_volume_arc(args))


if __name__ == '__main__':
    main()
