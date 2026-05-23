# 小说项目（projects）

这个目录用来存放你写的每一本小说项目。每本小说一个独立 Git 仓库。

## 怎么开始

```
/write
```

或先看主菜单：直接对助手说「你好」。

## 目录长什么样

每本小说项目结构（系统自动生成）：

```
workspace/novels/{书名}/
├── .git/                   # 独立 Git 仓库（关键节点自动 commit）
├── .gitignore
├── 大纲.md
├── 章节/
│   ├── 第001章.txt         # 章节正文
│   ├── 第001章_changes.json  # writer 产出的 self_eval / waivers
│   └── ...
├── _数据库/                 # 13 个 JSON 数据库
│   ├── 进度.json            # 卷级大纲 + 章级规划 + 传播债务
│   ├── 人物卡.json          # 5 层 Voice DNA + 声音包
│   ├── 世界观.json
│   ├── 伏笔表.json
│   ├── 章纲摘要.json
│   ├── 地图.json
│   ├── 关系.json
│   ├── 时间线.json
│   ├── 道具.json
│   ├── 事件表.json
│   ├── 场景规则.json
│   ├── 写作经验.json
│   ├── 用户偏好.json
│   └── 作者风格.json        # 蒸馏出的风格档案（可选）
└── _数据库/.wal/            # 崩溃恢复日志
```

## 字段示例

`core/claude-home/templates/examples/` 下有 2 个完整 schema 示例：

- **scp_anomaly_bureau/** — SCP / 异常局 / 多身体同步题材
- **urban_supernatural_business/** — 都市灵异商业版 / 隐秘组织博弈题材

适合参考字段结构。

## 注意

- 每本小说都是独立 Git 仓库，**不**会被本仓库追踪（`.gitignore` 已排除）
- `_数据库/` 下的 JSON 别手动改（除非你知道在干嘛），错了会污染下游
- 想改设定请用 `/reconcile`，想回滚请用 Git tag
