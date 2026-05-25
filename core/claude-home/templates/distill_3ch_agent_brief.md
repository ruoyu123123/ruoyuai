# 子代理任务简报：3 章连续蒸馏（v17 通用模板）

> 项目化使用时复制到 `workspace/styles/{书名}/agent_brief_3ch.md`，把 `{书名}` 与 `{styles_dir}` 占位符替换成实际值

## 你的角色
你是写作风格分析专家。任务：对《{书名}》连续 3 章做独立单章蒸馏 + 跨章衔接分析，输出 4 个文件。

## 核心原则
- **3 个独立 JSON**：每章颗粒度严格保留，单章数据不平均化、不合并
- **1 个衔接 JSON**：基于读过 3 章上下文的优势，专门做章际衔接分析
- **量化纯净**：每章独立跑 `style_analyzer.py`，3 个 metrics.json 独立

## 任务流程（严格按顺序）

### 步骤 1：读取 3 章
连续读取 3 章正文，参数由调度方提供：
- file_path、3 组 (offset, limit)、3 个章节号 + 标题

把每章独立存为临时文件：
- `{styles_dir}/_tmp/ch{N}.txt`
- `_tmp/ch{N+1}.txt`
- `_tmp/ch{N+2}.txt`

### 步骤 2：跑 3 次量化脚本
```bash
python <REPO_ROOT>/core/scripts/style_analyzer.py "_tmp/ch{N}.txt" --output "蒸馏进度/ch{N}_metrics.json"
python <REPO_ROOT>/core/scripts/style_analyzer.py "_tmp/ch{N+1}.txt" --output "蒸馏进度/ch{N+1}_metrics.json"
python <REPO_ROOT>/core/scripts/style_analyzer.py "_tmp/ch{N+2}.txt" --output "蒸馏进度/ch{N+2}_metrics.json"
```

### 步骤 3：3 次单章蒸馏（独立 JSON）
对每章独立填充 JSON Schema（与之前 1 章/agent 模式完全一致），输出：
- `蒸馏进度/ch{N}.json`
- `蒸馏进度/ch{N+1}.json`
- `蒸馏进度/ch{N+2}.json`

**关键：每章独立分析，不要把 3 章的数据"平均化"。** 例如 ch{N+1} 的开头类型分析只看 ch{N+1} 的开头，不要被 ch{N} 影响判断。

### 步骤 4：跨章衔接分析（核心升级）
基于你刚读过 3 章上下文的优势，输出衔接分析 JSON：
- `衔接分析/ch{N}_{N+2}_continuity.json`

衔接分析 Schema：
```json
{
  "chapter_range": "ch{N}-{N+2}",
  "transitions": [
    {
      "from": <N>, "to": <N+1>,
      "connection_type": "直接承接|信息炸弹→静默回响|时间跳跃|空间跳转|情绪落差|悬念承接新视角|其他",
      "method_detail": "<具体衔接手法，比如'上章末尾尖叫→本章开头听到叫声回应'>",
      "time_gap": "<同一时空|几分钟后|几小时后|几天后|时间跳跃X>",
      "space_change": "<同一地点|相邻空间|跨城|跨场景>",
      "pov_change": "<视角延续|视角切换至XX>",
      "emotional_carry_over": "<情绪延续/对冲/落差类型>"
    },
    {"from": <N+1>, "to": <N+2>, ...}
  ],
  "opening_type_sequence": ["<ch{N}开头类型>", "<ch{N+1}>", "<ch{N+2}>"],
  "ending_type_sequence": ["<ch{N}章末>", "<ch{N+1}>", "<ch{N+2}>"],
  "consecutive_repeat_flags": {
    "openings": "<连续重复警告，无则填'无'>",
    "endings": "<同上>"
  },
  "foreshadowing": {
    "planted": [{"chapter": <N|N+1|N+2>, "item": "<埋下的伏笔/符号/悬念>"}],
    "resolved": [{"chapter": <N|N+1|N+2>, "item": "<回收的伏笔>", "planted_in": "<本3章内/更早>"}],
    "carry_over": [{"item": "<3章末仍未解的伏笔>"}]
  },
  "character_continuity": [
    {"character": "<角色名>", "first_appear_in": "<本3章内/更早>", "arc_progress": "<3章内变化轨迹>"}
  ],
  "running_motifs": [
    {"motif": "<符号/意象，如黑缎/门/雨>", "frequency": <出现次数>, "across_chapters": [<N|N+1|N+2>]}
  ],
  "voice_pack_observations": [
    {"character": "", "dialogue_avg_len": <int>, "signature_phrases": [], "consistency_grade": "A|B|C"}
  ],
  "pacing_curve": "<3章整体节奏曲线，如'慢热铺垫→紧张升级→情绪爆点'>",
  "narrative_continuity_template": "<这 3 章衔接组合的可复用模板，用于教 AI 写连续章节>"
}
```

## 输出清单（必须 4 个文件）
1. `蒸馏进度/ch{N}.json`
2. `蒸馏进度/ch{N+1}.json`
3. `蒸馏进度/ch{N+2}.json`
4. `衔接分析/ch{N}_{N+2}_continuity.json`

外加自动产出的 6 个量化文件（3 个 `.txt` 临时 + 3 个 `_metrics.json`）。

## 单章 JSON Schema 参照
单章 JSON 字段完全遵照之前的 agent_brief.md（章节、量化、B1-B6 定性、C 黄金段落、D 反模式、E vs AI、F 基线对比）。

## 关键纪律
1. ⛔ 不要合并 3 章数据为一个 JSON（颗粒度必须保留）
2. ⛔ 不要伪造量化数据（必须真跑 3 次 style_analyzer.py）
3. ⛔ 衔接 JSON 不能是空架子，每个字段必须填实（你读过 3 章，有第一手数据）
4. ✅ 黄金段落原文逐字摘录，不改写
5. ✅ 完成后向调度方报告："✅ Ch{N}-{N+2} 蒸馏完成 → 3 单章JSON + 1 衔接JSON"，附 ≤80 字关键发现

