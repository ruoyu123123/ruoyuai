# Regression Gold Suite（v22.5 L10）

## 用途

业界 2026 共识：**每个 production regression 应自动变 test case**。

本目录积累历史高分 / 低分章节的「契约 snapshot」：
- 当系统改 prompt / scanner / manifest schema 后跑回归
- 验证「同样的 input_context 仍能产出 expected_behavior」

## 目录结构

```
regression_gold_suite/
├── success/      # 高分章节 case（score ≥ 7.5 自动 ingest）
│   └── success_chXXX_YYYYMMDD.json
├── failure/      # 低分章节 case（score ≤ 5.0 自动 ingest）
│   └── failure_chXXX_YYYYMMDD.json
└── README.md
```

## 自动 ingest 流程

```bash
python core/scripts/regression_test_learner.py <project> auto_ingest
```

会扫所有有 score 的章节，按阈值自动归类：
- ≥ 7.5 → success/
- ≤ 5.0 → failure/

## 跑回归

```bash
python core/scripts/regression_test_learner.py <project> run_regression
```

当前版本仅 schema 兼容性检查（实际重跑 writer 需 Agent tool spawn）。

## 触发时机

- 修改任何 agent prompt 后
- 修改 scanner 阈值后
- 修改 build_manifest 字段后
- 修改 schema 后
- 升级 v 版本前
