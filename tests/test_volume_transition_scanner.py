#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""volume_transition_scanner 测试 — 三规则（钩零命中/cast硬重置/卷首空开）+ shadow + 边界。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import volume_transition_scanner as mod  # noqa: E402

ENV = "VOLUME_TRANSITION_MODE"


def _set_mode(v):
    old = os.environ.get(ENV)
    if v is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = v
    return old


def _restore(old):
    if old is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = old


def _mk_project(shijianji=None, ledger=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps(shijianji or {"clusters": []}, ensure_ascii=False), encoding="utf-8")
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(ledger or {"clusters": []}, ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_skeleton():
    old = _set_mode("off")
    try:
        proj = _mk_project()
        rep = mod.scan(proj)
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
        assert rep["issues"] == []
    finally:
        _restore(old)


def test_no_db_skips():
    old = _set_mode("active")
    try:
        proj = Path(tempfile.mkdtemp())  # 无 _数据库
        rep = mod.scan(proj)
        assert "无 _数据库" in rep.get("note", "")
        assert rep["issues"] == []
    finally:
        _restore(old)


def test_single_volume_skips():
    """只有一个卷·无过渡点·skip。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘"]},
            ]})
        rep = mod.scan(proj)
        assert "卷数 < 2" in rep.get("note", "")
        assert rep["issues"] == []
    finally:
        _restore(old)


def test_hook_miss_triggers():
    """规则①：上卷末 hook_text 关键词在下卷首零兑现 → 触发。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "林尘大战王虎",
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙将在北境苏醒，吞噬星辰"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘"],   # 共享 cast 排除规则②触发
                 "scope_summary": "他在山间打坐修炼",
                 "scene_storyboard": [{"summary": "他在山间打坐修炼"}]},
            ]})
        rep = mod.scan(proj)
        codes = [i["code"] for i in rep["issues"]]
        assert mod.HOOK_MISS_CODE in codes, rep
        assert rep["close_hook_coverage"] < mod.HOOK_COVERAGE_FLOOR
    finally:
        _restore(old)


def test_hard_reset_triggers():
    """规则②：上下卷 cast overlap=0 → 触发。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "决战古城"},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["陌生甲", "陌生乙"],   # 完全不重叠
                 "scope_summary": "新地点新人物",
                 "scene_storyboard": [{"summary": "陌生甲来到新的城里寻人"}]},
            ]})
        rep = mod.scan(proj)
        codes = [i["code"] for i in rep["issues"]]
        assert mod.HARD_RESET_CODE in codes, rep
        assert rep["cast_overlap_ratio"] == 0
    finally:
        _restore(old)


def test_clean_transition_passes():
    """完整 hook + cast 共享 + 新 setting 锚词 → PASS。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "黑龙苏醒在北境",
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙北境苏醒吞噬"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘", "新弟子"],  # 林尘延续
                 "scope_summary": "黑龙北境苏醒，林尘抵达新的城关",
                 "scene_storyboard": [{"summary": "林尘抵达新的城关，黑龙的影子掠过北境"}]},
            ]})
        rep = mod.scan(proj)
        codes = [i["code"] for i in rep["issues"]]
        assert mod.HOOK_MISS_CODE not in codes
        assert mod.HARD_RESET_CODE not in codes
        # 有新 cast(新弟子) + setting 锚词「新的」「抵达」 → 规则③不触发
        assert mod.EMPTY_OPEN_CODE not in codes
    finally:
        _restore(old)


def test_shadow_records_no_issue():
    """shadow 高违规 → issues 不暴露 + 不上 verdict。"""
    old = _set_mode("shadow")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘"],
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙将在北境苏醒"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["陌生甲"], "scope_summary": "他在山间打坐",
                 "scene_storyboard": [{"summary": "他在山间打坐修炼"}]},
            ]})
        rep = mod.scan(proj)
        assert rep["mode"] == "shadow"
        assert rep["issues"] == []   # shadow 不上报
        assert rep["warning"] is None
    finally:
        _restore(old)


def test_empty_open_triggers():
    """规则③：卷首 scene1 无新 cast 且无 setting 锚词 → 触发。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘"]},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘"],  # 与上卷完全重合
                 "scope_summary": "林尘睡了一觉醒来吃了饭",
                 "scene_storyboard": [{"summary": "林尘睡了一觉醒来吃了饭"}]},
            ]})
        rep = mod.scan(proj)
        codes = [i["code"] for i in rep["issues"]]
        assert mod.EMPTY_OPEN_CODE in codes, rep
    finally:
        _restore(old)


# ═══════════════════════════════════════════════════════════════════════════
# 🔴 2026-07-01 语义覆盖率补齐（仅规则① hook 覆盖率：embedding cosine 替代 2-gram
# 字面重叠·规则②③是精确集合运算/固定 regex 锚词格式匹配，本次不改）
# ═══════════════════════════════════════════════════════════════════════════

def test_has_real_embedding_backend_false_by_default():
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v


def test_has_real_embedding_backend_true_when_set():
    old_eb = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "local"
        assert mod._has_real_embedding_backend() is True
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_default_close_hook_match_method_is_bigram():
    """🔴 零回归锁：未配置 EMBED_BACKEND（默认）→ close_hook_match_method 报字面
    bigram（与改动前行为逐字节一致）。复用 test_hook_miss_triggers 同款数据。"""
    old_mode = _set_mode("active")
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "林尘大战王虎",
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙将在北境苏醒，吞噬星辰"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘"],
                 "scope_summary": "他在山间打坐修炼",
                 "scene_storyboard": [{"summary": "他在山间打坐修炼"}]},
            ]})
        rep = mod.scan(proj)
        assert rep.get("close_hook_match_method") == "bigram_keyword_overlap", rep
        assert mod.HOOK_MISS_CODE in [i["code"] for i in rep["issues"]], rep
    finally:
        _restore(old_mode)
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v


def test_hook_miss_semantic_path_recognizes_synonym():
    """真 embedding 后端 mock：上卷末钩「巨龙即将苏醒」下卷首写「黑龙睁开双眼」字面
    2-gram 零重叠（literal 会误判钩零命中），embedding 余弦相似度应识别为同义 → 不
    误报 HOOK_MISS。验证语义路径被正确使用（close_hook_match_method=embedding_cosine）。"""
    old_mode = _set_mode("active")
    old_eb = os.environ.get("EMBED_BACKEND")
    try:
        last_finale = {"cluster_id": "c1", "volume": 1, "status": "done",
                       "chapter_range": [1, 3], "cast": ["林尘"],
                       "scope_summary": "林尘对峙巨龙",
                       "volume_transition_hooks": {
                           "close": {"hook_text": "巨龙即将苏醒"}
                       }}
        next_first = {"cluster_id": "c2", "volume": 2,
                     "cast": ["林尘"],
                     "scope_summary": "黑龙睁开双眼",
                     "scene_storyboard": [{"summary": "黑龙睁开双眼"}]}
        proj = _mk_project(shijianji={"clusters": [last_finale, next_first]})
        # 前置断言：用模块自身的抽取函数还原真实 open_text，确认字面 2-gram 确实零重叠
        # （同义改写零容错场景：literal 会误判钩零命中）
        _close_hook_text = mod._close_hook(last_finale)
        _open_text = mod._scene1_text(next_first) + " " + str(next_first.get("scope_summary") or "")
        assert not (mod._kw(_close_hook_text) & mod._kw(_open_text)), \
            (mod._kw(_close_hook_text), mod._kw(_open_text))

        os.environ["EMBED_BACKEND"] = "mock"
        import embedding_store
        orig = embedding_store.compute_embedding

        def _mock_embed(text):
            return [1.0, 0.0] if "龙" in text else [0.0, 1.0]

        embedding_store.compute_embedding = _mock_embed
        try:
            rep = mod.scan(proj)
        finally:
            embedding_store.compute_embedding = orig

        assert rep["close_hook_match_method"] == "embedding_cosine", rep
        assert rep["close_hook_coverage"] == 1.0, rep
        assert mod.HOOK_MISS_CODE not in [i["code"] for i in rep["issues"]], rep
    finally:
        _restore(old_mode)
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_hook_semantic_path_falls_back_when_encode_fails():
    """真后端配置但 embedding 编码异常 → 回退字面 bigram（不崩·不误判为语义路径）。"""
    old_mode = _set_mode("active")
    old_eb = os.environ.get("EMBED_BACKEND")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "林尘大战王虎",
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙将在北境苏醒，吞噬星辰"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘"],
                 "scope_summary": "他在山间打坐修炼",
                 "scene_storyboard": [{"summary": "他在山间打坐修炼"}]},
            ]})
        os.environ["EMBED_BACKEND"] = "mock"
        import embedding_store
        orig = embedding_store.compute_embedding

        def _boom(text):
            raise RuntimeError("模拟真后端编码失败")

        embedding_store.compute_embedding = _boom
        try:
            rep = mod.scan(proj)
        finally:
            embedding_store.compute_embedding = orig

        assert rep.get("close_hook_match_method") == "bigram_keyword_overlap", rep
        assert mod.HOOK_MISS_CODE in [i["code"] for i in rep["issues"]], rep
    finally:
        _restore(old_mode)
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_rule_2_hard_reset_untouched_by_semantic_backend():
    """规则②(cast硬重置=精确集合运算) 即便真 embedding 后端就绪也不该被语义化
    （本任务范围只改规则①）。复用 test_hard_reset_triggers 同款数据 + mock 后端。"""
    old_mode = _set_mode("active")
    old_eb = os.environ.get("EMBED_BACKEND")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "决战古城"},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["陌生甲", "陌生乙"],   # 完全不重叠 → 应仍触发②
                 "scope_summary": "新地点新人物",
                 "scene_storyboard": [{"summary": "陌生甲来到新的城里寻人"}]},
            ]})
        os.environ["EMBED_BACKEND"] = "mock"
        import embedding_store
        orig = embedding_store.compute_embedding
        embedding_store.compute_embedding = lambda text: [1.0, 0.0]
        try:
            rep = mod.scan(proj)
        finally:
            embedding_store.compute_embedding = orig
        codes = [i["code"] for i in rep["issues"]]
        assert mod.HARD_RESET_CODE in codes, rep
        assert rep["cast_overlap_ratio"] == 0, rep
    finally:
        _restore(old_mode)
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_prefetch_called_once_with_hook_and_open_text():
    """🔴 2026-07-03 Wave-4：语义路径下规则① 应一次性 prefetch [close_hook, open_text]
    两段文本，而非各自触发一次后端调用。"""
    old_mode = _set_mode("active")
    old_eb = os.environ.get("EMBED_BACKEND")
    try:
        last_finale = {"cluster_id": "c1", "volume": 1, "status": "done",
                       "chapter_range": [1, 3], "cast": ["林尘"],
                       "scope_summary": "林尘对峙巨龙",
                       "volume_transition_hooks": {
                           "close": {"hook_text": "巨龙即将苏醒"}
                       }}
        next_first = {"cluster_id": "c2", "volume": 2,
                     "cast": ["林尘"],
                     "scope_summary": "黑龙睁开双眼",
                     "scene_storyboard": [{"summary": "黑龙睁开双眼"}]}
        proj = _mk_project(shijianji={"clusters": [last_finale, next_first]})
        expected_close_hook = mod._close_hook(last_finale)
        expected_open_text = mod._scene1_text(next_first) + " " + str(next_first.get("scope_summary") or "")

        os.environ["EMBED_BACKEND"] = "mock"
        import embedding_store
        orig_embed = embedding_store.compute_embedding
        orig_prefetch = embedding_store.prefetch_embeddings
        calls = []

        def fake_prefetch(texts):
            calls.append(list(texts))
            return {"total": len(texts), "unique": len(set(texts)),
                    "cache_hits": 0, "computed": len(set(texts))}

        embedding_store.compute_embedding = lambda text: [1.0, 0.0] if "龙" in text else [0.0, 1.0]
        embedding_store.prefetch_embeddings = fake_prefetch
        try:
            rep = mod.scan(proj)
        finally:
            embedding_store.compute_embedding = orig_embed
            embedding_store.prefetch_embeddings = orig_prefetch

        assert len(calls) == 1, calls
        assert calls[0] == [expected_close_hook, expected_open_text], calls[0]
        assert rep["close_hook_match_method"] == "embedding_cosine", rep
    finally:
        _restore(old_mode)
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_prefetch_not_called_without_real_backend():
    """默认（无真后端）→ 规则①语义分支不执行 → prefetch_embeddings 零调用（零回归）。"""
    old_mode = _set_mode("active")
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "林尘大战王虎",
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙将在北境苏醒，吞噬星辰"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘"],
                 "scope_summary": "他在山间打坐修炼",
                 "scene_storyboard": [{"summary": "他在山间打坐修炼"}]},
            ]})
        import embedding_store
        orig_prefetch = embedding_store.prefetch_embeddings
        calls = []

        def fake_prefetch(texts):
            calls.append(list(texts))
            return {}

        embedding_store.prefetch_embeddings = fake_prefetch
        try:
            rep = mod.scan(proj)
        finally:
            embedding_store.prefetch_embeddings = orig_prefetch
        assert calls == []
        assert rep["close_hook_match_method"] == "bigram_keyword_overlap"
    finally:
        _restore(old_mode)
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v


def test_rule_3_empty_open_untouched_by_semantic_backend():
    """规则③(scene1 缺新钩=固定 regex 锚词匹配) 即便真 embedding 后端就绪也不该被
    语义化（本任务范围只改规则①）。复用 test_empty_open_triggers 同款数据 + mock 后端
    （注：规则③要求 cast 无新增，天然与规则②「cast 全无重叠」互斥，故与②分开测）。"""
    old_mode = _set_mode("active")
    old_eb = os.environ.get("EMBED_BACKEND")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘"]},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘"],  # 与上卷完全重合 → 无新 cast
                 "scope_summary": "林尘睡了一觉醒来吃了饭",
                 "scene_storyboard": [{"summary": "林尘睡了一觉醒来吃了饭"}]},  # 无锚词
            ]})
        os.environ["EMBED_BACKEND"] = "mock"
        import embedding_store
        orig = embedding_store.compute_embedding
        embedding_store.compute_embedding = lambda text: [1.0, 0.0]
        try:
            rep = mod.scan(proj)
        finally:
            embedding_store.compute_embedding = orig
        codes = [i["code"] for i in rep["issues"]]
        assert mod.EMPTY_OPEN_CODE in codes, rep
    finally:
        _restore(old_mode)
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)
