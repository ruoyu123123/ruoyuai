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
│   ├── cluster_001_draft/  # 故事块草稿 + cluster_changes.json + pending_tail.txt
│   ├── 第001章/            # splitter 切完后的章节输出
│   ├── 第002章/
│   └── ...
├── _数据库/                 # 34 个核心子系统 JSON
│   ├── 进度.json            # 卷级大纲 + cluster 进度 + 传播债务
│   ├── 事件簇.json          # cluster brief + 走向承接
│   ├── 人物卡.json          # 5 层 Voice DNA + 声音包
│   ├── 世界观.json
│   ├── 世界状态.json
│   ├── 伏笔表.json
│   ├── 故事块摘要.json
│   ├── 地图.json
│   ├── 关系.json
│   ├── 时间线.json
│   ├── 道具.json
│   ├── 事件表.json
│   ├── 场景规则.json
│   ├── 写作经验.json
│   ├── 用户偏好.json
│   └── 作者风格.json        # 蒸馏出的风格档案（可选）
└── _数据库/.wal/            # 各 step 的产物与回执存放区（summary/state_delta/archive/receipt 等）
```

## 字段示例

`core/claude-home/templates/examples/` 下有 2 个完整 schema 示例：

- **scp_anomaly_bureau/** — SCP / 异常局 / 多身体同步题材
- **urban_supernatural_business/** — 都市灵异商业版 / 隐秘组织博弈题材

适合参考字段结构。

## 注意

- 每本小说都是独立 Git 仓库，**不**会被本仓库追踪（`.gitignore` 已排除）
- `_数据库/` 下的 JSON 不作为手动编辑入口，事实状态统一由 `/cluster-save-state` 回库
- `/db` 只用于只读查看、搜索、导出和定位
- 想改设定：当前未保存的 cluster 回草稿层修正后重跑 `/cluster-write`；已保存状态通过后续 cluster 承接变化。想回滚请用 Git tag
