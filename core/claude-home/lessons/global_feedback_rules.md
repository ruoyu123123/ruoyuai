<!-- 2026-06-18 精简：从1106行→~140行。纯重复CLAUDE.md/过时条目已移除。独立教训保留。 -->
# 🔴 全局 feedback 规则汇编（随 exe 出货）

> 随 exe 出货：frozen 环境无开发机 memory 时，gen_writer/build_manifest fallback 读本文件。
> 只保留 CLAUDE.md 不覆盖的独立操作教训。完整规则见 CLAUDE.md + memory/*.md。

---

## feedback-author-goldstandard-comparison-gate

机械质检全过 ≠ 仿写到位。必拿真作者原文金标准比调性。

- **情绪标点偏低 = 喜剧引擎没落地的代理信号**（实证：跑偏稿感叹号 0.2 vs 作者 4.9 = 24x↓）
- 量化走 `core/scripts/replication_fidelity_check.py`（标点/句段 vs 作者基线）
- **新书必拷双文件**：`作者风格_FINAL.json`→`作者风格.json` + `skill_FINAL.md`→`作者风格_skill.md`。漏 skill = writer 只有干数字缺笔法 → 跑偏
- 跨栈对比：让 gen-model 也诊断（比只 Claude 自评更能照出目标模型仿没仿到）

---

## feedback-build-manifest-cluster-brief-injection-gate

**坑1**：`build_manifest.py` 的 `inject_event_cluster_context` 只在 cluster status ∈ active 白名单（`in_progress/writer_done/splitter_done/done/进行中/已完成`）时注入 brief。`/outline` 写的 status 若为孤儿值（如 `active`）→ brief 丢失 → writer 偏短偏离。

**必做检查**：`/outline` 后验 `ch_001.json` 的 `event_cluster_context.mode == 'on'`，否则改 status 为 `in_progress`。

**坑2**：负例 few-shot 才绑得住 flash 题材惯性（抽象禁令压不过 prior）。点名禁止具体形态 > 抽象规则。

**坑3**：走向卡必多 agent 对抗验证（self-assessment 会漏 blocker）。

---

## feedback-cluster-distill-v2-charter

蒸馏复刻验证按 cluster 颗粒度（废 drill 单段）。两档：chapter 中检 → cluster 终验。

- **cluster 复刻拆 3 段 sub-call**（≤5000 字/call · wall-clock ≤10min）防 502 EOF
- **cluster 评分 6 维新增**：arc 形状拟合 / emotion_curve cosine≥0.7 / 衔接模板覆盖≥70% / kicker 分布 / scene_summary_ratio 偏差≤15% / voice_pack 合规
- **Article 6 回灌**：复刻产物灌 gen_writer 写同 cluster → reagan_shape≤1 等价类偏差 / SFS 差≤5 → 不通过 exit 2 拦截

---

## feedback-dialogue-quote-distill-bug

蒸馏档案常误用「」（LLM 总结包裹符），实际原文用 ""（U+201C/U+201D）。

- 校验时机：distill-style step 5 / write step 1 / outline step 3
- 用 Python codepoint 校验原文真实引号，不信 LLM 自报
- 项目锁定：`用户偏好.json.style_preferences.dialogue_quote_style`
- 引号分用途：真对话 "" / 内心独白「」/ 专有名词《》/ 引文『』

---

## feedback-dialogue-quote-unicode-distinction

批量拆段脚本必须 Unicode codepoint 区分左/右引号（U+201C ≠ U+201D）。

- 对话段（含未闭合引号 opens > closes）禁止按句末拆段，合并直到闭合
- 段内 `\n` 单换行切开未闭合引号 → 也要合并
- ❌ 不用 `text.count('"')` 当万能双引号计数

---

## feedback-distill-sfs-multi-ref

phase-3 SFS 评分必须 `--multi-ref-from-dir <原文目录> --multi-ref-count 5`。单 ref 对高方差作者失真（实证：蛊真人 v0 单ref 67→多ref 84，+17 分全是测量误差）。数据<30 章可用单 ref。

---

## feedback-inverted-modifier-sentence-mold-overuse

gen-model 写作盲区：「前置长定语+的+主语后置」倒装句式模具反复用（摸出手机的陆参/愣住的陆参）。机械 scanner 查不出（只查同主语 streak，漏查同语法骨架）。

- **修复**：`gen_fixer.py --mode comprehensive` 能治（倒装 64→13）
- ⚠️ `--mode validator-repair` 在 13k 草稿稳定 no-op（gen-model 偷懒回显）→ 改走 comprehensive

---

## feedback-malformed-toolcall-fewshot-poisoning

长会话工具调用反复畸形 = Claude Code bug#62344 上下文 few-shot 自我投毒。触发：长会话+大文件+1400行XML式skill。

- retry 最糟（照抄坏模板滚雪球）
- 唯一修复 = `/clear` 开新会话（前写 resume 备忘到磁盘）
- 预防：长流程少重复 Read 超长文件；能拆短会话就拆

---

## feedback-no-micro-task-workaround

遇 502/限流/超时，禁止拆小 agent 颗粒度绕过。正确做法：暂停 → 向用户说明 → 让用户决策（等/切 provider/暂停 plan）。拆小颗粒度产次品数据（缺 cluster continuity）。

---

## feedback-no-screenplay-stage-directions-in-novels

连续小说章末严禁任何场景过渡（剧本体 + 文学过渡都禁）。章末 = 钩子不是收束。

**章末 cliffhanger 4 条件**（不满足 = AI 装神弄鬼）：
1. 强锚定到具体伏笔/secret（不要诡异氛围）
2. 优先钩到下一个 cluster（1-cluster 距离）
3. 风格匹配整书设定
4. 末句 = 具体、可验证的异常（不要抽象"不对劲"）

**banned**：`（镜头XX）` / `*`/`***` 分隔符 / 听觉淡出 / 视觉淡出 / 物件全知镜头 / 时间收束句。

---

## feedback-no-token-saving

脚本/agent 不主动截断 input/context，全量传 LLM。禁止 `[:8000]` 等截断。摘要类 UI 展示可截，传给 LLM 的不截。

---

## feedback-smart-side-characters-no-dumbing-down

所有配角都是聪明人，有算计有城府。信息差喜剧靠主角独有硬信息（只有他知道双开盘），不靠别人犯蠢。配角降智 = 主角优势廉价化。

---

## feedback-verify-stderr-not-exitcode

验证脚本必须查 stderr 的 `Traceback|KeyError|AttributeError|TypeError|ValueError`，不能信 exit code 或编排器 N/N 汇总（会吞 exit=1 崩溃）。

- 跑法：`python x.py ... 2> err.txt` 然后 grep err.txt
- Windows 专属问题需 PowerShell 复验（os.replace 占用/文件锁/中文路径）
- 大改后跑敌对验证（独立 agent 实跑复现）
