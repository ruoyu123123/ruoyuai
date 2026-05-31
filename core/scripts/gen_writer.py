#!/usr/bin/env python3
"""
gen_writer.py — Gen-Model 正文生成器（OpenAI 兼容协议 /v1/chat/completions）

v27 freestyle 模式（默认）：
  python gen_writer.py \
    --project "workspace/novels/<book>" \
    --cluster 6
  → writer 不知道目标章数 · 按 cluster.scope_summary + scene_storyboard 自由发挥
  → 字数自然涌现（splitter 后期按 3000-4500/章 切，章数由内容决定）

v26 兼容模式（显式锁字数 · 仅用于回归测试）：
  python gen_writer.py \
    --project "workspace/novels/<book>" \
    --cluster 3 \
    --chapter-start 11 --chapter-end 15 \
    --target-cjk 13000-22000

读取 manifest + 风格 skill + 调研 cache + cluster_brief + 7 项硬约束，
组装 prompt，调当前 active 的 gen-model profile（OpenAI 兼容），
失败时按 fallback 链尝试下一个 profile，
写出 cluster draft + changes.json + 自动跑 scanner 校验。

配置：参见 .env 中 GEN__<name>__* 字段 + GEN_MODEL_ACTIVE。
管理：python core/scripts/gen_model.py list / switch / show / add
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 让本脚本可独立运行（同目录 import）
sys.path.insert(0, str(Path(__file__).parent))
from gen_model_loader import (  # noqa: E402
    GenModelLoader,
    GenModelConfigError,
    GenModelExhaustedError,
    Profile,
)
import chapter_io as cio  # noqa: E402 · CJK 计数 + changes schema 规范化权威口径
import cluster_lookup  # noqa: E402 · cluster_id 归一化（int 6 ↔ "cluster_006" ↔ "6"）
import snippet_seed  # noqa: E402 · 真实原文「语感种子」播种（env SNIPPET_SEED_MODE 默认 on · 2026-05-31 放量）


# ============ 依赖检查 ============
def check_deps():
    missing = []
    try:
        import openai  # noqa
    except ImportError:
        missing.append('openai')
    try:
        from dotenv import load_dotenv  # noqa
    except ImportError:
        missing.append('python-dotenv')
    if missing:
        print(f"[ERROR] 缺少依赖: {missing}", file=sys.stderr)
        print(f"  请运行: pip install {' '.join(missing)}", file=sys.stderr)
        sys.exit(2)


# ============ Max tokens 解析 ============
def load_model_capabilities_cache() -> dict:
    """加载 model_probe.py 写出的能力缓存"""
    cache_path = Path(__file__).parent.parent.parent / '.claude' / '.model_capabilities.json'
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def resolve_max_tokens(profile: Profile) -> tuple[int, str]:
    """决定 max_tokens 的优先级：
       1. profile.max_tokens 显式（最高）
       2. 缓存的 recommended_max_tokens_for_writing
       3. 保守默认值 16000
       返回 (max_tokens, source)
    """
    if profile.max_tokens is not None:
        return profile.max_tokens, 'profile_explicit'

    cache = load_model_capabilities_cache()
    caps = cache.get('model_capabilities', {}).get(profile.model)
    if caps:
        return caps.get('recommended_max_tokens_for_writing', 16000), f"cache:{caps.get('source','unknown')}"

    return 16000, 'default_fallback_16k_NO_PROBE_YET'


# ============ best-of-N 择优（写作端 · 非迭代规避同质化 · 2026-05-31）============
# 根因（本批任务说明 · arxiv 实证）：单稿直生 + av_judge 只事后单次诊断不回灌；self-refine
#   反复迭代会同质化（模型把自己的输出当锚反复收敛到同一坨）。best-of-N 走「N 稿并行生成
#   + 配对判别 + 综合择优」——selection（择优）≠ refine（迭代改），天然规避同质化。
# 怎么择优（advisory · 不黑箱 · 北极星⑤）：
#   ① SFS（style_evaluator.compute_style_only_sfs）= 统计指纹相似度（越高越像作者 · 透明可读）。
#   ② AV-judge 配对判别（av_judge.pairwise_drift_count）= 读者视角 4 维走味计数（越少越像）。
#   综合分 = SFS 归一 - 走味维度惩罚 → 排序取最高（SFS 当裁判透明 · AV-judge 只 select 不强判）。
# env BEST_OF_N：默认 2（active · N≥2 真生效）· 设 1 = 关（退回单稿直生 · 零回归逃生口）。
BEST_OF_N_DEFAULT = 2
BEST_OF_N_MAX = 5  # 上限防 token 失控（用户质量优先但不无限）
# 综合分：每个走味维度的惩罚（满分 100 的 SFS 尺度上扣多少 · 4 维全走味最多扣 40）。
AV_DRIFT_PENALTY_PER_DIM = 10.0


def _best_of_n() -> int:
    """读 env BEST_OF_N：默认 2（active 放量 · N≥2 真生效）· 1=关 · 钳到 [1, BEST_OF_N_MAX]。

    空 / 非法值 → 默认 2（与「默认全开 active」纪律一致：用户说默认关掉写它干什么）。
    设 BEST_OF_N=1 是唯一的关闭口（退回单稿直生 · 零回归）。
    """
    raw = (os.environ.get("BEST_OF_N") or "").strip()
    if not raw:
        return BEST_OF_N_DEFAULT
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return BEST_OF_N_DEFAULT
    if n < 1:
        return 1
    return min(n, BEST_OF_N_MAX)


def _candidate_temperatures(base_temp: float, n: int) -> list[float]:
    """为 N 个候选生成**有差异**的 temperature（多样性 = best-of-N 价值来源 · 非迭代）。

    第一稿用 profile 原始 temperature（保持基线行为不变 · 单稿等价）；后续候选在其上加阶梯
    抖动（+0.1, +0.2, …），钳到 [0.2, 1.2] 合理区间。温度差异让 N 稿真的不同（避免 N 个一样的稿
    白烧 token），同时不偏离作者风格太远（小步抖动 · 非大跨度）。
    """
    temps = [base_temp]
    step = 0.1
    for i in range(1, n):
        t = base_temp + step * i
        # 钳到合理写作温度区间（过低=刻板 / 过高=发散跑偏）
        t = max(0.2, min(1.2, round(t, 2)))
        temps.append(t)
    return temps


# ============ v27 freestyle helpers ============
def _infer_cluster_start_ch(project_root: Path, cluster_id: int) -> int:
    """v27 freestyle：从事件簇.json + 已写章节推导 cluster 起始章号

    优先级：
    1. 事件簇.json.clusters[N].chapter_range[0]（v26 schema · 向后兼容）
    2. 事件簇.json.clusters[N].ch_start（v27 新 schema）
    3. 上一 cluster 末章 + 1（从 章节/ 目录扫）
    4. cluster_001 = 1 兜底
    """
    db = project_root / '_数据库'
    ec_path = db / '事件簇.json'
    if ec_path.exists():
        try:
            ec = json.loads(ec_path.read_text(encoding='utf-8'))
            for c in ec.get('clusters', []):
                cid_raw = c.get('cluster_id', '')
                m = re.search(r'(\d+)', str(cid_raw))
                if m and int(m.group(1)) == cluster_id:
                    if c.get('ch_start'):
                        return int(c['ch_start'])
                    cr = c.get('chapter_range') or []
                    if cr and len(cr) >= 1:
                        return int(cr[0])
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    # 兜底：扫 章节/第NNN章/ 找最大章号 + 1
    chapters_dir = project_root / '章节'
    max_ch = 0
    if chapters_dir.exists():
        for p in chapters_dir.iterdir():
            m = re.match(r'第(\d+)章', p.name)
            if m:
                max_ch = max(max_ch, int(m.group(1)))
    if cluster_id == 1:
        return 1
    return max_ch + 1 if max_ch > 0 else 1


# ============ Prompt 组装 ============
def _collect_feedback_rules() -> str:
    """L3 防御：自动扫 ~/.claude/projects/.../memory/feedback_*.md，
    抽取 type=feedback 的全局规则段，注入 writer system prompt。

    设计目标：让 writer 在每段生成时都看到全局禁令（不依赖主代理记得）。
    抽取策略：取 lesson 文件的「## 规则」段（如有），否则取文件头部 1500 字。
    """
    try:
        memory_dir = Path.home() / ".claude" / "projects" / "D--Desktop-ruoyuai" / "memory"
        if not memory_dir.exists():
            return ""
        rules_chunks = []
        for f in sorted(memory_dir.glob("feedback_*.md")):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            # frontmatter 检查 type=feedback
            if "type: feedback" not in text:
                continue
            # 抽 "## 规则" 段或文件正文头部
            m = re.search(r"##\s*规则[\s\S]*?(?=\n##\s|\Z)", text)
            chunk = m.group(0) if m else text[text.find("---\n", 5) + 4:]
            chunk = chunk.strip()[:2500]
            if not chunk:
                continue
            rules_chunks.append(f"### 来自 {f.stem}\n\n{chunk}")
        if not rules_chunks:
            return ""
        header = "# 🔴 全局 feedback 规则（自动注入 · 来自 memory/feedback_*.md）\n\n"
        header += "以下是历史用户反馈沉淀的全局禁令，写作时**逐条遵守**。违反 = 出货后被打回 + lesson 复发。\n\n"
        return header + "\n\n---\n\n".join(rules_chunks)
    except Exception:
        return ""


def read_text(p: Path, limit_chars: int = None) -> str:
    if not p.exists():
        return f"[WARN] 文件不存在: {p}"
    t = p.read_text(encoding='utf-8')
    if limit_chars and len(t) > limit_chars:
        t = t[:limit_chars] + f"\n... [truncated at {limit_chars} chars]"
    return t


def _build_style_fingerprint_section(manifest_path: Path) -> str:
    """从 manifest.author_style_fingerprint 抽显式量化指令拼成 writer prompt 段。

    L1a 升格消费（2026-05-31）：build_manifest 在 PROFILE_INJECT_MODE=active 下注入
    多维量化风格指纹（句长/段长/单句独行/标点/虚词/签名搭配 + 显式 directives 文案）。
    本函数把 directives 升到 prompt 前部醒目位置 —— 实证「显式数值目标」> 让模型看样本自己悟。

    返回值：
      · 指纹缺失 / 为 None（PROFILE_INJECT_MODE=off/shadow）/ 无 directives → ""（不注入·零回归）。
      · 有 directives → 拼成「作者量化风格指纹」段（advisory · 标注可校准偏离·北极星⑤不硬锁）。
    """
    if not manifest_path.exists():
        return ""
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return ""
    fp = m.get('author_style_fingerprint')
    if not isinstance(fp, dict):
        return ""
    directives = fp.get('directives') or []
    if not directives:
        return ""
    src = fp.get('source', 'author_profile')
    n_ch = fp.get('n_chapters')
    src_note = f"（数据源 {src}" + (f" · {n_ch} 章聚合" if n_ch else "") + "）"
    lines = [
        "## 🎯 作者量化风格指纹（写作时显式校准的多维目标硬数字 · advisory）",
        "",
        f"以下是从作者风格档蒸馏出的**量化风格目标**{src_note}。实证表明把这些数值**显式告知你**"
        "比让你看样本自己悟更有效。**逐条对照校准你的节奏**——它们是目标不是硬锁，",
        "遇到本场景必要的偏离（如高潮处句长骤变）可偏离，但默认贴合：",
        "",
    ]
    for d in directives:
        lines.append(f"- {d}")
    return "\n".join(lines)


def _ctx_reorder_mode() -> str:
    """写作上下文位置重排开关（env CTX_REORDER_MODE · 默认 active · P0）。

    lost-in-the-middle / RoPE recency 实证：long-context 中段注意力最弱（U 型），
    紧贴生成点（prompt 末尾）注意力最强。现状 build_prompt 把**第一权威风格 skill**
    落在 U 型最低的中段，而紧贴生成点「现在请写正文」之前的是 manifest 事实索引
    （非风格锚）→ 位置层北极星偏移。

    active（默认）：把风格 skill + 语感种子锚移到 prev_ch 之后、「现在请写正文」之前
                    （生成点近邻 RoPE 高位）；manifest 事实索引留中段。
    off / shadow：保持原版 join 顺序（零回归回退路径）。
    """
    return (os.environ.get("CTX_REORDER_MODE") or "active").strip().lower()


def _skill_primacy_mode() -> str:
    """skill 硬约束 primacy 重排开关（env SKILL_PRIMACY_MODE · 默认 active · 第8轮同族补完）。

    IFScale 实证：长指令文档里的硬约束维（段长契约 / 禁用词 / 对话格式）在 skill **中段衰减**
    （指令越多、文档越长，中段那几条越容易被模型"读过即忘"）。现状第8轮 ctx 重排把整个风格 skill
    下沉到生成点近邻，但 skill 内部仍是一大段连续文本——里面的硬约束维没有被 **primacy 强调**，
    长 skill 中段那几条照样衰减。

    active（默认）：在生成点近邻（风格锚区）补一段**精简硬约束 primacy 重述**——只点名 3 个最易
                    衰减的硬约束维（段长契约 / 禁用词 / 对话格式），轻量重述非整 prompt 复制
                    （黑箱零成本），把硬约束维 primacy 提到显著位置。
    off / shadow：不注入该段（零回归回退路径）。
    """
    return (os.environ.get("SKILL_PRIMACY_MODE") or "active").strip().lower()


def _build_hard_constraint_primacy_block() -> str:
    """生成点近邻的「硬约束维 primacy 重述」段（SKILL_PRIMACY_MODE · 默认 active）。

    轻量重述（非整 prompt / 整 skill 复制 · 黑箱零成本）：只点名 3 个在长 skill 中段最易衰减的
    硬约束维——段长契约 / 禁用词 / 对话格式——贴生成点 RoPE 高位强调，对抗 IFScale 中段衰减。
    advisory 措辞（北极星⑤不硬锁 · 以作者风格档为第一权威，本段只是把"已在 skill 里写过的硬约束维"
    提到显著位置重申，不新增规则、不覆盖 skill）。

    off/shadow → ""（不注入 · 零回归）。
    """
    if _skill_primacy_mode() != "active":
        return ""
    return (
        "## ⚙️ 硬约束维 primacy 重述（生成点近邻强调 · advisory · 不覆盖上方风格 skill）\n"
        "\n"
        "下面 3 个维度在长风格档里最容易被『读过即忘』（IFScale 中段衰减），写之前再对齐一遍——"
        "**以上方作者风格 skill 的具体规定为准**，本段只是把这 3 条提到显著位置重申，不新增规则：\n"
        "\n"
        "- **段长契约**：贴合作者风格 skill 规定的段长 / 单句独行节奏；skill 未规定时默认非对话段"
        "一段只收一个句末结束符（。！？……），看到一段堆 ≥2 句立刻拆段。\n"
        "- **禁用词**：结构性 AI 套话（与此同时 / 值得一提的是 / 不仅如此 / 事实上）零容忍；"
        "工艺签名词以作者风格 skill 的 signature 为准（skill 列了就是作者笔法，没列就默认避免）。\n"
        "- **对话格式**：引号样式、对话独行、口癖停顿都沿用作者风格 skill 与人物卡 voice_pack，"
        "全篇统一不漂移。"
    )


def build_prompt(project_root: Path, cluster_id: int, ch_start: int,
                 ch_end: int = None, target_cjk: str = None) -> tuple:
    """组装 system + user prompt

    v27 freestyle：ch_end/target_cjk 可缺省。
    - ch_end 缺 → user prompt 不暴露目标章数（writer 不知道目标章数）
    - target_cjk 缺 → 不注入「目标字数」段（让 AI 按 scope_summary 自由产出 · 自然涌现）
    - 每章字数硬范围（3000-4500）由 splitter 按字数切时执行；writer 不预设章数/每章字数（v27 freestyle · 原 system prompt 铁律 #4 已删）
    """
    db = project_root / '_数据库'
    freestyle = (ch_end is None)

    # 读取核心资料
    # 2026-05-29 北极星复审 L2：去 load-time 截断（原 30000/25000 仍砍大文件，与
    # feedback_no_token_saving「全量传 LLM」+ 作者档第一权威冲突；诡异接待处 skill 34575 字被砍 9k+）。
    # 全量读——作者风格 skill 是写作第一权威，不得在 load 时截断。
    manifest_path = db / '.manifest' / f'ch_{ch_start:03d}.json'
    manifest = read_text(manifest_path)

    # L1a 升格消费（2026-05-31）：build_manifest 在 env PROFILE_INJECT_MODE=active 下产出
    # manifest.author_style_fingerprint（多维量化目标硬数字 + 显式指令文案），但 writer 此前
    # 只把整 manifest 当 raw text 塞进 prompt 尾部「数据库索引」段 —— 量化指纹深埋 JSON 里
    # writer 难以识别为「写作目标」。这里**显式解析**该字段，单独拼成醒目的「作者量化风格指纹」
    # 段注在 prompt 前部（advisory · 北极星⑤不硬锁），让 writer 真消费多维数值目标。
    style_fp_section = _build_style_fingerprint_section(manifest_path)

    # 风格 skill（全量，不截断）
    style_skill = read_text(db / '作者风格_skill.md')

    # 调研 cache（找最新的）
    cache_dir = db / '.research_cache'
    cache_text = ""
    if cache_dir.exists():
        caches = sorted(cache_dir.glob(f'inspiration_cluster_{cluster_id:03d}_*.md'),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        if not caches:
            caches = sorted(cache_dir.glob('inspiration_*.md'),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if caches:
            cache_text = read_text(caches[0])  # 2026-05-30 北极星：去截断，全量传 LLM（feedback_no_token_saving）

    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
    progress = json.loads((db / '进度.json').read_text(encoding='utf-8'))
    cluster_blueprint = progress.get('cluster_blueprint', {})
    plans = []
    # cluster_id 是 int（如 6），blueprint key 可能是 "cluster_006"/"6"/"cluster_6"
    # → 两边归一化为 'cluster_NNN' 后匹配（修复类型不匹配导致 storyboard 注入失效）
    _norm_cid = cluster_lookup.normalize_cluster_id(cluster_id)
    if _norm_cid:
        # 2026-05-29 复审复修 SC-1：cluster_blueprint 可能是 list（城南实测 list(25)），
        # 裸 .items() 会 AttributeError 崩 writer 路径。先 normalize_blueprint 归一成 dict 再迭代。
        for _bp_key, _bp_val in cluster_lookup.normalize_blueprint(cluster_blueprint).items():
            if cluster_lookup.normalize_cluster_id(_bp_key) == _norm_cid:
                plans = _bp_val.get('scene_storyboard', [])
                break
    if freestyle:
        relevant_plans = [p for p in plans if ch_start <= p.get('ch', 0) <= ch_start + 30]
    else:
        relevant_plans = [p for p in plans if ch_start <= p.get('ch', 0) <= ch_end]
    plan_text = json.dumps(relevant_plans, ensure_ascii=False, indent=2) if relevant_plans else "[]（v27 freestyle · 完全按 cluster_brief.scene_storyboard 自由发挥）"

    # 人物卡（全量，不截断——含 voice_pack 是声纹复刻第一依据，截断 = 后登场角色声纹丢失）
    char_card = read_text(db / '人物卡.json')

    # 用户偏好（全量，不截断）
    pref = read_text(db / '用户偏好.json')

    # v22.gov.align.fix Gap T2-X: 读 事件簇.json 找当前 cluster 的 brief 注入 prompt
    # 之前 build_prompt 完全没读 事件簇.json，导致 cluster.scope_summary 硬约束未注入 → LLM 自由发挥跑偏 task
    cluster_brief = {}
    cluster_brief_text = ""
    cluster_hard_constraints_text = ""
    try:
        ec_path = db / '事件簇.json'
        if ec_path.exists():
            ec = json.loads(ec_path.read_text(encoding='utf-8'))
            for c in ec.get('clusters', []):
                # cluster_id 是 int，事件簇 c['cluster_id'] 是字符串 → 两边归一化后比对
                if cluster_lookup.normalize_cluster_id(c.get('cluster_id')) == cluster_lookup.normalize_cluster_id(cluster_id):
                    cluster_brief = c
                    break
            if cluster_brief:
                cluster_brief_text = json.dumps(cluster_brief, ensure_ascii=False, indent=2)
                # 从 scope_summary 提取硬约束（≥/≤/百分比/角色数等）
                scope = cluster_brief.get('scope_summary', '')
                ewr = cluster_brief.get('expected_word_range', {})
                constraints = []
                if scope:
                    # 简单提硬约束关键词：「≥ N」「≤ N」「占比」「至少」「不少于」「角色」
                    import re as _re
                    for m in _re.finditer(r'(?:至少|不少于|≥)\s*(\d+)\s*([个名位章字]?)', scope):
                        n = m.group(1); unit = m.group(2) or '项'
                        constraints.append(f"- 至少 {n} {unit}（出自 scope_summary）")
                    for m in _re.finditer(r'(?:不超过|至多|≤)\s*(\d+)\s*([个名位章字]?)', scope):
                        n = m.group(1); unit = m.group(2) or '项'
                        constraints.append(f"- 至多 {n} {unit}（出自 scope_summary）")
                    for m in _re.finditer(r'(?:占比|比例)\s*[≥>]\s*(\d+)\s*%?', scope):
                        constraints.append(f"- 比例 ≥ {m.group(1)}%（出自 scope_summary）")
                    for m in _re.finditer(r'(?:占比|比例)\s*[≤<]\s*(\d+)\s*%?', scope):
                        constraints.append(f"- 比例 ≤ {m.group(1)}%（出自 scope_summary）")
                # v27：freestyle 时跳过 cluster.expected_word_range（让 AI 自由发挥）
                # v26 兼容：显式传 ch_end + cluster.expected_word_range 存在时仍注入字数硬约束
                if (not freestyle) and ewr and ewr.get('min') and ewr.get('max'):
                    constraints.append(f"- 字数硬约束：{ewr['min']}-{ewr['max']} {ewr.get('unit', 'CJK_chars')}（cluster.expected_word_range · 必遵守，写完自查不达标即重写）")
                # 额外读 cluster_brief 的 hard_constraints 字段（v22.gov.align.fix 新 schema）
                for hc in cluster_brief.get('hard_constraints', []) or []:
                    if isinstance(hc, dict):
                        metric = hc.get('metric', '?')
                        if 'min' in hc:
                            constraints.append(f"- {metric} ≥ {hc['min']}（cluster.hard_constraints · 必遵守）")
                        if 'max' in hc:
                            constraints.append(f"- {metric} ≤ {hc['max']}（cluster.hard_constraints · 必遵守）")
                if constraints:
                    cluster_hard_constraints_text = "\n".join(constraints)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[gen_writer] 事件簇.json 读取失败（不阻塞）: {e}", file=sys.stderr)

    # 前一章末尾（用于衔接，如果 ch_start > 1）
    prev_ch_section = ""
    if ch_start > 1:
        prev_p = project_root / '章节' / f'第{ch_start-1:03d}章' / f'第{ch_start-1:03d}章.txt'
        if prev_p.exists():
            prev_text = prev_p.read_text(encoding='utf-8')
            prev_ch_section = "## 前一章末尾（衔接用）\n\n" + prev_text[-1500:]

    # L3 防御（feedback_no_screenplay_stage_directions_in_novels 等）：
    # 自动扫 memory/feedback_*.md，把 type=feedback 的全局规则注入 writer prompt 头部
    feedback_rules_text = _collect_feedback_rules()

    # 🎴 真实原文「语感种子」播种（P0 · env SNIPPET_SEED_MODE 默认 on · 2026-05-31 放量 · 真生效）：
    # 注 1-2 段作者真实原文当语感锚点，防长 cluster 中后段退化回通用 AI 腔。
    # 按 cluster.scope_summary 的风格/情绪寄存器选样（非题材匹配 · Catch Me 论文避坑），
    # 并带「只借语感起手势 · 绝不抄情节内容」避坑指令（防抄袭+防内容泄漏）。
    _scope_for_seed = cluster_brief.get('scope_summary', '') if cluster_brief else ''
    seed_section, seed_trace = snippet_seed.make_seed_block_for_writer(
        project_root, scope_text=_scope_for_seed)

    # 2026-05-29 北极星修复 [H3-write]：终极目标=写出和【该作者】风格一致的文章。
    # 故 system prompt 第一权威是「作者风格档(下方风格 skill)」，不是写死的通用爽文工艺。
    # 铁律分两层：① 常驻硬铁律(格式/世界观/穿帮防护·任何风格都不可破·不可被 skill 覆盖)；
    # ② 风格工艺默认基线(仅当作者 skill 未规定该维度时兜底·skill 规定了则以 skill 为准)。
    # 删除原写死「冷峻俯瞰」默认风 + 原 rule4「单章字数 2500-5000」(违反 freestyle「writer 不知章数」)。
    system = (feedback_rules_text + "\n\n") if feedback_rules_text else ""
    system += """你是长篇小说的写作引擎。**最高准则：复刻下方「风格 skill / 作者风格档」描述的那位作者的写法**——句式节奏、用词偏好、signature 笔法、情绪处理、对话风格都要像那位作者。你的任务是写一个故事块（cluster）的完整正文，覆盖多章。

# 第一权威 = 作者风格档（下方「风格 skill」段）
- 风格 skill 里规定的句长 / 段长 / 对话占比 / 签名词 / 开篇收束手法 = **第一权威**，凡 skill 写了的维度，**以 skill 为准**，下方「风格工艺默认基线」在该维度自动让位。
- 风格 skill 未规定的维度，才用下方默认基线兜底。
- 没有任何写死的「默认文风」——你的文风由作者风格档决定，不是某种预设爽文腔。

# 一、常驻硬铁律（格式/世界观/穿帮防护 · 任何风格都不可破 · skill 不能覆盖）

## H1. 真实世界时间 0 容忍
禁用 20XX 年 / 公元 / 月份名 / 星期几。用「沙盒第 N 周期」「寒月第十七日」「异常历 X 年」等（除非作者风格档/世界观明确是现实题材）。

## H2. 不写双标点
不写「，。」/「。，」等双标点错误。

## H3. AI 结构套话零容忍（结构性机器腔 · 与作者签名词无关）
禁用：与此同时 / 值得一提的是 / 不仅如此 / 事实上。这 4 个是结构性 AI 腔，任何作者都不会用，skill 不能放行。
另：「然而」高频转折 = 机器腔（偶用可，避免每段用「然而」起转折——与 CLAUDE.md 反 AI 腔基线一致）。
（注：顿时/淡淡/仿佛/似乎/缓缓地说 等是「工艺/签名词」，归第二层——若作者风格档把它们列为签名笔法则允许。）

## H4. cluster 契约（user prompt 顶部「CLUSTER 硬约束」段如有）
scope_summary 描述的场景类型/角色构成是**剧情硬契约**（如"对白场景"应有足够角色 + 对话为主），不可跑偏成别的场景。这是「写对剧情」不是「写某种文风」。

# 二、风格工艺默认基线（仅当作者 skill 未规定该维度时兜底 · skill 规定了以 skill 为准）

## C1. voice 工艺 applies_to 边界（默认）
若 skill 未另规定：voice_pack 的极短句节奏只用于对话/笔注；叙述段 1-3 句/段为常态，段长 < 80 字时避免句号 ≥ 3 个的碎句堆叠。
（错误范式：「那只手不是他的。手大。粗。」正确范式：「那只手不是他的，手大而粗。」——但若作者风格档本身就是短句碎切流，以 skill 为准。）

## C2. 指示性名词节制（默认）
「那只手」「那个声音」等指示短语 5 段内同 token 避免 ≥ 4 次（除非 skill 标为刻意复沓手法）。

## C3. 段首主语多样（默认）
角色名/代词段首避免连续 ≥3 段相同；用省主语 / 部位代指 / 动作起头 / 物件起头替代（除非 skill 标为排比手法）。

## C4. 破折号节制（默认）
破折号 ≤ 8/千字（除非 skill 偏好高频破折号）。

## C5. 工艺/签名禁用词（默认 · 作者档可豁免）
默认避免：顿时 / 紧锁 / 显然 / 淡淡 / 此刻 / 仿佛 / 似乎 / 缓缓地说 / 沉吟片刻 / 心中一凛 / 微微挑眉 / 嘴角勾起一抹。
**但若作者风格档的 signature / golden_passages 表明该作者惯用其中某些词，则它们是作者签名笔法，不在此限**——复刻作者优先于通用反 AI 腔。

# 元 anti-slop 防御（已知重犯模式 · 默认基线）

- **poetry-mode 短句三连**：禁用「第一遍 / 第二遍 / 第三遍」「那一瞬 / 那一瞬 / 他记着那一瞬」类 3+ 段独立短段含同短语
- **signature 短语暴涨**：voice signature 单章 ≤ 2 次，跨章不暴涨
- **元-vocab disclaimer**：「他没有这个词」「他不知道这是 X」类全 cluster ≤ 2 次
- **否定动作三件套**：「没说话/没出声/没接腔」单 cluster ≤ 25 次
- **章末抒情模板**：禁写「他不再是 X 的那个 X 了」类抒情收束

# 输出格式（DCAS 模式 · 用户明确偏好）

**核心原则：你输出的是「一整块连续叙事正文」，给后续 chapter-splitter 决定章节自然截断点的素材。**

**严禁预设章节分界**：
- ❌ 不要写「第 N 章 标题」/「第N章 标题」等章节标记
- ❌ 不要用「——」或其他分章分隔符把故事切开
- ❌ 不要写 Markdown 标题（# / ## 等）
- ❌ 不要写「以下是」/「故事开始」等元话语

**正确做法**：把整个故事块当一篇长散文写。场景之间用**自然过渡**（空行 / 时间标记句 / 视角切换句）衔接，**不打章节标签**。splitter 后期会根据自然截断点（场景结束 / 时间跳跃 / POV 切换 / 情绪峰值回落）切分章节并各自命名。

为什么这样：writer（你）擅长连续叙事的内在节奏；splitter（另一个 agent）擅长判断章节边界。预设章节边界 = writer 为「章末必须有钩子」强行设计信息炸弹结尾 = 显得刻意。让 splitter 在你写完后选自然截断点 = 章节边界看起来像页面物理限制而非刻意叙事设计。

完成正文后再输出 JSON 格式的 CHANGES 部分（用 ```json ... ``` 包裹）。
"""

    # v22.gov.align.fix Gap T2-X: cluster 硬约束段（顶部显著位）
    cluster_constraints_section = ""
    if cluster_brief_text:
        cluster_constraints_section = f"""## ⚠️ CLUSTER 硬约束（最高优先级 · 违反即重写）

cluster_brief 完整内容：
```json
{cluster_brief_text}
```

### 🎯 必遵守硬指标清单（从 cluster.scope_summary + expected_word_range + hard_constraints 提取）

{cluster_hard_constraints_text if cluster_hard_constraints_text else '（本 cluster 无硬约束 · 按一般指南）'}

**写完正文必自查**：上述硬指标**任一**不达标 → 视为重大失败，自查 JSON 中明确标 `cluster_constraints_violated: true`。
**scope_summary 不是参考，是契约**：cluster.scope_summary 描述的场景类型（如"对白场景"）、角色数（如"≥3 角色"）、占比（如"对话占比 ≥60%"）等是**硬契约**，不是建议。

---

"""

    # v27 freestyle vs v26 兼容：章数 + 目标字数描述
    if freestyle:
        task_intro = f"""# 写作任务

为本项目 cluster_{cluster_id:03d} 写**一整块连续叙事**。

**🎯 v27 自由发挥模式**：
- 你**不知道**目标章数（章数由 splitter 后期按 3000-4500 CJK/章 自然切，章数由你写的内容多少决定）
- 你**不知道**目标总字数（按 cluster.scope_summary + scene_storyboard 写够即可，不必凑字数）
- **专注做对的事**：写完 cluster_brief.scope_summary 描述的所有场景 + 兑现 foreshadowing_to_plant
- 自然结尾即止 — 写完 cluster 主线就停（一般 12000-25000 CJK 是健康范围，不强求）

⚠️ **重要提醒**：你输出的是**一整块叙事**，不是分好章的成品。**严禁**写「第 N 章 标题」/「——」分章符。把整个故事块当一篇长散文写，场景之间自然过渡。
"""
    else:
        task_intro = f"""# 写作任务

为本项目 cluster_{cluster_id:03d} 写**一整块连续叙事**（预计后续 splitter 切成 ch{ch_start}-ch{ch_end} 共 {ch_end - ch_start + 1} 章，但**你不要预先分章**）。

**目标字数**: {target_cjk} CJK（整块总字数；splitter 后每章自然落在 2500-5000）

⚠️ **重要提醒**：你输出的是**一整块叙事**，不是分好章的成品。**严禁**写「第 N 章 标题」/「——」分章符。把整个故事块当一篇长散文写，场景之间自然过渡。
"""

    # 种子段拼接（mode=off/shadow 时 seed_section 为空 → 不注入 · 零回归）
    seed_block = (seed_section + "\n\n") if seed_section else ""
    # 作者量化风格指纹段（PROFILE_INJECT_MODE=off/shadow 或无指纹时为空 → 不注入 · 零回归）
    style_fp_block = (style_fp_section + "\n\n") if style_fp_section else ""
    # 硬约束维 primacy 重述段（SKILL_PRIMACY_MODE=off/shadow 时为空 → 不注入 · 零回归）
    primacy_section = _build_hard_constraint_primacy_block()
    primacy_block = (primacy_section + "\n\n") if primacy_section else ""

    # ── 风格 skill 段（第一权威）+ 语感种子锚 ──
    style_skill_section = f"""## 风格 skill

{style_skill}"""

    # CTX_REORDER（P0 · 位置层北极星偏移修）：lost-in-the-middle / RoPE recency 实证——
    # context 中段注意力最弱（U 型），紧贴生成点（prompt 末尾）注意力最强。
    # 原版 join 把**第一权威风格 skill**落在中段最低注意力区，而紧贴生成点的是 manifest
    # 事实索引（非风格锚）= 位置偏移。active 模式把风格 skill + 语感种子锚移到 prev_ch 之后、
    # 「现在请写正文」之前的生成点近邻（RoPE 高位），manifest 事实索引留中段。
    # 正交于 D1-D9 / snippet 种子（那些管「注什么内容」）——本开关只管「注在哪个位置」。
    reorder_active = (_ctx_reorder_mode() == "active")
    if reorder_active:
        # 中段：cluster_blueprint + 人物卡 + 调研 cache + 用户偏好 + manifest（事实索引）
        # 风格 skill + seed 锚下沉到 prev_ch 之后、生成点之前。
        #
        # 第8轮同族补完（style_fp 下沉 + 硬约束 primacy）：第8轮 ctx 重排只下沉了风格 skill + seed 锚，
        # 量化指纹 style_fp_block 仍留在 prompt 顶部（task_intro 后），落在 lost-in-the-middle dead zone。
        # 量化数值约束（句长/段长/对话占比目标）比叙述性 skill 更怕稀释（4 源验证），现把 style_fp_block 也
        # 下沉到生成点近邻（RoPE 高位 · 风格锚区），紧跟风格 skill 之后；再补一段硬约束维 primacy 重述
        # （IFScale 实证：长 skill 中段硬约束维衰减）。三者都贴生成点，顺序：
        #   prev_ch → seed → 风格 skill → 量化指纹 → 硬约束 primacy → 生成点。
        prev_then_anchor = f"""{prev_ch_section}

{seed_block}{style_skill_section}

{style_fp_block}{primacy_block}"""
        user = f"""{task_intro}
{cluster_constraints_section}## cluster_blueprint（必落 anchors）

```json
{plan_text}
```

## 人物卡（必读 voice_pack）

```json
{char_card}
```

## 调研 cache（写作前必读 synthesis）

{cache_text}

## 用户偏好

{pref}

## manifest（数据库索引）

{manifest}

{prev_then_anchor}

---

# 现在请写正文"""
    else:
        # off / shadow：原版 join 顺序（零回归回退路径）
        user = f"""{task_intro}
{cluster_constraints_section}{style_fp_block}{seed_block}## cluster_blueprint（必落 anchors）

```json
{plan_text}
```

## 人物卡（必读 voice_pack）

```json
{char_card}
```

{style_skill_section}

## 调研 cache（写作前必读 synthesis）

{cache_text}

## 用户偏好

{pref}

## manifest（数据库索引）

{manifest}

{prev_ch_section}

{primacy_block}---

# 现在请写正文"""

    # 生成点尾部（两个 join 分支共用 · 紧贴生成点的指令 + 自查项）
    gen_point_tail = f"""

{"按 7 项硬铁律 + 元 anti-slop 防御 · 完整覆盖 cluster_brief 的所有 scene_storyboard 自由发挥（章数由 splitter 后期切，你不必管）。" if freestyle else f"按 7 项硬铁律 + 元 anti-slop 防御，写 {ch_end - ch_start + 1} 章完整故事块。"}

**自查项**（写完后请在 CHANGES JSON 里自报）：
- word_count_cjk
- paragraph_count
- narrative_paras_with_short_sentence_overuse（≤ 0）
- max_demonstrative_noun_repeat_in_5para_window（≤ 3）
- paragraph_head_subject_3plus_streak_count（≤ 0）
- dash_count / dash_per_thousand（≤ 8）
- meta_vocab_disclaimer_count（≤ 2）
- negation_action_count（≤ 25）
- story_block_ch_range
- facts_locked
- foreshadowing_planted / foreshadowing_paid（**每条务必带 id 字段，引用 cluster_brief.foreshadowing_to_plant 或 manifest 待回收伏笔的真实 fs_id——save_state 据 id 更新伏笔表 resolved 状态；无对应 fs_id 的新伏笔可只给 desc**）

现在开始写。"""

    user += gen_point_tail

    return system, user, seed_trace


# ============ Gen-Model 调用（含 fallback 链） ============
def _stream_once(client, profile, system: str, user: str, max_tokens: int,
                 prior_assistant: str | None = None) -> tuple[str, "str | None"]:
    """单次 stream 生成，返回 (text, finish_reason)。

    2026-05-30 加强：捕获 finish_reason（原循环只累加 content，从不读 finish_reason，
    导致命中 max_tokens 的截断被静默吞掉 → freestyle 长草稿半截入库）。
    prior_assistant 非空 → 续写模式（把已生成内容回填，要求接着写不重复）。
    """
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    if prior_assistant:
        messages.append({"role": "assistant", "content": prior_assistant})
        # CTX_REORDER（P0 · 位置层北极星偏移修）：截断续写回合原本只有「接着写别重复」，
        # 风格 skill / 语感锚全落在最初那条 user（已被 prior_assistant 推到中段 U 型最低注意力区），
        # 续写生成点近邻没有任何风格约束 → recency 漂移（越续越退化回通用 AI 腔）。
        # 这里在续写指令里补一行**精简风格锚**（签名句长 / 对话格式 / 禁结构套话 3 条），
        # 贴生成点 RoPE 高位重申最关键约束，防长草稿尾段风格崩塌。advisory 性质（不硬锁）。
        cont_msg = ("上一条回复因长度上限被截断了。请接着上文最后一个字继续往下写，"
                    "不要重复已经写过的内容、不要重新开头，直接续写后续正文"
                    "（如果正文已写完，就补上结尾的 CHANGES JSON 块）。")
        if _ctx_reorder_mode() == "active":
            cont_msg += (
                "\n\n续写仍须贴合作者风格档：① 句长 / 段长节奏沿用前文（别越写越碎或越堆长）；"
                "② 对话格式与前文一致（同款引号、对话独行）；"
                "③ 严禁结构性 AI 套话（与此同时 / 值得一提的是 / 不仅如此 / 事实上）。")
        messages.append({"role": "user", "content": cont_msg})
    stream = client.chat.completions.create(
        model=profile.model, messages=messages, max_tokens=max_tokens,
        temperature=profile.temperature, stream=True,
    )
    text = ""
    finish_reason = None
    for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        piece = getattr(choice.delta, 'content', None)
        if piece:
            text += piece
            sys.stderr.write(piece)
            sys.stderr.flush()
        if getattr(choice, 'finish_reason', None):
            finish_reason = choice.finish_reason
    return text, finish_reason


# API 调用健壮性常量（2026-05-30 加固）
GEN_MODEL_TIMEOUT = 180.0  # 与 ai_wrapper.py:154 对齐
GEN_MODEL_MAX_RETRIES = 3  # 同 profile 限流/超时的有限重试次数
GEN_MODEL_RETRY_BASE_DELAY = 2.0  # 指数退避基础秒数（2,4,8）


def call_gen_model(loader: GenModelLoader, system: str, user: str) -> tuple[str, Profile]:
    """调当前 active profile；失败时按 fallback 链尝试。

    返回 (full_text, used_profile)。
    抛 GenModelExhaustedError（active + 整条 fallback 链全失败）。

    2026-05-30 加固：
      · OpenAI client 显式 timeout（对齐 ai_wrapper）防止无限挂起。
      · RateLimitError / APITimeoutError 在**同 profile** 做有限指数退避重试（再降级 fallback），
        避免一次 429/超时就降级到次优模型。
      · HTTP 200 但零 content（内容过滤 / reasoning model 全进 reasoning_content / 空输出）
        视为失败 → 切下一 profile；全链皆空才 raise（杜绝写空草稿报成功）。
    """
    import time

    from openai import OpenAI

    try:
        from openai import APITimeoutError, RateLimitError
    except ImportError:  # 极旧 SDK 兜底（不应发生 · openai>=1.x 均有）
        APITimeoutError = RateLimitError = ()

    candidates = loader.get_callable_profiles()
    failures: list[tuple[str, str]] = []

    for i, profile in enumerate(candidates):
        max_tokens, mt_source = resolve_max_tokens(profile)
        if i == 0:
            print(f"[gen_writer] 调用 active profile: {profile.name} "
                  f"({profile.model} @ {profile.base_url})", file=sys.stderr)
            print(f"[gen_writer] max_tokens={max_tokens} (source: {mt_source})",
                  file=sys.stderr)
            print(f"[gen_writer] temperature={profile.temperature}", file=sys.stderr)
        else:
            print(f"\n[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"[gen_writer] prompt size: system={len(system)} chars, user={len(user)} chars",
              file=sys.stderr)

        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url,
                        timeout=GEN_MODEL_TIMEOUT)
        full_text = ""
        try:
            # 同 profile 内：限流/超时做有限指数退避重试，其余异常立即降级 fallback
            attempt = 0
            while True:
                try:
                    full_text, finish_reason = _stream_once(client, profile, system, user, max_tokens)
                    break
                except (RateLimitError, APITimeoutError) as re_err:
                    attempt += 1
                    if attempt > GEN_MODEL_MAX_RETRIES:
                        raise  # 重试耗尽 → 落到外层 except → 降级 fallback
                    delay = GEN_MODEL_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    print(f"\n[gen_writer] ⚠️ {profile.name} 限流/超时 "
                          f"({type(re_err).__name__})，{delay:.0f}s 后同 profile 重试 "
                          f"{attempt}/{GEN_MODEL_MAX_RETRIES}…", file=sys.stderr)
                    time.sleep(delay)
            # 截断检测 + 自动续写（finish_reason == "length" = 命中 max_tokens 被截断）
            cont_rounds = 0
            while finish_reason == "length" and cont_rounds < 3:
                cont_rounds += 1
                print(f"\n[gen_writer] ⚠️ 输出截断(finish_reason=length)，自动续写第 {cont_rounds}/3 轮…",
                      file=sys.stderr)
                cont_text, finish_reason = _stream_once(
                    client, profile, system, user, max_tokens, prior_assistant=full_text)
                full_text += cont_text
            if finish_reason == "length":
                print(f"\n[gen_writer] ⚠️ WARN 续写 {cont_rounds} 轮后仍可能未写完"
                      f"（草稿尾部/CHANGES 块可能不完整 · 下游 cjk 偏短检查兜底）", file=sys.stderr)
        except Exception as e:
            reason = str(e)[:200]
            print(f"\n[FALLBACK] {profile.name} 调用失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue  # 切下一个 profile

        # 空响应守卫：HTTP 200 但零 content（内容过滤 / reasoning model 全进 reasoning_content）
        # 视为失败，切下个 profile（与 except 路径对齐），杜绝写空草稿报成功。
        if not full_text.strip():
            reason = "返回空内容（HTTP 200 但零 content · 可能内容过滤/reasoning model 全进 reasoning_content）"
            print(f"\n[FALLBACK] {profile.name} {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue  # 切下一个 profile

        # 成功
        print(f"\n[gen_writer] 接收完毕 ({len(full_text)} chars) via {profile.name}",
              file=sys.stderr)
        return full_text, profile

    # 全链失败
    raise GenModelExhaustedError(failures)


# ============ best-of-N：生成 N 稿 + 配对重排 + 综合择优 ============
def gather_author_ref_text(project_root: Path, max_chars: int = 6000) -> str:
    """定位作者真实原文当 SFS / AV-judge 的锚（best-of-N 择优用 · 找不到则空）。

    复用 snippet_seed.resolve_originals_dir 的同款原文池定位（项目自带 原文/ → 风格库 原文/），
    取若干章拼成参考文本（截到 max_chars 控 SFS / judge token）。找不到原文池 → 返回 ""，
    调用方据此优雅降级（无锚则跳 best-of-N · 退回单稿 · 不报错 · 不阻断写作）。
    """
    try:
        originals = snippet_seed.resolve_originals_dir(project_root)
    except Exception:
        originals = None
    if not originals or not originals.exists():
        return ""
    chunks: list[str] = []
    total = 0
    for p in sorted(originals.glob("*.txt")):
        try:
            t = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not t:
            continue
        chunks.append(t)
        total += len(t)
        if total >= max_chars:
            break
    ref = "\n\n".join(chunks)
    return ref[:max_chars] if len(ref) > max_chars else ref


def generate_n_drafts(loader: GenModelLoader, system: str, user: str,
                      n: int) -> list[dict]:
    """生成 N 个候选稿（temperature 阶梯抖动 · 非迭代 · 规避 self-refine 同质化）。

    每个候选独立调一次 call_gen_model（active→fallback 链复用 · 不另起调用栈）。
    通过临时改 candidate profile 的 temperature 让 N 稿真有差异（多样性 = best-of-N 价值）。
    单个候选生成失败（GenModelExhaustedError）→ 记录跳过，不中断其余候选；全失败由调用方 raise。

    返回 [{idx, reply, profile, temperature, error}]（error 非空 = 该候选生成失败被跳过）。
    """
    candidates = loader.get_callable_profiles()
    if not candidates:
        raise GenModelExhaustedError([("<none>", "no callable profiles")])
    base_temp = candidates[0].temperature
    temps = _candidate_temperatures(base_temp, n)
    drafts: list[dict] = []
    for i, temp in enumerate(temps):
        # 临时把 active profile 的 temperature 改成本候选温度（生成后还原 · 不污染 loader）。
        active0 = candidates[0]
        orig_temp = active0.temperature
        active0.temperature = temp
        print(f"\n[gen_writer][best-of-N] 生成候选 {i+1}/{n} (temperature={temp})",
              file=sys.stderr)
        try:
            reply, used_profile = call_gen_model(loader, system, user)
            drafts.append({"idx": i, "reply": reply, "profile": used_profile,
                           "temperature": temp, "error": None})
        except GenModelExhaustedError as e:
            print(f"[gen_writer][best-of-N] 候选 {i+1} 生成失败（跳过）: {str(e)[:150]}",
                  file=sys.stderr)
            drafts.append({"idx": i, "reply": None, "profile": None,
                           "temperature": temp, "error": str(e)[:200]})
        finally:
            active0.temperature = orig_temp
    return drafts


def score_candidate(body: str, author_ref: str, loader: GenModelLoader,
                    use_av_judge: bool = True) -> dict:
    """给单个候选稿打分（SFS 统计指纹 + AV-judge 配对走味计数 · 全 advisory · 透明可读）。

    · sfs：style_evaluator.compute_style_only_sfs(author_ref, body) → style_only_sfs（0-100，越高越像）。
      无 author_ref 或 style_evaluator 不可用 → sfs=None（调用方据此退化排序）。
    · av_drift_count：av_judge.pairwise_drift_count → 走味维度数（0-4，越少越像）。
      use_av_judge=False（无锚 / AV_JUDGE_MODE=off 等）或调用失败 → av_drift_count=None。
    · composite：综合分 = sfs - AV_DRIFT_PENALTY_PER_DIM * av_drift_count（缺项各自降级）。
      仅 sfs 缺 → composite=None（调用方按 av_drift_count 升序兜底）；仅 av 缺 → composite=sfs。

    全 advisory：任何子项失败都不抛错（写作流水线不被择优层中断 · 北极星⑤）。
    """
    result: dict = {"sfs": None, "sfs_subscores": None,
                    "av_drift_count": None, "av_drift_dims": [],
                    "composite": None, "errors": []}
    # ① SFS 统计指纹（透明裁判）
    if author_ref and author_ref.strip():
        try:
            import style_evaluator as se
            sfs = se.compute_style_only_sfs(author_ref, body)
            result["sfs"] = round(float(sfs.get("style_only_sfs")), 2)
            result["sfs_subscores"] = sfs.get("subscores")
        except Exception as e:  # noqa: BLE001 — 择优层不阻断写作
            result["errors"].append(f"sfs: {str(e)[:120]}")
    # ② AV-judge 配对走味计数（读者视角 · 只 select 不强判）
    if use_av_judge and author_ref and author_ref.strip():
        try:
            import av_judge as avj
            av = avj.pairwise_drift_count(loader, author_ref, body)
            if av.get("error") is None and av.get("drift_count") is not None:
                result["av_drift_count"] = av["drift_count"]
                result["av_drift_dims"] = av.get("drift_dims", [])
            elif av.get("error"):
                result["errors"].append(f"av_judge: {av['error'][:120]}")
        except Exception as e:  # noqa: BLE001 — 择优层不阻断写作
            result["errors"].append(f"av_judge: {str(e)[:120]}")
    # ③ 综合分
    sfs, avc = result["sfs"], result["av_drift_count"]
    if sfs is not None:
        penalty = AV_DRIFT_PENALTY_PER_DIM * avc if avc is not None else 0.0
        result["composite"] = round(sfs - penalty, 2)
    return result


def select_best_draft(scored: list[dict]) -> tuple[int, str]:
    """从打分后的候选里选最佳（综合分降序 · 缺 SFS 时按走味数升序兜底 · 确定性可测）。

    scored: [{idx, body, score: {composite, sfs, av_drift_count, ...}, error}]（已过滤生成失败的）。
    排序键（全候选可比 · 缺项一致降级）：
      1. composite 有值 → 用 composite 降序（最像作者排最前）。
      2. 全候选都没 composite（无 author_ref / SFS 全挂）→ 按 av_drift_count 升序（走味越少越好），
         av 也没有 → 退回原始 idx 升序（= 单稿等价 · 第一稿优先 · 零回归保底）。
    返回 (best_idx_in_list, reason)。reason 解释凭什么选（不黑箱 · 北极星⑤）。
    """
    if not scored:
        raise ValueError("无可选候选（全部生成失败）")
    if len(scored) == 1:
        return 0, "唯一候选（N=1 或仅 1 稿生成成功）"

    have_composite = [s for s in scored if s["score"].get("composite") is not None]
    if have_composite:
        best = max(range(len(scored)),
                   key=lambda i: (scored[i]["score"].get("composite") is not None,
                                  scored[i]["score"].get("composite") or float("-inf")))
        sc = scored[best]["score"]
        reason = (f"综合分最高 composite={sc.get('composite')} "
                  f"(SFS={sc.get('sfs')} · AV走味={sc.get('av_drift_count')})")
        return best, reason

    # 无 composite：按走味数升序兜底
    have_av = any(s["score"].get("av_drift_count") is not None for s in scored)
    if have_av:
        best = min(range(len(scored)),
                   key=lambda i: (scored[i]["score"].get("av_drift_count")
                                  if scored[i]["score"].get("av_drift_count") is not None
                                  else 99))
        return best, (f"无 SFS 锚 · 按 AV-judge 走味数升序选 "
                      f"(走味={scored[best]['score'].get('av_drift_count')})")
    # 全无打分信号 → 第一稿（单稿等价 · 零回归）
    return 0, "无 SFS/AV 打分信号 · 退回第一稿（零回归保底）"


def best_of_n_pipeline(loader: GenModelLoader, system: str, user: str,
                       project_root: Path, n: int) -> tuple[str, "Profile", dict]:
    """best-of-N 主流程：生成 N 稿 → 各自打分 → 综合择优 → 返回最佳稿。

    返回 (best_reply, best_profile, selection_trace)。
    selection_trace 记录每个候选的分数 + 选中理由（写进 changes 不黑箱 · 北极星⑤）。
    优雅降级：无 author_ref → 跳 SFS/AV-judge，仍生成 N 稿但按 idx 选第一稿（等价单稿 · 不报错）。
    """
    author_ref = gather_author_ref_text(project_root)
    use_av_judge = bool(author_ref.strip())
    if not author_ref.strip():
        print("[gen_writer][best-of-N] 未找到作者原文池 · 跳过 SFS/AV-judge 打分 "
              "（仍生成 N 稿但退回第一稿 · 优雅降级）", file=sys.stderr)

    drafts = generate_n_drafts(loader, system, user, n)
    ok_drafts = [d for d in drafts if d.get("error") is None and d.get("reply")]
    if not ok_drafts:
        # 全部候选生成失败 → 汇总 raise（与单稿全失败行为一致）
        raise GenModelExhaustedError(
            [("best_of_n", "; ".join(d.get("error", "?") for d in drafts) or "all empty")])

    scored: list[dict] = []
    for d in ok_drafts:
        body, _changes = split_text_and_changes(d["reply"])
        sc = score_candidate(body, author_ref, loader, use_av_judge=use_av_judge)
        scored.append({"idx": d["idx"], "reply": d["reply"], "profile": d["profile"],
                       "temperature": d["temperature"], "body_cjk": cio.count_cjk(body),
                       "score": sc, "error": None})
        print(f"[gen_writer][best-of-N] 候选 idx={d['idx']} temp={d['temperature']}: "
              f"SFS={sc['sfs']} AV走味={sc['av_drift_count']} composite={sc['composite']} "
              f"cjk={cio.count_cjk(body)}", file=sys.stderr)

    best_i, reason = select_best_draft(scored)
    best = scored[best_i]
    print(f"\n[gen_writer][best-of-N] ✅ 选中候选 idx={best['idx']} "
          f"(temp={best['temperature']}) — {reason}", file=sys.stderr)

    trace = {
        "best_of_n": n,
        "candidates_generated": len(drafts),
        "candidates_scored": len(scored),
        "author_ref_found": use_av_judge,
        "selected_idx": best["idx"],
        "selection_reason": reason,
        "candidates": [
            {"idx": s["idx"], "temperature": s["temperature"], "cjk": s["body_cjk"],
             "sfs": s["score"]["sfs"], "av_drift_count": s["score"]["av_drift_count"],
             "av_drift_dims": s["score"]["av_drift_dims"],
             "composite": s["score"]["composite"], "errors": s["score"]["errors"]}
            for s in scored
        ],
    }
    return best["reply"], best["profile"], trace


# ============ 输出解析与保存 ============
def split_text_and_changes(reply: str) -> tuple:
    """从返回拆出正文 + CHANGES JSON"""
    # 去掉 $ 末尾锚定（对齐 gen_fixer 写法）：LLM 在 json 块后多输出尾随文字也能匹配。
    # 用 finditer 取「最后一个」```json``` 块，正文 = 该块之前的内容。
    json_matches = list(re.finditer(r'```json\s*\n(.*?)\n```', reply, re.DOTALL))
    if json_matches:
        last = json_matches[-1]
        changes_json = last.group(1).strip()
        body = reply[:last.start()].rstrip()
    else:
        body = reply.strip()
        changes_json = "{}"

    # 去掉可能的 "# 正文" 这类元标题
    body = re.sub(r'^#\s*(正文|cluster.*)\s*\n', '', body, flags=re.MULTILINE)

    # DCAS 模式（用户偏好）：如果 gen-model 仍误带「第 N 章 标题」分章标记，stderr 警告
    # 不主动删除（让 splitter 决定怎么处理），只提示 prompt 没生效
    if re.search(r'^第\s*[一二三四五六七八九十百千\d]+\s*章\s', body, re.MULTILINE):
        print("[gen_writer] [WARN] gen-model 输出含「第 N 章 标题」分章标记 — "
              "splitter 应忽略这些标记重新决定截断点。"
              "若反复出现，调高 prompt 强度或换 profile。", file=sys.stderr)

    try:
        changes_obj = json.loads(changes_json)
    except json.JSONDecodeError:
        changes_obj = {"_doc": "返回 CHANGES JSON 解析失败", "raw": changes_json[:2000]}
    return body, changes_obj


def save_output(project_root: Path, cluster_id: int, body: str, changes: dict,
                ch_start: int, ch_end: int, used_profile: Profile,
                seed_trace: dict = None, best_of_n_trace: dict = None):
    """写 draft + changes.json

    v27 freestyle：ch_end=None 时 ch_range 写 'TBD_by_splitter'（splitter 后期填）。
    seed_trace：snippet_seed 播种痕迹（用了几段 / 哪个模式）· 留 changes 不黑箱（北极星⑤）。
    best_of_n_trace：best-of-N 择优痕迹（N 稿各自分数 + 选中理由）· 留 changes 透明可审（北极星⑤）。
    """
    # 空 body 守卫（2026-05-30 加固）：拒写空草稿并报错，避免 cjk=0 草稿入库还报成功。
    # 上游 call_gen_model 已对空响应切 fallback，此处是最后一道防线（含解析后正文为空的情况）。
    if not body.strip():
        raise ValueError(
            f"[gen_writer] 拒绝写入空草稿（cluster_{cluster_id:03d}）：解析后正文为空。"
            f"可能是 gen-model 返回空内容或全为 CHANGES JSON 无正文 — 请检查 profile 输出。"
        )

    draft_dir = project_root / '章节' / f'cluster_{cluster_id:03d}_draft'
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f'cluster_{cluster_id:03d}_draft.txt'
    changes_path = draft_dir / f'cluster_{cluster_id:03d}_changes.json'

    draft_path.write_text(body, encoding='utf-8')

    # 补全 changes 元数据
    cjk = cio.count_cjk(body)  # v27 修复：统一 CJK 口径走 chapter_io（覆盖扩展 CJK）
    freestyle = (ch_end is None)
    ch_range_str = f'{ch_start}-TBD_by_splitter' if freestyle else f'{ch_start}-{ch_end}'

    # v27 P0 修复（schema 统一）：先把 LLM 输出的 changes 经 normalize_changes 归一·
    # 兼容三种布局（顶层 factual / 顶层 CHANGES / 顶层裸字段）·全部转 {factual, self_eval}·
    # 杜绝下游 audit_hub CHANGES_MISSING 误报 + waivers 读不到。
    changes = cio.normalize_changes(changes)
    se = changes['self_eval']
    se.setdefault('ecas_metadata', {})
    se['ecas_metadata'].update({
        'cluster_id': f'cluster_{cluster_id:03d}',
        'ch_start': ch_start,
        'ch_range': ch_range_str,
        'chapter_count_decided_by_splitter': freestyle,
        'generated_by': 'gen_writer.py',
        'generated_by_profile': used_profile.name,
        'generated_by_model': used_profile.model,
        'generated_at': datetime.now().isoformat(),
        'cjk_actual': cjk,
        'writer_mode': 'freestyle_v27' if freestyle else 'locked_v26',
        'snippet_seed': seed_trace or {'snippet_seed_mode': 'on', 'injected': False},
        'best_of_n': best_of_n_trace or {'best_of_n': 1, 'note': '单稿直生（BEST_OF_N=1 或未启用）'},
    })
    se.setdefault('waivers', [])
    se.setdefault('uncertainty_flags', [])
    changes.setdefault('schema_version', 'v2.cluster')
    changes_path.write_text(json.dumps(changes, ensure_ascii=False, indent=2),
                            encoding='utf-8')

    print(f"\n[gen_writer] 写出:", file=sys.stderr)
    print(f"  正文: {draft_path} ({cjk} CJK)", file=sys.stderr)
    print(f"  CHANGES: {changes_path}", file=sys.stderr)
    print(f"  profile: {used_profile.name} ({used_profile.model})", file=sys.stderr)
    return draft_path, cjk


# ============ Scanner 自校验 ============
def run_scanners(draft_path: Path) -> dict:
    """跑两个 scanner 校验"""
    scanners = ['narrative_short_sentence_scanner.py', 'repeat_noun_density_scanner.py']
    results = {}
    scripts_dir = Path(__file__).parent
    for sc in scanners:
        sc_path = scripts_dir / sc
        if not sc_path.exists():
            results[sc] = {'verdict': 'SKIP', 'reason': 'scanner not found'}
            continue
        r = subprocess.run(
            [sys.executable, str(sc_path), str(draft_path)],
            capture_output=True, text=True, encoding='utf-8'
        )
        try:
            d = json.loads(r.stdout)
            results[sc] = {'verdict': d.get('verdict'), 'violations_count': d.get('violations_count')}
        except Exception as e:
            # v27 修复：静默 except 加日志（之前完全静默吞错·debug 困难）
            print(f"  [run_scanners] {sc} 输出 JSON 解析失败 ({e})·exit={r.returncode}·stderr_preview={(r.stderr or '')[:150]}",
                  file=sys.stderr)
            results[sc] = {'verdict': 'ERROR', 'stdout_preview': r.stdout[:300]}
    return results


# ============ 主入口 ============
def main():
    check_deps()
    parser = argparse.ArgumentParser(description='Gen-Model 正文生成器（OpenAI 兼容协议）')
    parser.add_argument('--project', required=True, help='项目根路径')
    parser.add_argument('--cluster', type=int, required=True, help='cluster id（整数）')
    parser.add_argument('--chapter-start', type=int, default=None,
                        help='[v27 optional] cluster 起始章号 · 缺省时从事件簇.json 推导（cluster_001=1 / 后续=上 cluster 末章+1）')
    parser.add_argument('--chapter-end', type=int, default=None,
                        help='[v27 deprecated optional] cluster 结束章号 · 缺省 = freestyle 模式（writer 不知章数 · splitter 按字数切）')
    parser.add_argument('--target-cjk', default=None,
                        help='[v27 optional] 整 cluster 目标字数 · 缺省 = freestyle（按 scope_summary 自然涌现）')
    parser.add_argument('--dry-run', action='store_true', help='只输出 prompt，不调 API')
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"[ERROR] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    # v27：ch_start 缺省 → 从事件簇.json + 已写章节推导
    ch_start = args.chapter_start
    if ch_start is None:
        ch_start = _infer_cluster_start_ch(project_root, args.cluster)
        print(f"[gen_writer] [v27 freestyle] 推导 ch_start={ch_start} (cluster_{args.cluster:03d})",
              file=sys.stderr)

    # 模式判断 + 提示
    if args.chapter_end is None:
        print(f"[gen_writer] [v27 freestyle 模式] writer 不知目标章数 · splitter 按字数切 · 章数自然涌现",
              file=sys.stderr)
    else:
        print(f"[gen_writer] [v26 兼容模式] ch_start={ch_start} ch_end={args.chapter_end} target_cjk={args.target_cjk}",
              file=sys.stderr)

    # dry-run 模式不需要 active profile
    if args.dry_run:
        system, user, seed_trace = build_prompt(project_root, args.cluster, ch_start,
                                                args.chapter_end, args.target_cjk)
        print("=== SYSTEM PROMPT ===")
        print(system)
        print("\n=== USER PROMPT ===")
        print(user)
        print(f"\n[dry-run] system={len(system)} chars / user={len(user)} chars",
              file=sys.stderr)
        print(f"[dry-run] snippet_seed: {seed_trace}", file=sys.stderr)
        # 显示当前 active profile 信息
        try:
            loader = GenModelLoader()
            p = loader.get_active_profile()
            print(f"[dry-run] active profile: {p.name} ({p.model} @ {p.base_url})",
                  file=sys.stderr)
        except GenModelConfigError as e:
            print(f"[dry-run] [WARN] active profile 未就绪: {e}", file=sys.stderr)
        return

    # 加载 gen-model 配置
    try:
        loader = GenModelLoader()
        active = loader.get_active_profile()  # 校验 active 就绪
    except GenModelConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        print("  跑 python core/scripts/gen_model.py list / switch 修复",
              file=sys.stderr)
        sys.exit(2)

    print(f"[gen_writer] 加载配置: {loader.env_path}", file=sys.stderr)
    print(f"[gen_writer] active = {active.name}", file=sys.stderr)
    chain = loader.get_fallback_chain()
    if chain:
        print(f"[gen_writer] fallback chain = {','.join(chain)}", file=sys.stderr)

    system, user, seed_trace = build_prompt(project_root, args.cluster, ch_start,
                                            args.chapter_end, args.target_cjk)

    # best-of-N（默认 active · N≥2 真生效 · BEST_OF_N=1 退回单稿直生 · 2026-05-31）：
    # N 稿并行生成（temperature 阶梯抖动）→ SFS + AV-judge 配对判别打分 → 综合择优。
    # selection（择优）≠ refine（迭代）→ 天然规避 self-refine 同质化（arxiv 实证）。
    n = _best_of_n()
    best_of_n_trace = None
    try:
        if n >= 2:
            print(f"\n[gen_writer][best-of-N] BEST_OF_N={n} · 生成 {n} 稿配对重排择优",
                  file=sys.stderr)
            reply, used_profile, best_of_n_trace = best_of_n_pipeline(
                loader, system, user, project_root, n)
        else:
            print(f"[gen_writer][best-of-N] BEST_OF_N=1 · 单稿直生（已关闭择优）",
                  file=sys.stderr)
            reply, used_profile = call_gen_model(loader, system, user)
    except GenModelExhaustedError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(3)

    body, changes = split_text_and_changes(reply)
    draft_path, cjk = save_output(project_root, args.cluster, body, changes,
                                  ch_start, args.chapter_end, used_profile,
                                  seed_trace=seed_trace, best_of_n_trace=best_of_n_trace)

    print(f"\n[gen_writer] 跑 scanner...", file=sys.stderr)
    scan_results = run_scanners(draft_path)
    print(f"\n=== Scanner Results ===", file=sys.stderr)
    for sc, res in scan_results.items():
        print(f"  {sc}: {res}", file=sys.stderr)

    # 字数检查（v27 freestyle 无硬下限 · v26 兼容才校验目标）
    if args.target_cjk:
        try:
            target_min, target_max = map(int, args.target_cjk.split('-'))
            if cjk < target_min:
                print(f"\n[WARN] 字数 {cjk} < 目标下限 {target_min}", file=sys.stderr)
            elif cjk > target_max:
                print(f"\n[WARN] 字数 {cjk} > 目标上限 {target_max}", file=sys.stderr)
            else:
                print(f"\n[OK] 字数 {cjk} 在目标范围 [{target_min}, {target_max}]",
                      file=sys.stderr)
        except (ValueError, AttributeError):
            print(f"\n[gen_writer] target_cjk 解析失败，跳过字数校验: {args.target_cjk}",
                  file=sys.stderr)
    else:
        # v27 freestyle：软提示（splitter 健康区间）
        if cjk < 8000:
            print(f"\n[v27 freestyle] [HINT] cjk={cjk} 偏短 · 切 3 章可能不够（splitter 可能从下个 cluster 补料）",
                  file=sys.stderr)
        elif cjk > 30000:
            print(f"\n[v27 freestyle] [HINT] cjk={cjk} 偏长 · splitter 会切成 7+ 章",
                  file=sys.stderr)
        else:
            print(f"\n[v27 freestyle] [OK] cjk={cjk} 健康区间 8000-30000",
                  file=sys.stderr)


if __name__ == '__main__':
    main()
