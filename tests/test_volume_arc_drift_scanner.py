#!/usr/bin/env python3
"""volume_arc_drift_scanner.py 专属回归测试（零 LLM / 零联网 / 仅标准库）。

已有间接覆盖（tests/test_volume_arc_drift_vol.py）已钉死纯辅助函数：
  - _cluster_vol（vol→volume→parent_me 回退）
  - _kw（段内 2-gram 不跨标点拼假 bigram）
  - _current_vol（复用 _cluster_vol fallback）

本文件**聚焦尚未被覆盖的核心确定性逻辑**——即 scan() 的卷级漂移判据本身 +
main() CLI 退出码契约：
  - 所有 SKIP（数据不足）早退分支：无在写 cluster / 大势卡无本卷终点 / 无 key_milestones。
  - progress 计算的两条路：ME 池存在 → 用【卷 ME 完成度】；ME 池缺 → 退回 cluster 计数。
  - _me_vol 的卷字段解析（volume→vol→id 锚定 [Vv]\\d+）。
  - milestone 覆盖率（关键词重叠判「已触及」）+ 账本 summary 并入 written_text。
  - 漂移判据阈值 progress>=0.5 且 coverage < progress-0.25 → 报 VOLUME_ARC_DRIFT(advisory)。
  - advisory 顾问位不变量（gate_level=advisory，绝不 hard_gate）。
  - main() CLI：无 _数据库 → SKIP exit 0；有 issue → exit 1；无 issue → exit 0。

scan() 是纯函数（读 JSON、纯算），故 in-process 直接调真函数锁真实行为；
main() 含 sys.exit，故 in-process 捕 SystemExit 取退出码（不 mock 被测逻辑）。
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import volume_arc_drift_scanner as mod  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────
# 工具：造项目目录 + 写三个数据源
# ──────────────────────────────────────────────────────────────────────────
def _mk_project() -> Path:
    # 🔴 2026-07-04：content_backend_available() 查真文件系统（venv/infer 脚本/模型目录），
    # 本机若已备好 bge 模型会恒真——不像旧 EMBED_BACKEND 是环境变量、每测试后手工清。这里
    # 顺带把它重置为 False（每个测试的第一行都会调 _mk_project，早于各测试自己的显式覆盖·
    # 防跨测试非确定性污染又不依赖 pytest fixture——本文件底部 __main__ 自跑器不过 pytest）。
    import embedding_store
    embedding_store.content_backend_available = lambda: False
    proj = Path(tempfile.mkdtemp()) / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write(proj: Path, name: str, obj) -> None:
    (proj / "_数据库" / name).write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _write_all(proj: Path, dashishi=None, shijianji=None, ledger=None) -> None:
    if dashishi is not None:
        _write(proj, "大势卡.json", dashishi)
    if shijianji is not None:
        _write(proj, "事件簇.json", shijianji)
    if ledger is not None:
        _write(proj, "故事块摘要.json", ledger)


# ══════════════════════════════════════════════════════════════════════════
# SKIP（数据不足）早退分支 —— 全部返回空 issues，且带 _note
# ══════════════════════════════════════════════════════════════════════════
def test_scan_skip_when_no_active_cluster():
    """无在写/已写 cluster → cur_vol=None → 直接 SKIP，issues 为空。"""
    proj = _mk_project()
    # cluster 既未落章也非 active 状态 → _current_vol 返 None
    _write_all(proj,
               dashishi={"volumes": [{"vol": 1, "key_milestones": ["夺旗"]}]},
               shijianji={"clusters": [{"volume": 1, "status": "candidate"}]})
    r = mod.scan(proj)
    assert r["issues"] == [], r
    assert "_note" in r and "无在写" in r["_note"], r


def test_scan_skip_when_no_volume_endpoint():
    """有在写 cluster 但大势卡无本卷终点定义 → SKIP。"""
    proj = _mk_project()
    _write_all(proj,
               dashishi={"volumes": [{"vol": 9, "key_milestones": ["别卷"]}]},
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 3], "status": "已完成"}]})
    r = mod.scan(proj)
    assert r["issues"] == [], r
    assert "大势卡无 vol1" in r["_note"], r


def test_scan_skip_when_no_milestones():
    """本卷存在但 key_milestones 为空/缺失 → SKIP。"""
    proj = _mk_project()
    _write_all(proj,
               dashishi={"volumes": [{"vol": 1, "key_milestones": []}]},
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 3], "status": "已完成"}]})
    r = mod.scan(proj)
    assert r["issues"] == [], r
    assert "无 key_milestones" in r["_note"], r


# ══════════════════════════════════════════════════════════════════════════
# _me_vol —— ME 卷字段解析
# ══════════════════════════════════════════════════════════════════════════
def test_me_vol_volume_vol_and_id_anchor():
    """_me_vol 在 scan 内部·这里通过构造数据驱动其行为间接断言：
    ME 的卷归属 volume → vol → id 锚定 [Vv]\\d+（不裸吃 me_id 里非卷号数字）。"""
    proj = _mk_project()
    # 3 个 ME 全归 vol1：分别用 volume / vol / id="ME-V1-03"
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["甲里程碑", "乙里程碑"]}],
        "major_events": [
            {"volume": 1, "status": "completed"},          # volume 字段
            {"vol": 1, "status": "completed"},              # vol 字段
            {"id": "ME-V1-03", "status": "scheduled"},      # id 锚定，未完成
        ],
    }
    # 一个已写 cluster（含触及关键词），让流程走到 progress 算式
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 3], "status": "已完成",
                    "scope_summary": "甲里程碑 达成"}]})
    r = mod.scan(proj)
    # 3 个 vol1 ME，2 个 completed → progress = 2/3 ≈ 0.67（证明 3 个 ME 都被归到 vol1）
    assert r["progress"] == round(2 / 3, 2), r


def test_me_vol_id_anchor_ignores_non_volume_digits():
    """id="ME-2024-001"（无 [Vv] 前缀）→ _me_vol 不应把 2024/001 当卷号 → 该 ME 不归本卷。"""
    proj = _mk_project()
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["阿尔法"]}],
        "major_events": [
            {"volume": 1, "status": "completed"},
            {"id": "ME-2024-001", "status": "completed"},  # 无 V/v → 不归 vol1
        ],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 3], "status": "已完成",
                    "scope_summary": "阿尔法 完成"}]})
    r = mod.scan(proj)
    # 只 1 个 ME 归 vol1 且 completed → progress = 1/1 = 1.0（若误吃 2024 当卷号则会变 0.5）
    assert r["progress"] == 1.0, r


# ══════════════════════════════════════════════════════════════════════════
# progress 双路：ME 池存在 vs 缺失退回 cluster 计数
# ══════════════════════════════════════════════════════════════════════════
def test_progress_falls_back_to_cluster_count_when_no_me_pool():
    """无 ME 池数据 → progress = written/vol_clusters（带标记的回退路）。"""
    proj = _mk_project()
    # 大势卡无 major_events → 退回 cluster 计数；本卷 4 个 cluster，2 个已写
    _write_all(proj,
               dashishi={"volumes": [{"vol": 1, "key_milestones": ["终点"]}]},
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 3], "status": "已完成",
                    "scope_summary": "无关内容"},
                   {"volume": 1, "chapter_range": [4, 6], "status": "已完成",
                    "scope_summary": "无关内容"},
                   {"volume": 1, "status": "candidate"},
                   {"volume": 1, "status": "candidate"},
               ]})
    r = mod.scan(proj)
    # 2 written / 4 vol_clusters = 0.5
    assert r["progress"] == 0.5, r


# ══════════════════════════════════════════════════════════════════════════
# 漂移判据 —— 核心算法分支
# ══════════════════════════════════════════════════════════════════════════
def test_drift_fires_when_coverage_lags_progress():
    """卷过半（progress>=0.5）且 coverage 明显落后（< progress-0.25）→ 报 VOLUME_ARC_DRIFT。"""
    proj = _mk_project()
    # 3 个 ME 全 completed → progress=1.0；2 个 milestone 但已写内容与其零重叠 → coverage=0
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["夺取王座", "击败魔王"]}],
        "major_events": [
            {"volume": 1, "status": "completed"},
            {"volume": 1, "status": "completed"},
            {"volume": 1, "status": "completed"},
        ],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 9], "status": "已完成",
                    "scope_summary": "主角在村庄里种田钓鱼喝茶聊天"}]})
    r = mod.scan(proj)
    assert r["progress"] == 1.0 and r["milestone_coverage"] == 0.0, r
    assert len(r["issues"]) == 1, r
    iss = r["issues"][0]
    assert iss["code"] == "VOLUME_ARC_DRIFT", iss
    assert iss["vol"] == 1
    # 两个未触及 milestone 都被收集（截断 40 字）
    assert any("夺取王座" in u for u in iss["untouched_milestones"]), iss
    assert any("击败魔王" in u for u in iss["untouched_milestones"]), iss


def test_drift_gate_level_is_advisory_never_hard_gate():
    """🔴 不变量：漂移 issue 永远 advisory（北极星⑤·绝不 hard_gate），退出码也只 1。"""
    proj = _mk_project()
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["登顶绝巅"]}],
        "major_events": [{"volume": 1, "status": "completed"}],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 9], "status": "已完成",
                    "scope_summary": "完全无关的日常琐事描写流水账"}]})
    r = mod.scan(proj)
    assert r["issues"], r
    for iss in r["issues"]:
        assert iss["gate_level"] == "advisory", iss
        assert iss["gate_level"] != "hard_gate"
        assert iss["severity"] == "warning", iss


def test_no_drift_when_coverage_keeps_up():
    """milestone 被已写内容触及（关键词重叠）→ coverage 跟上 progress → 不报漂移。"""
    proj = _mk_project()
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["夺取王座", "击败魔王"]}],
        "major_events": [
            {"volume": 1, "status": "completed"},
            {"volume": 1, "status": "completed"},
        ],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 9], "status": "已完成",
                    "scope_summary": "主角夺取王座之后又击败魔王登基为帝"}]})
    r = mod.scan(proj)
    assert r["progress"] == 1.0, r
    assert r["milestone_coverage"] == 1.0, r
    assert r["issues"] == [], r


def test_no_drift_when_progress_below_half():
    """卷推进不足一半（progress<0.5）→ 即便 coverage=0 也不报（阈值守门）。"""
    proj = _mk_project()
    # 5 个 ME 仅 1 个 completed → progress=0.2 < 0.5
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["登顶绝巅夺旗"]}],
        "major_events": [
            {"volume": 1, "status": "completed"},
            {"volume": 1, "status": "scheduled"},
            {"volume": 1, "status": "scheduled"},
            {"volume": 1, "status": "scheduled"},
            {"volume": 1, "status": "scheduled"},
        ],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 3], "status": "已完成",
                    "scope_summary": "村庄日常种田钓鱼喝茶聊天"}]})
    r = mod.scan(proj)
    assert r["progress"] == 0.2, r
    # 已写内容与里程碑零关键词重叠 → coverage=0；但 progress<0.5 守门 → 仍不报漂移
    assert r["milestone_coverage"] == 0.0, r
    assert r["issues"] == [], "progress<0.5 不该触发漂移"


def test_ledger_summary_counts_toward_coverage():
    """账本 故事块摘要.json 的章 summary 并入 written_text → 即便 scope_summary 不含关键词，
    账本 summary 命中 milestone 也算「已触及」。"""
    proj = _mk_project()
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["屠龙"]}],
        "major_events": [{"volume": 1, "status": "completed"}],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"cluster_id": "cluster_001", "volume": 1,
                    "chapter_range": [1, 3], "status": "已完成",
                    "scope_summary": "毫不相干的开场"}]},
               ledger={"clusters": [
                   {"cluster_id": "cluster_001",
                    "chapters": {"1": {"summary": "主角终于屠龙成功"}}}]})
    r = mod.scan(proj)
    # scope_summary 无「屠龙」但账本章 summary 有 → coverage 命中
    assert r["milestone_coverage"] == 1.0, r
    assert r["issues"] == [], r


# ══════════════════════════════════════════════════════════════════════════
# main() CLI 退出码契约 —— in-process 捕 SystemExit
# ══════════════════════════════════════════════════════════════════════════
def _run_main(proj: Path):
    """in-process 跑 main()，捕 SystemExit 取退出码（不 mock 被测逻辑）。"""
    old_argv = sys.argv
    sys.argv = ["volume_arc_drift_scanner.py", str(proj)]
    out, err = io.StringIO(), io.StringIO()
    code = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            mod.main()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    finally:
        sys.argv = old_argv
    return code, out.getvalue(), err.getvalue()


def test_main_skip_when_no_db_dir_exits_0():
    """无 _数据库 目录 → [SKIP] → exit 0（数据不足不致命）。"""
    proj = Path(tempfile.mkdtemp()) / "空书"
    proj.mkdir(parents=True, exist_ok=True)  # 不建 _数据库
    code, _out, err = _run_main(proj)
    assert code == 0, (code, err)
    assert "[SKIP]" in err, err


def test_main_exits_1_on_drift():
    """有漂移 issue → main 退出码 1，且 stdout 是合法 JSON 含 VOLUME_ARC_DRIFT。"""
    proj = _mk_project()
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["称霸天下"]}],
        "major_events": [{"volume": 1, "status": "completed"}],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 9], "status": "已完成",
                    "scope_summary": "无关琐碎日常絮叨"}]})
    code, out, _err = _run_main(proj)
    assert code == 1, (code, out)
    parsed = json.loads(out)
    assert parsed["issues"][0]["code"] == "VOLUME_ARC_DRIFT", parsed


def test_main_exits_0_when_no_drift():
    """无漂移（数据不足 SKIP 也算）→ main 退出码 0。"""
    proj = _mk_project()
    # 无在写 cluster → scan 返回空 issues → exit 0
    _write_all(proj,
               dashishi={"volumes": [{"vol": 1, "key_milestones": ["x"]}]},
               shijianji={"clusters": []})
    code, out, _err = _run_main(proj)
    assert code == 0, (code, out)
    parsed = json.loads(out)
    assert parsed["issues"] == [], parsed


# ══════════════════════════════════════════════════════════════════════════
# 🔴 2026-07-04 内容语义 embedding 路径（W6-C 迁移：风格模型→bge 内容模型）
# 北极星③「大势已定」核心哨兵，语义路径不能引入回归——下面锁死默认路径与旧行为逐字节
# 一致，再单独验证 mock 内容后端时语义路径被正确使用。手写 save/restore（不用 pytest
# monkeypatch fixture）——本文件底部 __main__ 直接零参调用 globals() 里的 test_*，需与之兼容。
# ══════════════════════════════════════════════════════════════════════════

def test_content_backend_ready_false_by_default():
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        assert mod._content_backend_ready() is False
    finally:
        embedding_store.content_backend_available = orig


def test_content_backend_ready_true_when_available():
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: True
    try:
        assert mod._content_backend_ready() is True
    finally:
        embedding_store.content_backend_available = orig


def _mk_synonym_project() -> Path:
    """milestone「夺取王座」 vs 已写内容「登上帝位」——同义但字面 2-gram 零重叠的场景。"""
    proj = _mk_project()
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["夺取王座"]}],
        "major_events": [{"volume": 1, "status": "completed"}],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 9], "status": "已完成",
                    "scope_summary": "主角登上帝位统治天下"}]})
    return proj


def test_default_bigram_path_reports_drift_for_synonym_case():
    """🔴 零回归锁：内容后端不可用（默认，_mk_project 已重置）→ match_method=
    bigram_keyword_overlap，milestone「夺取王座」与已写内容「登上帝位」字面 2-gram 零重叠
    → 报 VOLUME_ARC_DRIFT（对照组：证明默认路径与改动前行为逐字节一致）。"""
    proj = _mk_synonym_project()
    # 前置断言：证明这确实是「字面零重叠但语义相同」的场景
    assert not (mod._kw("夺取王座") & mod._kw("主角登上帝位统治天下"))
    r = mod.scan(proj)
    assert r["match_method"] == "bigram_keyword_overlap", r
    assert r["milestone_coverage"] == 0.0, r
    assert len(r["issues"]) == 1 and r["issues"][0]["code"] == "VOLUME_ARC_DRIFT", r


def test_semantic_path_used_when_backend_mocked_no_false_drift():
    """内容后端 mock：同一同义改写场景下，余弦相似度应正确识别「夺取王座」≈
    「登上帝位」→ 不误报漂移。验证语义路径被正确使用（match_method=embedding_cosine）。"""
    proj = _mk_synonym_project()

    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_batch = embedding_store.compute_content_embeddings_batch
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _mock_embed(text):
        # 语义分组：「帝位」/「王座」同指「统治地位」这一概念 → 同向量
        return [1.0, 0.0] if ("帝位" in text or "王座" in text) else [0.0, 1.0]

    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embeddings_batch = lambda texts: [_mock_embed(t) for t in texts]
    embedding_store.compute_content_embedding = _mock_embed
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = mod.scan(proj)
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embeddings_batch = orig_batch
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch

    assert r["match_method"] == "embedding_cosine", r
    assert r["milestone_coverage"] == 1.0, r   # 语义识别为已触及
    assert r["issues"] == [], r                 # 不误报漂移


def test_semantic_touch_floor_env_override():
    """VOLUME_ARC_SEMANTIC_TOUCH_FLOOR 覆盖默认值·非法值回退默认。"""
    old = os.environ.get("VOLUME_ARC_SEMANTIC_TOUCH_FLOOR")
    try:
        os.environ["VOLUME_ARC_SEMANTIC_TOUCH_FLOOR"] = "0.8"
        assert mod._semantic_touch_floor() == 0.8
        os.environ["VOLUME_ARC_SEMANTIC_TOUCH_FLOOR"] = "not_a_float"
        assert mod._semantic_touch_floor() == mod.MILESTONE_SEMANTIC_TOUCH_FLOOR
    finally:
        if old is not None:
            os.environ["VOLUME_ARC_SEMANTIC_TOUCH_FLOOR"] = old
        else:
            os.environ.pop("VOLUME_ARC_SEMANTIC_TOUCH_FLOOR", None)


def test_semantic_path_falls_back_when_encode_fails():
    """内容后端就绪但 embedding 编码异常 → 回退字面 bigram（不崩·不误判为语义路径）。"""
    proj = _mk_synonym_project()

    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _boom(text):
        raise RuntimeError("模拟内容后端编码失败")

    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embedding = _boom
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = mod.scan(proj)
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch

    assert r["match_method"] == "bigram_keyword_overlap", r
    assert r["milestone_coverage"] == 0.0, r


# ══════════════════════════════════════════════════════════════════════════
# 🔴 2026-07-03 Wave-4：语义路径批量 prefetch（一次 prefetch 取代已写内容+逐条
# milestone 各自触发后端调用）。手写 save/restore（不用 monkeypatch fixture）——
# 本文件底部 __main__ 直接零参调用 globals() 里的 test_*，需与之兼容。
# ══════════════════════════════════════════════════════════════════════════

def test_prefetch_called_once_with_written_text_and_milestones():
    """语义路径下 scan() 应一次性 prefetch 已写内容聚合 + 全部 milestone 文本，
    而非已写内容 1 次 + 每条 milestone 各自触发一次后端调用。"""
    proj = _mk_project()
    dashishi = {
        "volumes": [{"vol": 1, "key_milestones": ["夺取王座", "击败魔王", "寻回圣物"]}],
        "major_events": [
            {"volume": 1, "status": "completed"},
            {"volume": 1, "status": "completed"},
        ],
    }
    _write_all(proj, dashishi=dashishi,
               shijianji={"clusters": [
                   {"volume": 1, "chapter_range": [1, 9], "status": "已完成",
                    "scope_summary": "主角登上帝位统治天下"}]})

    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings
    calls = []

    def fake_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)),
                "cache_hits": 0, "computed": len(set(texts))}

    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embedding = lambda t: (
        [1.0, 0.0] if ("帝位" in t or "王座" in t) else [0.0, 1.0])
    embedding_store.prefetch_content_embeddings = fake_prefetch
    try:
        r = mod.scan(proj)
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch

    assert len(calls) == 1, calls
    expected = {"主角登上帝位统治天下", "夺取王座", "击败魔王", "寻回圣物"}
    assert set(calls[0]) == expected, calls[0]
    assert r["match_method"] == "embedding_cosine", r


def test_prefetch_not_called_without_content_backend():
    """默认（内容后端不可用）→ 整段语义分支不执行 → prefetch_content_embeddings 零调用
    （零回归）。"""
    proj = _mk_synonym_project()
    import embedding_store
    orig_prefetch = embedding_store.prefetch_content_embeddings
    calls = []

    def fake_prefetch(texts):
        calls.append(list(texts))
        return {}

    embedding_store.prefetch_content_embeddings = fake_prefetch
    try:
        r = mod.scan(proj)
    finally:
        embedding_store.prefetch_content_embeddings = orig_prefetch

    assert calls == []
    assert r["match_method"] == "bigram_keyword_overlap"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(globals()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    print(f"[volume_arc_drift_scanner] {'ALL OK' if not fails else str(fails)+' FAIL'}")
    sys.exit(1 if fails else 0)
