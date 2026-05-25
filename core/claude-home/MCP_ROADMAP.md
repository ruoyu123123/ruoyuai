# MCP（Model Context Protocol）接入路线图（v21 P9.2）

## 背景

MCP 2026 已成 de facto 标准 — Anthropic 2024.11 开源 → OpenAI / Google / xAI 全部采纳。可类比 「AI 的 USB-C」。

已有专为小说写作设计的 MCP server：
- **Book Series MCP**：character / plot / world building / continuity 跟踪
- **Writer MCP**：character knowledge + relationships
- **Claude-LaTeX MCP**：学术/科幻/哲学创作

我们 18 个数据库 + 50+ 脚本 → 都可暴露为 MCP server。

## 我们暴露 MCP 的价值

| 受益方 | 用途 |
|---|---|
| **用户** | 在 Cursor / Copilot / Claude Desktop 不下载本系统也能用我们的数据库 |
| **其他作家工具** | Sudowrite / NovelCrafter 可接入我们的 cross_chapter scanner |
| **本地编辑器** | VSCode / JetBrains 通过 MCP 接入小说写作上下文 |
| **跨周目 / 跨作者** | 多本小说共享 MCP server 实例 |

## MCP server 设计

```
ruoyu-fiction-mcp-server/
├── server.py              # MCP 主入口
├── tools/
│   ├── read_database.py   # 读 34 子系统 JSON
│   ├── run_scanner.py     # 调 18 cross_chapter scanner
│   ├── build_manifest.py  # 生成 manifest
│   ├── apply_change.py    # 应用 _changes.json
│   └── ...
├── resources/
│   ├── character_arc      # 暴露为 MCP resource
│   ├── world_state
│   ├── foreshadowing
│   └── ...
└── prompts/
    ├── outline_planner    # 暴露为 MCP prompt template
    ├── voice_keeper
    └── ...
```

## 实施分阶段

### Phase 1（quick win · 1-2 周）
- 用 `mcp-python-sdk` 包装 5 个核心工具（read_database / build_manifest / clock_engine / fate_engine / cross_chapter_pattern_scan）
- 测试 Claude Desktop 接入
- 发布到 modelcontextprotocol/servers GitHub awesome list

### Phase 2（中期 · 1 月）
- 完整 18 数据库 + 18 scanner + 主要 agent prompt 全 MCP 化
- 写 MCP server 文档
- 注册到 LobeHub / awesome-mcp-servers

### Phase 3（长期 · 多人协作）
- 多本小说共享 MCP server（持久化数据库）
- 用户级 vs 项目级数据库分离
- 跨编辑器互操作

## 参考实现

- [Book Series MCP](https://lobehub.com/mcp/rlryals-book-series-mcp) — 同类小说 MCP 范例
- [writer-mcp GitHub](https://github.com/huangjien/writer-mcp) — 角色知识 MCP
- [MCP Servers GitHub](https://github.com/modelcontextprotocol/servers) — 官方 server list

## 实施 checklist

- [ ] Phase 1: 5 个核心 tools 包装 + Claude Desktop 测试
- [ ] Phase 2: 18 数据库 + 18 scanner 全暴露
- [ ] Phase 3: 多项目共享 + 跨编辑器互操作

## 与 SKILL.md 的关系

- **MCP server** = 「工具 + 资源 + 数据」的 runtime（动态）
- **SKILL.md** = 「instructions + scripts」的 package（静态）

两者互补：
- 简单可复用知识 → SKILL.md
- 复杂数据库 + 实时计算 → MCP server
