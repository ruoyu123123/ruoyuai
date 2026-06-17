"""style_repair_engine 回归测试 — 钉死中文小说机械修复引擎的确定性行为。

被测脚本 core/scripts/style_repair_engine.py 是**纯机械修复层**（不调 LLM / 不联网）：
  - merge_short_sentences  短句合并（提升逗句比）
  - fix_onomatopoeia       拟声破折号补叹号
  - fix_banned_words       禁用词替换（空 replacement → (deleted) 标记）
  - fix_ai_tags            AI 对话标签替换
  - apply_fixes            单遍四步顺序执行
  - apply_fixes_iterative  三遍迭代直到收敛
  - generate_dialogue_guide 对话密度不足时产 LLM 改写指令
  - analyze_repair_potential 分析报告（聚合 style_analyzer profile）

零依赖：只用标准库；test_* 无参数；断言失败 raise AssertionError。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core" / "scripts"))
import style_repair_engine as mod


# ────────────────────────────────────────────────────────────────────
# 1. 禁用词替换：有替换词 → 替换；空替换词 → 标 (deleted) 不动文本
# ────────────────────────────────────────────────────────────────────
def test_fix_banned_words_replaces_and_marks_deleted():
    # "顿时" 有非空首选替换 "忽然间"；"与此同时" 首选替换是 ""（删除标记）
    text = "他顿时愣住。与此同时，雨停了。顿时又下了起来。"
    fixed, fixes = mod.fix_banned_words(text)

    # 有替换词的被真正替换掉
    assert "顿时" not in fixed
    assert "忽然间" in fixed

    by_word = {f["word"]: f for f in fixes}

    # "顿时" 出现 2 次 → count=2 且记录 replacement
    assert by_word["顿时"]["count"] == 2
    assert by_word["顿时"]["replacement"] == "忽然间"
    assert "note" not in by_word["顿时"]

    # "与此同时" 空替换 → 标记 (deleted)，文本里仍保留（需手动删）
    assert by_word["与此同时"]["replacement"] == "(deleted)"
    assert by_word["与此同时"]["note"] == "需手动确认删除位置"
    assert by_word["与此同时"]["count"] == 1
    assert "与此同时" in fixed  # 空替换不动文本


def test_fix_banned_words_no_hit_returns_empty_fixes():
    text = "干净的句子，没有任何待替换词汇。"
    fixed, fixes = mod.fix_banned_words(text)
    assert fixed == text
    assert fixes == []


# ────────────────────────────────────────────────────────────────────
# 2. AI 对话标签替换
# ────────────────────────────────────────────────────────────────────
def test_fix_ai_tags_replaces_known_tags():
    text = "他淡淡地说完。她缓缓地说着。又微微一笑道。"
    fixed, fixes = mod.fix_ai_tags(text)
    # AI_TAGS 映射：淡淡地说→说 / 缓缓地说→开口 / 微微一笑道→笑了一声
    assert "淡淡地说" not in fixed
    assert "缓缓地说" not in fixed
    assert "微微一笑道" not in fixed
    assert "说完" in fixed
    assert "开口" in fixed
    assert "笑了一声" in fixed
    tags = {f["tag"] for f in fixes}
    assert "淡淡地说" in tags
    assert "缓缓地说" in tags
    assert "微微一笑道" in tags
    for f in fixes:
        assert f["count"] >= 1


# ────────────────────────────────────────────────────────────────────
# 3. 拟声破折号补叹号
# ────────────────────────────────────────────────────────────────────
def test_fix_onomatopoeia_appends_exclamation():
    text = "轰隆——\n普通叙述行。\n咔嚓——\n"
    fixed = mod.fix_onomatopoeia(text)
    lines = fixed.split("\n")
    assert lines[0] == "轰隆——！"
    assert lines[1] == "普通叙述行。"  # 非拟声行原样保留
    assert lines[2] == "咔嚓——！"
    # 已有叹号的拟声行不会被本正则匹配（正则要求行尾是破折号 + 可选空白）
    text2 = "嘭！\n"
    assert mod.fix_onomatopoeia(text2).split("\n")[0] == "嘭！"


# ────────────────────────────────────────────────────────────────────
# 4. 短句合并：把多个超短句用逗号粘连，拉高逗句比
# ────────────────────────────────────────────────────────────────────
def test_merge_short_sentences_joins_short_clauses():
    # 三个都是 <=12 字 / 下一句 <=15 字的短句 → 被逗号合并
    line = "他抬头。看见天。心里发慌。"
    merged = mod.merge_short_sentences(line)
    # 合并后逗号数量增加（出现至少一个新逗号）
    assert "，" in merged
    # 合并产物以句末结束符收尾
    assert merged.rstrip().endswith(("。", "！", "？", "!", "?", "”", "」", "』"))


def test_merge_short_sentences_protects_dialogue_lines():
    # 对话行（以引号开头）整行保留，不被拆/合
    dialogue = '“你来了？我等很久了。”'
    out = mod.merge_short_sentences(dialogue)
    assert out == dialogue  # 引号行原样返回


def test_merge_short_sentences_single_clause_unchanged():
    # 只有一个句末标记的行不会被合并逻辑改动（parts < 2 直接 append 原 line）
    line = "这是一句完整的话。"
    assert mod.merge_short_sentences(line) == line


# ────────────────────────────────────────────────────────────────────
# 5. apply_fixes 单遍：四步顺序执行，log 记录命中的步骤
# ────────────────────────────────────────────────────────────────────
def test_apply_fixes_logs_steps_and_fixes():
    # 选 "微微一笑道" 作 AI 标签样本：它不含任何 BANNED_WORDS 键（"微微" 是
    # QUOTA 词非禁用词），故能存活到 fix_ai_tags 步骤被命中。
    # （注意 apply_fixes 顺序 banned→ai_tags：若用 "淡淡地说" 会先被 "淡淡"→"随口"
    #  替换掉，ai_tags 步骤就抓不到了——这是引擎真实的级联顺序。）
    text = "他顿时一惊。\n他微微一笑道。\n"
    fixed, log = mod.apply_fixes(text)
    # 禁用词 + AI 标签都该被命中
    assert "顿时" not in fixed
    assert "微微一笑道" not in fixed
    assert "笑了一声" in fixed
    assert "fix_banned_words" in log["steps"]
    assert "fix_ai_tags" in log["steps"]
    assert log["banned_fixes"]  # 非空
    assert log["tag_fixes"]


def test_apply_fixes_clean_text_no_steps():
    text = "一段干净通顺的散文，没有任何待修复的机械问题在里头存在。"
    fixed, log = mod.apply_fixes(text)
    # 干净文本不一定完全等于原文（merge 可能微调），但不应命中禁用词/AI 标签步骤
    assert "fix_banned_words" not in log["steps"]
    assert "fix_ai_tags" not in log["steps"]
    assert log["banned_fixes"] == []
    assert log["tag_fixes"] == []


# ────────────────────────────────────────────────────────────────────
# 6. apply_fixes_iterative：迭代直到收敛 + pass/converged 标记
# ────────────────────────────────────────────────────────────────────
def test_apply_fixes_iterative_converges_and_marks():
    text = "他顿时愣住。她缓缓地说。\n"
    fixed, per_pass = mod.apply_fixes_iterative(text, max_passes=3)
    assert "顿时" not in fixed
    assert "缓缓地说" not in fixed
    # 每个 pass 都带 pass 编号 + converged 标记
    assert all("pass" in p and "converged" in p for p in per_pass)
    assert [p["pass"] for p in per_pass] == list(range(1, len(per_pass) + 1))
    # 最后一遍一定是收敛的（要么提前收敛，要么文本已不再变）
    assert per_pass[-1]["converged"] is True
    # 不超过 max_passes 遍
    assert len(per_pass) <= 3


def test_apply_fixes_iterative_already_clean_stops_at_pass_1():
    # 完全无可机械修的纯净对话/拟声文本 → 第一遍即收敛
    text = '“我在这里。”\n'
    fixed, per_pass = mod.apply_fixes_iterative(text, max_passes=3)
    assert fixed == text
    assert len(per_pass) == 1
    assert per_pass[0]["pass"] == 1
    assert per_pass[0]["converged"] is True


def test_apply_fixes_iterative_clamps_max_passes_floor_to_one():
    # max_passes <= 0 被 max(1, ...) 钳到至少跑 1 遍（不会空转返回 0 遍）
    text = "他顿时一惊。"
    fixed, per_pass = mod.apply_fixes_iterative(text, max_passes=0)
    assert len(per_pass) >= 1
    assert "顿时" not in fixed


# ────────────────────────────────────────────────────────────────────
# 7. generate_dialogue_guide：对话达标 → 空；不足 → 含 summary + 块
# ────────────────────────────────────────────────────────────────────
def test_generate_dialogue_guide_returns_empty_when_target_met():
    text = "随便一段文本。"
    # profile dialogue_ratio >= 0.40 → 直接返回 []
    guides = mod.generate_dialogue_guide(text, {"dialogue_ratio": 0.55})
    assert guides == []


def test_generate_dialogue_guide_flags_long_narrative_blocks():
    # 构造一段 >=3 行连续叙述（无引号开头）→ 应被识别为待改写块
    narrative_lines = "\n".join(f"这是第{i}行纯叙述内容，描述场景与动作，没有任何对话发生。" for i in range(6))
    text = narrative_lines + '\n“终于有人说话了。”\n'
    guides = mod.generate_dialogue_guide(text, {"dialogue_ratio": 0.05})
    assert guides, "对话严重不足时应产出指令"
    # 第一条必是 summary
    assert guides[0]["type"] == "summary"
    assert "deficit_chars" in guides[0]
    assert guides[0]["deficit_chars"] >= 0
    # 后续条目是 dialogue_insertion 类型且带定位信息
    insertions = [g for g in guides if g["type"] == "dialogue_insertion"]
    assert insertions
    assert "location" in insertions[0]
    assert insertions[0]["narrative_lines"] >= 3


# ────────────────────────────────────────────────────────────────────
# 8. analyze_repair_potential：聚合 profile → issues/repairs 分流
# ────────────────────────────────────────────────────────────────────
def test_analyze_repair_potential_detects_banned_and_structure():
    # 含禁用词 + 短句堆叠（高逗句比反例：句号多逗号少）
    text = "他顿时一惊。她紧锁眉头。他显然慌了。\n他淡淡地说。\n"
    result = mod.analyze_repair_potential(text)
    # 输出契约字段齐全
    for key in ("profile_before", "issues", "repairs_programmatic",
                "repairs_llm_needed", "can_fix_programmatically", "needs_llm"):
        assert key in result
    # 禁用词命中 → issues 里有 banned_words 维度 + 程序化修复项
    dims = {iss["dim"] for iss in result["issues"]}
    assert "banned_words" in dims
    assert "ai_tags" in dims
    assert "禁用词替换（fix_banned_words）" in result["repairs_programmatic"]
    assert "AI 对话标签替换（fix_ai_tags）" in result["repairs_programmatic"]
    # 计数字段与列表长度一致
    assert result["can_fix_programmatically"] == len(result["repairs_programmatic"])
    assert result["needs_llm"] == len(result["repairs_llm_needed"])


def test_analyze_repair_potential_empty_text_does_not_crash():
    # 空文本：profile 各项为 0/空，不抛异常，issues 不含禁用词维度
    result = mod.analyze_repair_potential("")
    assert isinstance(result["issues"], list)
    dims = {iss["dim"] for iss in result["issues"]}
    assert "banned_words" not in dims  # 空文本无禁用词命中
    assert result["can_fix_programmatically"] == len(result["repairs_programmatic"])
