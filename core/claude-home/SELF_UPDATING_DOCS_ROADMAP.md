# 自更新文档路线图（v22.5 L18 / Karpathy LLM Wiki 风格）

## 背景

Andrej Karpathy 2026.04 概念：LLM 读源 → 编译 wiki → 自动更新。每 git commit 触发 agent 更新文档。

我们的痛点：
- README / CACHE_STRATEGY / MODEL_ROUTING / SELF_EVOLVING_ROADMAP / LEARNING_COVERAGE_MAP 等 12+ 文档手动维护
- 每加新 feature 经常忘改文档
- 文档过期 = 用户用不准

## 路线图

### Phase 1: 文档过期检测器
`docs_freshness_check.py`：
- 每个 docs/*.md 含 last_updated_at
- 扫各文档引用的脚本/agent 文件 mtime
- 文档 mtime < 引用文件 mtime → 标 STALE

### Phase 2: 自更新 hook
- post-commit hook：spawn doc-updater agent
- agent 读 commit diff → 决定哪些文档应更新
- 输出建议（不直接改）→ 用户审

### Phase 3: 完整 Karpathy LLM Wiki
- core/claude-home/wiki/ 作为 second brain
- 每 commit 自动更新 wiki entries
- 互链 / topic summary / contradiction detection

## 紧迫度

- 当前 30+ docs 手动维护成本上升
- Phase 1 立即做（轻量）
- Phase 2/3 累积更多 docs 后再做

## 参考

- [Karpathy LLM Wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [Build Karpathy's Wiki in 90 min](https://startupgtm.substack.com/p/self-updating-ai-wiki-knowledge-base)
