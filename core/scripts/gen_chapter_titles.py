#!/usr/bin/env python3
"""
gen_chapter_titles.py — splitter 后调 gen-model 为每章生成网文化章标题（三档混合策略）

# 网文章标题策略（基于调研，《BookC》《饲养全人类》《十日终焉》《斩神》70 章样本）

| 档 | 比例 | 字数 | 用途 | 例 |
|---|---|---|---|---|
| **normal** | 80% | 2-4 字 | 主体章节冷峻意象/名词 | 「葬」「断牙」「孤峰」「断梯」 |
| **mid** | 15% | 5-8 字 | cluster 收尾 / fate event 节点 / 重大转折 | 「凿齿夜袭十三死」「益死前刻最后一道」 |
| **high** | 5% | 8-14 字 | 绝对高潮章（卷高潮 / 神战 / 史诗节点） | 「徇射穿了第二个太阳」 |

# 自动分级规则
- cluster_position=tail（每 cluster 末章）→ mid
- 用户传 --high-chapters N,M 显式指定 → high
- 其余 → normal

# 避重逻辑
gen-model 调用时传入已生成的章标题列表，prompt 明确要求不重复（最多允许命运母题章重复 1 次，如「葬礼」类）。

用法:
  python gen_chapter_titles.py --project <path> --chapters 1-40 --high-chapters 11,40
  python gen_chapter_titles.py --project <path> --chapters 5,6,7,8

按用户全局规则 splitter_post_chapter_title_regen + 网文化调研结论。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_model_loader import GenModelLoader, GenModelConfigError, reasoning_extra_body
try:
    import cluster_lookup  # blueprint list 归一守卫
except Exception:  # 防御：缺模块退回原 dict 守卫
    cluster_lookup = None


def parse_chapters(s: str) -> list[int]:
    result = []
    for part in s.split(','):
        part = part.strip()
        if '-' in part:
            a, b = map(int, part.split('-'))
            result.extend(range(a, b + 1))
        else:
            result.append(int(part))
    return result


def parse_high_list(s: str | None) -> set[int]:
    if not s:
        return set()
    return set(parse_chapters(s))


def read_chapter(project: Path, ch: int) -> tuple[Path, str]:
    p = project / '章节' / f'第{ch:03d}章' / f'第{ch:03d}章.txt'
    if not p.exists():
        return p, ''
    return p, p.read_text(encoding='utf-8')


def read_changes(project: Path, ch: int) -> dict:
    p = project / '章节' / f'第{ch:03d}章' / f'第{ch:03d}章_changes.json'
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def strip_existing_title(text: str) -> str:
    """如果文件开头已有「第NNN章 X」格式标题，去掉"""
    pattern = re.compile(r'^第\s*[一二三四五六七八九十百千\d]+\s*章[ \t]+\S.*?\n\s*\n', re.MULTILINE)
    m = pattern.match(text)
    if m:
        return text[m.end():]
    return text


# 🔴 storyboard hint 是「大纲指令/场景描述文本」(如
# 「倒叙强冲突开场。铁十字街一间逼仄昏暗的出租屋里，主角顶着占卜…」)，**不是章标题**。
# 标题 gen 失败(间歇 500/EMPTY_RESPONSE)时绝不能把它原样当标题写进正文。下面两个
# helper 负责：① 判定 hint 是否「干净到能当标题」；② 失败时给干净 fallback。
_DIRECTIVE_MARKERS = (
    "主角", "男主", "女主", "某角色", "占位", "倒叙", "强冲突", "开场", "场景",
    "回溯", "反转", "揭底", "镜头", "POV", "pov", "storyboard", "scene",
    "高潮章", "本章", "本场", "钩子", "兑现", "伏笔", "节点",
)
_TITLE_PUNCT = "。！？，、；：…．,.!?;\n\r\t"


def _strip_title_wrappers(s: str) -> str:
    return (s or "").strip().strip("「」“”\"'《》【】（）() *—-").strip()


def _is_clean_title(s: str) -> bool:
    """判断一个字符串是否像「干净的章标题」(而非 storyboard 指令/描述句)。

    干净标题：剥包裹符后非空、≤14 字、不含句末/分句标点、不含大纲指令/占位标记词。
    storyboard hint 多含句号/逗号或「主角」「倒叙强冲突开场」之类 → 判脏，拒用。
    """
    s = _strip_title_wrappers(s)
    if not s or len(s) > 14:
        return False
    if any(p in s for p in _TITLE_PUNCT):
        return False
    if any(m in s for m in _DIRECTIVE_MARKERS):
        return False
    return True


def _clean_fallback_title(ch: int, hint: str) -> str:
    """标题 gen 失败时的干净 fallback：

    优先级 = 干净 hint > 保守「第N章」无副标题。
    **绝不**把含句末标点/占位词「主角」的 storyboard 指令文本(如
    「倒叙强冲突开场。…主角顶着占卜」)当标题——那种 hint 判脏后退「第N章」。
    """
    if _is_clean_title(hint):
        return _strip_title_wrappers(hint)
    return f"第{ch}章"


def classify_tier(ch: int, changes: dict, high_set: set[int]) -> str:
    """自动分级 normal/mid/high"""
    if ch in high_set:
        return 'high'
    pos = changes.get('ecas_metadata', {}).get('cluster_position', '')
    if pos == 'tail':
        return 'mid'
    return 'normal'


def _load_title_style(project: Path) -> dict | None:
    """从风格库读 title_style.json，做 per-book 校准。"""
    style_path = project / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return None
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    work = (sd.get("meta") or {}).get("work") or sd.get("work")
    if not work:
        return None
    for parent in [project, *project.parents]:
        f = parent / "workspace" / "styles" / work / "title_style.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def gen_one_title(loader: GenModelLoader, ch: int, body: str, hint: str,
                   tier: str, history_titles: list[str], title_style: dict | None = None) -> str:
    from openai import OpenAI
    # 全局规则：不节省 token · 全量传 body 给 LLM
    body_sample = body
    history_str = '、'.join(f"「{t}」" for t in history_titles[-20:]) if history_titles else '（无）'

    tier_brief = {
        'normal': "2-4 字主体（冷峻意象/名词，对标《BookC》「绯红/仪式/笔记」、《十日终焉》「空屋/说谎/灾难」）",
        'mid': "5-8 字事件标签（卷节点 / fate event 兑现章用，含动作或物件信息，对标《饲养全人类》「沙盘构想/拯救虫猿部落」）",
        'high': "8-14 字句子/对话钩子（绝对高潮章用，可以是动作描述/对话片段/反差，对标《斩神》「你可以叫我……林医生」）",
    }[tier]

    # per-book 校准段（如果有风格库 title_style.json）
    perbook_calibration = ""
    if title_style and not title_style.get("error"):
        td = title_style.get("tier_distribution_pct", {})
        struct = title_style.get("structure_distribution_pct", {})
        samples = title_style.get("golden_samples_per_tier", {})
        high_chars = title_style.get("high_freq_chars", [])
        if td:
            perbook_calibration = f"""

# ⭐ per-book 风格校准（覆盖默认 80/15/5，优先匹配原作者实际分布）

原作者实际 tier 分布：normal={td.get('normal', 0):.0%} / mid={td.get('mid', 0):.0%} / high={td.get('high', 0):.0%}
主结构偏好：{', '.join(f'{k} {v:.0%}' for k, v in sorted(struct.items(), key=lambda x: -x[1])[:3])}
高频字 TOP10：{', '.join(c for c, _ in high_chars[:10])}

原作者真实 {tier} 档样本：{', '.join(f"「{t}」" for t in samples.get(tier, [])[:5]) or '（无）'}

⚠️ 优先匹配上面的 per-book 数据，不要盲套默认 80/15/5。"""

    system = f"""你是网文章节命名工程师。基于 2026 中文网文 TOP 榜爆款 70 章调研得出的混合策略。

# 网文章标题策略（强制三档）

默认主体分布：80% 2-4 字 / 15% 5-8 字 / 5% 8-14 字（仅作 fallback，per-book 数据优先）

# 本章档位：**{tier}**（{tier_brief}）
{perbook_calibration}

# 通用硬规则

1. **绝不重复**已生成过的章标题（哪怕母题重复都不行——「葬」用过就别再「葬」「葬礼」）
2. **风格贴本项目 voice**（参考调研：冷峻意象 / 物件感 / 不带 YY 网文味 / 项目调性匹配章节内容）
3. **不带标点不带引号不带「第N章」前缀**
4. **不写任何元话语**（如「好的」「我们」「用户要求」「让我」），**直接只输出标题文字**

# 反例（不要这样写）
- ❌「校正」「层叠」「观测」（normal 档但太抽象无画面）
- ❌「他YY 了反派」（YY 网文味）
- ❌「第二个太阳出现」（normal 档变成流水账复述）
- ❌「好的，章标题是 X」「我们生成…」（元话语，必直接输标题）

# 正例（按档位）

**normal（2-4 字）**：「断牙」「孤峰」「弓断」「葬」「祂」「天裂」「夜雾」「续刻」「冷雨」「断翅」
**mid（5-8 字）**：「凿齿夜袭」「沉手中的箭」「益死前最后一刻」「饲养者的雨」「断梯前夜」「不周山微震」
**high（8-14 字）**：「徇射穿了第二个太阳」「沉倒下时手里还握着箭」「益死前藏起加密手稿」「饲养者第一次被反观测」"""

    user = f"""# 章节 ch{ch} (档位: **{tier}**)

## cluster_blueprint 提示（hint，可参考可忽略）
{hint}

## 已生成的历史章标题（**严禁重复**任何一个）

{history_str}

## 正文（节选）

{body_sample}

---

请输出**一个**网文化标题（本章档位 **{tier}**，对应字数和风格见 system 段）。**绝不重复历史标题**。"""

    profile = loader.get_active_profile()
    client = OpenAI(api_key=profile.api_key, base_url=profile.base_url)
    _xb = reasoning_extra_body(profile)  # reasoning 控制·防 thinking 暴走 content 空(elysiver reasoning_effort)
    try:
        resp = client.chat.completions.create(
            model=profile.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=8000,  # reasoning 模型 thinking 占预算·留够正文(章标题短·8000 够 thinking+标题)
            temperature=0.8,
            **({"extra_body": _xb} if _xb else {}),
        )
        raw = resp.choices[0].message.content.strip()
        # 反元话语后处理 —— DeepSeek 等 reasoning model 易输出
        # 「好的，」「我们被要求」「让我」「首先」「这是」等思考链片段，必须剥离
        # 策略：按行/句切，过滤含元话语关键词的行，取最后一行/最短的标题候选
        META_PATTERNS = [
            r'^好的[，,]?', r'^我们?被要求', r'^让我', r'^首先', r'^第一步',
            r'^我需要', r'^这是', r'^以下是', r'^标题[：:]', r'^章标题[：:]',
            r'^我.{0,3}为', r'^用户要求', r'我们?生成一个', r'输出一个',
            r'^根据', r'^现在', r'^接下来', r'^考虑',
        ]
        # 按行/句分割
        candidates = re.split(r'[\n。]', raw)
        candidates = [c.strip().strip('「」"\'《》【】 *') for c in candidates if c.strip()]
        # 过滤掉含元话语开头的行
        clean = [c for c in candidates if not any(re.search(p, c) for p in META_PATTERNS)]
        # 优先取「短且不含元话语」的候选
        clean = [c for c in clean if c and len(c) <= 14]
        if clean:
            title = clean[-1]  # 取最后一个清洁候选（通常 deepseek 推理后才输出真标题）
        else:
            # fallback: 从原始 raw 末尾抠最后非元话语片段
            title = candidates[-1] if candidates else ''
            # 再剥离元话语前缀
            for pat in META_PATTERNS:
                title = re.sub(pat, '', title).strip('，,。：:、 ')
        title = re.sub(r'^第\s*[一二三四五六七八九十百千\d]+\s*章[ \t]*', '', title)
        if len(title) > 14:
            title = title[:14]
        # 终极后处理：若仍含元话语关键词，回退到干净 fallback（绝不用 storyboard 指令原文）
        if any(kw in title for kw in ['我们', '好的', '用户', '让我', '生成', '需要', '我们被']):
            return _clean_fallback_title(ch, hint)
        return title or _clean_fallback_title(ch, hint)
    except Exception as e:
        print(f"[ch{ch}] gen 失败: {e}", file=sys.stderr)
        return _clean_fallback_title(ch, hint)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--chapters', required=True, help='1-40 or 5,6,7')
    parser.add_argument('--high-chapters', default='',
                        help='显式标 high 档位的章（5%% 高潮章），如 11,40')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    project = Path(args.project).resolve()
    # 🔴 pending_tail 守卫：整稿 < 单章下限时 splitter 切 0 章·
    # 全退 pending_tail 等下 cluster 拼接（chapter_range=[]）→ data_flow 回填 --chapters "[]"/空。
    # 本 cluster 无章可命名 → no-op 放行（绝不让 parse_chapters("[]") 的 int("[]") 崩溃整条管线）。
    _raw_chapters = (args.chapters or "").strip().strip("[]").strip()
    if not _raw_chapters:
        print("[gen_chapter_titles] 本 cluster 0 章（整稿 < 单章下限·退 pending_tail 等下 "
              "cluster 拼接）·无章可命名·跳过", file=sys.stderr)
        sys.exit(0)
    chapters = parse_chapters(_raw_chapters)
    high_set = parse_high_list(args.high_chapters)
    print(f"[gen_chapter_titles v2] {len(chapters)} 章 / high={sorted(high_set)}")

    try:
        loader = GenModelLoader()
        active = loader.get_active_profile()
        print(f"[gen_chapter_titles] active: {active.name} ({active.model})", file=sys.stderr)
    except GenModelConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

    progress_path = project / '_数据库' / '进度.json'
    if not progress_path.exists():
        print(f"[ERROR] 进度.json 不存在: {progress_path}", file=sys.stderr)
        sys.exit(2)
    try:
        progress = json.loads(progress_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[ERROR] 进度.json 读取失败: {e}", file=sys.stderr)
        sys.exit(2)
    # cluster 模式只读 cluster_blueprint。cluster_blueprint 可能是 list（而非 dict），
    # 裸 .items() 会 AttributeError 崩——读 hint 前用 normalize_blueprint 归一成 dict 再迭代。
    if cluster_lookup is not None:
        _bp = cluster_lookup.normalize_blueprint(progress)
    else:
        _bp = progress.get('cluster_blueprint', {})
        if not isinstance(_bp, dict):
            _bp = {}
    hint_map = {}
    for cid, cdata in _bp.items():
        if not isinstance(cdata, dict):
            continue
        for p in cdata.get('scene_storyboard', []) or []:
            if isinstance(p, dict) and 'ch' in p:
                hint_map[p['ch']] = p.get('title', '')

    # 加载 per-book title_style 校准
    title_style = _load_title_style(project)
    if title_style:
        td = title_style.get("tier_distribution_pct", {})
        print(f"[gen_chapter_titles v2.4] per-book title_style 已加载："
              f"normal={td.get('normal', 0):.0%} / mid={td.get('mid', 0):.0%} / high={td.get('high', 0):.0%}")
    else:
        print(f"[gen_chapter_titles v2.4] 无 per-book title_style，走默认 80/15/5", file=sys.stderr)

    new_titles = {}
    history_titles: list[str] = []
    tier_counts = {'normal': 0, 'mid': 0, 'high': 0}

    for ch in chapters:
        p, body = read_chapter(project, ch)
        if not body:
            print(f"  ch{ch}: 文件不存在，跳过", file=sys.stderr)
            continue
        body_stripped = strip_existing_title(body)
        hint = hint_map.get(ch, '')
        changes = read_changes(project, ch)
        tier = classify_tier(ch, changes, high_set)
        tier_counts[tier] += 1

        if args.dry_run:
            print(f"  ch{ch} [{tier}] (hint='{hint}'): [dry-run]")
            continue

        title = gen_one_title(loader, ch, body_stripped, hint, tier, history_titles, title_style=title_style)
        new_titles[ch] = title
        history_titles.append(title)
        new_body = f"第{ch:03d}章 {title}\n\n{body_stripped}"
        p.write_text(new_body, encoding='utf-8')
        marker = {'normal': '·', 'mid': '★', 'high': '★★★'}[tier]
        print(f"  {marker} ch{ch} [{tier}]: 「{title}」 (hint:「{hint}」)")

    if not args.dry_run and new_titles:
        # cluster 模式只写 cluster_blueprint。写回必须 in-place 改持久化对象
        # （normalize_blueprint 会新建 dict，改它不落盘）。故直接按 cluster_blueprint
        # 真实形态原地改：
        #   dict → 遍历各 cluster 的 scene_storyboard
        #   list（逐章 scene 记录的旧项目遗留形态）→ 直接遍历列表项
        _raw_bp = progress.get('cluster_blueprint')

        def _apply_title(cp):
            if isinstance(cp, dict) and cp.get('ch') in new_titles:
                cp['_old_title'] = cp.get('title', '')
                cp['title'] = new_titles[cp['ch']]

        if isinstance(_raw_bp, dict):
            for cid, cdata in _raw_bp.items():
                if not isinstance(cdata, dict):
                    continue
                for cp in cdata.get('scene_storyboard', []) or []:
                    _apply_title(cp)
        elif isinstance(_raw_bp, list):
            for cp in _raw_bp:
                _apply_title(cp)
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2),
                                  encoding='utf-8')
        total = sum(tier_counts.values())
        print(f"\n[gen_chapter_titles] 完成 {total} 章 + 同步进度.json", file=sys.stderr)
        print(f"  分布: normal={tier_counts['normal']} ({100*tier_counts['normal']//total}%) / "
              f"mid={tier_counts['mid']} ({100*tier_counts['mid']//total}%) / "
              f"high={tier_counts['high']} ({100*tier_counts['high']//total}%)")


if __name__ == '__main__':
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
