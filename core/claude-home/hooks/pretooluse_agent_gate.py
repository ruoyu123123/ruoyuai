#!/usr/bin/env python3
"""
PreToolUse Hook: 校验 Agent 工具调用的 prompt 合规性
只拦截 Agent 工具，其他工具直接放行
exit 0 = 放行, exit 2 = 拒绝

v2 cluster 化（2026-05-28）：ECAS agent 强制 RESEARCH_REF 规则不变·
本 hook 不区分 cluster vs chapter mode，由 audit_hub 子进程 env CLUSTER_MODE=1 传递。

Phase 2 升级
-----------
- 规则 5：多步流水线 Agent 必须含 PLAN_ID/STEP 字段
- 规则 6：PLAN_ID 豁免 — 含 PLAN_ID 视为契约完整，跳过 PROJECT/CHAPTER/MANIFEST 强制
- 修 bug：novel_keywords 改为词组匹配，"章"单字不再独立触发误判

P1-1 升级
---------
- 规则 8：PLAN_ID 引用的 plan 防篡改校验 — plan JSON 被旁路篡改（伪造 step 状态
  绕过跳步防御）→ 在 Agent spawn 前拦下。增值项，校验自身出错一律放行。

P2-10 升级
----------
- 规则 9：内容级注入模式检测（warn-only，不拦截）—— 扫描 prompt 中常见 prompt
  injection 模板（"ignore previous instructions" / "忽略之前指令" 等），命中
  ≥2 个不同 pattern 时 stderr 警告。仅警告不拦截，避免误伤合法包含此类字符串
  的角色对话/研究内容；agent 看到警告自行判断是否真注入。

v26 清理
--------
- 删除原规则 12（novel-writer single 模式废弃门禁 + .allow_single_mode.flag 旁路）。
  v26 起 chapter mode 整套废弃、无降级无旁路，所有写作统一走 cluster mode；
  single 模式门禁是 v25 遗留死防御，已物理移除。
  规则 1 的 CHAPTER 契约字段保留（cluster 起首章锚点，合法）。
"""
import json
import os
import sys
import re

# ============ 关键词配置 ============

# 写作 Agent 名称特征（词组优先，避免"章"单字误判）
NOVEL_NAME_KEYWORDS = [
    "Writer", "writer", "Validator", "validator",
    "Voice", "voice", "novel-writer", "novel-validator", "novel-voice",
    "写第", "审第", "修第",        # 词组匹配："写第N章" / "审第N章"
    "写作", "正文",
    "写章节", "蒸馏",              # 含完整词组
]

# 多步流水线 Agent 关键词（命中即认为是多步命令）
# 🔴 v26: chapter mode (write-chapter / save-state) 已废弃，移除关键词；改 cluster mode
MULTISTEP_KEYWORDS_DESC = [
    "cluster-save-state", "cluster save state",
    "cluster-write", "cluster write",
    "distill-style", "distill style",
    "outline", "reconcile",
    "check-quality", "check quality",
]
MULTISTEP_KEYWORDS_PROMPT = [
    "cluster_save_state", "cluster_write",
    "distill style",
    "全 7 阶段", "三章窗口",
    "12 步流水线", "7 步流水线",
]

# P2-10 规则 9：常见 prompt injection 模板（英中双语）
# 命中策略：≥2 个不同 pattern 才警告（单个可能误报，多个高度可疑）
INJECTION_PATTERNS = [
    (r"ignore\s+(?:the\s+)?(?:previous|all|above|prior)\s+instruction",
     "英文 ignore previous instructions"),
    (r"disregard\s+(?:the\s+)?(?:above|prior|earlier)",
     "英文 disregard above"),
    (r"forget\s+(?:everything|all|all\s+previous)",
     "英文 forget everything"),
    (r"new\s+system\s+(?:prompt|message)[:：]",
     "英文 new system prompt"),
    (r"override\s+(?:system|previous|all)\s+(?:prompt|instruction)",
     "英文 override system"),
    (r"忽略\s*(?:之前|上述|所有|以上|前面|前文)\s*(?:的)?\s*(?:指令|要求|内容|系统|提示)",
     "中文 忽略之前指令"),
    (r"无视\s*(?:前面|上文|之前|系统)",
     "中文 无视前文"),
    (r"忘记\s*(?:之前|所有|前面|系统).{0,12}(?:指令|提示|要求)",
     "中文 忘记之前指令"),
    (r"重新\s*定义\s*你\s*(?:是|为)",
     "中文 重新定义你是"),
    (r"新\s*(?:的)?\s*(?:系统|身份|指令)\s*[:：]",
     "中文 新系统/身份/指令"),
]


def is_multistep_agent(desc: str, prompt: str) -> bool:
    """多步流水线 Agent 判定：desc 或 prompt 命中关键词即为多步。"""
    desc_l = desc.lower()
    if any(kw in desc_l for kw in MULTISTEP_KEYWORDS_DESC):
        return True
    if any(kw in prompt for kw in MULTISTEP_KEYWORDS_PROMPT):
        return True
    return False


def main():
    raw = sys.stdin.read(32768)
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool = data.get("tool_name", "")
    if tool != "Agent":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    prompt = tool_input.get("prompt", "")
    desc = tool_input.get("description", "")
    subagent_type = tool_input.get("subagent_type", "") or ""

    if not prompt:
        sys.exit(0)

    # v27 修复（feedback: claude agent prompt 含 "writer"/"修" 字眼被误判为 novel-writer）：
    # 严判 — 仅当 subagent_type 是 novel-* 系列才视为写作 agent。
    # subagent_type=claude / general-purpose / Explore 等通用 agent 即便 prompt/desc 含
    # "writer"/"修第" 等字眼也放行（这些是任务描述不是 agent 类型）。
    # 兜底：subagent_type 缺失（罕见）时退回旧 desc 关键词判断。
    NOVEL_SUBAGENT_TYPES = {
        "novel-writer", "novel-validator-checker", "novel-validator-repair",
        "novel-voice-checker", "novel-voice-keeper",
        "novel-foreshadower", "novel-reflector", "novel-summarizer",
        "novel-outline-planner", "novel-chapter-splitter",
        "novel-reading-reflector", "novel-adversarial-reader",
        "novel-counterfactual-judge", "novel-meta-judge",
        "novel-meta-prompt-optimizer", "novel-researcher",
    }
    if subagent_type:
        # subagent_type 明确给了 — 按 type 精确判断
        is_novel_agent = subagent_type in NOVEL_SUBAGENT_TYPES
    else:
        # subagent_type 缺失 — 兜底走旧 desc 关键词
        is_novel_agent = any(kw in desc for kw in NOVEL_NAME_KEYWORDS)

    # 规则 6（豁免）：含 PLAN_ID 视为契约完整
    has_plan_id = "PLAN_ID:" in prompt

    # ============ 规则 1：写作 Agent 必须包含契约字段 ============
    # 规则 6 豁免：含 PLAN_ID 直接跳过本规则
    if is_novel_agent and not has_plan_id:
        has_project = "PROJECT:" in prompt
        has_chapter = "CHAPTER:" in prompt or "CURRENT_CHAPTER:" in prompt
        has_manifest = "MANIFEST:" in prompt
        has_mode = "MODE:" in prompt

        # v27 修复：写作 agent 子类判别 — 优先 subagent_type 精确匹配，desc 关键词兜底
        _AUX_TYPES = {"novel-validator-checker", "novel-validator-repair",
                      "novel-voice-checker", "novel-voice-keeper",
                      "novel-foreshadower", "novel-summarizer",
                      "novel-reflector", "novel-outline-planner",
                      "novel-reading-reflector"}
        if subagent_type:
            is_writer = (subagent_type == "novel-writer")
            is_aux = (subagent_type in _AUX_TYPES)
        else:
            is_writer = ("writer" in desc.lower() or "写第" in desc)
            is_aux = any(kw in desc.lower() for kw in
                         ["validator", "voice", "修第", "审第", "摘要", "伏笔", "经验", "规划"])

        # writer 需要 PROJECT + CHAPTER + MANIFEST
        if is_writer:
            if not (has_project and has_chapter and has_manifest):
                missing = []
                if not has_project: missing.append("PROJECT")
                if not has_chapter: missing.append("CHAPTER")
                if not has_manifest: missing.append("MANIFEST")
                print(f"❌ [Hook] Writer Agent 缺少必填字段: {', '.join(missing)}",
                      file=sys.stderr)
                print(f"   prompt 前 200 字: {prompt[:200]}", file=sys.stderr)
                sys.exit(2)
        # validator/voice/summarizer/etc 需要 PROJECT + CHAPTER + MODE
        elif is_aux:
            if not (has_project and has_chapter and has_mode):
                missing = []
                if not has_project: missing.append("PROJECT")
                if not has_chapter: missing.append("CHAPTER/CURRENT_CHAPTER")
                if not has_mode: missing.append("MODE")
                print(f"❌ [Hook] Agent 缺少必填字段: {', '.join(missing)}",
                      file=sys.stderr)
                print(f"   prompt 前 200 字: {prompt[:200]}", file=sys.stderr)
                sys.exit(2)
        # 兜底：至少有一个契约字段
        else:
            has_any = has_project or has_chapter or has_manifest or has_mode
            if not has_any:
                print(f"❌ [Hook] 写作 Agent 调用缺少契约字段（PROJECT/CHAPTER/MANIFEST/MODE）",
                      file=sys.stderr)
                print(f"   prompt 前 200 字: {prompt[:200]}", file=sys.stderr)
                sys.exit(2)

    # ============ 规则 2：prompt 长度门禁 ============
    prompt_len = len(prompt)

    # 下限：写作 agent 的 prompt 不应太短（漏传信号）
    if is_novel_agent and prompt_len < 50:
        print(f"❌ [Hook] 写作 Agent prompt 过短（{prompt_len} 字符 < 50），疑似漏传",
              file=sys.stderr)
        sys.exit(2)

    # 上限：任何 agent 的 prompt 不应超过 15000 字符（塞满信号）
    if prompt_len > 15000:
        print(f"❌ [Hook] Agent prompt 过长（{prompt_len} 字符 > 15000），疑似塞满旧模式",
              file=sys.stderr)
        print("   应使用 manifest + progressive disclosure，不要在 prompt 里塞所有规则",
              file=sys.stderr)
        sys.exit(2)

    # ============ 规则 3：禁止在 prompt 里塞整个 skill 文件内容 ============
    if re.search(r"^---\s*$", prompt, re.MULTILINE) and "description:" in prompt:
        print("❌ [Hook] Agent prompt 包含 frontmatter，疑似塞入了整个 skill 文件",
              file=sys.stderr)
        print("   应让 agent 自己 Read 需要的文件，不要在 prompt 里内联",
              file=sys.stderr)
        sys.exit(2)

    # ============ 规则 4：写作 agent 不应包含大段规则文本 ============
    if is_novel_agent:
        rule_words = re.findall(r"(必须|禁止|严禁|不得|不允许)", prompt)
        if len(rule_words) > 8:
            print(f"⚠️ [Hook] 写作 Agent prompt 含 {len(rule_words)} 条规则性指令（>8）",
                  file=sys.stderr)
            print("   建议精简：规则应在 agent 定义文件中，不在调用 prompt 里重复",
                  file=sys.stderr)
            # 只警告不拦截

    # ============ 规则 5：多步流水线 Agent 必须含 PLAN_ID/STEP 字段 ============
    if is_multistep_agent(desc, prompt):
        has_step = "STEP:" in prompt or "STEPS:" in prompt
        if not (has_plan_id or has_step):
            print(f"❌ [Hook] 多步流水线 Agent 缺 PLAN_ID/STEP 字段", file=sys.stderr)
            print(f"   先调 plan_tracker.py create 生成 plan_id，再在 Agent prompt 含 PLAN_ID: <id>",
                  file=sys.stderr)
            print(f"   prompt 前 200: {prompt[:200]}", file=sys.stderr)
            sys.exit(2)

    # ============ 规则 8（P1-1）：PLAN_ID 引用的 plan 防篡改校验 ============
    # prompt 含 PLAN_ID 时，校验该 plan 的 attestation——若 plan JSON 被旁路篡改
    # （Agent 直接编辑 / 注入写盘，典型是伪造 step 状态绕过跳步防御），在 Agent
    # spawn 前就拦下。本检查是【增值】项：plan_tracker 不可导入 / plan 找不到 /
    # 校验出错——一律放行（plan_state=None），绝不让防篡改校验本身成为新故障点。
    m = re.search(r"PLAN_ID:\s*(\S+)", prompt)
    if m:
        plan_state = None
        try:
            _scripts_dir = os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
            if _scripts_dir not in sys.path:
                sys.path.insert(0, _scripts_dir)
            import plan_tracker
            plan_state = plan_tracker.verify_plan(m.group(1).strip())
        except Exception:
            plan_state = None  # 校验自身出错 → 放行，不阻断主流程
        if plan_state == "tampered":
            print("❌ [Hook] PLAN_ID 指向的 plan 防篡改校验失败（tampered）",
                  file=sys.stderr)
            print("   plan JSON 被 plan_tracker 之外的途径改过，疑似伪造 step 状态绕过跳步防御",
                  file=sys.stderr)
            print("   排查注入来源；若是合法手动修改，跑 plan_tracker.py reattest <plan_id>",
                  file=sys.stderr)
            sys.exit(2)

    # ============ 规则 10（v23 ECAS）：ECAS agent spawn 必须含 RESEARCH_REF ============
    # CLAUDE.md v17.7 调研先行规则在 ECAS 流程层强制：
    # writer (MODE=ecas) / outline-planner (MODE=ecas_cluster_brief) prompt
    # 必须含 `RESEARCH_REF:` 字段（指向 .research_cache/*.md 路径）
    # 否则 = 凭模型记忆生成 = feedback_research_first_principle 违规
    is_ecas_mode = (
        ("MODE: ecas" in prompt) or
        ("MODE: ecas_cluster_brief" in prompt) or
        ("MODE: ecas_multi_chapter" in prompt) or
        ("CLUSTER_ID:" in prompt and is_novel_agent)
    )
    if is_ecas_mode:
        has_research_ref = (
            "RESEARCH_REF:" in prompt or
            "research_cache" in prompt.lower() or
            ".research_cache/" in prompt
        )
        # splitter 不需要调研（纯算法切分），豁免
        _sat = tool_input.get("subagent_type", "") or ""
        is_splitter = "novel-chapter-splitter" in desc or "splitter" in _sat.lower()
        # ecas-checkpoint 等纯验证类豁免
        is_checkpoint_validator = "ecas-checkpoint" in desc.lower() or "checkpoint" in desc.lower()
        # 蒸馏分析类 agent 豁免 — 它们只分析已有作品风格，不生成情节，不需要调研
        # 触发场景：/distill-style 阶段 1 单章/cluster 衔接蒸馏 sub-agent
        # 区分：蒸馏复刻（生成）已被规则 11 单独拦截；这里豁免的是「分析」类
        is_distill_agent = (
            "蒸馏" in desc
            or "distill-style" in desc.lower()
            or "distill style" in desc.lower()
            or ("PLAN_ID:" in prompt and "distill-style" in prompt)
        )
        if not (has_research_ref or is_splitter or is_checkpoint_validator or is_distill_agent):
            print(f"❌ [Hook v23] ECAS agent spawn 缺 RESEARCH_REF 字段", file=sys.stderr)
            print(f"   ECAS 模式（writer/outline-planner ecas_cluster_brief）必须先 spawn novel-researcher 生成 .research_cache/*.md", file=sys.stderr)
            print(f"   然后在本 spawn prompt 加 'RESEARCH_REF: <path>' 字段", file=sys.stderr)
            print(f"   v17.7 调研先行规则 + feedback_research_first_principle 教训硬约束", file=sys.stderr)
            print(f"   豁免: novel-chapter-splitter / ecas-checkpoint 类纯算法/验证 agent", file=sys.stderr)
            print(f"   prompt 前 300: {prompt[:300]}", file=sys.stderr)
            sys.exit(2)

    # ============ 规则 11（v22.cluster.3）：蒸馏复刻必须走 gen-model，禁用 sub-agent ============
    # 蒸馏 phase-2 / phase-5 的复刻测试是为了验证「skill 能不能让目标 LLM 模仿出风格」。
    # 正式写作走 gen-model（OpenAI 兼容协议外部模型），所以蒸馏闭环必须同栈。
    # 若用 Claude sub-agent 复刻 → skill 在 Claude 上能跑出来不代表在 gen-model 上能跑出来
    # → v0→v1 升级针对错的模型 → 无效迭代。
    #
    # 拦截特征（任一命中即拦）：
    #   1) description 含 "复刻" + 同时 prompt 含 "skill_v" 或 "复刻测试/v"
    #   2) description 形如 "v{N} 复刻" / "复刻测试" / "phase-2 复刻" / "phase-5 复刻"
    #   3) output path 落在 workspace/styles/*/复刻测试/v*_round*/test_*_replica.txt
    #
    # 豁免：prompt 含 "DISTILL_REPLICATE_BYPASS=1"（明确旁路标记，仅紧急救火用）
    is_replicate_task = False
    desc_l = desc.lower()
    if ("复刻测试" in desc) or ("v0 复刻" in desc) or ("v1 复刻" in desc) or \
       ("v2 复刻" in desc) or ("phase-2 复刻" in desc) or ("phase-5 复刻" in desc) or \
       ("replica" in desc_l and "test" in desc_l):
        is_replicate_task = True
    elif "复刻" in desc and ("skill_v" in prompt or "复刻测试/v" in prompt or
                           "test_opening_replica" in prompt or
                           "test_battle_replica" in prompt or
                           "test_psychology_replica" in prompt):
        is_replicate_task = True

    bypass = "DISTILL_REPLICATE_BYPASS=1" in prompt

    if is_replicate_task and not bypass:
        print("❌ [Hook v22.cluster.3] 蒸馏复刻测试禁用 Agent 工具", file=sys.stderr)
        print("   蒸馏 phase-2 / phase-5 复刻必须走 gen-model（外部 OpenAI 兼容模型）", file=sys.stderr)
        print("   正确用法：", file=sys.stderr)
        print("   python core/scripts/distill_replicate.py \\", file=sys.stderr)
        print("     --style-skill workspace/styles/<书名>/skill_v<N>.md \\", file=sys.stderr)
        print("     --type opening|battle|psychology|dialogue|description|transition \\", file=sys.stderr)
        print("     --output workspace/styles/<书名>/复刻测试/v<N>_round<M>/test_<type>_replica.txt \\", file=sys.stderr)
        print("     [--ref-chapter <参考章>] [--target-words 1200]", file=sys.stderr)
        print("   理由：蒸馏闭环必须用最终写作要用的 gen-model 测，否则升级针对错的模型 = 无效迭代", file=sys.stderr)
        print("   紧急旁路：prompt 加 'DISTILL_REPLICATE_BYPASS=1'（仅救火用，会留 lesson 记录）", file=sys.stderr)
        sys.exit(2)

    # 规则 12（v25 novel-writer single 模式废弃门禁）已于 v26 删除：
    # chapter mode 整套废弃、无降级无旁路，single 模式门禁 + .allow_single_mode.flag
    # 是遗留死防御。规则 1 的 CHAPTER 契约字段（cluster 起首章锚点）保留。

    # ============ 规则 9（P2-10）：内容级注入模式检测（warn-only） ============
    # 扫描 prompt 中常见 prompt injection 模板。命中 ≥2 个不同 pattern → 警告。
    # 仅 stderr 警告不 exit 2 —— 避免误伤合法包含此类字符串的角色对话/研究内容
    # （如某个反派 NPC 说话内容就含「忽略之前」）。Agent 看到警告自行判断。
    prompt_lower = prompt.lower()
    injection_hits = []
    for pat, label in INJECTION_PATTERNS:
        try:
            if re.search(pat, prompt_lower, re.IGNORECASE):
                injection_hits.append(label)
        except re.error:
            continue  # 正则编译错跳过该条
    if len(injection_hits) >= 2:
        print(f"⚠️ [Hook] prompt 含 {len(injection_hits)} 个 injection 模板特征 "
              f"（仅警告不拦截）：", file=sys.stderr)
        for h in injection_hits[:5]:
            print(f"   - {h}", file=sys.stderr)
        print("   若是 NPC 对话/研究文本的合法内容请忽略；若是被注入请审查来源",
              file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
