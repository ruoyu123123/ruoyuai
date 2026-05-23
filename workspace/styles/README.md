# 风格库（styles）

这个目录用来存放从参考小说蒸馏出的「作者风格档案」，供后续写作时调用。

## 怎么生成

```
/distill-style <小说链接 / 本地 txt 路径>
```

系统会逐章蒸馏，全书完成后在本目录下产出：

```
workspace/styles/{书名}/
├── 作者风格_FINAL.json     # 风格档案（35 维度）
├── skill_FINAL.md          # 写作 skill（注入 writer agent）
├── chapter_ranges.jsonl    # 章节范围
├── distillation_log.md     # 蒸馏过程日志
├── 蒸馏进度/                # 逐章中间数据
└── ...
```

## 注意

- **不要把第三方版权小说的蒸馏产物上传到公开仓库**——蒸馏数据里通常包含原文摘录
- 自蒸馏自有原创作品没问题
- 蒸馏可能很慢（2000+ 章的书要逐章跑），这是设计上的硬性规则，防止采样偷懒

## 字段说明

参考 `core/claude-home/templates/distill_3ch_agent_brief.md`。
