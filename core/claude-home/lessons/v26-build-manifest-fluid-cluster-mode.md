# v26 lesson · build_manifest cluster mode fluid 化

## 触发场景
用户原话（2026-05-26）：
> 为什么要补 ch6-11 cluster_002 占位，一个故事块能切出多少章节应该是最后切章节的时候才定的啊

## 根因

`core/scripts/build_manifest.py:461`：
```python
if not self.chapter_plan():
    fatal.append(f"chapter_plan[{self.ch}] 不存在，大纲未覆盖本章")
```

`chapter_plan()` 方法（line 119）：
```python
def chapter_plan(self) -> dict | None:
    prog = self.load("进度", {})
    plans = prog.get("chapter_plan", [])
    for p in plans:
        if p.get("ch") == self.ch or p.get("chapter") == self.ch:
            return p
    return None
```

**问题**：cluster mode 下，cluster_002 的具体章数 + 章细节应由 step 6 splitter 切 cluster_draft.txt 时决定。**预先在 chapter_plan 锁 ch6-11 全部细节 = chapter mode 思维残留**。

调度器主代理被 fatal 误导，给 chapter_plan 加了 ch6-11 6 章占位 —— **违反 CLAUDE.md fluid 涌现哲学**：
> 故事块（cluster）+ 涟漪效应让单卷章数无法预先确定 — 总章数由 ME 触发节奏 + 用户涟漪选择自然涌现

> splitter 推迟到 step 6 · 章节只是输出格式 · 不是迭代单位

## 应修方案（v26 + 1 后续 patch）

### Option A（推荐 · 小改动）
`build_manifest.py:461` 把 fatal 降为 warning：
```python
if not self.chapter_plan():
    # v26 cluster mode fluid 化: chapter_plan[N] 缺失不阻塞 · writer 从 事件簇.json.clusters[N] 取信息
    warning.append(f"chapter_plan[{self.ch}] 不存在 (cluster mode fluid · 信息从 事件簇.json 取)")
```

### Option B（更彻底 · cluster-aware fallback）
`chapter_plan()` 加 cluster fallback：
```python
def chapter_plan(self) -> dict | None:
    prog = self.load("进度", {})
    plans = prog.get("chapter_plan", [])
    for p in plans:
        if p.get("ch") == self.ch or p.get("chapter") == self.ch:
            return p
    # v26 fallback: 从 事件簇.json 取 cluster_<N> 的 scope_summary + 起首 scene 作雏形
    shijianji = self.load("事件簇", {})
    for cluster in shijianji.get("clusters", []):
        cr = cluster.get("chapter_range", [])
        if cr and len(cr) == 2 and cr[0] <= self.ch <= cr[1]:
            if cluster.get("scene_storyboard"):
                first_scene = cluster["scene_storyboard"][0]
                return {
                    "ch": self.ch,
                    "vol": cluster.get("vol"),
                    "cluster": cluster.get("cluster_id"),
                    "title": cluster.get("title", "") + " · 起首",
                    "characters": first_scene.get("characters", []),
                    "key_events": [s.get("title", "") for s in cluster["scene_storyboard"][:3]],
                    "scene_type": [first_scene.get("type", "悬疑")],
                    "_fluid_from_event_cluster": True,
                }
    return None
```

## 中期方案

- step 1 build_manifest 在 cluster mode (key 含 cluster_) 下完全不调 chapter_plan(N) · 改读 事件簇.json.clusters[N]
- chapter_plan 字段只为 chapter mode 保留（v26 已废 chapter mode · 但 chapter_plan 数据结构仍被其他脚本读 · 不能简单删）

## 学到的元规则

「调度器主代理不应被 fatal 误导加非 v26 fluid 哲学的数据。看到 fatal 优先看错误信息是否合理，而不是机械补缺。」

## 相关文件

- `core/scripts/build_manifest.py:119-125, 461`
- `workspace/novels/城南火葬场夜班/_数据库/进度.json`（被错误加 ch7-11 已修正）
- `workspace/novels/城南火葬场夜班/_数据库/事件簇.json.clusters[1]`（真实 cluster_002 信息源）

## 沉淀到 memory

候选 lesson key: `feedback_build_manifest_cluster_mode_fluid_chapter_plan_fallback`
